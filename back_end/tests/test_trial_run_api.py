import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
import src.api.trial_run as trial_run_module
from src.api.trial_run import trial_run_state
from src.strategy import Direction, OffsetFlag, Order, OrderStatus, OrderType, Position
from src.trading.types import MarketData

from tests.helpers import RecordingGateway


def _config_path(root, name):
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}-{uuid.uuid4().hex}.json"


def _trial_config(
    path,
    password="secret-password",
    *,
    auto_arm=True,
    warmup_bars=1,
    readiness_bars=1,
    hold_bars=3,
    bar_timeout_seconds=90,
    no_fill_timeout_seconds=None,
    chase_max_attempts=None,
):
    payload = {
        "trial_run": {
            "enabled": True,
            "allowed_symbol": "rb2510",
            "account_id": "trial-account",
            "auto_arm": auto_arm,
            "bar_timeout_seconds": bar_timeout_seconds,
            "vnpy_environment": "测试",
        },
        "strategy": {
            "name": "verify",
            "symbol": "rb2510",
            "volume": 1,
            "warmup_bars": warmup_bars,
            "readiness_bars": readiness_bars,
            "hold_bars": hold_bars,
            "order_type": "limit",
        },
        "trading": {
            "gateway": "vnpy",
            "username": "trial-account",
            "password": password,
            "broker_id": "2071",
            "td_server": "tcp://td.example:123",
            "md_server": "tcp://md.example:123",
            "app_id": "trial-app",
            "auth_code": "trial-auth-code",
            "vnpy_environment": "测试",
        },
        "risk": {
            "enabled": True,
            "allowed_symbols": ["rb2510"],
            "max_order_volume": 1,
            "max_position_volume": 1,
            "max_orders_per_minute": 5,
            "max_active_orders": 2,
            "max_market_data_age_seconds": 5,
            "allow_market_orders": True,
        },
    }
    if no_fill_timeout_seconds is not None:
        payload["trial_run"]["no_fill_timeout_seconds"] = no_fill_timeout_seconds
    if chase_max_attempts is not None:
        payload["strategy"]["chase_max_attempts"] = chase_max_attempts
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bar(close, symbol="rb2510"):
    return pd.Series({
        "symbol": symbol,
        "datetime": pd.Timestamp.now(),
        "open": close - 2,
        "high": close + 2,
        "low": close - 3,
        "close": close,
        "volume": 100,
    })


def _tick(price, symbol="rb2510", timestamp=None):
    return MarketData(
        symbol=symbol,
        last_price=price,
        bid_price_1=price - 1,
        ask_price_1=price + 1,
        bid_volume_1=10,
        ask_volume_1=10,
        volume=100,
        turnover=price * 100,
        timestamp=timestamp or datetime.now(),
    )


class TrialGateway(RecordingGateway):
    def __init__(self):
        super().__init__()
        self.subscribed_symbols = []

    def subscribe_market_data(self, symbols):
        self.subscribed_symbols.append(list(symbols))


def install_gateway(monkeypatch):
    import src.trading

    gateway = TrialGateway()
    monkeypatch.setattr(src.trading, "create_gateway", lambda gateway_type="vnpy": gateway)
    return gateway


def login(client, *, environment="测试"):
    response = client.post(
        "/auth/login",
        json={
            "username": "test-account",
            "password": "test-password",
            "broker_id": "2071",
            "gateway_type": "vnpy",
            "environment": environment,
        },
    )
    assert response.status_code == 200


def teardown_function():
    trading_state.clear_main()
    trial_run_state.reset()


def test_trial_run_config_is_public_and_prefills_non_password_connection_fields(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "config"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["allowed_symbol"] == "rb2510"
    assert body["auto_arm"] is True
    assert body["bar_timeout_seconds"] == 90
    assert body["account_id"] == ""
    assert body["masked_account_id"] == "tr****nt"
    assert "password" not in body["config"]["trading"]
    assert body["trading"]["broker_id"] == "2071"
    assert body["trading"]["td_server"] == "tcp://td.example:123"
    assert body["trading"]["md_server"] == "tcp://md.example:123"
    assert body["trading"]["app_id"] == "trial-app"
    assert "auth_code" not in body["trading"]
    assert "trial-auth-code" not in response.text
    assert "secret-password" not in response.text
    assert "trial-account" not in response.text


def test_trial_run_config_exposes_fill_verification_boundaries(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "fill-boundaries"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["no_fill_timeout_seconds"] = 10
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["strategy"]["chase_max_attempts"] = 5
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["no_fill_timeout_seconds"] == 10
    assert body["simulate_fill_enabled"] is True
    assert body["config"]["trial_run"]["no_fill_timeout_seconds"] == 10
    assert body["config"]["trial_run"]["simulate_fill_enabled"] is True
    assert body["strategy"]["chase_max_attempts"] == 5


def test_trial_run_config_rejects_more_than_five_chase_replacements(monkeypatch, tmp_path):
    config_path = _trial_config(
        _config_path(tmp_path, "too-many-replacements"),
        chase_max_attempts=6,
    )
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "strategy.chase_max_attempts 必须在 0 到 5 之间" in body["validation_errors"]


def test_trial_run_config_requires_strategy_symbol(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "missing-symbol"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["strategy"].pop("symbol")
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "strategy.symbol 不能为空" in body["validation_errors"]


def test_trial_run_config_rejects_unknown_futures_product(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "unknown-product"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["allowed_symbol"] = "ZZ9999"
    payload["strategy"]["symbol"] = "ZZ9999"
    payload["risk"]["allowed_symbols"] = ["ZZ9999"]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert any("不是已知且交易所一致" in item for item in response.json()["validation_errors"])


def test_trial_run_config_requires_short_auto_arm_warmup(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "invalid-auto-arm"), warmup_bars=2)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "strategy.warmup_bars must be 1 when trial_run.auto_arm is true" in body["validation_errors"]


def test_trial_run_mutations_require_login(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "auth-required"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        for path in [
            "/trial-run/prepare",
            "/trial-run/start",
            "/trial-run/arm",
            "/trial-run/stop",
            "/trial-run/reset",
            "/trial-run/simulate-fill",
        ]:
            response = client.post(path)
            assert response.status_code == 401


def test_trial_run_prepare_auto_arms_and_sends_entry_after_first_bar(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "prepare"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        status = client.get("/trial-run/status")
        assert status.status_code == 200
        assert status.json()["state"] == "idle"

        login(client)

        prepared = client.post("/trial-run/prepare")
        assert prepared.status_code == 200
        assert prepared.json()["success"] is True
        assert prepared.json()["status"]["state"] == "waiting_market_data"
        assert prepared.json()["status"]["strategy_id"] == "verify_trial"
        assert prepared.json()["status"]["run_id"]
        assert prepared.json()["status"]["current_track"] == "real"
        assert prepared.json()["status"]["auto_arm"] is True
        assert prepared.json()["status"]["first_tick_bar_enabled"] is True
        assert gateway.subscribed_symbols == [["rb2510"]]
        assert gateway.sent_signals == []

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))

        status = client.get("/trial-run/status")
        assert status.status_code == 200
        assert status.json()["state"] == "entry_pending"
        assert status.json()["tick_count"] == 1
        assert status.json()["bar_count"] == 1
        assert status.json()["first_tick_bar_enabled"] is True
        assert status.json()["first_tick_bar_emitted"] is True
        assert status.json()["market_issue"] == ""
        assert len(entry.strategy.signals) == 1
        assert len(gateway.sent_signals) == 1
        assert status.json()["current_order_id"] == "ORDER_1"
        assert status.json()["order_chain"][0]["order_id"] == "ORDER_1"


def test_trial_run_wait_seconds_uses_exact_execution_current_order(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "exact-current-order"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))
        current = gateway.orders["ORDER_1"]
        current.create_time = datetime.now()
        external = Order(
            order_id="EXTERNAL",
            symbol="rb2510",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3130,
            volume=1,
            status=OrderStatus.SUBMITTED,
        )
        external.create_time = datetime.now() - timedelta(seconds=120)
        gateway.orders[external.order_id] = external

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    assert response.json()["current_order_id"] == "ORDER_1"
    assert response.json()["execution_issue"] != "waiting_counterparty"


def test_apply_simulated_fill_fills_trial_order_and_updates_strategy(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulated-fill"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))

        assert len(gateway.sent_signals) == 1
        order_id = "ORDER_1"

        from src.trading.simulated_fill import apply_simulated_fill

        result = apply_simulated_fill(entry.engine, order_id)

    assert result.order.status.value == "filled"
    assert result.order.traded_volume == result.order.volume
    assert result.trade.order_id == order_id
    assert result.trade.symbol == "rb2510"
    assert any(position.volume == 1 for position in gateway.positions.values())
    assert any(trade.order_id == order_id for trade in entry.strategy.trades)
    assert entry.strategy.get_position("rb2510").volume == 1


def test_trial_run_simulate_fill_is_fail_closed_during_ledger_migration(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulate-fill-api"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["trial_run"]["vnpy_environment"] = "测试"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        response = client.post("/trial-run/simulate-fill", json={"order_id": "ORDER_1"})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "failure_code": "simulation_migration_in_progress",
        "message": "隔离模拟账本尚未启用",
    }
    assert gateway.positions == {}


def test_trial_run_status_exposes_migration_and_execution_contract(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulate-fill-disabled"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "running"
    assert body["current_track"] == "real"
    assert body["order_chain"] == []
    assert body["simulation_state"] == "migration_in_progress"
    assert body["simulate_fill_allowed"] is False
    assert body["simulation_environment_allowed"] is True
    assert body["runtime_environment"] == "测试"


@pytest.mark.parametrize(
    ("config_environment", "runtime_environment"),
    [
        ("测试", "实盘"),
        ("实盘", "测试"),
        ("测试", "仿真"),
        ("仿真", "测试"),
    ],
)
def test_trial_run_simulation_environment_requires_both_sources_non_production(
    monkeypatch,
    tmp_path,
    config_environment,
    runtime_environment,
):
    config_path = _trial_config(_config_path(tmp_path, "simulate-fill-production"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["trial_run"]["vnpy_environment"] = config_environment
    payload["trading"]["vnpy_environment"] = config_environment
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client, environment=runtime_environment)
        response = client.get("/trial-run/status")

    assert response.status_code == 200
    assert response.json()["simulation_environment_allowed"] is False


def test_main_config_snapshot_is_runtime_authoritative_and_secret_free(monkeypatch):
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client, environment="测试")
        snapshot = trading_state.main_config_snapshot()

    assert snapshot == {
        "gateway": "vnpy",
        "environment": "测试",
        "td_server": "",
        "md_server": "",
    }
    assert "password" not in snapshot
    assert "auth_code" not in snapshot


def test_trial_run_prepare_rejects_another_registered_strategy(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "strategy-conflict"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        engine = trading_state.primary_engine()
        assert engine is not None
        trading_state.register("other_strategy", object(), engine, {})

        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "trial_run_engine_busy"


def test_trial_run_prepare_rejects_nonzero_broker_position(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "position-conflict"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        gateway.positions["rb2510.long"] = Position(
            symbol="rb2510.SHFE",
            direction=Direction.LONG,
            volume=1,
        )

        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "broker_position_not_flat"


def test_trial_run_prepare_rejects_active_broker_order(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "order-conflict"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        gateway.orders["EXISTING"] = Order(
            order_id="EXISTING",
            symbol="SHFE.rb2510",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3130,
            volume=1,
            status=OrderStatus.SUBMITTED,
            offset=OffsetFlag.OPEN,
        )

        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "broker_active_order_exists"


def test_trial_run_prepare_rejects_when_entry_rate_capacity_cannot_reserve_close_slot(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "rate-capacity"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        engine = trading_state.primary_engine()
        for _ in range(4):
            engine.risk_manager.record_order()

        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "rate_capacity_not_ready"
    assert response.json()["detail"]["retry_after_seconds"] > 0


def test_trial_run_prepare_rejects_unavailable_account(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "account-unavailable"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        gateway.account = None

        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "broker_account_unavailable"


def test_trial_run_prepare_rejects_production_environment(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "production-prepare"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["vnpy_environment"] = "实盘"
    payload["trading"]["vnpy_environment"] = "实盘"
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client, environment="实盘")
        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "trial_run_environment_not_allowed"
    assert gateway.sent_signals == []


def test_trial_run_prepare_requires_fresh_broker_snapshot(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "stale-broker-snapshot"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    gateway.refresh_reconciliation = lambda timeout_seconds=8.0: {
        "ok": False,
        "fresh": False,
        "failure_code": "broker_snapshot_timeout",
    }
    app = create_app()

    with TestClient(app) as client:
        login(client)
        response = client.post("/trial-run/prepare")

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "broker_snapshot_timeout"


def test_trial_run_prepare_rejects_concurrent_prepare(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "concurrent-prepare"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert trial_run_state.try_begin_prepare() is True
        try:
            response = client.post("/trial-run/prepare")
        finally:
            trial_run_state.end_prepare()

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "trial_run_prepare_in_progress"


@pytest.mark.parametrize("path", ["/trial-run/stop", "/trial-run/reset"])
def test_trial_run_lifecycle_mutations_cannot_overlap_prepare(monkeypatch, tmp_path, path):
    config_path = _trial_config(_config_path(tmp_path, "lifecycle-conflict"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert trial_run_state.try_begin_prepare() is True
        try:
            response = client.post(path)
        finally:
            trial_run_state.end_prepare()

    assert response.status_code == 409
    assert response.json()["detail"]["failure_code"] == "trial_run_prepare_in_progress"


@pytest.mark.parametrize("path", ["/trial-run/stop", "/trial-run/reset"])
def test_trial_run_lifecycle_waits_for_cancel_confirmation_and_flat_reconciliation(
    monkeypatch,
    tmp_path,
    path,
):
    config_path = _trial_config(_config_path(tmp_path, "lifecycle-flat"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))
        execution = entry.engine.trial_run_execution
        assert execution is not None
        assert execution.current_order_id == "ORDER_1"

        first = client.post(path)
        assert first.status_code == 409
        assert first.json()["detail"]["failure_code"] == "trial_order_cancel_pending"
        assert gateway.cancelled_order_ids == ["ORDER_1"]
        assert trading_state.get("verify_trial") is entry
        assert entry.engine.trial_run_execution is execution

        retry = client.post(path)
        assert retry.status_code == 409
        assert retry.json()["detail"]["failure_code"] == "trial_order_cancel_pending"
        assert gateway.cancelled_order_ids == ["ORDER_1"]

        gateway.on_order(gateway.orders["ORDER_1"])
        completed = client.post(path)
        assert completed.status_code == 200
        assert trading_state.get("verify_trial") is None
        assert entry.engine.trial_run_execution is None
        expected_state = "idle" if path.endswith("reset") else "stopped"
        assert completed.json()["status"]["state"] == expected_state


def test_failed_order_manager_start_clears_bound_trial_strategy(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "failed-manager-start"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        engine = trading_state.primary_engine()
        assert engine is not None
        monkeypatch.setattr(engine.order_manager, "start", lambda: False)

        response = client.post("/trial-run/prepare")
        assert response.status_code == 500
        assert trading_state.get("verify_trial") is None
        assert engine.trial_run_execution is None
        assert engine.strategy is None

        engine.on_tick(_tick(3130))
        assert gateway.sent_signals == []


def test_failed_start_with_inflight_submission_keeps_registered_recovery_entry(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "failed-start-inflight"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        engine = trading_state.primary_engine()
        assert engine is not None

        def fail_with_inflight(_config):
            with engine._trial_submission_lock:
                engine._trial_submission_in_flight = True
            return False

        monkeypatch.setattr(engine, "start", fail_with_inflight)
        response = client.post("/trial-run/prepare")

        assert response.status_code == 500
        recovery_entry = trading_state.get("verify_trial")
        assert recovery_entry is not None
        assert recovery_entry.engine is engine
        assert engine.trial_run_execution is not None

        with engine._trial_submission_lock:
            engine._trial_submission_in_flight = False


def test_trial_run_stale_first_tick_never_emits_bar_or_order(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "stale-first-tick"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        entry = trading_state.get("verify_trial")
        assert entry is not None
        stale_ts = datetime.now() - timedelta(seconds=10)
        entry.engine.on_tick(_tick(3130, timestamp=stale_ts))

        status = client.get("/trial-run/status")
        assert status.status_code == 200
        body = status.json()
        assert body["state"] == "waiting_market_data"
        assert body["tick_count"] == 0
        assert body["bar_count"] == 0
        assert body["first_tick_bar_emitted"] is False
        assert body["market_issue"] == "stale_market_data"
        assert entry.strategy.signals == []
        assert gateway.sent_signals == []


def test_trial_run_manual_arm_when_auto_arm_disabled(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "manual-arm"), auto_arm=False, warmup_bars=2)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        prepared = client.post("/trial-run/prepare")
        assert prepared.status_code == 200
        assert prepared.json()["status"]["auto_arm"] is False
        assert gateway.subscribed_symbols == [["rb2510"]]

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.strategy.on_bar(_bar(3130))

        started = client.post("/trial-run/start")
        assert started.status_code == 200
        assert started.json()["action"] == "start"
        assert started.json()["status"]["state"] == "started"
        assert started.json()["status"]["market_ready"] is True


def test_trial_run_status_reports_tick_readiness_details(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "tick-status"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "entry_pending"
    assert body["market_ready"] is True
    assert body["tick_count"] == 1
    assert body["bar_count"] == 1
    assert body["first_tick_bar_emitted"] is True
    assert body["last_market_price"] == 3130
    assert body["last_market_timestamp"]
    assert body["chase_state"] == "waiting_timeout"
    assert body["rate_retry_after_seconds"] == 0.0


def test_trial_run_status_reports_waiting_counterparty_after_no_fill_timeout(monkeypatch, tmp_path):
    config_path = _trial_config(
        _config_path(tmp_path, "no-fill-timeout"),
        no_fill_timeout_seconds=10,
        chase_max_attempts=5,
    )
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.engine.on_tick(_tick(3130))
        assert gateway.orders
        for order in gateway.orders.values():
            order.create_time = datetime.now() - timedelta(seconds=11)

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["execution_issue"] == "waiting_counterparty"
    assert body["unfilled_wait_seconds"] >= 10
    assert "对手盘" in body["execution_warning"]
    assert body["no_fill_timeout_seconds"] == 10
    assert body["chase_max_attempts"] == 5


def test_trial_run_status_reports_cached_gateway_tick_before_strategy_receives_it(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "cached-tick-status"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        gateway.latest_ticks = {"rb2510": _tick(3120)}

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["market_ready"] is False
    assert body["last_market_price"] == 3120
    assert body["last_market_timestamp"]
    assert body["market_data_age_seconds"] >= 0
    assert body["market_issue"] == "first_tick_bar_not_emitted"
    assert body["first_tick_bar_enabled"] is True
    assert body["first_tick_bar_emitted"] is False


def test_trial_run_status_reports_no_tick_timeout(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "no-tick-timeout"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        prepared_at = trial_run_state.snapshot()["prepared_at"]
        monkeypatch.setattr(trial_run_module.time, "time", lambda: prepared_at + 16)

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["market_issue"] == "no_tick_timeout"
    assert body["no_bar_wait_seconds"] == 16
    assert body["tick_count"] == 0
    assert body["bar_count"] == 0
    assert body["market_warning"]


def test_trial_run_status_reports_cached_symbol_mismatch(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "symbol-mismatch"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        gateway.latest_ticks = {"ag2510": _tick(7320, symbol="ag2510")}

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["market_issue"] == "symbol_mismatch"
    assert body["last_market_price"] == 0


def test_trial_run_prepare_can_use_example_config_when_local_missing(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "example-fallback"))
    monkeypatch.delenv("QUANT_TRIAL_CONFIG", raising=False)
    monkeypatch.setattr(trial_run_module, "_LOCAL_CONFIG", _config_path(tmp_path, "missing-local"))
    monkeypatch.setattr(trial_run_module, "_EXAMPLE_CONFIG", config_path)
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)

        prepared = client.post("/trial-run/prepare")

    assert prepared.status_code == 200
    assert prepared.json()["status"]["strategy_id"] == "verify_trial"
    assert gateway.subscribed_symbols == [["rb2510"]]


def test_trial_run_start_returns_conflict_until_market_ready(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "conflict"), auto_arm=False, warmup_bars=2)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        response = client.post("/trial-run/start")

    assert response.status_code == 409
