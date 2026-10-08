"""Saved CTP login defaults; fake gateway only and no private local settings."""
import json

import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.api.security import session_store
from src.settings import ctp_defaults, ctp_server_presets, load_ctp_config
from test_api_auth import FakeVnpyGateway


@pytest.fixture
def saved_config(tmp_path, monkeypatch):
    path = tmp_path / "ctp.json"
    trading = {
        "broker_id": "SAVED_BROKER", "td_server": "tcp://fixture:1", "md_server": "tcp://fixture:2",
        "app_id": "SAVED_APP", "auth_code": "PRIVATE_AUTH_FIXTURE",
        "username": "PRIVATE_ACCOUNT_FIXTURE", "password": "PRIVATE_PASSWORD_FIXTURE",
        "td_servers": [{"label": "备用线路", "value": "tcp://fixture:3", "auth_code": "EXTRA_SECRET"}],
    }
    path.write_text(json.dumps({"trading": trading, "strategy": {"symbol": "IGNORED"}}), encoding="utf-8")
    monkeypatch.setenv("QUANT_CTP_CONFIG", str(path))
    monkeypatch.setenv("QUANT_ENV", "test")
    monkeypatch.setenv("QUANT_LEDGER_PATH", ":memory:")
    return path, trading


@pytest.fixture
def client(saved_config, monkeypatch):
    class CapturingGateway(FakeVnpyGateway):
        def connect(self, config):
            self.login_config = config
            return super().connect(config)

    monkeypatch.setattr("src.trading.create_gateway", lambda *_: CapturingGateway())
    api.trading_state.clear_main()
    session_store.revoke_all()
    with TestClient(api.create_app()) as http:
        yield http
    api.trading_state.clear_main()
    session_store.revoke_all()


def test_public_defaults_and_openapi_never_disclose_credentials(client):
    response = client.get("/auth/servers")
    assert response.status_code == 200
    data = response.json()
    assert data["defaults"] == {
        "broker_id": "SAVED_BROKER", "td_server": "tcp://fixture:1", "md_server": "tcp://fixture:2",
        "app_id": "SAVED_APP", "environment": "实盘", "auth_code_configured": True,
    }
    assert len(data["td_servers"]) == 2
    assert data["md_servers"][0]["value"] == "tcp://fixture:2"
    for secret in ("PRIVATE_AUTH_FIXTURE", "PRIVATE_ACCOUNT_FIXTURE", "PRIVATE_PASSWORD_FIXTURE", "EXTRA_SECRET"):
        assert secret not in response.text
        assert secret not in client.get("/openapi.json").text


@pytest.mark.parametrize("legacy_blank_fields", [False, True])
def test_account_password_only_login_inherits_saved_config_without_contract(client, legacy_blank_fields):
    payload = {"username": "INPUT_ACCOUNT", "password": "INPUT_PASSWORD"}
    if legacy_blank_fields:
        payload.update({key: "  " for key in ("broker_id", "td_server", "md_server", "app_id", "auth_code")})
    response = client.post("/auth/login", json=payload)
    assert response.status_code == 200
    assert response.json()["strategy_started"] is False
    actual = api.trading_state.primary_engine().gateway.login_config
    for key, value in {
        "broker_id": "SAVED_BROKER", "td_server": "tcp://fixture:1", "md_server": "tcp://fixture:2",
        "app_id": "SAVED_APP", "auth_code": "PRIVATE_AUTH_FIXTURE", "vnpy_environment": "实盘",
        "username": "INPUT_ACCOUNT", "password": "INPUT_PASSWORD", "contract_margin_rates": {},
    }.items():
        assert actual[key] == value
    assert "symbol" not in actual


def test_request_overrides_environment_overrides_saved_config(client, monkeypatch):
    monkeypatch.setenv("QUANT_CTP_BROKER_ID", "ENV_BROKER")
    monkeypatch.setenv("QUANT_CTP_AUTH_CODE", "ENV_AUTH")
    assert client.get("/auth/servers").json()["defaults"]["broker_id"] == "ENV_BROKER"
    assert client.post("/auth/login", json={
        "username": "u", "password": "p", "broker_id": "INPUT_BROKER", "environment": "测试",
        "td_server": "tcp://override:1", "md_server": "tcp://override:2", "app_id": "INPUT_APP",
    }).status_code == 200
    config = api.trading_state.primary_engine().gateway.login_config
    assert config["broker_id"] == "INPUT_BROKER"
    assert config["auth_code"] == "ENV_AUTH"
    assert config["vnpy_environment"] == "测试"
    assert config["app_id"] == "INPUT_APP"
    assert config["td_server"] == "tcp://override:1"
    assert config["md_server"] == "tcp://override:2"
    assert client.post("/auth/login", json={"username": "u", "password": "p", "auth_code": "INPUT_AUTH"}).status_code == 200
    assert api.trading_state.primary_engine().gateway.login_config["auth_code"] == "INPUT_AUTH"


def test_local_file_refresh_and_margin_config_are_explicit(client, saved_config):
    path, trading = saved_config
    trading["broker_id"] = "UPDATED_BROKER"
    trading["contract_margin_rates"] = {"VERIFIED_CONTRACT": .12}
    path.write_text(json.dumps({"trading": trading}), encoding="utf-8")
    assert client.get("/auth/servers").json()["defaults"]["broker_id"] == "UPDATED_BROKER"
    assert client.post("/auth/login", json={"username": "u", "password": "p"}).status_code == 200
    assert api.trading_state.primary_engine().gateway.login_config["contract_margin_rates"] == {"VERIFIED_CONTRACT": .12}
    assert client.post("/auth/login", json={"username": "u", "password": "p", "contract_margin_rates": {}}).status_code == 200
    assert api.trading_state.primary_engine().gateway.login_config["contract_margin_rates"] == {}


@pytest.mark.parametrize("raw", ['{"auth_code":"SECRET_BROKEN', '[]', '{"trading": []}'])
def test_bad_config_returns_sanitized_error(client, saved_config, raw):
    saved_config[0].write_text(raw, encoding="utf-8")
    for response in (client.get("/auth/servers"), client.post("/auth/login", json={"username": "u", "password": "p"})):
        assert response.status_code == 503
        assert "SECRET_BROKEN" not in response.text
        assert "CTP 本地配置无法读取" in response.json()["detail"]


def test_presets_env_override_and_default_address_are_both_available(saved_config, monkeypatch):
    monkeypatch.setenv("QUANT_CTP_TD_PRESETS", "环境线路=tcp://fixture:4")
    assert [p["value"] for p in ctp_server_presets("td")] == ["tcp://fixture:1", "tcp://fixture:4"]


def test_missing_default_config_is_safe_and_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_CTP_CONFIG")
    monkeypatch.setattr("src.settings.__file__", str(tmp_path / "src/settings.py"))
    monkeypatch.chdir(tmp_path.parent)
    assert load_ctp_config() == {}
    assert ctp_defaults()["auth_code"] == ""
    assert ctp_defaults()["vnpy_environment"] == "实盘"
    config = tmp_path / "config/config_production.json"
    config.parent.mkdir()
    config.write_text('{"trading":{"broker_id":"RELATIVE_TO_MODULE"}}', encoding="utf-8")
    assert ctp_defaults()["broker_id"] == "RELATIVE_TO_MODULE"


def test_invalid_environment_and_margin_config_do_not_connect(client, saved_config):
    assert client.post("/auth/login", json={"username": "u", "password": "p", "environment": "unknown"}).status_code == 422
    path, trading = saved_config
    trading["contract_margin_rates"] = {"INVALID": 0}
    path.write_text(json.dumps({"trading": trading}), encoding="utf-8")
    assert client.post("/auth/login", json={"username": "u", "password": "p"}).status_code == 422
    assert api.trading_state.primary_engine() is None
