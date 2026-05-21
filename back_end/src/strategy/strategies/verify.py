"""
VerifyStrategy — minimal verification strategy for live trading chain.
"""

import logging

import pandas as pd
from ..base import StrategyBase
from ..types import Direction, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class VerifyStrategy(StrategyBase):
    """Minimal strategy to validate the full live trading chain.

    State machine:
      Wait market data -> Ready to start -> Started -> Buy 1 lot -> Hold -> Sell -> Done
    """

    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2510")
        self.warmup_bars = int(self.params.get("warmup_bars", 0))
        self.readiness_bars = max(1, int(self.params.get("readiness_bars", self.params.get("market_ready_bars", 1))))
        self.hold_bars = int(self.params.get("hold_bars", 10))
        self._volume = int(self.params.get("volume", 1))
        self._multiplier = int(self.params.get("contract_multiplier", 10))
        self._order_type = self._parse_order_type(self.params.get("order_type", "limit"))

        self._bar_count = 0
        self._bars_since_entry = 0
        self._bought = False
        self._closed = False
        self._entry_price = 0.0
        self.trade_authorized = False
        self.market_ready = False
        self.ready_to_arm = False
        self.completed = False
        self.trial_state = "waiting_market_data"
        self._entry_order_sent = False
        self._close_order_sent = False
        self._last_reject_reason = ""

        self._initialized = True
        logger.info(
            "VerifyStrategy 初始化: symbol=%s readiness=%d hold=%d volume=%d order_type=%s",
            self.symbol, self.readiness_bars, self.hold_bars, self._volume, self._order_type.value,
        )

    def _parse_order_type(self, value) -> OrderType:
        normalized = str(value or "limit").strip().lower()
        if normalized == OrderType.MARKET.value:
            return OrderType.MARKET
        return OrderType.LIMIT

    def start_verification(self) -> bool:
        if self.market_ready and self.ready_to_arm and not self._entry_order_sent and not self._bought and not self.completed:
            self.trade_authorized = True
            self.trial_state = "started"
            logger.info("验证交易已开始，等待下一根有效 Bar 开仓")
            return True
        logger.info("验证交易开始被拒: state=%s market_ready=%s bought=%s completed=%s", self.trial_state, self.market_ready, self._bought, self.completed)
        return False

    def authorize_trading(self) -> bool:
        return self.start_verification()

    def revoke_authorization(self):
        self.trade_authorized = False
        if self.ready_to_arm and not self._entry_order_sent and not self._bought and not self.completed:
            self.trial_state = "ready_to_start"
            logger.info("验证交易开始状态已撤销，回到待开始状态")

    def snapshot(self) -> dict:
        return {
            "state": self.trial_state,
            "symbol": self.symbol,
            "authorized": self.trade_authorized,
            "started": self.trade_authorized,
            "market_ready": self.market_ready,
            "ready_to_arm": self.ready_to_arm,
            "completed": self.completed,
            "bar_count": self._bar_count,
            "warmup_bars": self.warmup_bars,
            "readiness_bars": self.readiness_bars,
            "hold_bars": self.hold_bars,
            "bars_since_entry": self._bars_since_entry,
            "bought": self._bought,
            "closed": self._closed,
            "volume": self._volume,
            "entry_order_sent": self._entry_order_sent,
            "close_order_sent": self._close_order_sent,
            "order_type": self._order_type.value,
            "last_reject_reason": self._last_reject_reason,
        }

    def on_start(self):
        logger.info("策略启动，等待行情就绪... (需 %d 根有效 bar)", self.readiness_bars)

    def on_bar(self, bar: pd.Series):
        if self.completed or self.trial_state == "error":
            return

        symbol = bar.get("symbol", self.symbol)
        if str(symbol) != str(self.symbol):
            logger.info("忽略非试运行合约 Bar: %s", symbol)
            return

        close = float(bar["close"])
        if close <= 0:
            self._last_reject_reason = "invalid_market_price"
            self.trial_state = "waiting_market_data"
            logger.warning("行情价格无效，继续等待: %s close=%.2f", symbol, close)
            return

        self._bar_count += 1
        vol_so_far = int(bar.get("volume", 0))

        pos = self.get_position(symbol)

        if self._entry_order_sent and not self._bought:
            self.trial_state = "entry_pending"
            logger.info("开仓委托已发送，等待成交回报")
            return

        if self._bought and not self._closed:
            self._bars_since_entry += 1
            pnl_est = (close - self._entry_price) * self._volume * self._multiplier
            logger.info(
                "持仓中... Bar#%d | 价格: %.0f | 持仓: %d手@%.0f | 浮盈: %.0f",
                self._bar_count, close,
                getattr(pos, "volume", 0), self._entry_price, pnl_est,
            )

            if self._bars_since_entry >= self.hold_bars:
                if self._close_order_sent:
                    return
                signal = self.sell(self.symbol, close, self._volume, order_type=self._order_type)
                if signal:
                    self._close_order_sent = True
                    self.trial_state = "closing"
                    logger.info("信号发出: 平仓 %s %d手@%.0f", self.symbol, self._volume, close)
                else:
                    logger.error("平仓信号生成失败")
            return

        if self._close_order_sent and not self.completed:
            self.trial_state = "closing"
            return

        if self._closed or self.completed:
            return

        if not self.market_ready:
            if self._bar_count < self.readiness_bars:
                self.trial_state = "waiting_market_data"
                logger.info(
                    "行情就绪检查中... Bar#%d/%d | %s O=%.0f H=%.0f L=%.0f C=%.0f V=%d",
                    self._bar_count, self.readiness_bars,
                    symbol, bar["open"], bar["high"], bar["low"], close, vol_so_far,
                )
                return
            self.market_ready = True
            self.ready_to_arm = True
            logger.info(
                "行情已就绪，等待开始验证交易: Bar#%d/%d | %s close=%.0f volume=%d",
                self._bar_count, self.readiness_bars, symbol, close, vol_so_far,
            )

        if not self.trade_authorized:
            self.trial_state = "ready_to_start"
            return

        # Verification start is accepted out-of-band; the next valid bar sends one entry order.
        if not self._bought and not self._entry_order_sent:
            logger.info("验证交易已开始，发送验证买单")
            signal = self.buy(self.symbol, close, self._volume, order_type=self._order_type)
            if signal:
                self._entry_order_sent = True
                self._entry_price = close
                self._bars_since_entry = 0
                self.trial_state = "entry_pending"
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
        status = getattr(order, "status", None)
        if hasattr(status, "value"):
            status_value = status.value
        else:
            status_value = str(status or "")
        if status_value in {OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value}:
            self._last_reject_reason = getattr(order, "error_msg", "") or status_value
            self.trade_authorized = False
            if self._entry_order_sent and not self._bought:
                self.trial_state = "error"
            elif self._close_order_sent and not self.completed:
                self.trial_state = "error"

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
        if not self.trades or getattr(self.trades[-1], "trade_id", "") != getattr(trade, "trade_id", ""):
            self.update_position(trade.symbol, trade)

        if getattr(trade, "symbol", "") != self.symbol:
            return

        direction = getattr(trade, "direction", None)
        direction_value = direction.value if hasattr(direction, "value") else str(direction or "")
        if direction_value == Direction.LONG.value and self._entry_order_sent and not self._bought:
            self._bought = True
            self.trial_state = "holding"
            self._bars_since_entry = 0
            self._entry_price = float(getattr(trade, "price", self._entry_price) or self._entry_price)
            logger.info("验证开仓已成交，进入持仓观察")
        elif direction_value == Direction.SHORT.value and self._close_order_sent and not self.completed:
            self._closed = True
            self.completed = True
            self.trade_authorized = False
            self.trial_state = "completed"
            logger.info("验证平仓已成交，试运行闭环完成")

    def mark_signal_rejected(self, reason: str = ""):
        self._last_reject_reason = reason or "signal_rejected"
        self.trade_authorized = False
        self.trial_state = "error"
        logger.error("验证策略信号被拒绝: %s", self._last_reject_reason)
