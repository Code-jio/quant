"""
策略模块 - 策略基类与示例策略
"""

import logging
import traceback
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


class SignalError(Exception):
    """信号生成错误"""
    pass


class OrderError(Exception):
    """订单错误"""
    pass


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
    
    def validate(self) -> bool:
        """验证信号有效性"""
        if not self.symbol:
            return False
        if self.price <= 0:
            return False
        if self.volume <= 0:
            return False
        return True


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
    error_msg: str = ""
    
    def is_active(self) -> bool:
        return self.status in [OrderStatus.SUBMITTING, OrderStatus.SUBMITTED, OrderStatus.PARTFILLED]
    
    def validate(self) -> bool:
        """验证订单有效性"""
        if not self.symbol:
            return False
        if self.price <= 0:
            return False
        if self.volume <= 0:
            return False
        return True


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
    pnl: float = 0.0
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


class StrategyError(Exception):
    """策略错误"""
    pass


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
        self._initialized = False
        self._error_count = 0
        self._max_errors = 10
        
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
    
    def on_error(self, error: Exception, context: str = ""):
        """策略错误回调"""
        self._error_count += 1
        logger.error(f"策略 {self.name} 错误 ({context}): {error}")
        if self._error_count >= self._max_errors:
            logger.error(f"策略 {self.name} 错误次数过多 ({self._error_count}), 停止策略")
            return False
        return True
    
    def buy(self, symbol: str, price: float, volume: int, 
            order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """买入开多"""
        try:
            if volume <= 0:
                logger.warning(f"买入数量无效: {volume}")
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
            
            if not signal.validate():
                logger.warning(f"信号验证失败: {symbol}")
                return None
            
            self.signals.append(signal)
            return signal
            
        except Exception as e:
            self.on_error(e, "buy")
            return None
    
    def sell(self, symbol: str, price: float, volume: int,
             order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """卖出平多"""
        try:
            if volume <= 0:
                logger.warning(f"卖出数量无效: {volume}")
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
            
            if not signal.validate():
                logger.warning(f"信号验证失败: {symbol}")
                return None
            
            self.signals.append(signal)
            return signal
            
        except Exception as e:
            self.on_error(e, "sell")
            return None
    
    def short(self, symbol: str, price: float, volume: int,
              order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """卖出开空"""
        try:
            if volume <= 0:
                logger.warning(f"做空数量无效: {volume}")
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
            
            if not signal.validate():
                logger.warning(f"信号验证失败: {symbol}")
                return None
            
            self.signals.append(signal)
            return signal
            
        except Exception as e:
            self.on_error(e, "short")
            return None
    
    def cover(self, symbol: str, price: float, volume: int,
              order_type: OrderType = OrderType.MARKET) -> Optional[Signal]:
        """买入平空"""
        try:
            if volume <= 0:
                logger.warning(f"平空数量无效: {volume}")
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
            
            if not signal.validate():
                logger.warning(f"信号验证失败: {symbol}")
                return None
            
            self.signals.append(signal)
            return signal
            
        except Exception as e:
            self.on_error(e, "cover")
            return None
    
    def get_position(self, symbol: str) -> Position:
        """获取持仓"""
        return self.positions.get(symbol, Position(symbol=symbol, direction=Direction.NET, volume=0))
    
    def get_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取历史数据"""
        return self.data.get(symbol)
    
    def update_position(self, symbol: str, trade: Trade):
        """更新持仓"""
        try:
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
            
        except Exception as e:
            self.on_error(e, "update_position")


class MACrossStrategy(StrategyBase):
    """双均线策略"""
    
    def on_init(self):
        try:
            self.symbol = self.params.get('symbol', 'IF9999')
            self.fast_period = self.params.get('fast_period', 10)
            self.slow_period = self.params.get('slow_period', 20)
            self.position_ratio = self.params.get('position_ratio', 0.8)
            
            if self.fast_period >= self.slow_period:
                raise StrategyError(f"fast_period ({self.fast_period}) 必须小于 slow_period ({self.slow_period})")
            
            logger.info(f"双均线策略初始化: fast={self.fast_period}, slow={self.slow_period}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")
    
    def on_bar(self, bar: pd.Series):
        try:
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
            
            if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
                return
            
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
                        
        except Exception as e:
            self.on_error(e, "on_bar")


class RSIStrategy(StrategyBase):
    """RSI均值回归策略"""
    
    def on_init(self):
        try:
            self.symbol = self.params.get('symbol', 'IF9999')
            self.rsi_period = self.params.get('rsi_period', 14)
            self.oversold = self.params.get('oversold', 30)
            self.overbought = self.params.get('overbought', 70)
            self.position_ratio = self.params.get('position_ratio', 0.8)
            
            if self.oversold >= self.overbought:
                raise StrategyError(f"oversold ({self.oversold}) 必须小于 overbought ({self.overbought})")
            
            logger.info(f"RSI策略初始化: period={self.rsi_period}, oversold={self.oversold}, overbought={self.overbought}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")
    
    def on_bar(self, bar: pd.Series):
        try:
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
            
            if pd.isna(current_rsi):
                return
            
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
                        
        except Exception as e:
            self.on_error(e, "on_bar")


class BreakoutStrategy(StrategyBase):
    """突破策略"""
    
    def on_init(self):
        try:
            self.symbol = self.params.get('symbol', 'IF9999')
            self.lookback_period = self.params.get('lookback_period', 20)
            self.position_ratio = self.params.get('position_ratio', 0.8)
            
            logger.info(f"突破策略初始化: lookback={self.lookback_period}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")
    
    def on_bar(self, bar: pd.Series):
        try:
            symbol = self.symbol
            df = self.get_data(symbol)
            
            if df is None or len(df) < self.lookback_period:
                return
            
            current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
            if current_idx < self.lookback_period:
                return
            
            recent_high = df['high'].rolling(window=self.lookback_period).max().iloc[current_idx - 1]
            recent_low = df['low'].rolling(window=self.lookback_period).min().iloc[current_idx - 1]
            
            if pd.isna(recent_high) or pd.isna(recent_low):
                return
            
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
                        
        except Exception as e:
            self.on_error(e, "on_bar")


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
