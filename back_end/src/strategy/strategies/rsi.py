"""
RSI均值回归策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..errors import StrategyError

logger = logging.getLogger(__name__)


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
                    volume = int((self.current_capital * self.position_ratio) / bar['close'] / 100) * 100
                    if volume > 0:
                        if pos.volume != 0:
                            self.cover(symbol, bar['close'], abs(pos.volume))
                        self.buy(symbol, bar['close'], volume)

            elif current_rsi > self.overbought:
                if pos.is_long or pos.is_empty:
                    volume = int((self.current_capital * self.position_ratio) / bar['close'] / 100) * 100
                    if volume > 0:
                        if pos.volume != 0:
                            self.sell(symbol, bar['close'], abs(pos.volume))
                        self.short(symbol, bar['close'], volume)

        except Exception as e:
            self.on_error(e, "on_bar")
