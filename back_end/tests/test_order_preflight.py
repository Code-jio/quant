"""Read-only order evaluation and submission safety; no native orders."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.strategy import OrderStatus, Direction, OffsetFlag, Position
from src.trading.vnpy_gateway import VnpyGateway
from test_manual_trading import install_gateway, login, allow_market_orders
from test_review_trading import connected_engine, signal


def test_preflight_is_read_only_and_estimates_reserved_margin():
    engine, gw = connected_engine()
    gw.contract_specs = {"A": {"size": 10, "margin_rate": .1, "margin_per_lot": 20}}
    gw.account.available = 250
    oid = engine.send_signal(signal())
    gw.orders[oid].status = OrderStatus.SUBMITTED
    gw.account.trading_day = "20261015"
    before = deepcopy(engine.risk_manager.__dict__)
    changes = engine.ledger.db.total_changes
    queries: list[str] = []
    gw.request_margin_rate = queries.append
    engine.last_reject_reason = "previous rejection"
    result = engine.preflight(signal())
    assert result["allowed"]
    assert result["margin"] == {"required": 120, "reserved": 120, "available": 250, "remaining": 10}
    assert engine.ledger.db.total_changes == changes
    assert engine.risk_manager.__dict__ == before
    assert engine.last_reject_reason == "previous rejection"
    assert not queries and len(gw.sent_signals) == 1
    assert not engine.ledger.unresolved()
    engine.close()


def test_preflight_does_not_authorize_later_order():
    engine, gw = connected_engine()
    assert engine.preflight(signal())["allowed"]
    engine.set_emergency_stop(True, "operator")
    assert not engine.send_signal(signal())
    assert not gw.sent_signals
    engine.close()


def test_preview_rechecks_funds_and_gateway_disconnect_at_submission():
    engine, gw = connected_engine()
    gw.contract_specs = {"A": {"size": 10, "margin_rate": .1}}
    assert engine.preflight(signal())["allowed"]
    gw.account.available = 1
    assert not engine.send_signal(signal())
    assert "margin" in engine.last_reject_reason
    gw.account.available = 100000
    engine.status = gw.status
    gw.disconnect()
    assert not engine.send_signal(signal())
    assert not gw.sent_signals
    engine.close()


def test_real_dispatch_exception_still_leaves_unresolved_intent_and_stops():
    engine, gw = connected_engine()

    def uncertain_dispatch(order):
        raise RuntimeError("fixture: lost broker response")

    gw.send_order = uncertain_dispatch
    assert not engine.send_signal(signal())
    assert engine.risk_manager.emergency_stop
    assert len(engine.ledger.unresolved()) == 1
    assert not engine.preflight(signal())["allowed"]
    engine.close()


@pytest.mark.parametrize("age,day", [(1900, "20261015"), (0, "20261014"), (-60, "20261015")])
def test_margin_preview_rejects_expired_wrong_day_and_future_metadata(age, day):
    engine, gw = connected_engine()
    gw.account.trading_day = "20261015"
    gw.contract_specs = {"A": {"size": 10, "margin_rate": .1, "margin_source": "ctp_query",
                                "margin_trading_day": day,
                                "margin_updated_at": (datetime.now().astimezone() - timedelta(seconds=age)).isoformat()}}
    assert not engine.preflight(signal())["allowed"]
    assert not engine.send_signal(signal())
    assert not gw.sent_signals
    engine.close()


def test_close_preview_uses_offset_frozen_and_pending_quantity():
    engine, gw = connected_engine()
    gw.positions = {"A_short": Position("A", Direction.SHORT, 5, frozen=1, yd_volume=2)}
    order = signal(offset=OffsetFlag.CLOSE_TODAY)
    result = engine.preflight(order)
    assert result["allowed"] and result["close_available"] == 2
    order.volume = 3
    assert not engine.preflight(order)["allowed"]
    assert not gw.sent_signals
    engine.close()


def test_invalid_contract_tick_does_not_create_unknown_intent():
    engine, gw = connected_engine()
    gw.contract_specs = {"A": {"size": 10, "margin_rate": .1, "pricetick": 3, "min_volume": 1}}
    gw.validate_order = lambda order: VnpyGateway.validate_order(gw, order)
    assert not engine.send_signal(signal())
    assert "tick" in engine.last_reject_reason
    assert not gw.sent_signals and not engine.ledger.unresolved()
    assert not engine.risk_manager.emergency_stop
    engine.close()


@pytest.mark.parametrize("tick,unit", [(float("nan"), 1), (1, float("inf")), (1, 0)])
def test_invalid_native_contract_units_fail_before_dispatch(tick, unit):
    engine, gw = connected_engine()
    gw.contract_specs = {"A": {"size": 10, "margin_rate": .1, "pricetick": tick, "min_volume": unit}}
    gw.validate_order = lambda order: VnpyGateway.validate_order(gw, order)
    assert not engine.preflight(signal())["allowed"]
    assert not engine.send_signal(signal())
    assert not gw.sent_signals and not engine.ledger.unresolved()
    assert not engine.risk_manager.emergency_stop
    engine.close()


def test_preflight_lists_independent_blockers_and_unknown_margin():
    engine, gw = connected_engine()
    gw.contract_specs = {"A": {"size": 10, "margin_rate": None}}
    engine.set_emergency_stop(True, "operator")
    engine.missing_order_replays.add("missing")
    result = engine.preflight(signal())
    assert not result["allowed"]
    assert {c["key"] for c in result["checks"] if not c["passed"]} >= {"margin", "reconciliation", "risk"}
    assert result["margin"]["required"] is None
    assert result["margin"]["remaining"] is None
    engine.close()


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), datetime.now() + timedelta(hours=1)])
def test_invalid_or_future_tick_cannot_pass_freshness(timestamp):
    engine, gw = connected_engine()
    engine.configure_risk({"risk": {"max_market_data_age_seconds": 10}})
    engine.order_manager.market_data["A"] = {"timestamp": timestamp, "last_price": 100}
    assert not engine.send_signal(signal())
    assert not gw.sent_signals
    engine.close()


def test_preflight_api_requires_session_and_never_sends(monkeypatch):
    gateway = install_gateway(monkeypatch)
    body = dict(symbol="A", direction="long", offset="open", price=100, volume=1, order_type="limit")
    try:
        with TestClient(create_app()) as client:
            assert client.post("/orders/preflight", json=body).status_code == 401
            login(client)
            allow_market_orders(client)
            response = client.post("/orders/preflight", json=body)
            assert response.status_code == 200
            assert response.json()["allowed"] and response.json()["snapshot_only"]
            assert response.json()["order"] == body
            assert client.post("/orders/preflight", json={**body, "volume": 0}).status_code == 400
            assert not gateway.sent_signals
    finally:
        trading_state.clear_main()
