"""
策略基类定义
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Signal, Order, Trade, Position, Direction, OrderType

logger = logging.getLogger(__name__)

import pandas as pd
from .types import Direction, OrderType, OffsetFlag, Signal, Order, Trade, Position
from .errors import StrategyError


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
        self.current_date: Optional[Any] = None
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

    def on_error(self, error: Exception, context: str = "") -> bool:
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
                offset=OffsetFlag.CLOSE,
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
                offset=OffsetFlag.CLOSE,
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

    def update_position(self, symbol: str, trade: 'Trade'):
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
