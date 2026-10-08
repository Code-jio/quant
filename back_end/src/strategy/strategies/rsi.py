"""
RSI均值回归策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..types import Direction
from ..errors import StrategyError
from ...common.indicators import wilder_rsi

logger = logging.getLogger(__name__)


class RSIStrategy(StrategyBase):
    """RSI均值回归策略"""

    def on_init(self):
        try:
            self.symbol = self.params.get("symbol", "IF9999")
            self.rsi_period = self.params.get("rsi_period", 14)
            self.oversold = self.params.get("oversold", 30)
            self.overbought = self.params.get("overbought", 70)
            self.position_ratio = self.params.get("position_ratio", 0.8)

            if self.oversold >= self.overbought:
                raise StrategyError(f"oversold ({self.oversold}) 必须小于 overbought ({self.overbought})")

            logger.info(
                f"RSI策略初始化: period={self.rsi_period}, oversold={self.oversold}, overbought={self.overbought}"
            )
            self._initialized = True

        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")

    def on_bar(self, bar: pd.Series):
        try:
            symbol = self.symbol
            df = self.get_data(symbol)

            if df is None or len(df) < self.rsi_period:
                return
            closes = pd.concat([df["close"], pd.Series([float(bar["close"])])], ignore_index=True)
            current_rsi = wilder_rsi(closes, self.rsi_period).iloc[-1]

            if pd.isna(current_rsi):
                return

            target = None
            if current_rsi < self.oversold:
                target = Direction.LONG
            elif current_rsi > self.overbought:
                target = Direction.SHORT
            self.rebalance_target(symbol, float(bar["close"]), target)

        except Exception as e:
            self.on_error(e, "on_bar")
