"""
策略基类定义
"""

import logging
import math
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Mapping

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
        self._position_source: Optional[Mapping[str, Position]] = None
        self.orders: Dict[str, Order] = {}
        self.trades: List[Trade] = []
        self.data: Dict[str, pd.DataFrame] = {}
        self.current_date: Optional[Any] = None
        self.initial_capital: float = 1000000.0
        self.current_capital: float = 1000000.0
        self._initialized = False
        self._error_count = 0
        self._max_errors = int(self.params.get("max_errors", 10))
        self.contract_specs = {}
        self.allocation_weight = 1.0

    def order_volume(self, symbol: str, price: float) -> int:
        spec = self.contract_specs.get(symbol)
        if not spec or not math.isfinite(price) or price <= 0:
            return 0
        size = float(spec.get('size') or 0)
        margin = float(spec.get('margin_rate') or 0)
        unit = int(spec.get('min_volume') or 1)
        if not math.isfinite(size * margin) or size <= 0 or not 0 < margin <= 1 or unit < 1:
            return 0
        budget = max(0, self.current_capital) * self.params.get('position_ratio', .8) * self.allocation_weight
        return int(budget / (price * size * margin) / unit) * unit

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
            raise StrategyError(f"策略 {self.name} 错误次数超限 ({self._error_count}/{self._max_errors})") from error
        return True

    def buy(self, symbol: str, price: float, volume: int,
            order_type: OrderType = OrderType.LIMIT) -> Optional[Signal]:
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
             order_type: OrderType = OrderType.LIMIT) -> Optional[Signal]:
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
              order_type: OrderType = OrderType.LIMIT) -> Optional[Signal]:
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
              order_type: OrderType = OrderType.LIMIT) -> Optional[Signal]:
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
        source_position = self._get_position_from_source(symbol)
        if source_position is not None:
            return source_position
        if any(p.symbol==symbol for p in self.positions.values()) and symbol not in self.positions:
            saved=self._position_source
            try:
                self._position_source=self.positions
                return self._get_position_from_source(symbol)
            finally:
                self._position_source=saved
        return self.positions.get(symbol, Position(symbol=symbol, direction=Direction.NET, volume=0))

    def set_position_source(self, positions: Optional[Mapping[str, Position]]) -> None:
        """绑定外部持仓源，实盘优先读取网关持仓，策略本地持仓作为兜底。"""
        self._position_source = positions

    def _get_position_from_source(self, symbol: str) -> Optional[Position]:
        if self._position_source is None:
            return None

        matches = [
            pos for key, pos in self._position_source.items()
            if key == symbol or getattr(pos, "symbol", "") == symbol
        ]
        if not matches:
            return Position(symbol=symbol,direction=Direction.NET,volume=0)

        signed_volume = 0
        weighted_cost = 0.0
        weighted_price = 0.0
        total_abs_volume = 0
        frozen = 0
        pnl = 0.0

        for pos in matches:
            raw_volume = int(getattr(pos, "volume", 0) or 0)
            direction = getattr(pos, "direction", Direction.NET)
            if direction == Direction.SHORT:
                volume = -abs(raw_volume)
            elif direction == Direction.LONG:
                volume = abs(raw_volume)
            else:
                volume = raw_volume

            abs_volume = abs(volume)
            signed_volume += volume
            total_abs_volume += abs_volume
            weighted_cost += float(getattr(pos, "cost", 0.0) or 0.0) * abs_volume
            weighted_price += float(getattr(pos, "price", 0.0) or 0.0) * abs_volume
            frozen += int(getattr(pos, "frozen", 0) or 0)
            pnl += float(getattr(pos, "pnl", 0.0) or 0.0)

        if signed_volume > 0:
            direction = Direction.LONG
        elif signed_volume < 0:
            direction = Direction.SHORT
        else:
            direction = Direction.NET

        return Position(
            symbol=symbol,
            direction=direction,
            volume=signed_volume,
            frozen=min(frozen,abs(signed_volume)),
            price=weighted_price / total_abs_volume if total_abs_volume else 0.0,
            cost=weighted_cost / total_abs_volume if total_abs_volume else 0.0,
            pnl=pnl,
        )

    def get_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取历史数据"""
        return self.data.get(symbol)

    def update_position(self, symbol: str, trade: 'Trade'):
        """更新持仓"""
        try:
            if trade.offset is not None:
                side=trade.direction if trade.offset==OffsetFlag.OPEN else (Direction.SHORT if trade.direction==Direction.LONG else Direction.LONG)
                key=f'{symbol}_{side.value}'
                pos=self.positions.get(key,Position(symbol,side,0))
                if trade.offset==OffsetFlag.OPEN:
                    total=abs(pos.volume)+trade.volume
                    pos.cost=(pos.cost*abs(pos.volume)+trade.price*trade.volume)/total
                    pos.price=pos.cost
                    pos.volume=total
                else:
                    if trade.volume>abs(pos.volume):
                        # External position snapshots remain authoritative for inherited holdings.
                        if self._position_source is None:
                            raise ValueError('Close exceeds local position')
                    pos.volume=max(0,abs(pos.volume)-trade.volume)
                self.positions[key]=pos
                return
            if symbol not in self.positions:
                self.positions[symbol] = Position(symbol=symbol, direction=Direction.NET, volume=0)

            pos = self.positions[symbol]
            old_volume = self._signed_position_volume(pos)
            old_cost = float(pos.cost if pos.cost is not None else (pos.price if pos.price is not None else 0.0))
            delta = int(trade.volume) if trade.direction == Direction.LONG else -int(trade.volume)
            new_volume = old_volume + delta

            if old_volume == 0 or old_volume * delta > 0:
                old_abs = abs(old_volume)
                new_abs = abs(new_volume)
                pos.cost = (
                    (old_cost * old_abs + float(trade.price) * abs(delta)) / new_abs
                    if new_abs else 0.0
                )
            elif new_volume == 0:
                pos.cost = 0
            elif old_volume * new_volume > 0:
                pos.cost = old_cost
            else:
                pos.cost = float(trade.price)

            pos.volume = new_volume
            if new_volume > 0:
                pos.direction = Direction.LONG
            elif new_volume < 0:
                pos.direction = Direction.SHORT
            else:
                pos.direction = Direction.NET
                pos.price = 0

            if new_volume != 0:
                pos.price = pos.cost

            pos.pnl = 0

            realized_pnl = float(getattr(trade, "pnl", 0) or 0)
            commission = float(getattr(trade, "commission", 0) or 0)
            self.current_capital += realized_pnl
            if realized_pnl == 0 and commission:
                self.current_capital -= commission

        except Exception as e:
            self.on_error(e, "update_position")

    @staticmethod
    def _signed_position_volume(pos: Position) -> int:
        volume = int(getattr(pos, "volume", 0) or 0)
        if getattr(pos, "direction", Direction.NET) == Direction.SHORT:
            return -abs(volume)
        if getattr(pos, "direction", Direction.NET) == Direction.LONG:
            return abs(volume)
        return volume
