"""
订单管理器 - 管理所有订单相关的操作，包括普通报单、撤单和预埋单
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Callable
from datetime import datetime
from enum import Enum
import uuid
from dataclasses import dataclass, field

from ..strategy import Signal, Order, Trade, Direction, OrderType, OrderStatus, OffsetFlag

logger = logging.getLogger(__name__)


class PreOrderType(Enum):
    """预埋单类型"""
    STOP_ENTRY = "stop_entry"  # 触发入场
    LIMIT_ENTRY = "limit_entry"  # 限价入场
    STOP_LOSS = "stop_loss"  # 止损
    TAKE_PROFIT = "take_profit"  # 止盈
    TRAILING_STOP = "trailing_stop"  # 移动止损


class PreOrderStatus(Enum):
    """预埋单状态"""
    PENDING = "pending"  # 待触发
    TRIGGERED = "triggered"  # 已触发
    CANCELLED = "cancelled"  # 已取消
    EXPIRED = "expired"  # 已过期


class OrderManager:
    """订单管理器"""

    def __init__(self, gateway):  # 使用对象而非类型提示避免循环导入
        self.gateway = gateway
        self.lock = threading.RLock()

        # 普通订单存储
        self.active_orders: Dict[str, Order] = {}  # 活跃订单
        self.completed_orders: Dict[str, Order] = {}  # 已完成订单

        # 预埋单存储
        self.pre_orders: Dict[str, 'PreOrder'] = {}  # 预埋单
        self.active_pre_orders: Dict[str, 'PreOrder'] = {}  # 激活的预埋单

        # 回调函数
        self.on_order_callback: Optional[Callable] = None
        self.on_trade_callback: Optional[Callable] = None
        self.on_pre_order_status_change: Optional[Callable] = None

        # 市场数据
        self.market_data: Dict[str, dict] = {}

        # 控制线程
        self._running = False
        self._monitor_thread = None

    def start(self):
        """启动订单管理器"""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_pre_orders, daemon=True)
        self._monitor_thread.start()
        logger.info("订单管理器已启动")

    def stop(self):
        """停止订单管理器"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
        logger.info("订单管理器已停止")

    def submit_order(self, signal: Signal) -> str:
        """提交普通订单"""
        with self.lock:
            order_id = self.gateway.send_order(signal)
            if order_id:
                # 创建本地订单记录
                order = Order(
                    order_id=order_id,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    order_type=signal.order_type,
                    price=signal.price,
                    volume=signal.volume,
                    status=OrderStatus.SUBMITTED,
                    offset=getattr(signal, 'offset', OffsetFlag.OPEN),
                )
                self.active_orders[order_id] = order

                # 更新网关中的订单
                self.gateway.orders[order_id] = order

                logger.info(f"普通订单已提交: {order_id} - {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
            return order_id

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        with self.lock:
            # 先尝试在网关层撤销
            success = self.gateway.cancel_order(order_id)
            if success and order_id in self.active_orders:
                order = self.active_orders[order_id]
                order.status = OrderStatus.CANCELLED
                order.update_time = datetime.now()

                # 移动到已完成订单
                self.completed_orders[order_id] = self.active_orders.pop(order_id)

                # 更新网关中的订单状态
                if order_id in self.gateway.orders:
                    self.gateway.orders[order_id] = order

                # 调用回调
                if self.on_order_callback:
                    self.on_order_callback(order)

                logger.info(f"订单已撤销: {order_id}")
            elif success and order_id in self.gateway.orders:
                # 如果在网关中有但在管理器中没有，则直接更新网关订单
                order = self.gateway.orders[order_id]
                order.status = OrderStatus.CANCELLED
                order.update_time = datetime.now()

                if self.on_order_callback:
                    self.on_order_callback(order)

            return success

    def batch_submit_orders(self, signals: List[Signal]) -> List[str]:
        """批量提交订单"""
        order_ids = []
        for signal in signals:
            order_id = self.submit_order(signal)
            if order_id:
                order_ids.append(order_id)
        return order_ids

    def batch_cancel_orders(self, order_ids: List[str]) -> List[bool]:
        """批量撤销订单"""
        results = []
        for order_id in order_ids:
            result = self.cancel_order(order_id)
            results.append(result)
        return results

    def modify_order(self, order_id: str, new_price: Optional[float] = None, new_volume: Optional[int] = None) -> bool:
        """修改订单（实际上是撤销原订单并提交新订单）"""
        with self.lock:
            if order_id not in self.active_orders:
                logger.warning(f"订单不存在或已失效: {order_id}")
                return False

            original_order = self.active_orders[order_id]

            # 撤销原订单
            if not self.cancel_order(order_id):
                logger.error(f"无法撤销原订单: {order_id}")
                return False

            # 创建新订单
            new_signal = Signal(
                symbol=original_order.symbol,
                datetime=datetime.now(),
                direction=original_order.direction,
                price=new_price if new_price is not None else original_order.price,
                volume=new_volume if new_volume is not None else original_order.volume,
                order_type=original_order.order_type,
                comment=f"修改订单: 替代{order_id}"
            )

            new_order_id = self.submit_order(new_signal)
            if new_order_id:
                logger.info(f"订单已修改: {order_id} -> {new_order_id}")
                return True
            else:
                logger.error(f"无法创建新订单，尝试恢复原订单: {order_id}")
                rollback_id = self.submit_order(Signal(
                    symbol=original_order.symbol,
                    datetime=original_order.create_time or datetime.now(),
                    direction=original_order.direction,
                    price=original_order.price,
                    volume=original_order.volume,
                    order_type=original_order.order_type
                ))
                if rollback_id:
                    logger.info(f"原订单已恢复: {order_id} -> {rollback_id}")
                else:
                    logger.error(f"原订单恢复失败: {order_id}")
                return False

    def place_pre_order(self, pre_order: 'PreOrder') -> str:
        """放置预埋单"""
        with self.lock:
            # 生成预埋单ID
            pre_order_id = f"PRE_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8].upper()}"
            pre_order.pre_order_id = pre_order_id
            pre_order.status = PreOrderStatus.PENDING

            self.pre_orders[pre_order_id] = pre_order
            self.active_pre_orders[pre_order_id] = pre_order

            logger.info(f"预埋单已放置: {pre_order_id} - 类型:{pre_order.type.value} {pre_order.symbol} {pre_order.direction.value} {pre_order.volume}@{pre_order.trigger_price}")

            return pre_order_id

    def cancel_pre_order(self, pre_order_id: str) -> bool:
        """撤销预埋单"""
        with self.lock:
            if pre_order_id not in self.active_pre_orders:
                logger.warning(f"预埋单不存在或已失效: {pre_order_id}")
                return False

            pre_order = self.active_pre_orders[pre_order_id]
            pre_order.status = PreOrderStatus.CANCELLED
            pre_order.update_time = datetime.now()

            # 从活跃列表移除
            del self.active_pre_orders[pre_order_id]

            # 调用状态变化回调
            if self.on_pre_order_status_change:
                self.on_pre_order_status_change(pre_order)

            logger.info(f"预埋单已撤销: {pre_order_id}")
            return True

    def update_market_data(self, symbol: str, data: dict):
        """更新市场数据，用于监控预埋单触发条件"""
        self.market_data[symbol] = data

        # 检查相关的预埋单是否应被触发
        self._check_pre_order_triggers(symbol)

    def get_order(self, order_id: str) -> Optional[Order]:
        """获取订单"""
        with self.lock:
            return self.active_orders.get(order_id) or self.completed_orders.get(order_id)

    def get_pre_order(self, pre_order_id: str) -> Optional['PreOrder']:
        """获取预埋单"""
        with self.lock:
            return self.pre_orders.get(pre_order_id)

    def get_active_orders(self) -> List[Order]:
        """获取活跃订单"""
        with self.lock:
            return list(self.active_orders.values())

    def get_completed_orders(self) -> List[Order]:
        """获取已完成订单（包含已成交、已撤销、被拒等）"""
        with self.lock:
            return list(self.completed_orders.values())

    def get_active_pre_orders(self) -> List['PreOrder']:
        """获取活跃预埋单"""
        with self.lock:
            return list(self.active_pre_orders.values())

    def get_all_orders(self) -> List[Order]:
        """获取所有订单"""
        with self.lock:
            return list(self.active_orders.values()) + list(self.completed_orders.values())

    def get_orders_by_symbol(self, symbol: str) -> List[Order]:
        """获取指定品种的所有订单"""
        with self.lock:
            all_orders = self.get_all_orders()
            return [order for order in all_orders if order.symbol == symbol]

    def get_orders_by_status(self, status: OrderStatus) -> List[Order]:
        """获取指定状态的订单"""
        with self.lock:
            all_orders = self.get_all_orders()
            return [order for order in all_orders if order.status == status]

    def _check_pre_order_triggers(self, symbol: str):
        """检查指定品种的预埋单触发条件"""
        if symbol not in self.market_data:
            return

        market = self.market_data[symbol]
        current_price = market.get('last_price', 0)

        with self.lock:
            # 遍历活跃预埋单
            to_trigger = []
            for pre_order_id, pre_order in list(self.active_pre_orders.items()):
                if pre_order.symbol != symbol:
                    continue

                # 检查是否满足触发条件
                if self._should_trigger(pre_order, current_price):
                    to_trigger.append(pre_order_id)

            # 触发符合条件的预埋单
            for pre_order_id in to_trigger:
                pre_order = self.active_pre_orders[pre_order_id]
                self._trigger_pre_order(pre_order)

    def _should_trigger(self, pre_order: 'PreOrder', current_price: float) -> bool:
        """判断预埋单是否应被触发"""
        if pre_order.expires_at and datetime.now() > pre_order.expires_at:
            pre_order.status = PreOrderStatus.EXPIRED
            if pre_order.pre_order_id in self.active_pre_orders:
                del self.active_pre_orders[pre_order.pre_order_id]
            if self.on_pre_order_status_change:
                self.on_pre_order_status_change(pre_order)
            return False

        trigger_price = pre_order.trigger_price
        order_type = pre_order.type

        if order_type in [PreOrderType.STOP_ENTRY, PreOrderType.STOP_LOSS]:
            # 停损单：当价格达到或超过触发价时执行
            if pre_order.direction == Direction.LONG:
                # 买单 - 当价格上涨至或超过触发价时执行
                return current_price >= trigger_price
            else:
                # 卖单 - 当价格下跌至或低于触发价时执行
                return current_price <= trigger_price
        elif order_type in [PreOrderType.LIMIT_ENTRY, PreOrderType.TAKE_PROFIT]:
            # 限价单：当价格回落至触发价时执行
            if pre_order.direction == Direction.LONG:
                # 买单 - 当价格下跌至或低于触发价时执行
                return current_price <= trigger_price
            else:
                # 卖单 - 当价格上涨至或高于触发价时执行
                return current_price >= trigger_price
        elif order_type == PreOrderType.TRAILING_STOP:
            # 移动止损逻辑稍复杂，需要跟踪价格走势
            return self._check_trailing_stop_trigger(pre_order, current_price)

        return False

    def _check_trailing_stop_trigger(self, pre_order: 'PreOrder', current_price: float) -> bool:
        """检查移动止损预埋单触发条件。

        移动止损会随价格朝有利方向移动而收紧止损线，但不会向不利方向回退。
        """
        trailing_pct = pre_order.trailing_percent
        if trailing_pct <= 0:
            return False

        if pre_order.direction == Direction.LONG:
            best_price = getattr(pre_order, "_trail_best_price", None)
            if best_price is None or current_price > best_price:
                pre_order._trail_best_price = current_price
                pre_order.trigger_price = current_price * (1 - trailing_pct / 100)
                return False
            return current_price <= pre_order.trigger_price
        else:
            best_price = getattr(pre_order, "_trail_best_price", None)
            if best_price is None or current_price < best_price:
                pre_order._trail_best_price = current_price
                pre_order.trigger_price = current_price * (1 + trailing_pct / 100)
                return False
            return current_price >= pre_order.trigger_price

    def _trigger_pre_order(self, pre_order: 'PreOrder'):
        """触发预埋单"""
        # 更新状态
        pre_order.status = PreOrderStatus.TRIGGERED
        pre_order.update_time = datetime.now()

        # 从活跃列表移除
        if pre_order.pre_order_id in self.active_pre_orders:
            del self.active_pre_orders[pre_order.pre_order_id]

        # 创建普通订单并提交
        signal = Signal(
            symbol=pre_order.symbol,
            datetime=datetime.now(),
            direction=pre_order.direction,
            price=pre_order.exec_price if pre_order.exec_price > 0 else pre_order.trigger_price,
            volume=pre_order.volume,
            order_type=pre_order.order_type or OrderType.MARKET,
            comment=f"由预埋单触发: {pre_order.type.value}"
        )

        # 提交订单
        order_id = self.submit_order(signal)
        pre_order.related_order_id = order_id

        logger.info(f"预埋单已触发: {pre_order.pre_order_id} -> 订单{order_id}")

        # 调用状态变化回调
        if self.on_pre_order_status_change:
            self.on_pre_order_status_change(pre_order)

    def _monitor_pre_orders(self):
        """监控线程 - 检查预埋单触发条件"""
        while self._running:
            try:
                # 临时保存当前市场价格数据的副本进行检查
                with self.lock:
                    symbols_to_check = list(self.market_data.keys())

                for symbol in symbols_to_check:
                    self._check_pre_order_triggers(symbol)

                time.sleep(0.1)  # 每100毫秒检查一次
            except Exception as e:
                logger.error(f"监控预埋单时出错: {e}")
                time.sleep(1)


@dataclass
class PreOrder:
    """预埋单定义"""
    type: PreOrderType
    symbol: str
    direction: Direction
    volume: int
    trigger_price: float  # 触发价格
    exec_price: float = 0  # 执行价格，为0表示市价执行
    order_type: OrderType = OrderType.MARKET
    trailing_percent: float = 0.0  # 移动止损百分比
    expires_at: Optional[datetime] = None  # 过期时间
    comment: str = ""

    # 内部字段
    pre_order_id: str = ""
    status: PreOrderStatus = PreOrderStatus.PENDING
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)
    related_order_id: str = ""  # 关联的实际订单ID

    def is_active(self) -> bool:
        """是否为活跃预埋单"""
        return self.status == PreOrderStatus.PENDING