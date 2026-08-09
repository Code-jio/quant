"""
VerifyStrategy — minimal verification strategy for live trading chain.
"""

import logging
from datetime import datetime

import pandas as pd
from ..base import StrategyBase
from ..types import Direction, OrderStatus, OrderType
from ...trading.symbols import symbol_key, symbols_match

logger = logging.getLogger(__name__)


class VerifyStrategy(StrategyBase):
    """Minimal strategy to validate the full live trading chain.

    State machine:
      Wait market data -> Ready to start -> Started -> Buy 1 lot -> Hold -> Sell -> Done
    """

    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2610")
        self.warmup_bars = int(self.params.get("warmup_bars", 0))
        self.readiness_bars = max(1, int(self.params.get("readiness_bars", self.params.get("market_ready_bars", 1))))
        self.hold_bars = int(self.params.get("hold_bars", 10))
        self._volume = int(self.params.get("volume", 1))
        self._multiplier = int(self.params.get("contract_multiplier", 10))
        self._order_type = self._parse_order_type(self.params.get("order_type", "limit"))
        self._price_tick = self._parse_positive_float(
            self.params.get("price_tick", self.params.get("min_tick", 1.0)),
            default=1.0,
        )
        self._aggressive_ticks = self._parse_non_negative_int(self.params.get("aggressive_ticks", 1), default=1)
        self._chase_enabled = bool(self.params.get("chase_enabled", True))
        self._chase_interval_seconds = self._parse_positive_float(self.params.get("chase_interval_seconds", 2.0), default=2.0)
        self._chase_max_attempts = self._parse_non_negative_int(self.params.get("chase_max_attempts", 5), default=5)
        self._chase_step_ticks = self._parse_non_negative_int(self.params.get("chase_step_ticks", 1), default=1)
        self._chase_fallback_to_market = bool(self.params.get("chase_fallback_to_market", True))
        self.auto_arm = bool(self.params.get("auto_arm", False))

        self._bar_count = 0
        self._tick_count = 0
        self._bars_since_entry = 0
        self._bought = False
        self._closed = False
        self._entry_price = 0.0
        self._last_bar_time = ""
        self._last_market_price = 0.0
        self._last_market_timestamp = None
        self.trade_authorized = False
        self.market_ready = False
        self.ready_to_arm = False
        self.completed = False
        self.trial_state = "waiting_market_data"
        self._entry_order_sent = False
        self._close_order_sent = False
        self._last_reject_reason = ""
        self._last_order_price = 0.0
        self._last_order_pricing_source = ""
        self._entry_order_id = ""
        self._close_order_id = ""
        self._last_order_submitted_at = None
        self._chase_attempts = 0
        self._chase_pending_cancel_order_id = ""
        self._chase_resubmit_ready = False
        self._last_chase_reason = ""
        self._last_chase_order_id = ""
        self._last_chase_price = 0.0

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

    @staticmethod
    def _parse_positive_float(value, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_non_negative_int(value, default: int) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return default
        return max(0, parsed)

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        return symbol_key(symbol)

    @classmethod
    def _symbols_match(cls, left: str, right: str) -> bool:
        return symbols_match(left, right)

    @staticmethod
    def _market_price(market, keys: tuple[str, ...]) -> tuple[float, str]:
        for key in keys:
            value = None
            if hasattr(market, "get"):
                try:
                    value = market.get(key, None)
                except Exception:
                    value = None
            if value is None and hasattr(market, key):
                value = getattr(market, key)
            try:
                price = float(value or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0:
                return price, key
        return 0.0, ""

    @staticmethod
    def _market_timestamp(market):
        for key in ("timestamp", "datetime", "time"):
            value = None
            if hasattr(market, "get"):
                try:
                    value = market.get(key, None)
                except Exception:
                    value = None
            if value is None and hasattr(market, key):
                value = getattr(market, key)
            if value:
                return value
        return datetime.now()

    @staticmethod
    def _elapsed_seconds(later, earlier) -> float:
        if not earlier:
            return 0.0
        try:
            return max(0.0, (later - earlier).total_seconds())
        except Exception:
            return 0.0

    def _marketable_order_price(self, direction: Direction, fallback_price: float, market, extra_ticks: int = 0) -> float:
        fallback = float(fallback_price or 0)
        if self._order_type != OrderType.LIMIT:
            self._last_order_price = fallback
            self._last_order_pricing_source = "market_order_reference"
            return fallback

        total_ticks = self._aggressive_ticks + max(0, int(extra_ticks or 0))
        adjustment = total_ticks * self._price_tick
        if direction == Direction.LONG:
            quote, source = self._market_price(market, ("ask_price_1", "ask", "ask1", "ask_price"))
            base = quote if quote > 0 else fallback
            price = base + adjustment
            source = source or "last_price"
            source = f"{source}+{total_ticks}ticks"
        else:
            quote, source = self._market_price(market, ("bid_price_1", "bid", "bid1", "bid_price"))
            base = quote if quote > 0 else fallback
            price = base - adjustment
            source = source or "last_price"
            source = f"{source}-{total_ticks}ticks"
            if price <= 0:
                price = fallback
                source = "last_price"

        rounded = round(float(price), 6)
        self._last_order_price = rounded
        self._last_order_pricing_source = source
        return rounded

    def _pending_order_id(self) -> str:
        if self._entry_order_sent and not self._bought:
            return self._entry_order_id
        if self._close_order_sent and not self.completed:
            return self._close_order_id
        return ""

    def on_signal_submitted(self, signal, order_id: str):
        comment = str(getattr(signal, "comment", "") or "")
        if comment == "buy_open":
            self._entry_order_id = order_id
            self._entry_order_sent = True
            self.trial_state = "entry_pending"
        elif comment == "sell_close":
            self._close_order_id = order_id
            self._close_order_sent = True
            self.trial_state = "closing"
        self._last_order_submitted_at = getattr(signal, "datetime", None) or self.current_date or datetime.now()
        self._last_chase_order_id = order_id if self._chase_attempts else self._last_chase_order_id
        self._chase_pending_cancel_order_id = ""
        self._chase_resubmit_ready = False

    def next_chase_action(self, market) -> dict:
        if not self._chase_enabled or self.completed or self.trial_state == "error":
            return {}
        if self._bought and not self._close_order_sent:
            return {}

        symbol = getattr(market, "symbol", self.symbol)
        if not self._symbols_match(symbol, self.symbol):
            return {}
        fallback = float(getattr(market, "last_price", 0) or 0)
        if fallback <= 0 and hasattr(market, "get"):
            fallback = float(market.get("close", 0) or 0)
        if fallback <= 0:
            return {}

        if self._chase_resubmit_ready:
            # After cancel returns, check if we should market-fallback
            if self._chase_fallback_to_market and self._chase_attempts >= self._chase_max_attempts:
                signal = self._create_fallback_market_signal(market, fallback)
                if signal:
                    self._chase_resubmit_ready = False
                    return {"action": "submit", "signal": signal}
                return {}
            signal = self._create_chase_signal(market, fallback)
            if signal:
                self._chase_resubmit_ready = False
                return {"action": "submit", "signal": signal}
            return {}

        order_id = self._pending_order_id()
        if not order_id or self._chase_pending_cancel_order_id:
            return {}
        if self._chase_attempts >= self._chase_max_attempts:
            if self._chase_fallback_to_market and not self._chase_pending_cancel_order_id:
                # Cancel the last limit order; resubmit will use market fallback
                self._chase_pending_cancel_order_id = order_id
                self._last_chase_reason = "fallback_cancelling"
                return {"action": "cancel", "order_id": order_id}
            self._last_chase_reason = "max_attempts_reached"
            return {}

        age = self._elapsed_seconds(self._market_timestamp(market), self._last_order_submitted_at)
        if age < self._chase_interval_seconds:
            return {}

        self._chase_attempts += 1
        self._chase_pending_cancel_order_id = order_id
        self._last_chase_reason = f"unfilled_for_{round(age, 2)}s"
        return {"action": "cancel", "order_id": order_id}

    def _create_chase_signal(self, market, fallback: float):
        extra_ticks = self._chase_attempts * self._chase_step_ticks
        if self._entry_order_id and not self._bought and not self._entry_order_sent:
            order_price = self._marketable_order_price(Direction.LONG, fallback, market, extra_ticks=extra_ticks)
            signal = self.buy(self.symbol, order_price, self._volume, order_type=self._order_type)
            if signal:
                self._entry_order_sent = True
                self._entry_price = order_price
                self._last_chase_price = order_price
                self.trial_state = "entry_pending"
                logger.info("验证开仓追价重报: %s %d手@%.2f", self.symbol, self._volume, order_price)
            return signal
        if self._close_order_id and not self.completed and not self._close_order_sent:
            order_price = self._marketable_order_price(Direction.SHORT, fallback, market, extra_ticks=extra_ticks)
            signal = self.sell(self.symbol, order_price, self._volume, order_type=self._order_type)
            if signal:
                self._close_order_sent = True
                self._last_chase_price = order_price
                self.trial_state = "closing"
                logger.info("验证平仓追价重报: %s %d手@%.2f", self.symbol, self._volume, order_price)
            return signal
        return None

    def _create_fallback_market_signal(self, market, fallback: float):
        """Last-resort market order after chase limit orders all failed."""
        reference = float(self._market_price(market, ("last_price", "close", "last"))[0] or fallback or 0)
        if reference <= 0:
            reference = fallback
        if reference <= 0:
            return None
        if self._entry_order_id and not self._bought and not self._entry_order_sent:
            signal = self.buy(self.symbol, reference, self._volume, order_type=OrderType.MARKET)
            self._last_order_price = reference
            self._last_order_pricing_source = "market_fallback"
            self._last_chase_price = reference
            self._chase_pending_cancel_order_id = ""
            if signal:
                self._entry_order_sent = True
                self._entry_price = reference
                self.trial_state = "entry_pending"
                logger.info("追价耗尽，兜底市价买入: %s %d手 参考价%.2f", self.symbol, self._volume, reference)
            return signal
        if self._close_order_id and not self.completed and not self._close_order_sent:
            signal = self.sell(self.symbol, reference, self._volume, order_type=OrderType.MARKET)
            self._last_order_price = reference
            self._last_order_pricing_source = "market_fallback"
            self._last_chase_price = reference
            self._chase_pending_cancel_order_id = ""
            if signal:
                self._close_order_sent = True
                self.trial_state = "closing"
                logger.info("追价耗尽，兜底市价卖出: %s %d手 参考价%.2f", self.symbol, self._volume, reference)
            return signal
        return None

    def on_chase_cancel_failed(self, order_id: str):
        if self._chase_pending_cancel_order_id == order_id:
            self._chase_pending_cancel_order_id = ""
            self._last_chase_reason = "cancel_failed"

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

    def mark_market_data_stale(self, symbol: str, reason: str = ""):
        if not self._symbols_match(symbol, self.symbol):
            return
        if self.completed or self.trial_state == "error":
            return
        self._last_reject_reason = reason or f"Market data is stale for {symbol}"
        self.trade_authorized = False
        self.trial_state = "waiting_market_data"

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
            "tick_count": self._tick_count,
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
            "price_tick": self._price_tick,
            "aggressive_ticks": self._aggressive_ticks,
            "last_order_price": self._last_order_price,
            "last_order_pricing_source": self._last_order_pricing_source,
            "chase_enabled": self._chase_enabled,
            "chase_interval_seconds": self._chase_interval_seconds,
            "chase_attempts": self._chase_attempts,
            "chase_max_attempts": self._chase_max_attempts,
            "chase_step_ticks": self._chase_step_ticks,
            "chase_pending_cancel_order_id": self._chase_pending_cancel_order_id,
            "chase_resubmit_ready": self._chase_resubmit_ready,
            "last_chase_reason": self._last_chase_reason,
            "last_chase_order_id": self._last_chase_order_id,
            "last_chase_price": self._last_chase_price,
            "last_market_price": self._last_market_price,
            "last_market_timestamp": (
                self._last_market_timestamp.isoformat()
                if hasattr(self._last_market_timestamp, "isoformat")
                else str(self._last_market_timestamp or "")
            ),
            "last_reject_reason": self._last_reject_reason,
            "auto_arm": self.auto_arm,
            "last_bar_time": self._last_bar_time,
        }

    def on_start(self):
        logger.info("策略启动，等待有效行情 tick... (阈值 %d)", self.readiness_bars)

    def on_tick(self, tick):
        if self.completed or self.trial_state == "error":
            return

        symbol = getattr(tick, "symbol", self.symbol)
        price = float(getattr(tick, "last_price", 0) or 0)
        if not self._symbols_match(symbol, self.symbol):
            return
        if price <= 0:
            self._last_reject_reason = "invalid_market_price"
            self.trial_state = "waiting_market_data"
            logger.warning("行情 tick 价格无效，继续等待: %s last=%.2f", symbol, price)
            return

        self._tick_count += 1
        self._last_market_price = price
        self._last_market_timestamp = getattr(tick, "timestamp", None)
        self.current_date = self._last_market_timestamp

        if not self.market_ready:
            self.market_ready = True
            self.ready_to_arm = True
            self.trial_state = "ready_to_start"
            logger.info("收到有效行情 tick，等待开始验证交易: %s last=%.2f", symbol, price)
            return

        if self._chase_pending_cancel_order_id or self._chase_resubmit_ready:
            self.trial_state = "closing" if self._close_order_id and not self.completed else "entry_pending"
            return
        if self._entry_order_sent and not self._bought:
            self.trial_state = "entry_pending"
            return
        if self._bought or self._closed:
            return
        if not self.trade_authorized:
            self.trial_state = "ready_to_start"
            return

        logger.info("验证交易已开始，按最新 tick 发送验证买单")
        order_price = self._marketable_order_price(Direction.LONG, price, tick)
        signal = self.buy(self.symbol, order_price, self._volume, order_type=self._order_type)
        if signal:
            self._entry_order_sent = True
            self._entry_price = order_price
            self._bars_since_entry = 0
            self.trial_state = "entry_pending"
            logger.info("信号发出: 开仓 %s %d手@%.0f", self.symbol, self._volume, order_price)
        else:
            logger.error("开仓信号生成失败")

    def on_bar(self, bar: pd.Series):
        if self.completed or self.trial_state == "error":
            return

        symbol = bar.get("symbol", self.symbol)
        if not self._symbols_match(symbol, self.symbol):
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
        bar_dt = bar.get("datetime", "")
        self._last_bar_time = bar_dt.isoformat() if hasattr(bar_dt, "isoformat") else str(bar_dt or "")

        pos = self.get_position(symbol)

        if self._chase_pending_cancel_order_id or self._chase_resubmit_ready:
            self.trial_state = "closing" if self._close_order_id and not self.completed else "entry_pending"
            return
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
                order_price = self._marketable_order_price(Direction.SHORT, close, bar)
                signal = self.sell(self.symbol, order_price, self._volume, order_type=self._order_type)
                if signal:
                    self._close_order_sent = True
                    self.trial_state = "closing"
                    logger.info("信号发出: 平仓 %s %d手@%.0f", self.symbol, self._volume, order_price)
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
            self.ready_to_arm = True
            if self.auto_arm:
                self.trade_authorized = True
                self.trial_state = "started"
                logger.info("预热完成，试运行自动授权验证交易")
            else:
                self.trial_state = "ready_to_start"
                logger.info("预热完成，等待前端授权验证交易")
                return

        # Verification start is accepted out-of-band; the next valid market update sends one entry order.
        if not self._bought and not self._entry_order_sent:
            logger.info("验证交易已开始，按完成 bar 发送验证买单")
            order_price = self._marketable_order_price(Direction.LONG, close, bar)
            signal = self.buy(self.symbol, order_price, self._volume, order_type=self._order_type)
            if signal:
                self._entry_order_sent = True
                self._entry_price = order_price
                self._bars_since_entry = 0
                self.trial_state = "entry_pending"
                logger.info("信号发出: 开仓 %s %d手@%.0f", self.symbol, self._volume, order_price)
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
        order_id = str(getattr(order, "order_id", "") or "")
        if status_value == OrderStatus.CANCELLED.value and order_id == self._chase_pending_cancel_order_id:
            self._chase_pending_cancel_order_id = ""
            self._chase_resubmit_ready = True
            self._last_chase_reason = "cancelled_waiting_requote"
            if order_id == self._entry_order_id and not self._bought:
                self._entry_order_sent = False
                self.trial_state = "entry_pending"
            elif order_id == self._close_order_id and not self.completed:
                self._close_order_sent = False
                self.trial_state = "closing"
            logger.info("验证委托已撤，等待下一笔有效 tick 追价重报: %s", order_id)
            return
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

        if not self._symbols_match(getattr(trade, "symbol", ""), self.symbol):
            return

        direction = getattr(trade, "direction", None)
        direction_value = direction.value if hasattr(direction, "value") else str(direction or "")
        if direction_value == Direction.LONG.value and self._entry_order_sent and not self._bought:
            self._bought = True
            self._chase_pending_cancel_order_id = ""
            self._chase_resubmit_ready = False
            self.trial_state = "holding"
            self._bars_since_entry = 0
            self._entry_price = float(getattr(trade, "price", self._entry_price) or self._entry_price)
            logger.info("验证开仓已成交，进入持仓观察")
        elif direction_value == Direction.SHORT.value and self._close_order_sent and not self.completed:
            self._closed = True
            self.completed = True
            self._chase_pending_cancel_order_id = ""
            self._chase_resubmit_ready = False
            self.trade_authorized = False
            self.trial_state = "completed"
            logger.info("验证平仓已成交，试运行闭环完成")

    def mark_signal_rejected(self, reason: str = ""):
        self._last_reject_reason = reason or "signal_rejected"
        self.trade_authorized = False
        self._chase_pending_cancel_order_id = ""
        self._chase_resubmit_ready = False
        self.trial_state = "error"
        logger.error("验证策略信号被拒绝: %s", self._last_reject_reason)
