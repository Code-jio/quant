"""
Pre-order risk controls for live trading.

The checks are intentionally conservative and local. They run before any order
reaches the gateway, so both manual orders and strategy-generated signals share
the same guardrail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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
    allow_market_orders: bool = True
    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "RiskConfig":
        raw = raw or {}
        symbols = raw.get("allowed_symbols") or []
        blocked = raw.get("blocked_symbols") or []
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_order_volume=max(1, int(raw.get("max_order_volume", 1000))),
            max_position_volume=max(1, int(raw.get("max_position_volume", 10000))),
            max_active_orders=max(1, int(raw.get("max_active_orders", 200))),
            max_orders_per_minute=max(1, int(raw.get("max_orders_per_minute", 120))),
            max_daily_loss_ratio=max(0.0, float(raw.get("max_daily_loss_ratio", 0.10))),
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

    def configure(self, config: Optional[Mapping[str, Any]]) -> None:
        raw = (config or {}).get("risk", config or {})
        self.config = RiskConfig.from_mapping(raw)

    def set_day_open_balance(self, balance: float) -> None:
        self.day_open_balance = max(0.0, float(balance or 0.0))

    def check_signal(
        self,
        signal: Signal,
        *,
        positions: Optional[Mapping[str, Any]] = None,
        active_orders: Optional[Iterable[Any]] = None,
        account: Optional[AccountInfo] = None,
    ) -> RiskCheckResult:
        cfg = self.config
        if not cfg.enabled:
            return RiskCheckResult(True)

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

        active_count = self._active_order_count(active_orders)
        if active_count >= cfg.max_active_orders:
            return RiskCheckResult(False, f"Active order count {active_count} exceeds limit {cfg.max_active_orders}")

        rate_result = self._check_order_rate()
        if not rate_result.allowed:
            return rate_result

        daily_loss_result = self._check_daily_loss(account)
        if not daily_loss_result.allowed:
            return daily_loss_result

        current_volume = self._position_volume(symbol, positions)
        if signal.offset == OffsetFlag.OPEN:
            projected = abs(current_volume) + signal.volume
            if projected > cfg.max_position_volume:
                return RiskCheckResult(
                    False,
                    f"Projected position {projected} exceeds limit {cfg.max_position_volume}",
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

    def record_order(self) -> None:
        now = time.monotonic()
        self._order_timestamps.append(now)
        self._prune_order_timestamps(now)

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
