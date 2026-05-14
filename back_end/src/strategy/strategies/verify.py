"""
VerifyStrategy — minimal verification strategy for live trading chain.
"""

import logging

import pandas as pd
from ..base import StrategyBase

logger = logging.getLogger(__name__)


class VerifyStrategy(StrategyBase):
    """Minimal strategy to validate the full live trading chain.

    State machine:
      Warmup (bars < warmup_bars) -> Buy 1 lot -> Hold (hold_bars) -> Sell -> Done
    """

    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2510")
        self.warmup_bars = int(self.params.get("warmup_bars", 20))
        self.hold_bars = int(self.params.get("hold_bars", 10))
        self._volume = int(self.params.get("volume", 1))
        self._multiplier = int(self.params.get("contract_multiplier", 10))

        self._bar_count = 0
        self._bars_since_entry = 0
        self._bought = False
        self._closed = False
        self._entry_price = 0.0

        self._initialized = True
        logger.info(
            "VerifyStrategy 初始化: symbol=%s warmup=%d hold=%d volume=%d",
            self.symbol, self.warmup_bars, self.hold_bars, self._volume,
        )

    def on_start(self):
        logger.info("策略启动，等待 Bar 预热... (需 %d 根 bar)", self.warmup_bars)

    def on_bar(self, bar: pd.Series):
        self._bar_count += 1
        symbol = bar.get("symbol", self.symbol)
        close = float(bar["close"])
        vol_so_far = int(bar.get("volume", 0))

        pos = self.get_position(symbol)

        if self._bought and not self._closed:
            self._bars_since_entry += 1
            pnl_est = (close - self._entry_price) * self._volume * self._multiplier
            logger.info(
                "持仓中... Bar#%d | 价格: %.0f | 持仓: %d手@%.0f | 浮盈: %.0f",
                self._bar_count, close,
                getattr(pos, "volume", 0), self._entry_price, pnl_est,
            )

            if self._bars_since_entry >= self.hold_bars:
                signal = self.sell(self.symbol, close, self._volume)
                if signal:
                    logger.info("信号发出: 平仓 %s %d手@%.0f", self.symbol, self._volume, close)
                    self._closed = True
                else:
                    logger.error("平仓信号生成失败")
            return

        if self._closed:
            return

        # Warmup phase — just log progress
        if self._bar_count < self.warmup_bars:
            if self._bar_count % 5 == 0 or self._bar_count == 1:
                logger.info(
                    "预热中... Bar#%d/%d | %s O=%.0f H=%.0f L=%.0f C=%.0f V=%d",
                    self._bar_count, self.warmup_bars,
                    symbol, bar["open"], bar["high"], bar["low"], close, vol_so_far,
                )
            return

        # Warmup complete — send buy order
        if not self._bought:
            logger.info("预热完成，发送验证买单")
            signal = self.buy(self.symbol, close, self._volume)
            if signal:
                self._entry_price = close
                self._bought = True
                self._bars_since_entry = 0
                logger.info("信号发出: 开仓 %s %d手@%.0f", self.symbol, self._volume, close)
            else:
                logger.error("开仓信号生成失败")

    def on_order(self, order):
        logger.info(
            "订单更新: %s | %s | %s | %d/%d | %s",
            getattr(order, "order_id", ""),
            getattr(order, "symbol", ""),
            getattr(order, "direction", ""),
            getattr(order, "traded_volume", 0),
            getattr(order, "volume", 0),
            getattr(order, "status", ""),
        )

    def on_trade(self, trade):
        logger.info(
            "成交回报: %s | %s | %s | %d手@%.2f | 手续费: %.2f",
            getattr(trade, "trade_id", ""),
            getattr(trade, "symbol", ""),
            getattr(trade, "direction", ""),
            getattr(trade, "volume", 0),
            getattr(trade, "price", 0.0),
            getattr(trade, "commission", 0.0),
        )
        self.update_position(trade.symbol, trade)
