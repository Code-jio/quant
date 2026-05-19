"""
Pre-order risk controls for live trading.

The checks are intentionally conservative and local. They run before any order
reaches the gateway, so both manual orders and strategy-generated signals share
the same guardrail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..strategy import OffsetFlag, OrderType, Signal
from .types import AccountInfo


@dataclass
class RiskConfig:
    enabled: bool = True
    max_order_volume: int = 1000
    max_position_volume: int = 10000
    max_active_orders: int = 200
    max_orders_per_minute: int = 120
    max_daily_loss_ratio: float = 0.10
    max_order_value: float = 0.0
    max_position_value: float = 0.0
    max_price_deviation: float = 0.0
    max_market_data_age_seconds: float = 0.0
    duplicate_signal_window_seconds: float = 0.0
    default_contract_multiplier: float = 1.0
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
            max_orders_per_minute=max(1, int(raw.get("max_orders_per_minute", 120))),
            max_daily_loss_ratio=max(0.0, float(raw.get("max_daily_loss_ratio", 0.10))),
            max_order_value=max(0.0, float(raw.get("max_order_value", 0.0))),
            max_position_value=max(0.0, float(raw.get("max_position_value", 0.0))),
            max_price_deviation=max(0.0, float(raw.get("max_price_deviation", 0.0))),
            max_market_data_age_seconds=max(0.0, float(raw.get("max_market_data_age_seconds", 0.0))),
            duplicate_signal_window_seconds=max(0.0, float(raw.get("duplicate_signal_window_seconds", 0.0))),
            default_contract_multiplier=max(1.0, float(raw.get("default_contract_multiplier", 1.0))),
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


class RiskManager:
    """Validates signals before they are submitted to a gateway."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.config = RiskConfig.from_mapping(config)
        self.day_open_balance = 0.0
        self._order_timestamps: List[float] = []
        self._recent_signal_timestamps: Dict[str, float] = {}
        self.emergency_stop = False
        self.emergency_reason = ""

    def configure(self, config: Optional[Mapping[str, Any]]) -> None:
        raw = (config or {}).get("risk", config or {})
        self.config = RiskConfig.from_mapping(raw)

    def set_day_open_balance(self, balance: float) -> None:
        self.day_open_balance = max(0.0, float(balance or 0.0))

    def set_emergency_stop(self, enabled: bool, reason: str = "") -> None:
        self.emergency_stop = bool(enabled)
        self.emergency_reason = str(reason or "").strip()

    def status(self) -> Dict[str, Any]:
        cfg = self.config
        return {
            "enabled": cfg.enabled,
            "emergency_stop": self.emergency_stop,
            "emergency_reason": self.emergency_reason,
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
            "allow_market_orders": cfg.allow_market_orders,
            "allowed_symbols": sorted(cfg.allowed_symbols),
            "blocked_symbols": sorted(cfg.blocked_symbols),
        }

    def check_signal(
        self,
        signal: Signal,
        *,
        positions: Optional[Mapping[str, Any]] = None,
        active_orders: Optional[Iterable[Any]] = None,
        account: Optional[AccountInfo] = None,
        market_data: Optional[Mapping[str, Any]] = None,
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

        market_result = self._check_market_data(signal, market_data)
        if not market_result.allowed:
            return market_result

        active_count = self._active_order_count(active_orders)
        if active_count >= cfg.max_active_orders:
            return RiskCheckResult(False, f"Active order count {active_count} exceeds limit {cfg.max_active_orders}")

        rate_result = self._check_order_rate()
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

    def record_order(self, signal: Optional[Signal] = None) -> None:
        now = time.monotonic()
        self._order_timestamps.append(now)
        self._prune_order_timestamps(now)
        if signal is not None:
            self._recent_signal_timestamps[self._signal_key(signal)] = now

    def _check_order_rate(self) -> RiskCheckResult:
        now = time.monotonic()
        self._prune_order_timestamps(now)
        if len(self._order_timestamps) >= self.config.max_orders_per_minute:
            return RiskCheckResult(False, f"Order rate exceeds {self.config.max_orders_per_minute}/minute")
        return RiskCheckResult(True)

    def _prune_order_timestamps(self, now: float) -> None:
        cutoff = now - 60.0
        self._order_timestamps = [ts for ts in self._order_timestamps if ts >= cutoff]

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

    def _check_market_data(self, signal: Signal, market_data: Optional[Mapping[str, Any]]) -> RiskCheckResult:
        cfg = self.config
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
        window = self.config.duplicate_signal_window_seconds
        if window <= 0:
            return RiskCheckResult(True)
        now = time.monotonic()
        cutoff = now - window
        self._recent_signal_timestamps = {
            key: ts for key, ts in self._recent_signal_timestamps.items() if ts >= cutoff
        }
        last_ts = self._recent_signal_timestamps.get(self._signal_key(signal))
        if last_ts is not None and now - last_ts < window:
            return RiskCheckResult(False, f"Duplicate signal within {round(window, 2)}s window")
        return RiskCheckResult(True)

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
