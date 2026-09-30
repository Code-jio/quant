"""Fail-closed contracts for durable live risk state."""

from datetime import datetime

import pytest

from src.strategy import Direction, OffsetFlag, OrderType, Signal
from src.trading.risk import RiskManager


class ToggleStore:
    """Minimal state store whose write health can be changed by a test."""

    def __init__(self) -> None:
        self.healthy = True
        self.saved = []

    def save(self, scope, trading_day, state) -> None:
        if not self.healthy:
            raise RuntimeError("disk unavailable")
        self.saved.append((scope, trading_day, dict(state)))


def _signal() -> Signal:
    return Signal(
        symbol="rb2505",
        datetime=datetime.now(),
        direction=Direction.LONG,
        price=100.0,
        volume=1,
        order_type=OrderType.LIMIT,
        offset=OffsetFlag.OPEN,
    )


def _bound_manager(store: ToggleStore, *, enabled: bool = True) -> RiskManager:
    manager = RiskManager({"enabled": enabled})
    manager._state_store = store
    manager._state_scope = "live-account"
    manager._bound_trading_day = "2026-08-14"
    return manager


def test_disabled_risk_config_still_blocks_an_emergency_stop():
    manager = RiskManager({"enabled": False})
    manager.set_emergency_stop(True, "operator halt")

    result = manager.check_signal(_signal())

    assert result.allowed is False
    assert "Emergency stop" in result.reason


def test_persistence_failure_cannot_be_cleared_by_an_ordinary_resume():
    store = ToggleStore()
    manager = _bound_manager(store, enabled=False)
    store.healthy = False

    manager.record_order(_signal())

    assert manager.status()["persistence_recovery_required"] is True
    assert manager.check_signal(_signal()).allowed is False
    with pytest.raises(RuntimeError, match="probe_persistence_recovery"):
        manager.set_emergency_stop(False)


def test_resume_requires_a_successful_persistence_recovery_probe():
    store = ToggleStore()
    manager = _bound_manager(store)
    store.healthy = False
    manager.record_order(_signal())

    assert manager.probe_persistence_recovery() is False
    with pytest.raises(RuntimeError, match="probe_persistence_recovery"):
        manager.set_emergency_stop(False)

    store.healthy = True
    assert manager.probe_persistence_recovery() is True
    assert manager.status()["persistence_error"] == ""
    assert manager.status()["emergency_stop"] is True

    manager.set_emergency_stop(False)
    assert manager.check_signal(_signal()).allowed is True


def test_operator_emergency_stop_can_resume_without_recovery_probe():
    manager = RiskManager()
    manager.set_emergency_stop(True, "operator halt")

    manager.set_emergency_stop(False)

    assert manager.check_signal(_signal()).allowed is True
