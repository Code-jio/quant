"""Persistence contracts for live-only risk state."""

from datetime import datetime
from importlib import import_module
from multiprocessing import get_context
from queue import Empty

import pytest

from src.strategy import Direction, OffsetFlag, OrderType, Signal
from src.trading.engine import TradingEngine
from src.trading.gateway import GatewayBase
from src.trading.risk import RiskManager
from src.trading.types import AccountInfo, TradingStatus
from src.trading.vnpy_gateway import VnpyGateway


class FixedClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class ConnectingLiveGateway(GatewayBase):
    """Live gateway fake that discovers broker identity only during connect."""

    requires_persistent_risk_state = True

    def __init__(self):
        super().__init__("VNPY_CTP")
        self.trading_day = ""
        self.on_trading_day_callback = None

    def connect(self, _config):
        self.status = TradingStatus.CONNECTED
        self.trading_day = "2026-08-12"
        if self.on_trading_day_callback:
            self.on_trading_day_callback(self.trading_day)
        self.on_account(AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=50_000))
        return True

    def disconnect(self):
        self.status = TradingStatus.STOPPED

    def send_order(self, _signal):
        return ""

    def cancel_order(self, _order_id):
        return False

    def query_account(self):
        return self.account

    def query_positions(self):
        return []

    def query_orders(self):
        return []


def _save_risk_state_in_child_process(path, started, result):
    module = import_module("src.trading.risk_state_store")
    store = module.LiveRiskStateStore(path)
    started.set()
    try:
        store.save("child", "2026-08-12", {"orders": 1})
    except Exception as exc:  # pragma: no cover - asserted through the process queue
        result.put(("error", type(exc).__name__, str(exc)))
    else:
        result.put(("ok",))


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
    first.close_persistent_state()

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


def test_live_risk_store_serializes_writers_across_processes(tmp_path):
    state_path = tmp_path / "live-risk-state.json"
    store = _store(tmp_path)
    store.save("parent", "2026-08-12", {"orders": 1})
    context = get_context("spawn")
    started = context.Event()
    result = context.Queue()

    with store._exclusive_file_lock():
        child = context.Process(
            target=_save_risk_state_in_child_process,
            args=(state_path, started, result),
        )
        child.start()
        assert started.wait(timeout=10)
        with pytest.raises(Empty):
            result.get(timeout=0.2)

    assert result.get(timeout=10) == ("ok",)
    child.join(timeout=10)
    assert child.exitcode == 0
    assert store.load("parent")["state"]["orders"] == 1
    assert store.load("child")["state"]["orders"] == 1


def test_live_risk_scope_rejects_a_second_active_writer(tmp_path):
    first = _manager(FixedClock())
    second = _manager(FixedClock())
    first.bind_persistent_state(
        _store(tmp_path),
        scope="anonymous",
        trading_day="2026-08-12",
    )

    with pytest.raises(RuntimeError, match="active live risk writer"):
        second.bind_persistent_state(
            _store(tmp_path),
            scope="anonymous",
            trading_day="2026-08-12",
        )


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


def test_malformed_live_risk_payload_fails_closed_with_explicit_error(tmp_path):
    state_path = tmp_path / "live-risk-state.json"
    state_path.write_text(
        '{"version":1,"scopes":{"anonymous":{"trading_day":"2026-08-12",'
        '"state":{"day_open_balance":"not-a-number"}}}}',
        encoding="utf-8",
    )
    manager = _manager(FixedClock())
    store = _store(tmp_path)

    with pytest.raises(RuntimeError, match="risk state"):
        manager.bind_persistent_state(store, scope="anonymous", trading_day="2026-08-12")


def test_unbound_risk_manager_keeps_existing_in_memory_behavior():
    manager = _manager(FixedClock())
    manager.record_order(_signal())

    assert manager.status()["compliance"]["counters"]["orders_submitted"] == 1
    assert manager.check_signal(_signal(), positions={}, market_data={"last_price": 100.0}).allowed is False


def test_runtime_persistence_failure_halts_future_orders_without_masking_recorded_order(tmp_path):
    store = _store(tmp_path)
    original_save = store.save
    saves = 0

    def fail_after_bind(scope, trading_day, state):
        nonlocal saves
        saves += 1
        if saves > 1:
            raise RuntimeError("risk state disk unavailable")
        original_save(scope, trading_day, state)

    store.save = fail_after_bind
    manager = _manager(FixedClock())
    manager.bind_persistent_state(
        store,
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
    engine.risk_manager.close_persistent_state()

    restarted_gateway = VnpyGateway()
    restarted_gateway.trading_day = "2026-08-12"
    restarted_gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_000_000)
    restarted = TradingEngine(restarted_gateway)
    restarted.configure_risk(config)
    restored = restarted.risk_manager.status()

    assert restored["emergency_stop"] is True
    assert restored["emergency_reason"] == "operator halt"
    assert restored["compliance"]["counters"]["orders_submitted"] == 1


def test_live_engine_start_connects_before_binding_persistent_risk_state(tmp_path):
    gateway = ConnectingLiveGateway()
    engine = TradingEngine(gateway)
    state_path = tmp_path / "live-risk-state.json"

    try:
        assert engine.start({
            "broker_id": "9999",
            "initial_capital": 1_000_000,
            "live_risk_state_path": str(state_path),
        }) is True
        assert state_path.exists()
        assert engine.risk_manager.status()["emergency_stop"] is False
        assert engine.risk_manager.status()["day_open_balance"] == 50_000
    finally:
        engine.stop()


def test_first_live_risk_binding_uses_broker_balance_not_static_initial_capital(tmp_path):
    gateway = VnpyGateway()
    gateway.trading_day = "2026-08-12"
    gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=50_000)
    engine = TradingEngine(gateway)

    engine.configure_risk({
        "broker_id": "9999",
        "initial_capital": 1_000_000,
        "live_risk_state_path": str(tmp_path / "live-risk-state.json"),
    })

    assert engine.risk_manager.status()["day_open_balance"] == 50_000


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


def test_runtime_risk_config_update_keeps_existing_live_persistence_binding(tmp_path):
    gateway = VnpyGateway()
    gateway.trading_day = "2026-08-12"
    gateway.account = AccountInfo(account_id="LIVE-ACCOUNT-SECRET", balance=1_000_000)
    engine = TradingEngine(gateway)
    engine.configure_risk({
        "broker_id": "9999",
        "initial_capital": 1_000_000,
        "live_risk_state_path": str(tmp_path / "live-risk-state.json"),
    })
    engine.risk_manager.record_order(_signal())

    engine.configure_risk({"risk": {"max_order_volume": 3}})
    status = engine.risk_manager.status()

    assert status["max_order_volume"] == 3
    assert status["emergency_stop"] is False
    assert status["persistence_error"] == ""
    assert status["compliance"]["counters"]["orders_submitted"] == 1
