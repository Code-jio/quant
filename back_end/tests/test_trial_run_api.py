import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
import src.api.trial_run as trial_run_module
from src.api.trial_run import trial_run_state
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
):
    payload = {
        "trial_run": {
            "enabled": True,
            "allowed_symbol": "rb2510",
            "account_id": "trial-account",
            "auto_arm": auto_arm,
            "bar_timeout_seconds": bar_timeout_seconds,
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


def login(client):
    response = client.post(
        "/auth/login",
        json={
            "username": "test-account",
            "password": "test-password",
            "broker_id": "2071",
            "gateway_type": "vnpy",
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
    assert body["trading"]["auth_code"] == "trial-auth-code"
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
        for path in ["/trial-run/prepare", "/trial-run/start", "/trial-run/arm", "/trial-run/stop", "/trial-run/reset"]:
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
        assert prepared.json()["status"]["auto_arm"] is True
        assert prepared.json()["status"]["first_tick_bar_enabled"] is True
        assert gateway.subscribed_symbols == [["rb2510"]]

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


def test_trial_run_stale_first_tick_still_emits_bar_for_trial_flow(monkeypatch, tmp_path):
    """A slightly stale tick should still emit the first bar so the trial-run
    fast path isn't blocked in simulation environments."""
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
        # The stale tick should have emitted the first bar and triggered entry
        assert body["state"] == "entry_pending"
        assert body["tick_count"] == 0
        assert body["bar_count"] == 1
        assert body["first_tick_bar_emitted"] is True
        assert len(entry.strategy.signals) == 1
        assert len(gateway.sent_signals) == 1


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
