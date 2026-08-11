from types import SimpleNamespace

from src.api import _build_system_snapshot, trading_state
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
    assert snapshot["order_entry_ready"] is False
