"""Fail-closed production entry-gate coverage for the live trading API.

These tests intentionally exercise only local settings and the in-process API.
They never use a CTP endpoint, account, or credential.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.api.models import RiskConfigRequest
from src.settings import server_live_risk_config, validate_production_runtime
from src.trading.risk import RiskManager


def _complete_live_config(*, environment: str = "实盘", allowed_symbols=None):
    return {
        "mode": "live",
        "trading": {"vnpy_environment": environment},
        "risk": {
            "enabled": True,
            "max_order_volume": 1,
            "max_position_volume": 1,
            "max_active_orders": 1,
            "max_orders_per_minute": 5,
            "max_daily_loss_ratio": 0.01,
            "max_order_value": 100000.0,
            "max_position_value": 100000.0,
            "max_price_deviation": 0.01,
            "max_market_data_age_seconds": 3.0,
            "duplicate_signal_window_seconds": 5.0,
            "duplicate_cancel_window_seconds": 5.0,
            "default_contract_multiplier": 10.0,
            "contract_multipliers": {},
            "allow_market_orders": False,
            "allowed_symbols": ["rb9999"] if allowed_symbols is None else allowed_symbols,
            "blocked_symbols": [],
            "order_count_alert_threshold": 500,
            "cancel_count_alert_threshold": 300,
            "duplicate_open_alert_threshold": 1,
            "duplicate_close_alert_threshold": 1,
            "duplicate_cancel_alert_threshold": 1,
        },
    }


def _configure_valid_production(monkeypatch, tmp_path, *, payload=None):
    config_path = tmp_path / "live-config.json"
    config_path.write_text(
        json.dumps(payload or _complete_live_config()), encoding="utf-8"
    )
    monkeypatch.setenv("QUANT_ENV", "production")
    monkeypatch.setenv("QUANT_LIVE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("QUANT_ALLOW_SYNTHETIC_DATA", "false")
    monkeypatch.setenv("QUANT_SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("QUANT_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("QUANT_ALLOW_WS_QUERY_TOKEN", "false")
    monkeypatch.setenv("QUANT_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("QUANT_LIVE_RISK_STATE_PATH", str(tmp_path / "state" / "risk.json"))
    monkeypatch.setenv("QUANT_SESSION_DB", str(tmp_path / "session" / "sessions.db"))
    monkeypatch.setenv("QUANT_CORS_ORIGINS", "https://trade.example.test")
    return config_path


def test_production_accepts_only_complete_explicit_live_risk_config(monkeypatch, tmp_path):
    _configure_valid_production(monkeypatch, tmp_path)

    risk = validate_production_runtime()

    assert risk["enabled"] is True
    assert risk["max_order_volume"] == 1
    assert risk["allowed_symbols"] == ["rb9999"]


def test_production_rejects_missing_live_config_path(monkeypatch):
    monkeypatch.setenv("QUANT_ENV", "production")
    monkeypatch.delenv("QUANT_LIVE_CONFIG_PATH", raising=False)

    with pytest.raises(RuntimeError, match="QUANT_LIVE_CONFIG_PATH is required"):
        server_live_risk_config()


def test_api_startup_fails_closed_when_production_config_is_not_provided(monkeypatch):
    monkeypatch.setenv("QUANT_ENV", "production")
    monkeypatch.delenv("QUANT_LIVE_CONFIG_PATH", raising=False)

    with pytest.raises(RuntimeError, match="QUANT_LIVE_CONFIG_PATH is required"):
        with TestClient(create_app()):
            pass


def test_production_rejects_incomplete_or_non_live_risk_config(monkeypatch, tmp_path):
    incomplete = _complete_live_config()
    incomplete["risk"].pop("max_order_volume")
    _configure_valid_production(monkeypatch, tmp_path, payload=incomplete)
    with pytest.raises(RuntimeError, match="incomplete"):
        server_live_risk_config()

    non_live = _complete_live_config(environment="simulation")
    _configure_valid_production(monkeypatch, tmp_path, payload=non_live)
    with pytest.raises(RuntimeError, match="exactly to '实盘'"):
        server_live_risk_config()


def test_production_rejects_empty_allowed_symbols(monkeypatch, tmp_path):
    _configure_valid_production(
        monkeypatch, tmp_path, payload=_complete_live_config(allowed_symbols=[])
    )

    with pytest.raises(RuntimeError, match="non-empty allowed_symbols"):
        server_live_risk_config()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_order_volume", True, "positive JSON integer"),
        ("max_position_volume", 0, "positive JSON integer"),
        ("max_order_value", "100", "positive finite JSON number"),
        ("max_market_data_age_seconds", 0.0, "positive finite JSON number"),
        ("duplicate_cancel_window_seconds", -1.0, "positive finite JSON number"),
        ("default_contract_multiplier", 0.0, "positive finite JSON number"),
        ("max_daily_loss_ratio", 0.0, "number in \\(0, 1]"),
        ("max_price_deviation", 1.01, "number in \\(0, 1]"),
        ("order_count_alert_threshold", 0, "positive JSON integer"),
        ("duplicate_cancel_alert_threshold", 1.5, "positive JSON integer"),
        ("allow_market_orders", 0, "JSON boolean"),
    ],
)
def test_production_rejects_malformed_raw_risk_limits(
    monkeypatch, tmp_path, field, value, message
):
    payload = _complete_live_config()
    payload["risk"][field] = value
    _configure_valid_production(monkeypatch, tmp_path, payload=payload)

    with pytest.raises(RuntimeError, match=message):
        server_live_risk_config()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("allowed_symbols", "rb9999", "list of non-empty strings"),
        ("allowed_symbols", ["rb9999", 1], "list of non-empty strings"),
        ("blocked_symbols", ["rb9999"], "must not overlap"),
        ("contract_multipliers", [], "must be an object"),
        ("contract_multipliers", {"rb9999": 0}, "positive finite numbers"),
        ("contract_multipliers", {"": 10}, "keys must be non-empty strings"),
    ],
)
def test_production_rejects_invalid_symbol_and_multiplier_structures(
    monkeypatch, tmp_path, field, value, message
):
    payload = _complete_live_config()
    payload["risk"][field] = value
    _configure_valid_production(monkeypatch, tmp_path, payload=payload)

    with pytest.raises(RuntimeError, match=message):
        server_live_risk_config()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_production_rejects_nonstandard_json_numeric_constants(monkeypatch, tmp_path, constant):
    config_path = _configure_valid_production(monkeypatch, tmp_path)
    content = config_path.read_text(encoding="utf-8").replace("0.01", constant, 1)
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid JSON numeric constant"):
        server_live_risk_config()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("QUANT_ALLOW_SYNTHETIC_DATA", "true", "SYNTHETIC_DATA"),
        ("QUANT_SESSION_COOKIE_SECURE", "false", "secure session cookies"),
        ("QUANT_RATE_LIMIT_ENABLED", "false", "RATE_LIMIT_ENABLED"),
        ("QUANT_ALLOW_WS_QUERY_TOKEN", "true", "ALLOW_WS_QUERY_TOKEN"),
    ],
)
def test_production_rejects_unsafe_runtime_switches(monkeypatch, tmp_path, name, value, message):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        validate_production_runtime()


def test_production_rejects_missing_durable_runtime_paths(monkeypatch, tmp_path):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.delenv("QUANT_AUDIT_LOG_DIR")
    monkeypatch.delenv("QUANT_LIVE_RISK_STATE_PATH")
    monkeypatch.delenv("QUANT_SESSION_DB")

    with pytest.raises(RuntimeError, match="runtime path variables are required") as exc_info:
        validate_production_runtime()

    message = str(exc_info.value)
    assert "QUANT_AUDIT_LOG_DIR" in message
    assert "QUANT_LIVE_RISK_STATE_PATH" in message
    assert "QUANT_SESSION_DB" in message


@pytest.mark.parametrize(
    ("origins", "message"),
    [
        ("", "CORS_ORIGINS is required"),
        ("*", "wildcard"),
        ("http://trade.example.test", "must use HTTPS"),
        ("http://localhost.attacker.test", "must use HTTPS"),
        ("http://127.0.0.1.attacker.test", "must use HTTPS"),
        ("https://*.example.test", "must use HTTPS"),
        ("https://user:pass@example.test", "must use HTTPS"),
        ("https://example.test/path", "must use HTTPS"),
    ],
)
def test_production_rejects_unsafe_cors_origins(monkeypatch, tmp_path, origins, message):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("QUANT_CORS_ORIGINS", origins)

    with pytest.raises(RuntimeError, match=message):
        validate_production_runtime()


def test_api_login_rejects_client_supplied_risk_before_gateway_connects():
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "username": "no-network",
                "password": "no-network",
                "gateway_type": "vnpy",
                "risk": {"max_order_volume": 999999},
            },
        )

    assert response.status_code == 422


class _RiskEngine:
    def __init__(self):
        self.risk_manager = RiskManager(
            {
                "order_count_alert_threshold": 5,
                "cancel_count_alert_threshold": 5,
                "duplicate_open_alert_threshold": 5,
                "duplicate_close_alert_threshold": 5,
                "duplicate_cancel_alert_threshold": 5,
            }
        )

    def configure_risk(self, config):
        self.risk_manager.configure(config)


def _risk_config_endpoint():
    app = create_app()
    return next(route.endpoint for route in app.routes if route.path == "/risk/config")


@pytest.mark.parametrize(
    "field_name",
    [
        "order_count_alert_threshold",
        "cancel_count_alert_threshold",
        "duplicate_open_alert_threshold",
        "duplicate_close_alert_threshold",
        "duplicate_cancel_alert_threshold",
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1])
def test_runtime_compliance_thresholds_must_be_positive_integers(
    monkeypatch, field_name, invalid_value
):
    engine = _RiskEngine()
    monkeypatch.setattr(trading_state, "primary_engine", lambda: engine)
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])

    with pytest.raises(HTTPException) as exc_info:
        _risk_config_endpoint()(
            RiskConfigRequest(risk={field_name: invalid_value}),
            None,
        )

    assert exc_info.value.status_code == 422
    assert engine.risk_manager.config.__dict__[field_name] == 5


class _ResumeGateway:
    def __init__(self, reconciliation, *, reconciliation_ready, orders=None):
        self._reconciliation = reconciliation
        self._reconciliation_ready = reconciliation_ready
        self.orders = dict(orders or {})
        self.refresh_calls = 0

    def refresh_reconciliation(self, timeout_seconds=8.0):
        self.refresh_calls += 1
        return dict(self._reconciliation)

    def connection_snapshot(self):
        return {"reconciliation_ready": self._reconciliation_ready}


class _ResumeEngine:
    def __init__(self, gateway):
        self.gateway = gateway
        self.risk_manager = RiskManager()
        self.risk_manager.set_emergency_stop(True, "operator hold")


class _ActiveOrder:
    def is_active(self):
        return True


def _resume_endpoint():
    app = create_app()
    return next(route.endpoint for route in app.routes if route.path == "/risk/resume")


def _install_resume_dependencies(monkeypatch, engine):
    monkeypatch.setattr("src.api._unique_engines", lambda: [engine])
    monkeypatch.setattr(
        "src.api.audit_log.persistence_status",
        lambda: {"enabled": True, "ok": True, "error": ""},
    )
    monkeypatch.setattr("src.api._record_audit", lambda *args, **kwargs: True)
    monkeypatch.setattr(trading_state, "add_log", lambda *args, **kwargs: None)


@pytest.mark.parametrize(
    ("reconciliation", "reconciliation_ready"),
    [
        ({"ok": False, "fresh": False, "failure_code": "broker_snapshot_timeout"}, False),
        ({"ok": True, "fresh": True}, False),
    ],
)
def test_resume_requires_a_fresh_authoritative_reconciliation_and_ready_gate(
    monkeypatch, reconciliation, reconciliation_ready
):
    gateway = _ResumeGateway(
        reconciliation,
        reconciliation_ready=reconciliation_ready,
    )
    engine = _ResumeEngine(gateway)
    _install_resume_dependencies(monkeypatch, engine)

    with pytest.raises(HTTPException) as exc_info:
        _resume_endpoint()(None)

    assert exc_info.value.status_code == 409
    assert gateway.refresh_calls == 1
    assert engine.risk_manager.emergency_stop is True


def test_resume_rejects_active_or_pending_broker_orders_after_reconciliation(monkeypatch):
    gateway = _ResumeGateway(
        {"ok": True, "fresh": True},
        reconciliation_ready=True,
        orders={"PENDING-1": _ActiveOrder()},
    )
    engine = _ResumeEngine(gateway)
    _install_resume_dependencies(monkeypatch, engine)

    with pytest.raises(HTTPException) as exc_info:
        _resume_endpoint()(None)

    assert exc_info.value.status_code == 409
    assert gateway.refresh_calls == 1
    assert engine.risk_manager.emergency_stop is True


def test_resume_succeeds_only_after_clean_authoritative_reconciliation(monkeypatch):
    gateway = _ResumeGateway(
        {"ok": True, "fresh": True},
        reconciliation_ready=True,
    )
    engine = _ResumeEngine(gateway)
    _install_resume_dependencies(monkeypatch, engine)

    response = _resume_endpoint()(None)

    assert response == {"success": True, "emergency_stop": False}
    assert gateway.refresh_calls == 1
    assert engine.risk_manager.emergency_stop is False
