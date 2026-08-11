"""
Pre-order risk controls for live trading.

The checks are intentionally conservative and local. They run before any order
reaches the gateway, so both manual orders and strategy-generated signals share
the same guardrail.
"""

from __future__ import annotations

import time
import threading
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional

from ..strategy import OffsetFlag, OrderType, Signal
from .types import AccountInfo

if TYPE_CHECKING:
    from .risk_state_store import LiveRiskStateStore, LiveRiskWriterLease


logger = logging.getLogger(__name__)


def _risk_locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


@dataclass
class RiskConfig:
    enabled: bool = True
    max_order_volume: int = 1000
    max_position_volume: int = 10000
    max_active_orders: int = 200
    max_orders_per_minute: int = 5
    max_daily_loss_ratio: float = 0.10
    max_order_value: float = 0.0
    max_position_value: float = 0.0
    max_price_deviation: float = 0.0
    max_market_data_age_seconds: float = 0.0
    duplicate_signal_window_seconds: float = 0.0
    order_count_alert_threshold: int = 0
    cancel_count_alert_threshold: int = 0
    duplicate_open_alert_threshold: int = 0
    duplicate_close_alert_threshold: int = 0
    duplicate_cancel_alert_threshold: int = 0
    duplicate_cancel_window_seconds: float = 0.0
    default_contract_multiplier: float = 10.0
    contract_multipliers: Dict[str, float] = field(default_factory=dict)
    allow_market_orders: bool = True
    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "RiskConfig":
        raw = raw or {}
        symbols = raw.get("allowed_symbols") or []
        blocked = raw.get("blocked_symbols") or []
        multipliers = raw.get("contract_multipliers") or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_order_volume=max(1, int(raw.get("max_order_volume", 1000))),
            max_position_volume=max(1, int(raw.get("max_position_volume", 10000))),
            max_active_orders=max(1, int(raw.get("max_active_orders", 200))),
            max_orders_per_minute=max(1, int(raw.get("max_orders_per_minute", 5))),
            max_daily_loss_ratio=max(0.0, float(raw.get("max_daily_loss_ratio", 0.10))),
            max_order_value=max(0.0, float(raw.get("max_order_value", 0.0))),
            max_position_value=max(0.0, float(raw.get("max_position_value", 0.0))),
            max_price_deviation=max(0.0, float(raw.get("max_price_deviation", 0.0))),
            max_market_data_age_seconds=max(0.0, float(raw.get("max_market_data_age_seconds", 0.0))),
            duplicate_signal_window_seconds=max(0.0, float(raw.get("duplicate_signal_window_seconds", 0.0))),
            order_count_alert_threshold=max(0, int(raw.get("order_count_alert_threshold", 0))),
            cancel_count_alert_threshold=max(0, int(raw.get("cancel_count_alert_threshold", 0))),
            duplicate_open_alert_threshold=max(0, int(raw.get("duplicate_open_alert_threshold", 0))),
            duplicate_close_alert_threshold=max(0, int(raw.get("duplicate_close_alert_threshold", 0))),
            duplicate_cancel_alert_threshold=max(0, int(raw.get("duplicate_cancel_alert_threshold", 0))),
            duplicate_cancel_window_seconds=max(0.0, float(raw.get("duplicate_cancel_window_seconds", 0.0))),
            default_contract_multiplier=max(1.0, float(raw.get("default_contract_multiplier", 10.0))),
            contract_multipliers={
                str(symbol).strip(): max(1.0, float(value))
                for symbol, value in multipliers.items()
                if str(symbol).strip()
            },
            allow_market_orders=bool(raw.get("allow_market_orders", True)),
            allowed_symbols={str(s).strip() for s in symbols if str(s).strip()},
            blocked_symbols={str(s).strip() for s in blocked if str(s).strip()},
        )


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""
    remaining: int = 0
    retry_after_seconds: float = 0.0


class RiskManager:
    """Validates signals before they are submitted to a gateway."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None, monotonic_clock=None, clock=None) -> None:
        self.config = RiskConfig.from_mapping(config)
        self._lock = threading.RLock()
        self._monotonic = monotonic_clock or clock or time.monotonic
        self.day_open_balance = 0.0
        self._order_timestamps: List[float] = []
        self._recent_signal_timestamps: Dict[str, float] = {}
        self._recent_cancel_timestamps: Dict[str, float] = {}
        self._compliance_counters: Dict[str, int] = {
            "orders_submitted": 0,
            "cancel_requests": 0,
            "cancels_accepted": 0,
            "duplicate_open": 0,
            "duplicate_close": 0,
            "duplicate_cancel": 0,
        }
        self._compliance_alerts: List[Dict[str, Any]] = []
        self._alerted_compliance_counters: set[str] = set()
        self._compliance_trading_day = datetime.now().date().isoformat()
        self.emergency_stop = False
        self.emergency_reason = ""
        self._state_store: Optional[LiveRiskStateStore] = None
        self._state_writer_lease: Optional[LiveRiskWriterLease] = None
        self._state_scope = ""
        self._state_path_key = ""
        self._bound_trading_day = ""
        self._wall_clock = time.time
        self._persistence_error = ""

    @_risk_locked
    def configure(self, config: Optional[Mapping[str, Any]]) -> None:
        raw = (config or {}).get("risk", config or {})
        self.config = RiskConfig.from_mapping(raw)

    @_risk_locked
    def bind_persistent_state(
        self,
        store: "LiveRiskStateStore",
        *,
        scope: str,
        trading_day: str,
        day_open_balance: Optional[float] = None,
    ) -> None:
        """Bind this manager to one anonymous live account and broker day."""
        normalized_scope = str(scope or "").strip()
        normalized_day = str(trading_day or "").strip()
        if not normalized_scope:
            raise RuntimeError("risk state scope is required")
        if not normalized_day:
            raise RuntimeError("risk state trading day is required")

        state_path_key = str(store.path.resolve())
        if self._state_writer_lease is not None and (
            normalized_scope != self._state_scope or state_path_key != self._state_path_key
        ):
            raise RuntimeError("active live risk writer cannot change account scope or state path")

        acquired_lease = None
        if self._state_writer_lease is None:
            acquired_lease = store.acquire_writer_lease(normalized_scope)

        try:
            record = store.load(normalized_scope)
            self._state_store = store
            self._state_scope = normalized_scope
            self._state_path_key = state_path_key
            self._bound_trading_day = normalized_day
            if acquired_lease is not None:
                self._state_writer_lease = acquired_lease

            if record and str(record.get("trading_day") or "") == normalized_day:
                state = record.get("state")
                if not isinstance(state, dict):
                    raise RuntimeError("risk state payload is invalid")
                try:
                    self._restore_persistent_state(state, normalized_day)
                except RuntimeError:
                    raise
                except (TypeError, ValueError, OverflowError) as exc:
                    raise RuntimeError(f"risk state payload is invalid: {exc}") from exc
                self._persistence_error = ""
                return

            preserved_stop = self.emergency_stop
            preserved_reason = self.emergency_reason
            if record:
                previous = record.get("state")
                if not isinstance(previous, dict):
                    raise RuntimeError("risk state payload is invalid")
                preserved_stop = bool(previous.get("emergency_stop", preserved_stop))
                preserved_reason = str(previous.get("emergency_reason", preserved_reason) or "")

            self._reset_intraday_state(normalized_day)
            if day_open_balance is not None:
                self.day_open_balance = max(0.0, float(day_open_balance or 0.0))
            self.emergency_stop = preserved_stop
            self.emergency_reason = preserved_reason
            self._persist_state(raise_on_error=True)
        except Exception:
            if acquired_lease is not None:
                acquired_lease.release()
                self._state_writer_lease = None
                self._state_store = None
                self._state_scope = ""
                self._state_path_key = ""
                self._bound_trading_day = ""
            raise

    @_risk_locked
    def fail_closed_for_persistence(self, reason: str) -> None:
        """Disable submissions after live risk state cannot be trusted."""
        self._state_store = None
        if self._state_writer_lease is None:
            self._state_scope = ""
            self._state_path_key = ""
            self._bound_trading_day = ""
        self._persistence_error = str(reason or "Live risk state is unavailable")
        self.emergency_stop = True
        self.emergency_reason = self._persistence_error

    @_risk_locked
    def close_persistent_state(self) -> None:
        lease = self._state_writer_lease
        self._state_writer_lease = None
        self._state_store = None
        self._state_scope = ""
        self._state_path_key = ""
        self._bound_trading_day = ""
        if lease is not None:
            lease.release()

    @_risk_locked
    def set_day_open_balance(self, balance: float) -> None:
        self.day_open_balance = max(0.0, float(balance or 0.0))
        self._persist_state()

    @_risk_locked
    def set_emergency_stop(self, enabled: bool, reason: str = "") -> None:
        self.emergency_stop = bool(enabled)
        self.emergency_reason = str(reason or "").strip()
        self._persist_state()

    @_risk_locked
    def status(self) -> Dict[str, Any]:
        self._ensure_compliance_trading_day()
        cfg = self.config
        rate = self.order_rate_snapshot()
        return {
            "enabled": cfg.enabled,
            "emergency_stop": self.emergency_stop,
            "emergency_reason": self.emergency_reason,
            "persistence_error": self._persistence_error,
            "day_open_balance": self.day_open_balance,
            "max_order_volume": cfg.max_order_volume,
            "max_position_volume": cfg.max_position_volume,
            "max_active_orders": cfg.max_active_orders,
            "max_orders_per_minute": cfg.max_orders_per_minute,
            "max_daily_loss_ratio": cfg.max_daily_loss_ratio,
            "max_order_value": cfg.max_order_value,
            "max_position_value": cfg.max_position_value,
            "max_price_deviation": cfg.max_price_deviation,
            "max_market_data_age_seconds": cfg.max_market_data_age_seconds,
            "duplicate_signal_window_seconds": cfg.duplicate_signal_window_seconds,
            "duplicate_cancel_window_seconds": cfg.duplicate_cancel_window_seconds,
            "allow_market_orders": cfg.allow_market_orders,
            "allowed_symbols": sorted(cfg.allowed_symbols),
            "blocked_symbols": sorted(cfg.blocked_symbols),
            "rate_limit_remaining": rate["remaining"],
            "rate_limit_retry_after_seconds": rate["retry_after_seconds"],
            "rate_capacity_remaining": rate["remaining"],
            "rate_capacity_retry_after_seconds": rate["retry_after_seconds"],
            "compliance": {
                "trading_day": self._compliance_trading_day,
                "counters": dict(self._compliance_counters),
                "alerts": [dict(alert) for alert in self._compliance_alerts],
                "thresholds": {
                    "orders_submitted": cfg.order_count_alert_threshold,
                    "cancel_requests": cfg.cancel_count_alert_threshold,
                    "duplicate_open": cfg.duplicate_open_alert_threshold,
                    "duplicate_close": cfg.duplicate_close_alert_threshold,
                    "duplicate_cancel": cfg.duplicate_cancel_alert_threshold,
                },
            },
        }

    @_risk_locked
    def order_rate_snapshot(
        self,
        now_monotonic: Optional[float] = None,
        required_capacity: int = 1,
    ) -> Dict[str, Any]:
        now = float(self._monotonic() if now_monotonic is None else now_monotonic)
        self._prune_order_timestamps(now)
        used = len(self._order_timestamps)
        remaining = max(0, self.config.max_orders_per_minute - used)
        retry_after = 0.0
        required = max(1, int(required_capacity or 1))
        if remaining < required and self._order_timestamps:
            expire_count = required - remaining
            index = min(len(self._order_timestamps) - 1, expire_count - 1)
            retry_after = max(0.0, self._order_timestamps[index] + 60.0 - now)
        return {
            "remaining": remaining,
            "retry_after_seconds": round(retry_after, 3),
            "used": used,
            "limit": self.config.max_orders_per_minute,
        }

    @staticmethod
    def required_rate_capacity(signal: Signal) -> int:
        return 2 if signal.offset == OffsetFlag.OPEN else 1

    @_risk_locked
    def check_signal(
        self,
        signal: Signal,
        *,
        positions: Optional[Mapping[str, Any]] = None,
        active_orders: Optional[Iterable[Any]] = None,
        account: Optional[AccountInfo] = None,
        market_data: Optional[Mapping[str, Any]] = None,
        allow_stale_close: bool = False,
    ) -> RiskCheckResult:
        cfg = self.config
        if not cfg.enabled:
            return RiskCheckResult(True)

        if self.emergency_stop:
            suffix = f": {self.emergency_reason}" if self.emergency_reason else ""
            return RiskCheckResult(False, f"Emergency stop is active{suffix}")

        if not signal.validate():
            return RiskCheckResult(False, "Invalid signal: symbol, price or volume is invalid")

        symbol = signal.symbol.strip()
        if not symbol:
            return RiskCheckResult(False, "Symbol is required")
        if cfg.allowed_symbols and symbol not in cfg.allowed_symbols:
            return RiskCheckResult(False, f"Symbol is not in allowed list: {symbol}")
        if symbol in cfg.blocked_symbols:
            return RiskCheckResult(False, f"Symbol is blocked: {symbol}")

        if signal.volume > cfg.max_order_volume:
            return RiskCheckResult(False, f"Order volume {signal.volume} exceeds limit {cfg.max_order_volume}")

        if signal.order_type == OrderType.MARKET and not cfg.allow_market_orders:
            return RiskCheckResult(False, "Market orders are disabled by risk config")

        market_result = self._check_market_data(signal, market_data, allow_stale_close=allow_stale_close)
        if not market_result.allowed:
            return market_result

        active_count = self._active_order_count(active_orders)
        if active_count >= cfg.max_active_orders:
            return RiskCheckResult(False, f"Active order count {active_count} exceeds limit {cfg.max_active_orders}")

        rate_result = self._check_order_rate(signal)
        if not rate_result.allowed:
            return rate_result

        daily_loss_result = self._check_daily_loss(account)
        if not daily_loss_result.allowed:
            return daily_loss_result

        duplicate_result = self._check_duplicate_signal(signal)
        if not duplicate_result.allowed:
            return duplicate_result

        current_volume = self._position_volume(symbol, positions)
        effective_price = self._effective_price(signal, market_data)
        multiplier = self._contract_multiplier(symbol)
        order_value = effective_price * signal.volume * multiplier
        if cfg.max_order_value > 0 and order_value > cfg.max_order_value:
            return RiskCheckResult(False, f"Order value {round(order_value, 2)} exceeds limit {cfg.max_order_value}")

        if signal.offset == OffsetFlag.OPEN:
            projected = abs(current_volume) + signal.volume
            if projected > cfg.max_position_volume:
                return RiskCheckResult(
                    False,
                    f"Projected position {projected} exceeds limit {cfg.max_position_volume}",
                )
            projected_value = projected * effective_price * multiplier
            if cfg.max_position_value > 0 and projected_value > cfg.max_position_value:
                return RiskCheckResult(
                    False,
                    f"Projected position value {round(projected_value, 2)} exceeds limit {cfg.max_position_value}",
                )
        else:
            if abs(current_volume) <= 0:
                return RiskCheckResult(False, f"No position available to close for {symbol}")
            if signal.volume > abs(current_volume):
                return RiskCheckResult(
                    False,
                    f"Close volume {signal.volume} exceeds current position {abs(current_volume)}",
                )

        return RiskCheckResult(True)

    @_risk_locked
    def record_order(self, signal: Optional[Signal] = None) -> None:
        self._ensure_compliance_trading_day()
        now = float(self._monotonic())
        self._order_timestamps.append(now)
        self._prune_order_timestamps(now)
        self._increment_compliance_counter("orders_submitted")
        if signal is not None:
            self._recent_signal_timestamps[self._signal_key(signal)] = now
        self._persist_state()

    @_risk_locked
    def check_cancel_request(self, order_id: str) -> RiskCheckResult:
        self._ensure_compliance_trading_day()
        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id:
            return RiskCheckResult(False, "Order id is required for cancellation")

        window = self.config.duplicate_cancel_window_seconds
        if window <= 0:
            return RiskCheckResult(True)

        now = float(self._monotonic())
        cutoff = now - window
        self._recent_cancel_timestamps = {
            key: ts for key, ts in self._recent_cancel_timestamps.items() if ts >= cutoff
        }
        last_ts = self._recent_cancel_timestamps.get(normalized_order_id)
        if last_ts is not None and now - last_ts < window:
            self._increment_compliance_counter("duplicate_cancel")
            return RiskCheckResult(False, f"Duplicate cancel within {round(window, 2)}s window")

        self._recent_cancel_timestamps[normalized_order_id] = now
        self._persist_state()
        return RiskCheckResult(True)

    @_risk_locked
    def record_cancel(self, order_id: str, *, accepted: bool) -> None:
        self._ensure_compliance_trading_day()
        normalized_order_id = str(order_id or "").strip()
        if normalized_order_id and self.config.duplicate_cancel_window_seconds > 0:
            self._recent_cancel_timestamps.setdefault(normalized_order_id, float(self._monotonic()))
        self._increment_compliance_counter("cancel_requests")
        if accepted:
            self._increment_compliance_counter("cancels_accepted")
        self._persist_state()

    def _check_order_rate(self, signal: Optional[Signal] = None) -> RiskCheckResult:
        required = self.required_rate_capacity(signal) if signal is not None else 1
        snapshot = self.order_rate_snapshot(required_capacity=required)
        if snapshot["remaining"] < required:
            return RiskCheckResult(
                False,
                f"Order rate capacity is insufficient: need {required}, remaining {snapshot['remaining']}",
                remaining=snapshot["remaining"],
                retry_after_seconds=snapshot["retry_after_seconds"],
            )
        return RiskCheckResult(True)

    def _prune_order_timestamps(self, now: float) -> None:
        cutoff = now - 60.0
        self._order_timestamps = [ts for ts in self._order_timestamps if ts > cutoff]

    def _check_daily_loss(self, account: Optional[AccountInfo]) -> RiskCheckResult:
        if self.config.max_daily_loss_ratio <= 0 or self.day_open_balance <= 0 or account is None:
            return RiskCheckResult(True)
        if account.balance <= 0:
            return RiskCheckResult(True)
        loss_ratio = (self.day_open_balance - account.balance) / self.day_open_balance
        if loss_ratio >= self.config.max_daily_loss_ratio:
            pct = round(loss_ratio * 100, 2)
            limit = round(self.config.max_daily_loss_ratio * 100, 2)
            return RiskCheckResult(False, f"Daily loss {pct}% exceeds limit {limit}%")
        return RiskCheckResult(True)

    def _check_market_data(
        self,
        signal: Signal,
        market_data: Optional[Mapping[str, Any]],
        *,
        allow_stale_close: bool = False,
    ) -> RiskCheckResult:
        cfg = self.config
        if allow_stale_close and signal.offset in {
            OffsetFlag.CLOSE,
            OffsetFlag.CLOSE_TODAY,
            OffsetFlag.CLOSE_YESTERDAY,
        }:
            return RiskCheckResult(True)
        if not market_data:
            if cfg.max_market_data_age_seconds > 0:
                return RiskCheckResult(False, f"Market data is unavailable for {signal.symbol}")
            return RiskCheckResult(True)

        if cfg.max_market_data_age_seconds > 0:
            timestamp = market_data.get("timestamp")
            age = self._market_data_age(timestamp)
            if age is None:
                return RiskCheckResult(False, f"Market data timestamp is unavailable for {signal.symbol}")
            if age > cfg.max_market_data_age_seconds:
                return RiskCheckResult(
                    False,
                    f"Market data is stale for {signal.symbol}: {round(age, 2)}s",
                )

        if cfg.max_price_deviation > 0 and signal.order_type != OrderType.MARKET:
            latest = self._market_price(market_data)
            if latest <= 0:
                return RiskCheckResult(False, f"Latest market price is unavailable for {signal.symbol}")
            deviation = abs(float(signal.price) - latest) / latest
            if deviation > cfg.max_price_deviation:
                return RiskCheckResult(
                    False,
                    f"Price deviation {round(deviation * 100, 3)}% exceeds limit {round(cfg.max_price_deviation * 100, 3)}%",
                )

        return RiskCheckResult(True)

    def _check_duplicate_signal(self, signal: Signal) -> RiskCheckResult:
        self._ensure_compliance_trading_day()
        window = self.config.duplicate_signal_window_seconds
        if window <= 0:
            return RiskCheckResult(True)
        now = float(self._monotonic())
        cutoff = now - window
        self._recent_signal_timestamps = {
            key: ts for key, ts in self._recent_signal_timestamps.items() if ts >= cutoff
        }
        last_ts = self._recent_signal_timestamps.get(self._signal_key(signal))
        if last_ts is not None and now - last_ts < window:
            counter = "duplicate_open" if signal.offset == OffsetFlag.OPEN else "duplicate_close"
            self._increment_compliance_counter(counter)
            return RiskCheckResult(False, f"Duplicate signal within {round(window, 2)}s window")
        return RiskCheckResult(True)

    def _increment_compliance_counter(self, counter: str) -> None:
        previous = self._compliance_counters[counter]
        current = previous + 1
        self._compliance_counters[counter] = current

        threshold_by_counter = {
            "orders_submitted": self.config.order_count_alert_threshold,
            "cancel_requests": self.config.cancel_count_alert_threshold,
            "duplicate_open": self.config.duplicate_open_alert_threshold,
            "duplicate_close": self.config.duplicate_close_alert_threshold,
            "duplicate_cancel": self.config.duplicate_cancel_alert_threshold,
        }
        threshold = threshold_by_counter.get(counter, 0)
        if (
            threshold > 0
            and previous < threshold <= current
            and counter not in self._alerted_compliance_counters
        ):
            self._alerted_compliance_counters.add(counter)
            self._compliance_alerts.append(
                {
                    "counter": counter,
                    "count": current,
                    "threshold": threshold,
                    "timestamp": datetime.now().isoformat(),
                    "message": f"Compliance threshold reached: {counter}={current}, limit={threshold}",
                }
            )
            logger.warning(
                "合规阈值告警: counter=%s count=%s threshold=%s",
                counter,
                current,
                threshold,
            )
        self._persist_state()

    def _ensure_compliance_trading_day(self) -> None:
        trading_day = self._bound_trading_day or datetime.now().date().isoformat()
        if trading_day == self._compliance_trading_day:
            return
        self._reset_intraday_state(trading_day)
        self._persist_state()

    def _reset_intraday_state(self, trading_day: str) -> None:
        self._compliance_trading_day = str(trading_day)
        self._order_timestamps.clear()
        for counter in self._compliance_counters:
            self._compliance_counters[counter] = 0
        self._compliance_alerts.clear()
        self._alerted_compliance_counters.clear()
        self._recent_signal_timestamps.clear()
        self._recent_cancel_timestamps.clear()

    def _restore_persistent_state(self, state: Mapping[str, Any], trading_day: str) -> None:
        self._reset_intraday_state(trading_day)
        self.day_open_balance = max(0.0, float(state.get("day_open_balance", 0.0) or 0.0))
        self.emergency_stop = bool(state.get("emergency_stop", False))
        self.emergency_reason = str(state.get("emergency_reason", "") or "")

        counters = state.get("compliance_counters") or {}
        if not isinstance(counters, Mapping):
            raise RuntimeError("risk state compliance counters are invalid")
        for counter in self._compliance_counters:
            self._compliance_counters[counter] = max(0, int(counters.get(counter, 0) or 0))

        alerts = state.get("compliance_alerts") or []
        if not isinstance(alerts, list):
            raise RuntimeError("risk state compliance alerts are invalid")
        self._compliance_alerts = [dict(item) for item in alerts if isinstance(item, Mapping)]
        alerted = state.get("alerted_compliance_counters") or []
        self._alerted_compliance_counters = {
            str(item) for item in alerted if str(item) in self._compliance_counters
        }

        now_monotonic = float(self._monotonic())
        now_wall = float(self._wall_clock())

        def restore_timestamp(value: Any) -> float | None:
            try:
                epoch = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(epoch) or epoch <= 0:
                return None
            age = max(0.0, now_wall - epoch)
            return now_monotonic - age

        for value in state.get("order_timestamps", []) or []:
            restored = restore_timestamp(value)
            if restored is not None and now_monotonic - restored < 60.0:
                self._order_timestamps.append(restored)

        signal_window = self.config.duplicate_signal_window_seconds
        for key, value in dict(state.get("recent_signal_timestamps") or {}).items():
            restored = restore_timestamp(value)
            if restored is not None and signal_window > 0 and now_monotonic - restored < signal_window:
                self._recent_signal_timestamps[str(key)] = restored

        cancel_window = self.config.duplicate_cancel_window_seconds
        for key, value in dict(state.get("recent_cancel_timestamps") or {}).items():
            restored = restore_timestamp(value)
            if restored is not None and cancel_window > 0 and now_monotonic - restored < cancel_window:
                self._recent_cancel_timestamps[str(key)] = restored

    def _persistent_state(self) -> Dict[str, Any]:
        now_monotonic = float(self._monotonic())
        now_wall = float(self._wall_clock())

        def to_epoch(timestamp: float) -> float:
            return now_wall - max(0.0, now_monotonic - float(timestamp))

        return {
            "day_open_balance": self.day_open_balance,
            "emergency_stop": self.emergency_stop,
            "emergency_reason": self.emergency_reason,
            "order_timestamps": [to_epoch(ts) for ts in self._order_timestamps],
            "recent_signal_timestamps": {
                key: to_epoch(ts) for key, ts in self._recent_signal_timestamps.items()
            },
            "recent_cancel_timestamps": {
                key: to_epoch(ts) for key, ts in self._recent_cancel_timestamps.items()
            },
            "compliance_counters": dict(self._compliance_counters),
            "compliance_alerts": [dict(alert) for alert in self._compliance_alerts],
            "alerted_compliance_counters": sorted(self._alerted_compliance_counters),
        }

    def _persist_state(self, *, raise_on_error: bool = False) -> None:
        if self._state_store is None or not self._state_scope or not self._bound_trading_day:
            return
        try:
            self._state_store.save(
                self._state_scope,
                self._bound_trading_day,
                self._persistent_state(),
            )
            self._persistence_error = ""
        except RuntimeError as exc:
            self._persistence_error = f"Live risk state persistence failed: {exc}"
            self.emergency_stop = True
            self.emergency_reason = self._persistence_error
            logger.error("实盘风控状态落盘失败，已触发急停: %s", exc)
            if raise_on_error:
                raise RuntimeError(self._persistence_error) from exc

    def _contract_multiplier(self, symbol: str) -> float:
        if symbol in self.config.contract_multipliers:
            return self.config.contract_multipliers[symbol]
        product = "".join(ch for ch in symbol if ch.isalpha()).upper()
        if product in self.config.contract_multipliers:
            return self.config.contract_multipliers[product]
        return self.config.default_contract_multiplier

    def _effective_price(self, signal: Signal, market_data: Optional[Mapping[str, Any]]) -> float:
        if signal.order_type != OrderType.MARKET and signal.price > 0:
            return float(signal.price)
        return self._market_price(market_data)

    @staticmethod
    def _market_price(market_data: Optional[Mapping[str, Any]]) -> float:
        if not market_data:
            return 0.0
        for key in ("last_price", "price", "close", "ask_price_1", "bid_price_1"):
            try:
                value = float(market_data.get(key, 0) or 0)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _market_data_age(timestamp: Any) -> Optional[float]:
        if timestamp is None:
            return None
        if isinstance(timestamp, (int, float)):
            return max(0.0, time.time() - float(timestamp))
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return None
        if isinstance(timestamp, datetime):
            now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
            return max(0.0, (now - timestamp).total_seconds())
        return None

    @staticmethod
    def _signal_key(signal: Signal) -> str:
        direction = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)
        offset = signal.offset.value if hasattr(signal.offset, "value") else str(signal.offset)
        order_type = signal.order_type.value if hasattr(signal.order_type, "value") else str(signal.order_type)
        return "|".join(
            [
                signal.symbol.strip(),
                direction,
                offset,
                order_type,
                str(round(float(signal.price or 0), 6)),
                str(int(signal.volume)),
            ]
        )

    @staticmethod
    def _active_order_count(active_orders: Optional[Iterable[Any]]) -> int:
        if active_orders is None:
            return 0
        count = 0
        for order in active_orders:
            is_active = getattr(order, "is_active", None)
            if callable(is_active):
                count += 1 if is_active() else 0
            else:
                count += 1
        return count

    @staticmethod
    def _position_volume(symbol: str, positions: Optional[Mapping[str, Any]]) -> int:
        if not positions:
            return 0
        position = positions.get(symbol)
        if position is None:
            for pos in positions.values():
                if getattr(pos, "symbol", "") == symbol:
                    position = pos
                    break
        if position is None:
            return 0
        try:
            return int(getattr(position, "volume", 0))
        except (TypeError, ValueError):
            return 0
