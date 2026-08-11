from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api import (
    _build_system_snapshot,
    _cancel_all_active_orders,
    _collect_all_trades,
    _install_hook_on_engine,
    app,
    trading_state,
)
from src.api.security import SESSION_COOKIE_NAME, session_store
from src.observability import AuditEventLog
from src.strategy import Direction, Order, OrderStatus, OrderType, Signal, Trade
from src.trading.engine import TradingEngine
from src.trading.gateway import GatewayBase
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


def test_cancel_all_uses_one_deadline_and_reports_only_broker_confirmed_as_cancelled(monkeypatch):
    orders = {
        order_id: Order(
            order_id=order_id, symbol="rb2505", direction=Direction.LONG,
            order_type=OrderType.LIMIT, price=100.0, volume=1, status=OrderStatus.SUBMITTED,
        )
        for order_id in ("OID-1", "OID-2")
    }
    timeouts = []

    class CancelEngine:
        gateway = SimpleNamespace(orders=orders)

        @staticmethod
        def cancel_order(_order_id):
            return True

        @staticmethod
        def wait_cancel_confirmation(order_id, *, timeout):
            timeouts.append(timeout)
            return {
                "requested": True,
                "confirmed": order_id == "OID-1",
                "pending": order_id == "OID-2",
                "failed": False,
            }

    ticks = iter((100.0, 101.0, 102.0))
    monkeypatch.setattr("src.api.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(trading_state, "primary_engine", lambda: CancelEngine())
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])

    result = _cancel_all_active_orders(timeout_seconds=5.0)

    assert result["requested"] == 2
    assert result["confirmed"] == 1
    assert result["cancelled"] == 1
    assert result["pending"] == 1
    assert result["failed"] == 0
    assert timeouts == [4.0, 3.0]


def test_trade_collection_includes_primary_live_gateway_without_any_strategy(monkeypatch):
    live_trade = SimpleNamespace(
        trade_id="T-LIVE-1", order_id="OID-1", symbol="rb2505",
        direction=Direction.LONG, price=100.0, volume=1, commission=1.0, pnl=0.0,
        trade_time=__import__("datetime").datetime.now(),
    )
    gateway = SimpleNamespace(query_trades=lambda: [live_trade])
    monkeypatch.setattr(trading_state, "primary_engine", lambda: SimpleNamespace(gateway=gateway))
    monkeypatch.setattr(trading_state, "all_entries", lambda: [])

    assert [trade["trade_id"] for trade in _collect_all_trades()] == ["T-LIVE-1"]


def test_live_gateway_audit_persists_complete_order_and_trade_facts(tmp_path, monkeypatch):
    """Broker callbacks must leave restart-readable facts for live order review."""
    import src.api as api_module

    log = AuditEventLog(persistence_dir=tmp_path)
    monkeypatch.setattr(api_module, "audit_log", log)
    gateway = SimpleNamespace(
        name="VNPY_CTP",
        trading_day="2026-08-12",
        on_order_callback=None,
        on_trade_callback=None,
    )
    _install_hook_on_engine(SimpleNamespace(gateway=gateway))
    order = Order(
        order_id="OID-1", symbol="rb2505", direction=Direction.LONG,
        order_type=OrderType.LIMIT, price=3880.0, volume=2,
        status=OrderStatus.REJECTED, error_msg="ErrorID=31 insufficient funds",
    )
    trade = Trade(
        trade_id="T-1", order_id="OID-1", symbol="rb2505", direction=Direction.LONG,
        price=3880.0, volume=2, commission=1.2, pnl=3.4, trade_time=datetime.now(),
    )

    gateway.on_order_callback(order)
    gateway.on_trade_callback(trade)

    restored = AuditEventLog(persistence_dir=tmp_path)
    events = restored.query(limit=10)
    details = {event["event_type"]: event["detail"] for event in events}
    assert details["order"] == {
        "order_id": "OID-1", "symbol": "rb2505", "status": "rejected",
        "direction": "long", "price": 3880.0, "volume": 2,
        "error_msg": "ErrorID=31 insufficient funds", "trading_day": "2026-08-12",
    }
    assert details["trade"] == {
        "trade_id": "T-1", "order_id": "OID-1", "symbol": "rb2505",
        "direction": "long", "price": 3880.0, "volume": 2,
        "error_msg": "", "trading_day": "2026-08-12",
    }


def test_live_audit_persistence_failure_emergency_stops_future_order_submission(monkeypatch):
    """A live audit write failure is fail-closed: callbacks arm the existing risk gate."""
    import src.api as api_module

    class RecordingGateway(GatewayBase):
        def __init__(self):
            super().__init__("VNPY_CTP")
            self.status = TradingStatus.CONNECTED
            self.sent = []

        def connect(self, _config):
            return True

        def disconnect(self):
            return None

        def send_order(self, signal):
            self.sent.append(signal)
            return "OID-SENT"

        def cancel_order(self, _order_id):
            return True

        def query_account(self):
            return self.account

        def query_positions(self):
            return []

        def query_orders(self):
            return []

    class FailingAuditLog:
        @staticmethod
        def record(*_args, **_kwargs):
            raise OSError("audit disk full")

    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    monkeypatch.setattr(api_module, "audit_log", FailingAuditLog())
    _install_hook_on_engine(engine)
    gateway.on_order(Order(
        order_id="OID-AUDIT", symbol="rb2505", direction=Direction.LONG,
        order_type=OrderType.LIMIT, price=3880.0, volume=1, status=OrderStatus.SUBMITTED,
    ))

    assert engine.risk_manager.emergency_stop is True
    assert "audit" in engine.risk_manager.emergency_reason.lower()
    assert engine.send_signal(Signal(
        symbol="rb2505", datetime=datetime.now(), direction=Direction.LONG, price=3880.0, volume=1,
        order_type=OrderType.LIMIT,
    )) == ""
    assert gateway.sent == []
