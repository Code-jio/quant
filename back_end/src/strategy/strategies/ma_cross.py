"""
双均线策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..errors import StrategyError

logger = logging.getLogger(__name__)


class MACrossStrategy(StrategyBase):
    """双均线策略"""

    def on_init(self):
        try:
            self.symbol = self.params.get('symbol', 'IF9999')
            self.fast_period = self.params.get('fast_period', 10)
            self.slow_period = self.params.get('slow_period', 20)
            self.position_ratio = self.params.get('position_ratio', 0.8)
            self.contract_multiplier = self.params.get('contract_multiplier', 10)

            if self.fast_period >= self.slow_period:
                raise StrategyError(f"fast_period ({self.fast_period}) 必须小于 slow_period ({self.slow_period})")

            self._fast_ma = None
            self._slow_ma = None

            logger.info(f"双均线策略初始化: fast={self.fast_period}, slow={self.slow_period}")
            self._initialized = True

        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")

    def on_start(self):
        df = self.get_data(self.symbol)
        if df is not None and len(df) >= self.slow_period:
            self._fast_ma = df['close'].rolling(window=self.fast_period).mean()
            self._slow_ma = df['close'].rolling(window=self.slow_period).mean()

    def on_bar(self, bar: pd.Series):
        try:
            symbol = self.symbol
            df = self.get_data(symbol)

            if df is None or len(df) < self.slow_period:
                return

            if self._fast_ma is None or self._slow_ma is None:
                self._fast_ma = df['close'].rolling(window=self.fast_period).mean()
                self._slow_ma = df['close'].rolling(window=self.slow_period).mean()

            current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
            if current_idx < self.slow_period:
                return

            prev_fast = self._fast_ma.iloc[current_idx - 1]
            prev_slow = self._slow_ma.iloc[current_idx - 1]
            curr_fast = self._fast_ma.iloc[current_idx]
            curr_slow = self._slow_ma.iloc[current_idx]

            if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
                return

            pos = self.get_position(symbol)

            if prev_fast <= prev_slow and curr_fast > curr_slow:
                if pos.is_short or pos.is_empty:
                    per_lot_value = bar['close'] * self.contract_multiplier
                    volume = max(1, int((self.current_capital * self.position_ratio) / per_lot_value))
                    if volume > 0:
                        if pos.volume != 0:
                            self.cover(symbol, bar['close'], abs(pos.volume))
                        self.buy(symbol, bar['close'], volume)

            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                if pos.is_long or pos.is_empty:
                    per_lot_value = bar['close'] * self.contract_multiplier
                    volume = max(1, int((self.current_capital * self.position_ratio) / per_lot_value))
                    if volume > 0:
                        if pos.volume != 0:
                            self.sell(symbol, bar['close'], abs(pos.volume))
                        self.short(symbol, bar['close'], volume)

        except Exception as e:
            self.on_error(e, "on_bar")
