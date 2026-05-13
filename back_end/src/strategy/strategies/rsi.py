"""
RSI均值回归策略
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..errors import StrategyError
from ...data.indicators import calc_rsi

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
            self.contract_multiplier = self.params.get('contract_multiplier', 10)

            if self.oversold >= self.overbought:
                raise StrategyError(f"oversold ({self.oversold}) 必须小于 overbought ({self.overbought})")

            self._rsi = None

            logger.info(f"RSI策略初始化: period={self.rsi_period}, oversold={self.oversold}, overbought={self.overbought}")
            self._initialized = True

        except Exception as e:
            logger.error(f"策略初始化失败: {e}")
            raise StrategyError(f"策略初始化失败: {e}")

    def on_start(self):
        df = self.get_data(self.symbol)
        if df is not None and len(df) >= self.rsi_period + 1:
            self._rsi = calc_rsi(df['close'], self.rsi_period)

    def on_bar(self, bar: pd.Series):
        try:
            symbol = self.symbol
            df = self.get_data(symbol)

            if df is None or len(df) < self.rsi_period + 1:
                return

            if self._rsi is None:
                self._rsi = calc_rsi(df['close'], self.rsi_period)

            current_idx = df.index.get_loc(self.current_date) if self.current_date in df.index else len(df) - 1
            if current_idx < self.rsi_period + 1:
                return

            current_rsi = self._rsi.iloc[current_idx]

            if pd.isna(current_rsi):
                return

            pos = self.get_position(symbol)

            if current_rsi < self.oversold:
                if pos.is_short or pos.is_empty:
                    per_lot_value = bar['close'] * self.contract_multiplier
                    volume = max(1, int((self.current_capital * self.position_ratio) / per_lot_value))
                    if volume > 0:
                        if pos.volume != 0:
                            self.cover(symbol, bar['close'], abs(pos.volume))
                        self.buy(symbol, bar['close'], volume)

            elif current_rsi > self.overbought:
                if pos.is_long or pos.is_empty:
                    per_lot_value = bar['close'] * self.contract_multiplier
                    volume = max(1, int((self.current_capital * self.position_ratio) / per_lot_value))
                    if volume > 0:
                        if pos.volume != 0:
                            self.sell(symbol, bar['close'], abs(pos.volume))
                        self.short(symbol, bar['close'], volume)

        except Exception as e:
            self.on_error(e, "on_bar")
