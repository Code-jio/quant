"""Persistence contracts for live-only risk state."""

from datetime import datetime
from importlib import import_module

import pytest

from src.strategy import Direction, OffsetFlag, OrderType, Signal
from src.trading.engine import TradingEngine
from src.trading.risk import RiskManager
from src.trading.types import AccountInfo
from src.trading.vnpy_gateway import VnpyGateway


class FixedClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def _signal():
    return Signal(
        symbol="rb2505",
        datetime=datetime.now(),
        direction=Direction.LONG,
        price=100.0,
        volume=1,
        order_type=OrderType.LIMIT,
        offset=OffsetFlag.OPEN,
    )


def _store(tmp_path):
    try:
        module = import_module("src.trading.risk_state_store")
    except ModuleNotFoundError:
        pytest.fail("LiveRiskStateStore must provide durable live risk state")
    LiveRiskStateStore = getattr(module, "LiveRiskStateStore", None)
    assert LiveRiskStateStore is not None, "LiveRiskStateStore must be exported by risk_state_store"

    return LiveRiskStateStore(tmp_path / "live-risk-state.json")


def _manager(clock):
    return RiskManager(
        {
            "duplicate_signal_window_seconds": 60,
            "duplicate_cancel_window_seconds": 60,
            "max_orders_per_minute": 10,
        },
        monotonic_clock=clock,
    )


def test_bound_live_risk_state_restores_same_scope_and_broker_trading_day(tmp_path):
    clock = FixedClock()
    store = _store(tmp_path)
    first = _manager(clock)
    first.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-12")
    first.set_day_open_balance(1_000_000)
    first.set_emergency_stop(True, "operator halt")
    first.record_order(_signal())
    assert first.check_cancel_request("ORDER-1").allowed
    first.record_cancel("ORDER-1", accepted=True)

    restored = _manager(clock)
    restored.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-12")
    status = restored.status()

    assert status["day_open_balance"] == 1_000_000
    assert status["emergency_stop"] is True
    assert status["emergency_reason"] == "operator halt"
    assert status["compliance"]["counters"]["orders_submitted"] == 1
    assert status["compliance"]["counters"]["cancels_accepted"] == 1
    assert restored.order_rate_snapshot()["used"] == 1
    restored.set_emergency_stop(False)
    assert restored.check_signal(_signal(), positions={}, market_data={"last_price": 100.0}).allowed is False
    assert restored.check_cancel_request("ORDER-1").allowed is False


def test_new_broker_trading_day_resets_intraday_state_but_keeps_manual_halt(tmp_path):
    clock = FixedClock()
    store = _store(tmp_path)
    manager = _manager(clock)
    manager.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-12")
    manager.set_day_open_balance(1_000_000)
    manager.set_emergency_stop(True, "operator halt")
    manager.record_order(_signal())
    manager.check_cancel_request("ORDER-1")
    manager.record_cancel("ORDER-1", accepted=True)

    manager.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-13")
    manager.set_day_open_balance(1_200_000)
    status = manager.status()

    assert status["day_open_balance"] == 1_200_000
    assert status["emergency_stop"] is True
    assert status["emergency_reason"] == "operator halt"
    assert status["compliance"]["counters"]["orders_submitted"] == 0
    assert status["compliance"]["counters"]["cancels_accepted"] == 0
    assert manager.order_rate_snapshot()["used"] == 0
    manager.set_emergency_stop(False)
    assert manager.check_signal(_signal(), positions={}, market_data={"last_price": 100.0}).allowed is True
    assert manager.check_cancel_request("ORDER-1").allowed is True


def test_corrupt_live_risk_state_fails_closed_with_explicit_error(tmp_path):
    state_path = tmp_path / "live-risk-state.json"
    state_path.write_text("not valid json", encoding="utf-8")
    manager = _manager(FixedClock())
    store = _store(tmp_path)

    with pytest.raises(RuntimeError, match="risk state"):
        manager.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-12")


def test_unbound_risk_manager_keeps_existing_in_memory_behavior():
    manager = _manager(FixedClock())
    manager.record_order(_signal())

    assert manager.status()["compliance"]["counters"]["orders_submitted"] == 1
    assert manager.check_signal(_signal(), positions={}, market_data={"last_price": 100.0}).allowed is False


def test_runtime_persistence_failure_halts_future_orders_without_masking_recorded_order():
    class FailingAfterBindStore:
        def __init__(self):
            self.saves = 0

        def load(self, _scope):
            return None

        def save(self, _scope, _trading_day, _state):
            self.saves += 1
            if self.saves > 1:
                raise RuntimeError("risk state disk unavailable")

    manager = _manager(FixedClock())
    manager.bind_persistent_state(
        FailingAfterBindStore(),
        scope="anonymous",
        trading_day="2026-08-12",
    )

    manager.record_order(_signal())
    status = manager.status()

    assert status["compliance"]["counters"]["orders_submitted"] == 1
    assert status["emergency_stop"] is True
    assert "persistence" in status["emergency_reason"].lower()


def test_live_engine_binds_anonymous_persistent_state_and_restores_it_after_restart(tmp_path):
    state_path = tmp_path / "live-risk-state.json"
    config = {
        "broker_id": "9999",
        "initial_capital": 1_000_000,
        "live_risk_state_path": str(state_path),
    }
    gateway = VnpyGateway()
    gateway.trading_day = "2026-08-12"
    gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_000_000)
    engine = TradingEngine(gateway)

    engine.configure_risk(config)
    engine.risk_manager.set_emergency_stop(True, "operator halt")
    engine.risk_manager.record_order(_signal())

    assert state_path.exists()
    persisted = state_path.read_text(encoding="utf-8")
    assert "LIVE-ACCOUNT-SECRET" not in persisted
    assert '"9999"' not in persisted

    restarted_gateway = VnpyGateway()
    restarted_gateway.trading_day = "2026-08-12"
    restarted_gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_000_000)
    restarted = TradingEngine(restarted_gateway)
    restarted.configure_risk(config)
    restored = restarted.risk_manager.status()

    assert restored["emergency_stop"] is True
    assert restored["emergency_reason"] == "operator halt"
    assert restored["compliance"]["counters"]["orders_submitted"] == 1


def test_gateway_trading_day_callback_rebinds_engine_risk_and_waits_for_new_day_account_balance(tmp_path):
    gateway = VnpyGateway()
    gateway.trading_day = "2026-08-12"
    gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_000_000)
    engine = TradingEngine(gateway)
    engine.configure_risk({
        "broker_id": "9999",
        "initial_capital": 1_000_000,
        "live_risk_state_path": str(tmp_path / "live-risk-state.json"),
    })
    engine.risk_manager.set_emergency_stop(True, "operator halt")
    engine.risk_manager.record_order(_signal())

    gateway._set_trading_day("20260813")
    new_day = engine.risk_manager.status()

    assert new_day["compliance"]["trading_day"] == "2026-08-13"
    assert new_day["compliance"]["counters"]["orders_submitted"] == 0
    assert new_day["emergency_stop"] is True
    assert new_day["day_open_balance"] == 0

    gateway.on_account(AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_200_000))
    assert engine.risk_manager.status()["day_open_balance"] == 1_200_000
