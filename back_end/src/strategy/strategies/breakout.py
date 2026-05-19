"""
突破策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..errors import StrategyError

logger = logging.getLogger(__name__)


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
                    volume = int((self.current_capital * self.position_ratio) / bar['close'] / 100) * 100
                    if volume > 0:
                        if pos.volume != 0:
                            self.cover(symbol, bar['close'], abs(pos.volume))
                        self.buy(symbol, bar['close'], volume)

            elif bar['close'] < recent_low:
                if pos.is_long or pos.is_empty:
                    volume = int((self.current_capital * self.position_ratio) / bar['close'] / 100) * 100
                    if volume > 0:
                        if pos.volume != 0:
                            self.sell(symbol, bar['close'], abs(pos.volume))
                        self.short(symbol, bar['close'], volume)

        except Exception as e:
            self.on_error(e, "on_bar")
