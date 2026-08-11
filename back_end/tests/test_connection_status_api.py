from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api import _build_system_snapshot, app, trading_state
from src.api.security import SESSION_COOKIE_NAME, session_store
from src.strategy import Direction, Order, OrderStatus, OrderType
from src.trading.types import AccountInfo, TradingStatus


def test_system_snapshot_uses_independent_td_and_md_connection_states(monkeypatch):
    class GatewayWithIndependentChannels:
        name = "VNPY_CTP"
        status = TradingStatus.CONNECTED

        def connection_snapshot(self):
            return {
                "td_connected": True,
                "md_connected": False,
                "fully_connected": False,
                "contracts_ready": False,
                "reconciliation_ready": False,
                "trading_day": "2026-08-12",
                "order_entry_ready": False,
                "reconnecting": True,
                "reconnect_count": 2,
                "last_disconnect_reason": "market server disconnected",
                "changed_at": "2026-08-11T09:30:00",
            }

    engine = SimpleNamespace(
        gateway=GatewayWithIndependentChannels(),
        get_account=lambda: AccountInfo(account_id="TEST", balance=100000.0, available=100000.0),
    )
    monkeypatch.setattr(trading_state, "primary_engine", lambda: engine)
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])
    monkeypatch.setattr("src.api._get_network_speed", lambda: (0.0, 0.0))

    snapshot = _build_system_snapshot()

    assert snapshot["td_connected"] is True
    assert snapshot["md_connected"] is False
    assert snapshot["market_connected"] is False
    assert snapshot["contracts_ready"] is False
    assert snapshot["reconciliation_ready"] is False
    assert snapshot["trading_day"] == "2026-08-12"
    assert snapshot["order_entry_ready"] is False


def test_system_snapshot_defaults_to_order_entry_closed_without_a_gateway_snapshot(monkeypatch):
    engine = SimpleNamespace(
        gateway=SimpleNamespace(name="VNPY_CTP", status=TradingStatus.CONNECTED),
        get_account=lambda: AccountInfo(account_id="TEST", balance=100000.0, available=100000.0),
    )
    monkeypatch.setattr(trading_state, "primary_engine", lambda: engine)
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])
    monkeypatch.setattr("src.api._get_network_speed", lambda: (0.0, 0.0))

    snapshot = _build_system_snapshot()

    assert snapshot["contracts_ready"] is False
    assert snapshot["reconciliation_ready"] is False
    assert snapshot["trading_day"] == ""
    assert snapshot["order_entry_ready"] is False


def test_system_status_rest_exposes_live_order_entry_gates(monkeypatch):
    class GatewayWithLiveGates:
        name = "VNPY_CTP"
        status = TradingStatus.CONNECTED
        orders = {}

        def connection_snapshot(self):
            return {
                "td_connected": True,
                "md_connected": True,
                "fully_connected": True,
                "contracts_ready": True,
                "reconciliation_ready": True,
                "trading_day": "2026-08-12",
                "order_entry_ready": True,
                "reconnecting": False,
                "reconnect_count": 0,
                "last_disconnect_reason": "",
                "changed_at": "2026-08-12T09:30:00",
            }

    engine = SimpleNamespace(
        gateway=GatewayWithLiveGates(),
        get_account=lambda: AccountInfo(account_id="TEST", balance=100000.0, available=100000.0),
    )
    monkeypatch.setattr(trading_state, "primary_engine", lambda: engine)
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])
    monkeypatch.setattr("src.api._get_network_speed", lambda: (0.0, 0.0))

    token = session_store.create()
    try:
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, token)
            response = client.get("/system/status")
    finally:
        session_store.revoke(token)

    assert response.status_code == 200
    payload = response.json()
    assert payload["contracts_ready"] is True
    assert payload["reconciliation_ready"] is True
    assert payload["trading_day"] == "2026-08-12"
    assert payload["order_entry_ready"] is True


def test_cancel_order_rest_does_not_report_success_when_only_the_request_was_sent(monkeypatch):
    pending_order = Order(
        order_id="OID-1", symbol="rb2505", direction=Direction.LONG,
        order_type=OrderType.LIMIT, price=100.0, volume=1, status=OrderStatus.SUBMITTED,
    )
    gateway = SimpleNamespace(orders={"OID-1": pending_order})
    engine = SimpleNamespace(gateway=gateway, cancel_order=lambda _order_id: True)
    monkeypatch.setattr(trading_state, "primary_engine", lambda: engine)
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])
    token = session_store.create()
    try:
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, token)
            response = client.delete("/orders/OID-1")
    finally:
        session_store.revoke(token)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["pending"] is True
    assert payload["confirmed"] is False
