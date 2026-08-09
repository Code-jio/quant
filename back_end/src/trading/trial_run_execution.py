"""Typed acceptance state for the trial-run real and simulated tracks.

This module deliberately contains no gateway, API, persistence, or UI concerns.
It records evidence supplied by those layers and decides only what that
evidence is allowed to prove.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Any, Iterable, Mapping, Optional, Union
from uuid import uuid4

from .symbols import symbols_match


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class TrialRunOutcome(str, Enum):
    RUNNING = "running"
    PASSED_REAL = "passed_real"
    PASSED_SIMULATED = "passed_simulated"
    FAILED = "failed"
    ABORTED = "aborted"

    # Lower-case aliases make the serialized domain vocabulary convenient to
    # use without weakening the typed enum contract.
    running = RUNNING
    passed_real = PASSED_REAL
    passed_simulated = PASSED_SIMULATED
    failed = FAILED
    aborted = ABORTED


class TrialRunTrack(str, Enum):
    REAL = "real"
    SIMULATED = "simulated"

    real = REAL
    simulated = SIMULATED


class TrialRunExecutionError(ValueError):
    """Stable domain error raised when trial-run evidence is invalid."""

    def __init__(self, failure_code: str, message: str = "") -> None:
        self.failure_code = failure_code
        super().__init__(message or failure_code)


@dataclass
class TrialOrderAttempt:
    order_id: str
    role: str
    track: TrialRunTrack
    attempt: int
    parent_order_id: str
    symbol: str
    direction: str
    offset: str
    price: float
    volume: int
    status: str
    created_at: datetime
    updated_at: datetime


_BROKER_ACCEPTED_STATUSES = {
    "submitted",
    "accepted",
    "broker_accepted",
    "broker-accepted",
    "broker accepted",
    "partfilled",
    "partialfilled",
    "partial_filled",
    "filled",
}
_ORDER_UPDATE_STATUSES = {
    "submitting",
    "submitted",
    "accepted",
    "broker_accepted",
    "broker-accepted",
    "broker accepted",
    "partfilled",
    "partialfilled",
    "partial_filled",
    "filled",
    "cancelled",
    "canceled",
    "rejected",
}
_TERMINAL_ORDER_STATUSES = {"filled", "cancelled", "canceled", "rejected"}
_ORDER_STATUS_PHASES = {
    "submitting": 0,
    "submitted": 1,
    "accepted": 1,
    "broker_accepted": 1,
    "broker-accepted": 1,
    "broker accepted": 1,
    "partfilled": 2,
    "partialfilled": 2,
    "partial_filled": 2,
}


def _normalized_role(role: Any) -> str:
    return str(_enum_value(role) or "").strip().lower()


def _is_entry_role(role: Any) -> bool:
    return _normalized_role(role) == "entry"


def _is_exit_role(role: Any) -> bool:
    return _normalized_role(role) in {"exit", "close"}


def _roles_match(left: Any, right: Any) -> bool:
    return (
        (_is_entry_role(left) and _is_entry_role(right))
        or (_is_exit_role(left) and _is_exit_role(right))
    )


def _normalized_enum_value(value: Any) -> str:
    return str(_enum_value(value) or "").strip().lower()


def _canonical_order_status(status: Any) -> str:
    normalized = _normalized_role(status)
    return {
        "canceled": "cancelled",
        "partialfilled": "partfilled",
        "partial_filled": "partfilled",
        "broker_accepted": "accepted",
        "broker-accepted": "accepted",
        "broker accepted": "accepted",
    }.get(normalized, normalized)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace(" ", "")


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


@dataclass
class TrialRunExecutionState:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    symbol: str = "rb2610"
    volume: int = 1
    order_chain: list[TrialOrderAttempt] = field(default_factory=list)
    current_order_id: str = ""
    entry_order_id: str = ""
    close_order_id: str = ""
    real_entry_order_id: str = ""
    real_close_order_id: str = ""
    simulated_entry_order_id: str = ""
    simulated_close_order_id: str = ""
    real_trade_ids: list[str] = field(default_factory=list)
    real_trade_order_ids: dict[str, str] = field(default_factory=dict)
    simulated_trade_ids: list[str] = field(default_factory=list)
    real_entry_trade_id: str = ""
    real_close_trade_id: str = ""
    simulated_entry_trade_id: str = ""
    simulated_close_trade_id: str = ""
    real_submission_proof: bool = False
    simulated_position_volume: int = 0
    simulation_state: str = "not_started"
    current_track: TrialRunTrack = TrialRunTrack.REAL
    simulation_track: TrialRunTrack = TrialRunTrack.SIMULATED
    broker_reconcile_snapshot: dict[str, Any] = field(default_factory=dict)
    broker_position_volume: Optional[int] = None
    broker_active_order_ids: list[str] = field(default_factory=list)
    reconcile_ok: Optional[bool] = None
    failure_code: str = ""
    success_basis: str = ""
    final_outcome: TrialRunOutcome = TrialRunOutcome.RUNNING

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        if self.volume != 1:
            raise TrialRunExecutionError("invalid_trial_volume")
        if not self.symbol or not _clean_symbol(self.symbol):
            raise TrialRunExecutionError("invalid_trial_symbol")
        self.symbol = str(self.symbol).strip()
        if isinstance(self.final_outcome, str):
            self.final_outcome = TrialRunOutcome(self.final_outcome)
        if isinstance(self.current_track, str):
            self.current_track = TrialRunTrack(self.current_track)
        if isinstance(self.simulation_track, str):
            self.simulation_track = TrialRunTrack(self.simulation_track)

    @property
    def configured_symbol(self) -> str:
        return self.symbol

    @property
    def configured_volume(self) -> int:
        return self.volume

    @property
    def outcome(self) -> TrialRunOutcome:
        return self.final_outcome

    def _ensure_mutable(self) -> None:
        if self.final_outcome in {
            TrialRunOutcome.FAILED,
            TrialRunOutcome.ABORTED,
            TrialRunOutcome.PASSED_REAL,
            TrialRunOutcome.PASSED_SIMULATED,
        }:
            raise TrialRunExecutionError("invalid_trial_transition")

    def _validate_contract(self, symbol: Optional[str], volume: int) -> None:
        if symbol is not None and not symbols_match(self.symbol, symbol):
            raise TrialRunExecutionError("invalid_trial_symbol")
        if volume != 1:
            raise TrialRunExecutionError("invalid_trial_volume")

    def _prepare_mutation(self, symbol: Optional[str], volume: int) -> None:
        self._ensure_mutable()
        self._validate_contract(symbol, volume)

    def _find_order(self, order_id: str) -> Optional[TrialOrderAttempt]:
        return next((order for order in self.order_chain if order.order_id == order_id), None)

    def _owned_order(self, order_id: str) -> TrialOrderAttempt:
        order = self._find_order(order_id)
        if order is None:
            raise TrialRunExecutionError("order_not_owned_by_trial_run")
        return order

    def _append_order(
        self,
        *,
        order_id: str,
        role: str,
        track: TrialRunTrack,
        attempt: int,
        parent_order_id: str,
        symbol: str,
        direction: str,
        offset: str,
        price: float,
        status: str,
    ) -> TrialOrderAttempt:
        if not order_id or self._find_order(order_id) is not None:
            raise TrialRunExecutionError("invalid_trial_transition")
        now = _now()
        order = TrialOrderAttempt(
            order_id=order_id,
            role=_normalized_role(role),
            track=track,
            attempt=int(attempt),
            parent_order_id=str(parent_order_id),
            symbol=str(symbol),
            direction=_enum_value(direction),
            offset=_enum_value(offset),
            price=float(price),
            volume=1,
            status=str(status).strip().lower(),
            created_at=now,
            updated_at=now,
        )
        self.order_chain.append(order)
        return order

    @_synchronized
    def record_real_submission(
        self,
        order_id: str,
        role: str,
        price: float,
        direction: str = "long",
        offset: str = "open",
        attempt: int = 0,
        parent_order_id: str = "",
        status: str = "submitted",
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        normalized_role = _normalized_role(role)
        if not (_is_entry_role(normalized_role) or _is_exit_role(normalized_role)):
            raise TrialRunExecutionError("invalid_trial_transition")
        if attempt < 0 or attempt > 5:
            raise TrialRunExecutionError("invalid_trial_transition")
        status_value = _normalized_role(status)
        if status_value not in _ORDER_UPDATE_STATUSES:
            raise TrialRunExecutionError("invalid_trial_transition")
        parent = None
        latest_role_order_id = (
            self.entry_order_id if _is_entry_role(normalized_role) else self.close_order_id
        )
        if attempt == 0:
            if parent_order_id or latest_role_order_id:
                raise TrialRunExecutionError("invalid_trial_transition")
        else:
            parent = self._find_order(parent_order_id)
            if parent is None:
                raise TrialRunExecutionError("order_not_owned_by_trial_run")
            if (
                parent.track is not TrialRunTrack.REAL
                or not _roles_match(parent.role, normalized_role)
                or parent.status not in {"cancelled", "canceled"}
                or attempt != parent.attempt + 1
                or parent.order_id != latest_role_order_id
                or _normalized_enum_value(direction)
                != _normalized_enum_value(parent.direction)
                or _normalized_enum_value(offset) != _normalized_enum_value(parent.offset)
            ):
                raise TrialRunExecutionError("invalid_trial_transition")
        order = self._append_order(
            order_id=order_id,
            role=normalized_role,
            track=TrialRunTrack.REAL,
            attempt=attempt,
            parent_order_id=parent_order_id,
            symbol=self.symbol if symbol is None else str(symbol).strip(),
            direction=direction,
            offset=offset,
            price=price,
            status=status_value,
        )
        self.real_submission_proof = self.real_submission_proof or (
            order.status in _BROKER_ACCEPTED_STATUSES
        )
        if _is_entry_role(order.role):
            if not self.real_entry_order_id:
                self.real_entry_order_id = order.order_id
            self.entry_order_id = order.order_id
        if _is_exit_role(order.role):
            if not self.real_close_order_id:
                self.real_close_order_id = order.order_id
            self.close_order_id = order.order_id
        self.current_order_id = order.order_id
        self.current_track = TrialRunTrack.REAL
        self.simulation_state = "real_track"
        if order.status in _TERMINAL_ORDER_STATUSES:
            self.current_order_id = ""
        return order

    @_synchronized
    def owns_order(self, order_id: str) -> bool:
        """Return whether an order ID belongs to this run's order chain."""
        return self._find_order(str(order_id)) is not None

    @_synchronized
    def record_real_order_update(
        self,
        order_id: str,
        status: str,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: Optional[str] = None,
        traded_volume: Optional[int] = None,
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        order = self._owned_order(order_id)
        if order.track is not TrialRunTrack.REAL:
            raise TrialRunExecutionError("invalid_trial_transition")
        status_value = _normalized_role(status)
        if status_value not in _ORDER_UPDATE_STATUSES:
            raise TrialRunExecutionError("invalid_trial_transition")
        if (
            direction is not None
            and _normalized_enum_value(direction) != _normalized_enum_value(order.direction)
        ):
            raise TrialRunExecutionError("invalid_trial_transition")
        if traded_volume is not None and not 0 <= int(traded_volume) <= order.volume:
            raise TrialRunExecutionError("invalid_trial_volume")

        old_status = _canonical_order_status(order.status)
        new_status = _canonical_order_status(status_value)
        if old_status in {"filled", "rejected"} and new_status != old_status:
            raise TrialRunExecutionError("invalid_trial_transition")
        if old_status == "cancelled" and new_status not in {"cancelled", "filled"}:
            raise TrialRunExecutionError("invalid_trial_transition")
        if (
            old_status not in _TERMINAL_ORDER_STATUSES
            and new_status not in _TERMINAL_ORDER_STATUSES
            and _ORDER_STATUS_PHASES.get(new_status, -1)
            < _ORDER_STATUS_PHASES.get(old_status, -1)
        ):
            raise TrialRunExecutionError("invalid_trial_transition")

        updated = replace(order, status=status_value, updated_at=_now())
        self.order_chain[self.order_chain.index(order)] = updated
        if status_value in _BROKER_ACCEPTED_STATUSES:
            self.real_submission_proof = True
        if status_value in _TERMINAL_ORDER_STATUSES and self.current_order_id == order_id:
            self.current_order_id = ""
        return updated

    @_synchronized
    def record_real_cancel(
        self,
        order_id: str,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        order = self._owned_order(order_id)
        if order.track is not TrialRunTrack.REAL:
            raise TrialRunExecutionError("invalid_trial_transition")
        if order.status in {"filled", "rejected", "failed"}:
            raise TrialRunExecutionError("invalid_trial_transition")
        updated = self.record_real_order_update(
            order_id,
            status="cancelled",
            symbol=symbol,
            volume=volume,
        )
        self.simulation_state = "real_cancelled"
        return updated

    @_synchronized
    def record_real_trade(
        self,
        order_id: str,
        trade_id: str,
        *,
        role: Optional[str] = None,
        price: Optional[float] = None,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: Optional[str] = None,
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        order = self._owned_order(order_id)
        trade_key = str(trade_id)
        if order.track is not TrialRunTrack.REAL or not trade_key:
            raise TrialRunExecutionError("invalid_trial_transition")
        if direction is not None and _enum_value(direction) != order.direction:
            raise TrialRunExecutionError("invalid_trial_transition")
        resolved_role = _normalized_role(role or order.role)
        if not (
            (_is_entry_role(resolved_role) and _is_entry_role(order.role))
            or (_is_exit_role(resolved_role) and _is_exit_role(order.role))
        ):
            raise TrialRunExecutionError("invalid_trial_transition")
        if order.status == "rejected":
            raise TrialRunExecutionError("invalid_trial_transition")
        existing_role_trade_id = (
            self.real_entry_trade_id
            if _is_entry_role(resolved_role)
            else self.real_close_trade_id
        )
        if trade_key in self.real_trade_ids:
            if self.real_trade_order_ids.get(trade_key) == order.order_id:
                return order
            raise TrialRunExecutionError("invalid_trial_transition")
        if existing_role_trade_id:
            raise TrialRunExecutionError("invalid_trial_transition")
        updated = replace(order, status="filled", updated_at=_now())
        self.order_chain[self.order_chain.index(order)] = updated
        self.real_submission_proof = True
        self.real_trade_ids.append(trade_key)
        self.real_trade_order_ids[trade_key] = order.order_id
        if _is_entry_role(resolved_role):
            self.real_entry_trade_id = trade_key
        elif _is_exit_role(resolved_role):
            self.real_close_trade_id = trade_key
        if self.current_order_id == order_id:
            self.current_order_id = ""
        return updated

    @_synchronized
    def request_simulation(self, source_order_id: str) -> TrialOrderAttempt:
        """Record an operator request to migrate one unfilled real entry."""
        self._ensure_mutable()
        order = self._owned_order(source_order_id)
        if (
            order.track is not TrialRunTrack.REAL
            or not _is_entry_role(order.role)
            or self.current_order_id != source_order_id
            or _canonical_order_status(order.status) in _TERMINAL_ORDER_STATUSES
            or self.real_trade_ids
        ):
            raise TrialRunExecutionError("invalid_trial_transition")
        self.simulation_state = "cancel_pending"
        return order

    @_synchronized
    def cancel_simulation_request(self, source_order_id: str) -> TrialOrderAttempt:
        """Restore real-track state when the broker cancel request was not sent."""
        order = self._owned_order(source_order_id)
        if order.track is not TrialRunTrack.REAL:
            raise TrialRunExecutionError("invalid_trial_transition")
        self.current_track = TrialRunTrack.REAL
        self.simulation_state = "real_track"
        return order

    @_synchronized
    def record_simulated_submission(
        self,
        order_id: str,
        role: str,
        price: float,
        direction: str,
        offset: str,
        *,
        source_order_id: str = "",
        attempt: int = 0,
        parent_order_id: str = "",
        symbol: Optional[str] = None,
        volume: int = 1,
    ) -> TrialOrderAttempt:
        """Register a pending synthetic entry or close before it can fill."""
        self._prepare_mutation(symbol, volume)
        normalized_role = _normalized_role(role)
        if not (_is_entry_role(normalized_role) or _is_exit_role(normalized_role)):
            raise TrialRunExecutionError("invalid_trial_transition")
        if attempt < 0 or attempt > 5:
            raise TrialRunExecutionError("invalid_trial_transition")

        parent = None
        if _is_entry_role(normalized_role):
            source = self._owned_order(source_order_id)
            if (
                source.track is not TrialRunTrack.REAL
                or not _is_entry_role(source.role)
                or _canonical_order_status(source.status) != "cancelled"
                or self.real_trade_ids
                or self.simulated_entry_trade_id
                or self.simulated_position_volume != 0
            ):
                raise TrialRunExecutionError("invalid_trial_transition")
            if attempt == 0:
                if self.simulated_entry_order_id or parent_order_id:
                    raise TrialRunExecutionError("invalid_trial_transition")
                parent_order_id = source.order_id
            else:
                parent = self._find_order(parent_order_id)
                if (
                    parent is None
                    or parent.track is not TrialRunTrack.SIMULATED
                    or not _is_entry_role(parent.role)
                    or _canonical_order_status(parent.status) != "cancelled"
                    or parent.order_id != self.entry_order_id
                    or attempt != parent.attempt + 1
                ):
                    raise TrialRunExecutionError("invalid_trial_transition")
        else:
            if not self.simulated_entry_trade_id or self.simulated_position_volume != 1:
                raise TrialRunExecutionError("invalid_trial_transition")
            if attempt == 0:
                if self.simulated_close_order_id or parent_order_id:
                    raise TrialRunExecutionError("invalid_trial_transition")
                parent_order_id = self.simulated_entry_order_id
            else:
                parent = self._find_order(parent_order_id)
                if (
                    parent is None
                    or parent.track is not TrialRunTrack.SIMULATED
                    or not _is_exit_role(parent.role)
                    or _canonical_order_status(parent.status) != "cancelled"
                    or parent.order_id != self.close_order_id
                    or attempt != parent.attempt + 1
                ):
                    raise TrialRunExecutionError("invalid_trial_transition")

        if parent is not None and (
            _normalized_enum_value(direction) != _normalized_enum_value(parent.direction)
            or _normalized_enum_value(offset) != _normalized_enum_value(parent.offset)
        ):
            raise TrialRunExecutionError("invalid_trial_transition")

        order = self._append_order(
            order_id=order_id,
            role=normalized_role,
            track=TrialRunTrack.SIMULATED,
            attempt=attempt,
            parent_order_id=parent_order_id,
            symbol=self.symbol if symbol is None else str(symbol).strip(),
            direction=direction,
            offset=offset,
            price=price,
            status="submitted",
        )
        if _is_entry_role(order.role):
            if not self.simulated_entry_order_id:
                self.simulated_entry_order_id = order.order_id
            self.entry_order_id = order.order_id
        else:
            if not self.simulated_close_order_id:
                self.simulated_close_order_id = order.order_id
            self.close_order_id = order.order_id
        self.current_order_id = order.order_id
        self.current_track = TrialRunTrack.SIMULATED
        self.simulation_state = "ready" if _is_entry_role(order.role) else "closing"
        return order

    @_synchronized
    def record_simulated_order_update(
        self,
        order_id: str,
        status: str,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: Optional[str] = None,
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        order = self._owned_order(order_id)
        if order.track is not TrialRunTrack.SIMULATED:
            raise TrialRunExecutionError("invalid_trial_transition")
        status_value = _canonical_order_status(status)
        if status_value not in {"submitted", "cancelled", "filled"}:
            raise TrialRunExecutionError("invalid_trial_transition")
        if (
            direction is not None
            and _normalized_enum_value(direction) != _normalized_enum_value(order.direction)
        ):
            raise TrialRunExecutionError("invalid_trial_transition")
        old_status = _canonical_order_status(order.status)
        if old_status in _TERMINAL_ORDER_STATUSES and status_value != old_status:
            raise TrialRunExecutionError("invalid_trial_transition")
        updated = replace(order, status=status_value, updated_at=_now())
        self.order_chain[self.order_chain.index(order)] = updated
        if status_value in _TERMINAL_ORDER_STATUSES and self.current_order_id == order_id:
            self.current_order_id = ""
        if status_value == "cancelled":
            self.simulation_state = "cancelled"
        return updated

    @_synchronized
    def record_simulated_entry(
        self,
        order_id: str,
        trade_id: str,
        price: float,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: str = "long",
        offset: str = "open",
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        if self.simulated_entry_trade_id or self.simulated_position_volume != 0:
            raise TrialRunExecutionError("invalid_trial_transition")
        order = self._find_order(order_id)
        if order is None:
            order = self._append_order(
                order_id=order_id,
                role="entry",
                track=TrialRunTrack.SIMULATED,
                attempt=0,
                parent_order_id="",
                symbol=self.symbol if symbol is None else str(symbol).strip(),
                direction=direction,
                offset=offset,
                price=price,
                status="filled",
            )
        else:
            if (
                order.track is not TrialRunTrack.SIMULATED
                or not _is_entry_role(order.role)
                or _canonical_order_status(order.status) != "submitted"
                or _normalized_enum_value(direction) != _normalized_enum_value(order.direction)
                or _normalized_enum_value(offset) != _normalized_enum_value(order.offset)
            ):
                raise TrialRunExecutionError("invalid_trial_transition")
            existing = order
            order = replace(existing, status="filled", updated_at=_now())
            self.order_chain[self.order_chain.index(existing)] = order
        self.simulated_entry_order_id = order_id
        self.entry_order_id = order_id
        self.simulated_entry_trade_id = str(trade_id)
        self.simulated_trade_ids.append(str(trade_id))
        self.simulated_position_volume = 1
        self.current_order_id = ""
        self.current_track = TrialRunTrack.SIMULATED
        self.simulation_state = "holding"
        return order

    @_synchronized
    def record_simulated_close(
        self,
        order_id: str,
        trade_id: str,
        price: float,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: str = "short",
        offset: str = "close",
    ) -> TrialOrderAttempt:
        self._prepare_mutation(symbol, volume)
        if not self.simulated_entry_trade_id or self.simulated_position_volume != 1:
            raise TrialRunExecutionError("invalid_trial_transition")
        if self.simulated_close_trade_id or trade_id in self.simulated_trade_ids:
            raise TrialRunExecutionError("invalid_trial_transition")
        order = self._find_order(order_id)
        if order is None:
            order = self._append_order(
                order_id=order_id,
                role="close",
                track=TrialRunTrack.SIMULATED,
                attempt=0,
                parent_order_id=self.simulated_entry_order_id,
                symbol=self.symbol if symbol is None else str(symbol).strip(),
                direction=direction,
                offset=offset,
                price=price,
                status="filled",
            )
        else:
            if (
                order.track is not TrialRunTrack.SIMULATED
                or not _is_exit_role(order.role)
                or _canonical_order_status(order.status) != "submitted"
                or _normalized_enum_value(direction) != _normalized_enum_value(order.direction)
                or _normalized_enum_value(offset) != _normalized_enum_value(order.offset)
            ):
                raise TrialRunExecutionError("invalid_trial_transition")
            existing = self._owned_order(order_id)
            order = replace(existing, status="filled", updated_at=_now())
            self.order_chain[self.order_chain.index(existing)] = order
        self.simulated_close_order_id = order_id
        self.close_order_id = order_id
        self.simulated_close_trade_id = str(trade_id)
        self.simulated_trade_ids.append(str(trade_id))
        self.simulated_position_volume = 0
        self.current_order_id = ""
        self.current_track = TrialRunTrack.SIMULATED
        self.simulation_state = "flat"
        return order

    @_synchronized
    def record_late_real_fill_conflict(
        self,
        order_id: str,
        trade_id: str,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: Optional[str] = None,
    ) -> TrialOrderAttempt:
        """Invalidate a simulated run when canceled real liquidity arrives late."""
        self._validate_contract(symbol, volume)
        order = self._owned_order(order_id)
        trade_key = str(trade_id or "")
        if order.track is not TrialRunTrack.REAL or not trade_key:
            raise TrialRunExecutionError("invalid_trial_transition")
        if (
            direction is not None
            and _normalized_enum_value(direction) != _normalized_enum_value(order.direction)
        ):
            raise TrialRunExecutionError("invalid_trial_transition")
        if self.final_outcome not in {
            TrialRunOutcome.RUNNING,
            TrialRunOutcome.PASSED_SIMULATED,
            TrialRunOutcome.FAILED,
        }:
            raise TrialRunExecutionError("invalid_trial_transition")
        updated = replace(order, status="filled", updated_at=_now())
        self.order_chain[self.order_chain.index(order)] = updated
        if trade_key not in self.real_trade_ids:
            self.real_trade_ids.append(trade_key)
            self.real_trade_order_ids[trade_key] = order.order_id
            if _is_entry_role(order.role):
                self.real_entry_trade_id = trade_key
            elif _is_exit_role(order.role):
                self.real_close_trade_id = trade_key
        self.failure_code = "late_real_fill_conflict"
        self.success_basis = f"order_id={order_id}"
        self.final_outcome = TrialRunOutcome.FAILED
        self.simulation_state = "failed"
        return updated

    @_synchronized
    def require_current_order(
        self,
        order_id: str,
        *,
        symbol: Optional[str] = None,
        volume: int = 1,
        direction: Optional[str] = None,
    ) -> TrialOrderAttempt:
        self._validate_contract(symbol, volume)
        order = self._owned_order(order_id)
        if self.current_order_id != order_id:
            raise TrialRunExecutionError("order_not_current")
        if direction is not None and _enum_value(direction) != order.direction:
            raise TrialRunExecutionError("invalid_trial_transition")
        return order

    @_synchronized
    def evaluate(
        self,
        broker_position_volume: int,
        broker_active_order_ids: Iterable[str],
        reconcile_ok: bool,
    ) -> "TrialRunExecutionState":
        active_ids = [str(order_id) for order_id in broker_active_order_ids]
        self.broker_position_volume = int(broker_position_volume)
        self.broker_active_order_ids = active_ids
        self.reconcile_ok = bool(reconcile_ok)
        self.broker_reconcile_snapshot = {
            "position_volume": self.broker_position_volume,
            "active_order_ids": list(active_ids),
            "reconcile_ok": self.reconcile_ok,
        }
        if self.final_outcome is not TrialRunOutcome.RUNNING:
            return self

        broker_clean = (
            self.broker_position_volume == 0
            and not self.broker_active_order_ids
            and self.reconcile_ok is True
        )
        if (
            broker_clean
            and self._has_real_trade_proof("entry", self.real_entry_trade_id)
            and self._has_real_trade_proof("close", self.real_close_trade_id)
        ):
            self.final_outcome = TrialRunOutcome.PASSED_REAL
            self.success_basis = "real_entry_and_close_trade_proof"
        elif (
            broker_clean
            and self.real_submission_proof
            and not self.real_trade_ids
            and self._all_real_orders_cancelled()
            and self.simulated_entry_trade_id
            and self.simulated_close_trade_id
            and self.simulated_position_volume == 0
        ):
            self.final_outcome = TrialRunOutcome.PASSED_SIMULATED
            self.success_basis = "real_submission_and_simulated_entry_close_proof"
        return self

    def _has_real_trade_proof(self, role: str, trade_id: str) -> bool:
        order_id = self.real_trade_order_ids.get(trade_id)
        order = self._find_order(order_id) if order_id else None
        return bool(
            order
            and order.track is TrialRunTrack.REAL
            and (
                (_is_entry_role(order.role) and role == "entry")
                or (_is_exit_role(order.role) and role in {"close", "exit"})
            )
            and order.status == "filled"
        )

    def _all_real_orders_cancelled(self) -> bool:
        real_orders = [
            order for order in self.order_chain if order.track is TrialRunTrack.REAL
        ]
        return bool(real_orders) and all(
            order.status in {"cancelled", "canceled"} for order in real_orders
        )

    @_synchronized
    def mark_failed(self, code: str, basis: str = "") -> "TrialRunExecutionState":
        self._ensure_mutable()
        if not code:
            raise TrialRunExecutionError("invalid_trial_transition")
        self.failure_code = str(code)
        self.success_basis = str(basis)
        self.final_outcome = TrialRunOutcome.FAILED
        return self

    @_synchronized
    def mark_aborted(self, code: str) -> "TrialRunExecutionState":
        self._ensure_mutable()
        if not code:
            raise TrialRunExecutionError("invalid_trial_transition")
        self.failure_code = str(code)
        self.final_outcome = TrialRunOutcome.ABORTED
        return self

    @_synchronized
    def serialize(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["final_outcome"] = self.final_outcome.value
        payload["current_track"] = (
            self.current_track.value if self.current_track is not None else None
        )
        payload["simulation_track"] = self.simulation_track.value
        payload["order_chain"] = [
            {
                **asdict(order),
                "track": order.track.value,
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat(),
            }
            for order in self.order_chain
        ]
        return payload

    def to_dict(self) -> dict[str, Any]:
        return self.serialize()

    def to_json(self) -> str:
        return json.dumps(self.serialize(), sort_keys=True)

    @classmethod
    def deserialize(
        cls, payload: Union[str, Mapping[str, Any]]
    ) -> "TrialRunExecutionState":
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        orders = []
        for raw_order in data.pop("order_chain", []):
            order = dict(raw_order)
            order["track"] = TrialRunTrack(order["track"])
            order["created_at"] = datetime.fromisoformat(order["created_at"])
            order["updated_at"] = datetime.fromisoformat(order["updated_at"])
            orders.append(TrialOrderAttempt(**order))
        data["order_chain"] = orders
        data["final_outcome"] = TrialRunOutcome(data.get("final_outcome", "running"))
        if data.get("current_track") is not None:
            data["current_track"] = TrialRunTrack(data["current_track"])
        data["simulation_track"] = TrialRunTrack(
            data.get("simulation_track", TrialRunTrack.SIMULATED.value)
        )
        return cls(**data)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrialRunExecutionState":
        return cls.deserialize(payload)
