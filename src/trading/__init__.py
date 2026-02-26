"""
实盘交易模块 - 交易执行、持仓管理
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..strategy import (
    Signal, Order, Trade, Direction, OrderType, OrderStatus, Position
)

logger = logging.getLogger(__name__)


class TradingStatus(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    TRADING = "trading"
    ERROR = "error"


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
            self.on_order_callback(order)
    
    def on_trade(self, trade: Trade):
        """成交回调"""
        if self.on_trade_callback:
            self.on_trade_callback(trade)
    
    def on_position(self, position: Position):
        """持仓更新"""
        self.positions[position.symbol] = position
        if self.on_position_callback:
            self.on_position_callback(position)
    
    def on_account(self, account: AccountInfo):
        """账户更新"""
        self.account = account
        if self.on_account_callback:
            self.on_account_callback(account)
    
    def on_tick(self, tick: MarketData):
        """行情推送"""
        if self.on_tick_callback:
            self.on_tick_callback(tick)


class SimulatedGateway(GatewayBase):
    """模拟交易通道（用于模拟实盘测试）"""
    
    def __init__(self):
        super().__init__("SIM")
        self.order_id_counter = 0
        self.trade_id_counter = 0
        self.latest_ticks: Dict[str, MarketData] = {}
        self._running = False
        self._tick_thread: Optional[threading.Thread] = None
    
    def connect(self, config: Dict[str, Any]) -> bool:
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
    
    def disconnect(self):
        self.status = TradingStatus.STOPPED
        self._running = False
        if self._tick_thread:
            self._tick_thread.join()
        logger.info("模拟交易通道已断开")
    
    def send_order(self, signal: Signal) -> str:
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
        
        threading.Thread(target=self._simulate_fill, args=(order, signal), daemon=True).start()
        
        return order_id
    
    def _simulate_fill(self, order: Order, signal: Signal):
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
    
    def _update_position(self, trade: Trade):
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
        if order_id in self.orders:
            order = self.orders[order_id]
            if order.is_active():
                order.status = OrderStatus.CANCELLED
                self.on_order(order)
                return True
        return False
    
    def query_account(self) -> AccountInfo:
        return self.account
    
    def query_positions(self) -> List[Position]:
        return list(self.positions.values())
    
    def query_orders(self) -> List[Order]:
        return list(self.orders.values())
    
    def update_tick(self, symbol: str, price: float):
        """更新行情"""
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
    
    def start_quote_simulation(self, symbols: List[str], base_prices: Dict[str, float]):
        """启动行情模拟"""
        self._running = True
        
        def generate_ticks():
            import random
            while self._running:
                for symbol in symbols:
                    if symbol in base_prices:
                        change = base_prices[symbol] * random.uniform(-0.001, 0.001)
                        base_prices[symbol] += change
                        self.update_tick(symbol, base_prices[symbol])
                time.sleep(1)
        
        self._tick_thread = threading.Thread(target=generate_ticks, daemon=True)
        self._tick_thread.start()


class TradingEngine:
    """实盘交易引擎"""
    
    def __init__(self, gateway: GatewayBase = None):
        self.gateway = gateway or SimulatedGateway()
        self.strategy = None
        self.status = TradingStatus.STOPPED
        
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
    
    def set_strategy(self, strategy):
        """设置策略"""
        self.strategy = strategy
    
    def start(self, config: Dict[str, Any] = None) -> bool:
        """启动交易引擎"""
        if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
            logger.warning("交易引擎已在运行中")
            return False
        
        config = config or {}
        
        self.status = TradingStatus.CONNECTING
        
        if not self.gateway.connect(config):
            self.status = TradingStatus.ERROR
            return False
        
        if self.strategy:
            self.strategy.on_init()
            self.strategy.on_start()
        
        self.status = TradingStatus.TRADING
        logger.info("交易引擎已启动")
        return True
    
    def stop(self):
        """停止交易引擎"""
        if self.strategy:
            self.strategy.on_stop()
        
        self.gateway.disconnect()
        self.status = TradingStatus.STOPPED
        logger.info("交易引擎已停止")
    
    def send_signal(self, signal: Signal) -> str:
        """发送交易信号"""
        if self.status != TradingStatus.TRADING:
            logger.warning("交易引擎未在运行")
            return ""
        
        order_id = self.gateway.send_order(signal)
        logger.info(f"发送信号: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        return self.gateway.cancel_order(order_id)
    
    def get_account(self) -> AccountInfo:
        """获取账户信息"""
        return self.gateway.query_account()
    
    def get_positions(self) -> Dict[str, Position]:
        """获取持仓"""
        return self.gateway.positions
    
    def get_orders(self) -> Dict[str, Order]:
        """获取订单"""
        return self.gateway.orders
    
    def _on_order(self, order: Order):
        """订单回调"""
        if self.strategy:
            self.strategy.on_order(order)
    
    def _on_trade(self, trade: Trade):
        """成交回调"""
        if self.strategy:
            self.strategy.update_position(trade.symbol, trade)
            self.strategy.on_trade(trade)
