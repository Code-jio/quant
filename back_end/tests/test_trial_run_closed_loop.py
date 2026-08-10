import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.api.security import SESSION_COOKIE_NAME, session_store
from src.api.trial_run import trial_run_state
from src.strategy import Direction, OrderStatus, Position, Trade
from src.trading.trial_run_execution import TrialRunExecutionState, TrialRunOutcome
from src.trading.trial_run_store import TrialRunCheckpointStore
from src.trading.types import MarketData

from tests.test_trial_run_api import _config_path, _trial_config, install_gateway, login


def _tick(price, timestamp):
    return MarketData(
        symbol="rb2510",
        last_price=price,
        bid_price_1=price - 1,
        ask_price_1=price + 1,
        bid_volume_1=10,
        ask_volume_1=10,
        volume=100,
        turnover=price * 100,
        timestamp=timestamp,
    )


def _configure(path, *, simulate=True, hold_bars=1):
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = simulate
    payload["trial_run"]["max_hold_seconds"] = 75
    payload["strategy"]["hold_bars"] = hold_bars
    path.write_text(json.dumps(payload), encoding="utf-8")


def teardown_function():
    trading_state.clear_main()
    trial_run_state.reset()


def test_simulated_closed_loop_reaches_passed_simulated_without_gateway_position(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "closed-loop"), hold_bars=1)
    _configure(config_path, simulate=True, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()
    start = datetime.now()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, start))
        source = gateway.orders["ORDER_1"]
        source.status = OrderStatus.SUBMITTED
        entry.engine._on_order(source)
        entry.strategy._order_ownership["ORDER_1"]["submitted_monotonic"] -= 3

        assert client.post(
            "/trial-run/simulation/prepare",
            json={"source_order_id": "ORDER_1"},
        ).status_code == 200
        gateway.on_order(source)
        ready = client.post(
            "/trial-run/simulation/prepare",
            json={"source_order_id": "ORDER_1"},
        )
        assert ready.status_code == 200
        synthetic_entry_id = ready.json()["status"]["current_order_id"]
        assert client.post(
            "/trial-run/simulate-fill",
            json={"order_id": synthetic_entry_id},
        ).status_code == 200

        entry.engine.on_tick(_tick(3131, start + timedelta(minutes=1)))
        synthetic_close_id = entry.engine.trial_run_execution.current_order_id
        assert synthetic_close_id.startswith("SIM-C-")
        assert gateway.positions == {}
        assert client.post(
            "/trial-run/simulate-fill",
            json={"order_id": synthetic_close_id},
        ).status_code == 200

        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "passed_simulated"
    assert status["simulated_position_volume"] == 0
    assert gateway.positions == {}


def test_real_closed_loop_reaches_passed_real_after_flat_reconciliation(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "real-closed-loop"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()
    start = datetime.now()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, start))
        open_order = gateway.orders["ORDER_1"]
        open_order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(open_order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))

        entry.engine.on_tick(_tick(3131, start + timedelta(minutes=1)))
        close_order = gateway.orders["ORDER_2"]
        close_order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(close_order)
        gateway.on_position(Position("rb2510", Direction.LONG, 0, price=3131, cost=3131))
        gateway.on_trade(Trade("T-CLOSE", "ORDER_2", "rb2510", Direction.SHORT, 3131, 1))
        assert client.get("/trial-run/status").json()["outcome"] == "running"
        entry.engine.on_timer(entry.engine._monotonic())
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "passed_real"
    assert status["broker_position_volume"] == 0
    assert status["hold_deadline_at"]


def test_hold_deadline_fresh_tick_fallback_creates_close_and_stays_running(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "hold-deadline"), hold_bars=99)
    _configure(config_path, simulate=False, hold_bars=99)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()
    start = datetime.now()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, start))
        order = gateway.orders["ORDER_1"]
        order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))
        entry.engine._trial_max_hold_seconds = 1
        entry.engine._last_strategy_tick = _tick(3132, datetime.now())
        entry.engine._trial_hold_deadline_monotonic = entry.engine._monotonic() - 1
        entry.engine.on_timer(entry.engine._monotonic())
        assert "ORDER_2" in gateway.orders
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "running"
    assert status["failure_code"] == ""
    assert status["current_order_id"] == "ORDER_2"
    assert gateway.orders["ORDER_2"].status is not OrderStatus.REJECTED


@pytest.mark.parametrize("tick_mode", ["stale", "missing"])
def test_hold_deadline_without_fresh_tick_fails_closed(
    monkeypatch, tmp_path, tick_mode
):
    config_path = _trial_config(_config_path(tmp_path, f"hold-deadline-{tick_mode}"), hold_bars=99)
    _configure(config_path, simulate=False, hold_bars=99)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, datetime.now()))
        order = gateway.orders["ORDER_1"]
        order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))
        if tick_mode == "stale":
            entry.engine._last_strategy_tick = _tick(
                3132, datetime.now() - timedelta(seconds=30)
            )
        else:
            entry.engine._last_strategy_tick = None
        entry.engine._trial_hold_deadline_monotonic = entry.engine._monotonic() - 1
        entry.engine.on_timer(entry.engine._monotonic())
        status = client.get("/trial-run/status").json()

    assert "ORDER_2" not in gateway.orders
    assert status["failure_code"] == "flatten_required_market_data"
    assert status["broker_position_volume"] == 1


def test_reset_rejects_nonzero_real_position_and_preserves_trial_evidence(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "reset-holding"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, datetime.now()))
        order = gateway.orders["ORDER_1"]
        order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))

        response = client.post("/trial-run/reset")
        assert response.status_code == 409
        assert response.json()["detail"]["failure_code"] == "flatten_required"
        assert trading_state.get("verify_trial") is not None
        assert entry.engine.trial_run_execution is not None
        assert entry.engine._trial_run_stopping is False


def test_emergency_stop_and_manual_flatten_cannot_pass(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "manual-flatten"), hold_bars=99)
    _configure(config_path, simulate=False, hold_bars=99)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, datetime.now()))
        order = gateway.orders["ORDER_1"]
        order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))

        emergency = client.post(
            "/risk/emergency-stop",
            json={"reason": "operator stop", "cancel_orders": False},
        )
        assert emergency.status_code == 200
        assert client.post("/risk/resume").status_code == 200
        close = client.post(
            "/positions/rb2510/close",
            json={"volume": 1, "price": 3131, "order_type": "limit"},
        )
        assert close.status_code == 200
        manual_order = gateway.orders[close.json()["order_id"]]
        manual_order.status = OrderStatus.FILLED
        gateway.on_position(Position("rb2510", Direction.LONG, 0, price=3131, cost=3131))
        gateway.refresh_reconciliation()

        stopped = client.post("/trial-run/stop")
        assert stopped.status_code == 200
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "failed"
    assert status["success_basis"] == "manual_flatten_after_trial_failure"


def test_real_close_rejection_is_terminal_failure(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "close-rejected"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, datetime.now()))
        open_order = gateway.orders["ORDER_1"]
        open_order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(open_order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))
        entry.engine.on_tick(_tick(3131, datetime.now() + timedelta(minutes=1)))
        close_order = gateway.orders["ORDER_2"]
        close_order.status = OrderStatus.REJECTED
        entry.engine._on_order(close_order)
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "failed"
    assert status["failure_code"] == "real_close_rejected"


def test_real_close_submission_failure_is_terminal(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "close-submit-failed"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, datetime.now()))
        open_order = gateway.orders["ORDER_1"]
        open_order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(open_order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))
        monkeypatch.setattr(gateway, "send_order", lambda signal: "")

        entry.engine.on_tick(_tick(3131, datetime.now() + timedelta(minutes=1)))
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "failed"
    assert status["failure_code"] == "real_close_submission_failed"
    assert status["broker_position_volume"] == 1


def test_simulated_close_requires_current_id_and_broker_flatness(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "sim-close-guards"), hold_bars=1)
    _configure(config_path, simulate=True, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()
    start = datetime.now()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130, start))
        source = gateway.orders["ORDER_1"]
        source.status = OrderStatus.SUBMITTED
        entry.engine._on_order(source)
        entry.strategy._order_ownership["ORDER_1"]["submitted_monotonic"] -= 3
        assert client.post(
            "/trial-run/simulation/prepare", json={"source_order_id": "ORDER_1"}
        ).status_code == 200
        gateway.on_order(source)
        ready = client.post(
            "/trial-run/simulation/prepare", json={"source_order_id": "ORDER_1"}
        )
        synthetic_entry_id = ready.json()["status"]["current_order_id"]
        assert client.post(
            "/trial-run/simulate-fill", json={"order_id": "wrong-id"}
        ).status_code == 409
        assert client.post(
            "/trial-run/simulate-fill", json={"order_id": synthetic_entry_id}
        ).status_code == 200
        entry.engine.on_tick(_tick(3131, start + timedelta(minutes=1)))
        synthetic_close_id = entry.engine.trial_run_execution.current_order_id
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        assert client.post(
            "/trial-run/simulate-fill", json={"order_id": synthetic_close_id}
        ).status_code == 200
        status = client.get("/trial-run/status").json()

    assert status["outcome"] == "failed"
    assert status["failure_code"] == "broker_state_not_flat"
    assert status["broker_position_volume"] == 1


def test_repeated_trial_status_reads_are_read_only(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "status-read-only"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        execution = trading_state.get("verify_trial").engine.trial_run_execution
        before = execution.serialize()
        db_path = os.environ["QUANT_TRIAL_RUN_STATE_DB"]
        with sqlite3.connect(db_path) as connection:
            before_count = connection.execute(
                "SELECT COUNT(*) FROM trial_run_checkpoints"
            ).fetchone()[0]

        first = client.get("/trial-run/status")
        second = client.get("/trial-run/status")

        after = execution.serialize()
        with sqlite3.connect(db_path) as connection:
            after_count = connection.execute(
                "SELECT COUNT(*) FROM trial_run_checkpoints"
            ).fetchone()[0]

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["outcome"] == "running"
    assert second.json()["outcome"] == "running"
    assert after == before
    assert after_count == before_count


def test_logout_preserves_live_session_until_flatten_then_disconnects(
    monkeypatch, tmp_path
):
    config_path = _trial_config(_config_path(tmp_path, "logout-flatten"), hold_bars=99)
    _configure(config_path, simulate=False, hold_bars=99)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        engine = entry.engine
        execution = engine.trial_run_execution
        entry.engine.on_tick(_tick(3130, datetime.now()))
        order = gateway.orders["ORDER_1"]
        order.status = OrderStatus.SUBMITTED
        entry.engine._on_order(order)
        gateway.on_position(Position("rb2510", Direction.LONG, 1, price=3130, cost=3130))
        gateway.on_trade(Trade("T-ENTRY", "ORDER_1", "rb2510", Direction.LONG, 3130, 1))
        session_token = client.cookies.get(SESSION_COOKIE_NAME)

        blocked = client.post("/auth/logout")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["failure_code"] == "flatten_required"
        assert SESSION_COOKIE_NAME in client.cookies
        assert session_token and session_store.is_valid(session_token)
        assert trading_state.get("verify_trial") is entry
        assert trading_state.primary_engine() is engine
        assert engine.gateway is gateway
        assert engine._trial_run_stopping is False

        gateway.on_position(Position("rb2510", Direction.LONG, 0, price=3131, cost=3131))
        gateway.refresh_reconciliation()
        succeeded = client.post("/auth/logout")

    assert succeeded.status_code == 200
    assert execution.outcome is TrialRunOutcome.ABORTED
    assert execution.failure_code == "session_logout"
    assert not session_store.is_valid(session_token)
    assert trading_state.primary_engine() is None
    assert gateway.status.value == "stopped"


def test_restart_recovery_aborts_latest_and_new_prepare_checks_recovered_symbol(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "restart-shared.db"
    monkeypatch.setenv("QUANT_TRIAL_RUN_STATE_DB", str(db_path))
    config_path = _trial_config(_config_path(tmp_path, "restart-recovery"), hold_bars=1)
    _configure(config_path, simulate=False, hold_bars=1)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    recovered = TrialRunExecutionState(
        run_id="RUN-RECOVERED",
        symbol="au2606",
        volume=1,
    )

    first_app = create_app()
    with TestClient(first_app):
        TrialRunCheckpointStore(db_path).save(recovered)

    gateway.on_position(Position("au2606", Direction.LONG, 1, price=700, cost=700))
    second_app = create_app()
    with TestClient(second_app) as client:
        recovered_status = client.get("/trial-run/status").json()
        assert recovered_status["outcome"] == "aborted"
        assert recovered_status["failure_code"] == "backend_restarted"
        assert recovered_status["symbol"] == "au2606"

        login(client)
        blocked = client.post("/trial-run/prepare")

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["failure_code"] == "broker_position_not_flat"
