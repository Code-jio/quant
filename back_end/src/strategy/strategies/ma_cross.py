"""
双均线策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..types import Direction
from ..errors import StrategyError

logger = logging.getLogger(__name__)


class MACrossStrategy(StrategyBase):
    """双均线策略"""

    def on_init(self):
        try:
            self.symbol = self.params.get("symbol", "IF9999")
            self.fast_period = self.params.get("fast_period", 10)
            self.slow_period = self.params.get("slow_period", 20)
            self.position_ratio = self.params.get("position_ratio", 0.8)

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

            # data holds prior bars; the callback supplies the newly completed bar.
            closes = pd.concat([df["close"], pd.Series([float(bar["close"])])], ignore_index=True)
            fast_ma = closes.rolling(window=self.fast_period).mean()
            slow_ma = closes.rolling(window=self.slow_period).mean()

            prev_fast = fast_ma.iloc[-2]
            prev_slow = slow_ma.iloc[-2]
            curr_fast = fast_ma.iloc[-1]
            curr_slow = slow_ma.iloc[-1]

            if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
                return

            target = None
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                target = Direction.LONG
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                target = Direction.SHORT
            self.rebalance_target(symbol, float(bar["close"]), target)

        except Exception as e:
            self.on_error(e, "on_bar")
