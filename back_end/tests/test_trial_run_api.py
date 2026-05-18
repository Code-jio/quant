import json
import uuid
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.api.trial_run import trial_run_state

from tests.helpers import RecordingGateway


def _config_path(name):
    root = Path(__file__).resolve().parents[1] / ".test-artifacts" / "trial-run-tests"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}-{uuid.uuid4().hex}.json"


def _trial_config(path, password="secret-password"):
    payload = {
        "trial_run": {"enabled": True, "allowed_symbol": "rb2510", "account_id": "trial-account"},
        "strategy": {"name": "verify", "symbol": "rb2510", "volume": 1, "warmup_bars": 2, "hold_bars": 2, "order_type": "limit"},
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


def test_trial_run_config_is_public_and_masks_sensitive_fields(monkeypatch):
    config_path = _trial_config(_config_path("config"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["allowed_symbol"] == "rb2510"
    assert body["account_id"] == ""
    assert body["masked_account_id"] == "tr****nt"
    assert "password" not in body["config"]["trading"]
    assert "auth_code" not in response.text
    assert "secret-password" not in response.text
    assert "trial-auth-code" not in response.text
    assert "tcp://td.example:123" not in response.text
    assert "trial-account" not in response.text


def test_trial_run_config_requires_strategy_symbol(monkeypatch):
    config_path = _trial_config(_config_path("missing-symbol"))
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


def test_trial_run_mutations_require_login(monkeypatch):
    config_path = _trial_config(_config_path("auth-required"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        for path in ["/trial-run/prepare", "/trial-run/arm", "/trial-run/stop", "/trial-run/reset"]:
            response = client.post(path)
            assert response.status_code == 401


def test_trial_run_prepare_and_arm(monkeypatch):
    config_path = _trial_config(_config_path("prepare"))
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
        assert prepared.json()["status"]["state"] == "warming"
        assert prepared.json()["status"]["strategy_id"] == "verify_trial"
        assert gateway.subscribed_symbols == [["rb2510"]]

        entry = trading_state.get("verify_trial")
        assert entry is not None
        entry.strategy.on_bar(_bar(3130))
        entry.strategy.on_bar(_bar(3132))

        armed = client.post("/trial-run/arm")
        assert armed.status_code == 200
        assert armed.json()["status"]["state"] == "armed"


def test_trial_run_arm_returns_conflict_when_strategy_refuses(monkeypatch):
    config_path = _trial_config(_config_path("conflict"))
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200

        response = client.post("/trial-run/arm")

    assert response.status_code == 409
