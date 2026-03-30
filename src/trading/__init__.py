"""
实盘交易模块 - 交易执行、持仓管理
"""

import logging
import threading
import time
import traceback
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..strategy import (
    Signal, Order, Trade, Direction, OrderType, OrderStatus, Position
)
from ..common.exceptions import ExceptionHandler
from .order_manager import OrderManager, PreOrder, PreOrderType, PreOrderStatus

logger = logging.getLogger(__name__)


class TradingStatus(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    TRADING = "trading"
    ERROR = "error"


class TradingError(Exception):
    """交易异常"""
    pass


class GatewayError(Exception):
    """网关异常"""
    pass


@dataclass
class AccountInfo:
    """账户信息"""
    account_id: str = ""
    balance: float = 0.0
    available: float = 0.0
    margin: float = 0.0
    commission: float = 0.0
    position_pnl: float = 0.0
    total_pnl: float = 0.0
    error_msg: str = ""


@dataclass
class MarketData:
    """行情数据"""
    symbol: str
    last_price: float
    bid_price_1: float
    ask_price_1: float
    bid_volume_1: int
    ask_volume_1: int
    volume: int
    turnover: float
    timestamp: datetime = field(default_factory=datetime.now)
    error_msg: str = ""


class GatewayBase(ABC):
    """交易通道基类"""
    
    def __init__(self, name: str):
        self.name = name
        self.status = TradingStatus.STOPPED
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}
        self.account = AccountInfo()
        
        self.on_order_callback: Optional[Callable] = None
        self.on_trade_callback: Optional[Callable] = None
        self.on_position_callback: Optional[Callable] = None
        self.on_account_callback: Optional[Callable] = None
        self.on_tick_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None
    
    @abstractmethod
    def connect(self, config: Dict[str, Any]) -> bool:
        """连接"""
        pass
    
    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass
    
    @abstractmethod
    def send_order(self, signal: Signal) -> str:
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
    def query_positions(self) -> List[Position]:
        """查询持仓"""
        pass
    
    @abstractmethod
    def query_orders(self) -> List[Order]:
        """查询订单"""
        pass
    
    def on_order(self, order: Order):
        """订单回调"""
        self.orders[order.order_id] = order
        if self.on_order_callback:
            try:
                self.on_order_callback(order)
            except Exception as e:
                logger.error(f"订单回调执行失败: {e}")
    
    def on_trade(self, trade: Trade):
        """成交回调"""
        if self.on_trade_callback:
            try:
                self.on_trade_callback(trade)
            except Exception as e:
                logger.error(f"成交回调执行失败: {e}")
    
    def on_position(self, position: Position):
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
        super().__init__("SIM")
        self.order_id_counter = 0
        self.trade_id_counter = 0
        self.latest_ticks: Dict[str, MarketData] = {}
        self._running = False
        self._tick_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.exception_handler = ExceptionHandler()

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

    def send_order(self, signal: Signal) -> str:
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
                    status=OrderStatus.SUBMITTED
                )

                self.orders[order_id] = order

            thread = threading.Thread(target=self._simulate_fill, args=(order, signal), daemon=True)
            thread.start()

            return order_id

        except Exception as e:
            logger.error(f"发送订单失败: {e}")
            raise GatewayError(f"发送订单失败: {e}")

    def _simulate_fill(self, order: Order, signal: Signal):
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
            order.update_time = datetime.now()

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
                    trade_time=datetime.now()
                )

            self._update_position(trade)
            self.on_trade(trade)

        except Exception as e:
            logger.error(f"模拟成交失败: {e}")
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            self.on_order(order)

    def _update_position(self, trade: Trade):
        with self._lock:
            if trade.symbol not in self.positions:
                self.positions[trade.symbol] = Position(
                    symbol=trade.symbol,
                    direction=Direction.NET,
                    volume=0
                )

            pos = self.positions[trade.symbol]

            if trade.direction == Direction.LONG:
                if pos.volume >= 0:
                    pos.direction = Direction.LONG
                    pos.volume += trade.volume
                else:
                    pos.volume += trade.volume
                    if pos.volume > 0:
                        pos.direction = Direction.LONG
                    elif pos.volume < 0:
                        pos.direction = Direction.SHORT
                    else:
                        pos.direction = Direction.NET
            else:
                if pos.volume <= 0:
                    pos.direction = Direction.SHORT
                    pos.volume -= trade.volume
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

    def query_positions(self) -> List[Position]:
        with self._lock:
            return list(self.positions.values())

    def query_orders(self) -> List[Order]:
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
    "ctp": lambda: __import__('src.trading.ctp_gateway', fromlist=['create_ctp_gateway']).create_ctp_gateway(),
    "ctpplus": lambda: __import__('src.trading.ctp_plus_gateway', fromlist=['create_ctp_plus_gateway']).create_ctp_plus_gateway(),
}


def create_gateway(gateway_type: str = "simulated") -> GatewayBase:
    """创建网关实例"""
    if gateway_type not in GATEWAY_REGISTRY:
        logger.warning(f"未知网关类型: {gateway_type}, 使用模拟网关")
        gateway_type = "simulated"
    return GATEWAY_REGISTRY[gateway_type]()


class TradingEngine:
    """实盘交易引擎"""

    def __init__(self, gateway: GatewayBase = None):
        self.gateway = gateway or SimulatedGateway()
        self.strategy = None
        self.status = TradingStatus.STOPPED

        # 初始化订单管理器
        self.order_manager = OrderManager(self.gateway)
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
        self.gateway.on_error_callback = self._on_gateway_error

        # 设置订单管理器回调
        self.order_manager.on_order_callback = self._on_order
        self.order_manager.on_trade_callback = self._on_trade
        self.order_manager.on_pre_order_status_change = self._on_pre_order_status_change

        self._error_count = 0
        self._max_errors = 10
        self.exception_handler = ExceptionHandler()

    def set_strategy(self, strategy):
        """设置策略"""
        self.strategy = strategy

    def start(self, config: Dict[str, Any] = None) -> bool:
        """启动交易引擎"""
        try:
            if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
                logger.warning("交易引擎已在运行中")
                return False

            config = config or {}

            self.status = TradingStatus.CONNECTING

            if not self.gateway.connect(config):
                self.status = TradingStatus.ERROR
                return False

            # 启动订单管理器
            self.order_manager.start()

            if self.strategy:
                try:
                    self.strategy.on_init()
                    self.strategy.on_start()
                except Exception as e:
                    logger.error(f"策略初始化失败: {e}")
                    self.status = TradingStatus.ERROR
                    return False

            self.status = TradingStatus.TRADING
            logger.info("交易引擎已启动")
            return True

        except (ValueError, ImportError, ConnectionError, RuntimeError):
            self.status = TradingStatus.ERROR
            raise
        except Exception as e:
            logger.error(f"启动交易引擎失败: {e}\n{traceback.format_exc()}")
            self.status = TradingStatus.ERROR
            raise RuntimeError(f"启动交易引擎失败: {e}") from e

    def stop(self):
        """停止交易引擎"""
        try:
            if self.strategy:
                try:
                    self.strategy.on_stop()
                except Exception as e:
                    logger.error(f"策略停止失败: {e}")

            # 停止订单管理器
            self.order_manager.stop()

            self.gateway.disconnect()
            self.status = TradingStatus.STOPPED
            logger.info("交易引擎已停止")
        except Exception as e:
            logger.error(f"停止交易引擎失败: {e}")
            self.status = TradingStatus.STOPPED

    def send_signal(self, signal: Signal) -> str:
        """发送交易信号"""
        if self.status != TradingStatus.TRADING:
            logger.warning("交易引擎未在运行")
            return ""

        try:
            # 使用订单管理器提交订单
            order_id = self.order_manager.submit_order(signal)
            if order_id:
                logger.info(f"发送信号: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
            return order_id
        except Exception as e:
            self._error_count += 1
            logger.error(f"发送信号失败: {e}")
            if self._error_count >= self._max_errors:
                logger.error(f"错误次数过多 ({self._error_count}), 停止交易")
                self.stop()
            return ""

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        try:
            return self.order_manager.cancel_order(order_id)
        except Exception as e:
            logger.error(f"撤销订单失败: {e}")
            return False

    def place_pre_order(self, pre_order: PreOrder) -> str:
        """放置预埋单"""
        try:
            return self.order_manager.place_pre_order(pre_order)
        except Exception as e:
            logger.error(f"放置预埋单失败: {e}")
            return ""

    def cancel_pre_order(self, pre_order_id: str) -> bool:
        """撤销预埋单"""
        try:
            return self.order_manager.cancel_pre_order(pre_order_id)
        except Exception as e:
            logger.error(f"撤销预埋单失败: {e}")
            return False

    def update_market_data(self, symbol: str, data: dict):
        """更新市场数据，用于预埋单触发"""
        try:
            self.order_manager.update_market_data(symbol, data)
        except Exception as e:
            logger.error(f"更新市场数据失败: {e}")

    def get_account(self) -> AccountInfo:
        """获取账户信息"""
        try:
            return self.gateway.query_account()
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            return AccountInfo(error_msg=str(e))

    def get_positions(self) -> Dict[str, Position]:
        """获取持仓"""
        return self.gateway.positions

    def get_orders(self) -> Dict[str, Order]:
        """获取订单"""
        return self.order_manager.active_orders

    def get_pre_orders(self) -> Dict[str, PreOrder]:
        """获取预埋单"""
        return self.order_manager.pre_orders

    def _on_order(self, order: Order):
        """订单回调"""
        if self.strategy:
            try:
                self.strategy.on_order(order)
            except Exception as e:
                logger.error(f"策略订单回调失败: {e}")

    def _on_trade(self, trade: Trade):
        """成交回调"""
        if self.strategy:
            try:
                self.strategy.update_position(trade.symbol, trade)
                self.strategy.on_trade(trade)
            except Exception as e:
                logger.error(f"策略成交回调失败: {e}")

    def _on_pre_order_status_change(self, pre_order: PreOrder):
        """预埋单状态变更回调"""
        if self.strategy:
            try:
                logger.info(f"预埋单状态变更: {pre_order.pre_order_id} -> {pre_order.status.value}")
            except Exception as e:
                logger.error(f"预埋单状态变更回调失败: {e}")

    def _on_gateway_error(self, error: Exception, context: str = ""):
        """网关错误回调"""
        logger.error(f"网关错误 ({context}): {error}")
        self._error_count += 1
        if self._error_count >= self._max_errors:
            logger.error(f"错误次数过多，停止交易引擎")
            self.stop()
