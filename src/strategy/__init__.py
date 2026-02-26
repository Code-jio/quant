"""
策略模块 - 策略基类与示例策略
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class Direction(Enum):
    LONG = "long"
    SHORT = "short"
    NET = "net"


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class OrderStatus(Enum):
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PARTFILLED = "partfilled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Signal:
    """交易信号"""
    symbol: str
    datetime: datetime
    direction: Direction
    price: float
    volume: int
    order_type: OrderType = OrderType.MARKET
    stop_price: Optional[float] = None
    comment: str = ""


@dataclass
class Order:
    """订单"""
    order_id: str
    symbol: str
    direction: Direction
    order_type: OrderType
    price: float
    volume: int
    traded_volume: int = 0
    status: OrderStatus = OrderStatus.SUBMITTING
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)
    
    def is_active(self) -> bool:
        return self.status in [OrderStatus.SUBMITTING, OrderStatus.SUBMITTED, OrderStatus.PARTFILLED]


@dataclass
class Trade:
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    direction: Direction
    price: float
    volume: int
    commission: float = 0.0
    trade_time: datetime = field(default_factory=datetime.now)


@dataclass 
class Position:
    """持仓"""
    symbol: str
    direction: Direction
    volume: int
    frozen: int = 0
    price: float = 0.0
    cost: float = 0.0
    pnl: float = 0.0
    
    def __post_init__(self):
        if self.direction == Direction.NET:
            self.volume = abs(self.volume)
    
    @property
    def is_long(self) -> bool:
        return self.direction == Direction.LONG or (self.direction == Direction.NET and self.volume > 0)
    
    @property
    def is_short(self) -> bool:
        return self.direction == Direction.SHORT or (self.direction == Direction.NET and self.volume < 0)
    
    @property
    def is_empty(self) -> bool:
        return self.volume == 0


class StrategyBase(ABC):
    """策略基类"""
    
    def __init__(self, name: str, params: Dict[str, Any] = None):
        self.name = name
        self.params = params or {}
        self.indicators: Dict[str, pd.DataFrame] = {}
        self.signals: List[Signal] = []
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.trades: List[Trade] = []
        self.data: Dict[str, pd.DataFrame] = {}
        self.current_date: Optional[datetime] = None
        self.initial_capital: float = 1000000.0
        self.current_capital: float = 1000000.0
        
    @abstractmethod
    def on_init(self):
        """策略初始化 - 设置参数、加载指标等"""
        pass
    
    @abstractmethod
    def on_bar(self, bar: pd.Series):
        """K线回调 - 核心策略逻辑"""
        pass
    
    def on_start(self):
        """策略启动回调"""
        logger.info(f"策略 {self.name} 启动")
    
    def on_stop(self):
        """策略停止回调"""
        logger.info(f"策略 {self.name} 停止")
    
    def on_order(self, order: Order):
        """订单更新回调"""
        pass
    
    def on_trade(self, trade: Trade):
        """成交回调"""
        pass
    
    def buy(self, symbol: str, price: float, volume: int, 
            order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """买入开多"""
        if volume <= 0:
            return None
        signal = Signal(
            symbol=symbol,
            datetime=self.current_date,
            direction=Direction.LONG,
            price=price,
            volume=volume,
            order_type=order_type,
            comment="buy_open"
        )
        self.signals.append(signal)
        return signal
    
    def sell(self, symbol: str, price: float, volume: int,
             order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """卖出平多"""
        if volume <= 0:
            return None
        signal = Signal(
            symbol=symbol,
            datetime=self.current_date,
            direction=Direction.SHORT,
            price=price,
            volume=volume,
            order_type=order_type,
            comment="sell_close"
        )
        self.signals.append(signal)
        return signal
    
    def short(self, symbol: str, price: float, volume: int,
              order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """卖出开空"""
        if volume <= 0:
            return None
        signal = Signal(
            symbol=symbol,
            datetime=self.current_date,
            direction=Direction.SHORT,
            price=price,
            volume=volume,
            order_type=order_type,
            comment="short_open"
        )
        self.signals.append(signal)
        return signal
    
    def cover(self, symbol: str, price: float, volume: int,
              order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """买入平空"""
        if volume <= 0:
            return None
        signal = Signal(
            symbol=symbol,
            datetime=self.current_date,
            direction=Direction.LONG,
            price=price,
            volume=volume,
            order_type=order_type,
            comment="cover_close"
        )
        self.signals.append(signal)
        return signal
    
    def get_position(self, symbol: str) -> Position:
        """获取持仓"""
        return self.positions.get(symbol, Position(symbol=symbol, direction=Direction.NET, volume=0))
    
    def get_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取历史数据"""
        return self.data.get(symbol)
    
    def update_position(self, symbol: str, trade: Trade):
        """更新持仓"""
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol, direction=Direction.NET, volume=0)
        
        pos = self.positions[symbol]
        if trade.direction == Direction.LONG:
            if pos.volume >= 0:
                pos.direction = Direction.LONG
                pos.volume += trade.volume
            else:
                if abs(trade.volume) >= abs(pos.volume):
                    pos.volume = trade.volume + pos.volume
                    pos.direction = Direction.LONG if pos.volume > 0 else Direction.SHORT
                else:
                    pos.volume += trade.volume
        else:
            if pos.volume <= 0:
                pos.direction = Direction.SHORT
                pos.volume -= trade.volume
            else:
                if trade.volume >= pos.volume:
                    pos.volume = pos.volume - trade.volume
                    pos.direction = Direction.SHORT if pos.volume < 0 else Direction.LONG
                else:
                    pos.volume -= trade.volume
        
        if pos.volume == 0:
            pos.direction = Direction.NET
            pos.price = 0
            pos.cost = 0
        else:
            if pos.is_long:
                pos.cost = (pos.cost * (abs(pos.volume) - trade.volume) + trade.price * trade.volume) / abs(pos.volume)
            else:
                pos.cost = (pos.cost * (abs(pos.volume) - trade.volume) + trade.price * trade.volume) / abs(pos.volume)
        
        pos.pnl = 0


class MACrossStrategy(StrategyBase):
    """双均线策略"""
    
    def on_init(self):
        self.symbol = self.params.get('symbol', 'IF9999')
        self.fast_period = self.params.get('fast_period', 10)
        self.slow_period = self.params.get('slow_period', 20)
        self.position_ratio = self.params.get('position_ratio', 0.8)
        
        logger.info(f"双均线策略初始化: fast={self.fast_period}, slow={self.slow_period}")
    
    def on_bar(self, bar: pd.Series):
        symbol = self.symbol
        df = self.get_data(symbol)
        
        if df is None or len(df) < self.slow_period:
            return
        
        current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
        if current_idx < self.slow_period:
            return
        
        fast_ma = df['close'].rolling(window=self.fast_period).mean()
        slow_ma = df['close'].rolling(window=self.slow_period).mean()
        
        prev_fast = fast_ma.iloc[current_idx - 1]
        prev_slow = slow_ma.iloc[current_idx - 1]
        curr_fast = fast_ma.iloc[current_idx]
        curr_slow = slow_ma.iloc[current_idx]
        
        pos = self.get_position(symbol)
        
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            if pos.is_short or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.cover(symbol, bar['close'], abs(pos.volume))
                    self.buy(symbol, bar['close'], volume)
        
        elif prev_fast >= prev_slow and curr_fast < curr_slow:
            if pos.is_long or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.sell(symbol, bar['close'], pos.volume)
                    self.short(symbol, bar['close'], volume)


class RSIStrategy(StrategyBase):
    """RSI均值回归策略"""
    
    def on_init(self):
        self.symbol = self.params.get('symbol', 'IF9999')
        self.rsi_period = self.params.get('rsi_period', 14)
        self.oversold = self.params.get('oversold', 30)
        self.overbought = self.params.get('overbought', 70)
        self.position_ratio = self.params.get('position_ratio', 0.8)
        
        logger.info(f"RSI策略初始化: period={self.rsi_period}, oversold={self.oversold}, overbought={self.overbought}")
    
    def on_bar(self, bar: pd.Series):
        symbol = self.symbol
        df = self.get_data(symbol)
        
        if df is None or len(df) < self.rsi_period + 1:
            return
        
        current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
        if current_idx < self.rsi_period + 1:
            return
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        current_rsi = rsi.iloc[current_idx]
        
        pos = self.get_position(symbol)
        
        if current_rsi < self.oversold:
            if pos.is_short or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.cover(symbol, bar['close'], abs(pos.volume))
                    self.buy(symbol, bar['close'], volume)
        
        elif current_rsi > self.overbought:
            if pos.is_long or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.sell(symbol, bar['close'], pos.volume)
                    self.short(symbol, bar['close'], volume)


class BreakoutStrategy(StrategyBase):
    """突破策略"""
    
    def on_init(self):
        self.symbol = self.params.get('symbol', 'IF9999')
        self.lookback_period = self.params.get('lookback_period', 20)
        self.position_ratio = self.params.get('position_ratio', 0.8)
        
        logger.info(f"突破策略初始化: lookback={self.lookback_period}")
    
    def on_bar(self, bar: pd.Series):
        symbol = self.symbol
        df = self.get_data(symbol)
        
        if df is None or len(df) < self.lookback_period:
            return
        
        current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
        if current_idx < self.lookback_period:
            return
        
        recent_high = df['high'].rolling(window=self.lookback_period).max().iloc[current_idx - 1]
        recent_low = df['low'].rolling(window=self.lookback_period).min().iloc[current_idx - 1]
        
        pos = self.get_position(symbol)
        
        if bar['close'] > recent_high:
            if pos.is_short or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.cover(symbol, bar['close'], abs(pos.volume))
                    self.buy(symbol, bar['close'], volume)
        
        elif bar['close'] < recent_low:
            if pos.is_long or pos.is_empty:
                volume = int((self.initial_capital * self.position_ratio) / bar['close'] / 100) * 100
                if volume > 0:
                    self.sell(symbol, bar['close'], pos.volume)
                    self.short(symbol, bar['close'], volume)


STRATEGY_REGISTRY = {
    'ma_cross': MACrossStrategy,
    'rsi': RSIStrategy,
    'breakout': BreakoutStrategy,
}


def create_strategy(strategy_name: str, params: Dict[str, Any] = None) -> StrategyBase:
    """工厂函数：创建策略实例"""
    if strategy_name not in STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {strategy_name}, 可用: {list(STRATEGY_REGISTRY.keys())}")
    return STRATEGY_REGISTRY[strategy_name](strategy_name, params)
