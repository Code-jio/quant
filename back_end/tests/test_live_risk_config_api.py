"""Regression coverage for live risk-config patch semantics."""

from types import SimpleNamespace

from src.api import create_app, trading_state
from src.api.models import RiskConfigRequest
from src.trading.risk import RiskManager


def _risk_config_endpoint():
    app = create_app()
    return next(route.endpoint for route in app.routes if route.path == "/risk/config")


class _LiveEngine:
    def __init__(self, risk):
        self.risk_manager = RiskManager(risk)
        self.configure_calls = []

    def configure_risk(self, config):
        self.configure_calls.append(config)
        self.risk_manager.configure(config)


def test_risk_config_update_is_a_patch_of_primary_live_config(monkeypatch):
    current_risk = {
        "max_order_volume": 7,
        "max_position_volume": 51,
        "max_active_orders": 6,
        "max_orders_per_minute": 9,
        "max_daily_loss_ratio": 0.03,
        "max_order_value": 90000,
        "max_position_value": 300000,
        "max_price_deviation": 0.02,
        "max_market_data_age_seconds": 4,
        "duplicate_signal_window_seconds": 12,
        "duplicate_cancel_window_seconds": 8,
        "order_count_alert_threshold": 13,
        "cancel_count_alert_threshold": 2,
        "duplicate_open_alert_threshold": 3,
        "duplicate_close_alert_threshold": 4,
        "duplicate_cancel_alert_threshold": 5,
        "allow_market_orders": False,
        "allowed_symbols": ["rb2505", "IF2506"],
        "blocked_symbols": ["au2506"],
    }
    primary = _LiveEngine(current_risk)
    secondary = _LiveEngine({"max_order_volume": 1})
    primary.risk_manager._compliance_counters["orders_submitted"] = 3
    primary.risk_manager.set_emergency_stop(True, "manual live stop")
    persistent_store = object()
    primary.risk_manager._state_store = persistent_store
    primary.risk_manager._state_scope = "anonymous-live-scope"
    trading_state._main_config = {"risk": dict(current_risk)}
    monkeypatch.setattr(trading_state, "primary_engine", lambda: primary)
    monkeypatch.setattr(
        trading_state,
        "all_entries",
        lambda: [SimpleNamespace(engine=secondary)],
    )

    response = _risk_config_endpoint()(RiskConfigRequest(risk={"cancel_count_alert_threshold": 17}), None)

    assert response["success"] is True
    assert response["risk"] == primary.risk_manager.status()
    for engine in (primary, secondary):
        config = engine.risk_manager.config
        assert config.max_order_volume == 7
        assert config.max_position_volume == 51
        assert config.max_active_orders == 6
        assert config.max_orders_per_minute == 9
        assert config.max_daily_loss_ratio == 0.03
        assert config.max_order_value == 90000
        assert config.max_position_value == 300000
        assert config.max_price_deviation == 0.02
        assert config.max_market_data_age_seconds == 4
        assert config.duplicate_signal_window_seconds == 12
        assert config.duplicate_cancel_window_seconds == 8
        assert config.order_count_alert_threshold == 13
        assert config.cancel_count_alert_threshold == 17
        assert config.allow_market_orders is False
        assert config.allowed_symbols == {"rb2505", "IF2506"}
        assert config.blocked_symbols == {"au2506"}

    assert primary.risk_manager._compliance_counters["orders_submitted"] == 3
    assert primary.risk_manager.emergency_stop is True
    assert primary.risk_manager.emergency_reason == "manual live stop"
    assert primary.risk_manager._state_store is persistent_store
    assert primary.risk_manager._state_scope == "anonymous-live-scope"
    assert trading_state._main_config["risk"]["allowed_symbols"] == ["IF2506", "rb2505"]
