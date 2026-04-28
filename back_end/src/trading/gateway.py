"""
交易网关模块 - 网关抽象基类和内置实现
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..strategy import Signal, Order, Trade, Position
    from .types import AccountInfo, MarketData, TradingStatus
    from .errors import GatewayError

logger = logging.getLogger(__name__)

from .types import TradingStatus, AccountInfo, MarketData
from .errors import GatewayError


class GatewayBase(ABC):
    """交易通道基类"""

    def __init__(self, name: str):
        self.name = name
        self.status = TradingStatus.STOPPED
        self.orders: Dict[str, 'Order'] = {}
        self.positions: Dict[str, 'Position'] = {}
        self.account = AccountInfo()

        self.on_order_callback: Any = None
        self.on_trade_callback: Any = None
        self.on_position_callback: Any = None
        self.on_account_callback: Any = None
        self.on_tick_callback: Any = None
        self.on_error_callback: Any = None

    @abstractmethod
    def connect(self, config: Dict[str, Any]) -> bool:
        """连接"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def send_order(self, signal: 'Signal') -> str:
        """发送订单"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        pass

    @abstractmethod
    def query_account(self) -> AccountInfo:
        """查询账户"""
        pass

    @abstractmethod
    def query_positions(self) -> List['Position']:
        """查询持仓"""
        pass

    @abstractmethod
    def query_orders(self) -> List['Order']:
        """查询订单"""
        pass

    def on_order(self, order: 'Order'):
        """订单回调"""
        self.orders[order.order_id] = order
        if self.on_order_callback:
            try:
                self.on_order_callback(order)
            except Exception as e:
                logger.error(f"订单回调执行失败: {e}")

    def on_trade(self, trade: 'Trade'):
        """成交回调"""
        if self.on_trade_callback:
            try:
                self.on_trade_callback(trade)
            except Exception as e:
                logger.error(f"成交回调执行失败: {e}")

    def on_position(self, position: 'Position'):
        """持仓更新"""
        self.positions[position.symbol] = position
        if self.on_position_callback:
            try:
                self.on_position_callback(position)
            except Exception as e:
                logger.error(f"持仓回调执行失败: {e}")

    def on_account(self, account: AccountInfo):
        """账户更新"""
        self.account = account
        if self.on_account_callback:
            try:
                self.on_account_callback(account)
            except Exception as e:
                logger.error(f"账户回调执行失败: {e}")

    def on_tick(self, tick: MarketData):
        """行情推送"""
        if self.on_tick_callback:
            try:
                self.on_tick_callback(tick)
            except Exception as e:
                logger.error(f"行情回调执行失败: {e}")

    def on_error(self, error: Exception, context: str = ""):
        """错误回调"""
        logger.error(f"网关错误 ({context}): {error}")
        if self.on_error_callback:
            try:
                self.on_error_callback(error, context)
            except Exception as e:
                logger.error(f"错误回调执行失败: {e}")


class SimulatedGateway(GatewayBase):
    """模拟交易通道（用于模拟实盘测试）"""

    def __init__(self):
        from ..strategy import Direction, OrderStatus, Order, Trade, Position
        super().__init__("SIM")
        self.order_id_counter = 0
        self.trade_id_counter = 0
        self.latest_ticks: Dict[str, MarketData] = {}
        self._running = False
        self._tick_thread: Any = None
        self._lock = threading.Lock()

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            self.status = TradingStatus.CONNECTING
            logger.info(f"模拟交易通道连接中...")

            time.sleep(0.5)

            self.account = AccountInfo(
                account_id="SIM001",
                balance=config.get('initial_capital', 1000000.0),
                available=config.get('initial_capital', 1000000.0),
                margin=0.0,
                commission=0.0,
                position_pnl=0.0,
                total_pnl=0.0
            )

            self.status = TradingStatus.CONNECTED
            logger.info("模拟交易通道已连接")
            return True

        except Exception as e:
            logger.error(f"连接失败: {e}")
            self.status = TradingStatus.ERROR
            raise GatewayError(f"连接失败: {e}")

    def disconnect(self):
        try:
            self.status = TradingStatus.STOPPED
            self._running = False
            if self._tick_thread and self._tick_thread.is_alive():
                self._tick_thread.join(timeout=5)
            logger.info("模拟交易通道已断开")
        except Exception as e:
            logger.error(f"断开连接失败: {e}")

    def send_order(self, signal: 'Signal') -> str:
        from ..strategy import OrderStatus, Order, OffsetFlag
        try:
            with self._lock:
                self.order_id_counter += 1
                order_id = f"SIM_{self.order_id_counter}"

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

                self.orders[order_id] = order

            thread = threading.Thread(target=self._simulate_fill, args=(order, signal), daemon=True)
            thread.start()

            return order_id

        except Exception as e:
            logger.error(f"发送订单失败: {e}")
            raise GatewayError(f"发送订单失败: {e}")

    def _simulate_fill(self, order: 'Order', signal: 'Signal'):
        from ..strategy import Direction, OrderStatus, Trade, OffsetFlag
        try:
            time.sleep(0.1)

            tick = self.latest_ticks.get(signal.symbol)
            if tick:
                exec_price = tick.last_price
            else:
                exec_price = signal.price

            slip = exec_price * 0.0001
            exec_price = exec_price + slip if signal.direction == Direction.LONG else exec_price - slip

            order.status = OrderStatus.FILLED
            order.traded_volume = order.volume
            order.update_time = time.time()

            self.on_order(order)

            with self._lock:
                self.trade_id_counter += 1
                trade = Trade(
                    trade_id=f"TRADE_{self.trade_id_counter}",
                    order_id=order.order_id,
                    symbol=order.symbol,
                    direction=order.direction,
                    price=exec_price,
                    volume=order.volume,
                    commission=exec_price * order.volume * 0.0003,
                    trade_time=time.time()
                )

            offset = getattr(order, 'offset', OffsetFlag.OPEN)
            self._update_position(trade, offset)
            self.on_trade(trade)

        except Exception as e:
            logger.error(f"模拟成交失败: {e}")
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            self.on_order(order)

    def _update_position(self, trade: 'Trade', offset=None):
        from ..strategy import Direction, Position, OffsetFlag
        if offset is None:
            offset = OffsetFlag.OPEN
        with self._lock:
            if trade.symbol not in self.positions:
                self.positions[trade.symbol] = Position(
                    symbol=trade.symbol,
                    direction=Direction.NET,
                    volume=0
                )

            pos = self.positions[trade.symbol]

            is_close = offset in (OffsetFlag.CLOSE, OffsetFlag.CLOSE_TODAY, OffsetFlag.CLOSE_YESTERDAY)

            if is_close:
                # 平仓：减少持仓
                if trade.direction == Direction.LONG:
                    # 买入平空 → 空头减仓
                    pos.volume += trade.volume
                else:
                    # 卖出平多 → 多头减仓
                    pos.volume -= trade.volume

                if pos.volume > 0:
                    pos.direction = Direction.LONG
                elif pos.volume < 0:
                    pos.direction = Direction.SHORT
                else:
                    pos.direction = Direction.NET
            else:
                # 开仓
                if trade.direction == Direction.LONG:
                    pos.volume += trade.volume
                    if pos.volume > 0:
                        pos.direction = Direction.LONG
                    elif pos.volume < 0:
                        pos.direction = Direction.SHORT
                    else:
                        pos.direction = Direction.NET
                else:
                    pos.volume -= trade.volume
                    if pos.volume > 0:
                        pos.direction = Direction.LONG
                    elif pos.volume < 0:
                        pos.direction = Direction.SHORT
                    else:
                        pos.direction = Direction.NET

            self.on_position(pos)

    def cancel_order(self, order_id: str) -> bool:
        from ..strategy import OrderStatus
        try:
            with self._lock:
                if order_id in self.orders:
                    order = self.orders[order_id]
                    if order.is_active():
                        order.status = OrderStatus.CANCELLED
                        self.on_order(order)
                        return True
            return False
        except Exception as e:
            logger.error(f"撤销订单失败: {e}")
            return False

    def query_account(self) -> AccountInfo:
        return self.account

    def query_positions(self) -> List['Position']:
        with self._lock:
            return list(self.positions.values())

    def query_orders(self) -> List['Order']:
        with self._lock:
            return list(self.orders.values())

    def update_tick(self, symbol: str, price: float):
        """更新行情"""
        try:
            tick = MarketData(
                symbol=symbol,
                last_price=price,
                bid_price_1=price - 1,
                ask_price_1=price + 1,
                bid_volume_1=100,
                ask_volume_1=100,
                volume=1000,
                turnover=price * 1000
            )
            self.latest_ticks[symbol] = tick
            self.on_tick(tick)
        except Exception as e:
            logger.error(f"更新行情失败: {e}")

    def start_quote_simulation(self, symbols: List[str], base_prices: Dict[str, float]):
        """启动行情模拟"""
        self._running = True

        def generate_ticks():
            import random
            while self._running:
                try:
                    for symbol in symbols:
                        if symbol in base_prices:
                            change = base_prices[symbol] * random.uniform(-0.001, 0.001)
                            base_prices[symbol] += change
                            self.update_tick(symbol, base_prices[symbol])
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"行情模拟异常: {e}")

        self._tick_thread = threading.Thread(target=generate_ticks, daemon=True)
        self._tick_thread.start()


GATEWAY_REGISTRY = {
    "simulated": SimulatedGateway,
    "ctp": lambda: __import__(
        'src.trading.vnpy_gateway',
        fromlist=['create_vnpy_gateway']
    ).create_vnpy_gateway(),
    "vnpy": lambda: __import__(
        'src.trading.vnpy_gateway',
        fromlist=['create_vnpy_gateway']
    ).create_vnpy_gateway(),
}


def create_gateway(gateway_type: str = "simulated") -> GatewayBase:
    """创建网关实例"""
    if gateway_type not in GATEWAY_REGISTRY:
        logger.warning(f"未知网关类型: {gateway_type}，使用模拟网关")
        gateway_type = "simulated"
    return GATEWAY_REGISTRY[gateway_type]()
