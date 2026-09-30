"""Concurrency and retry contracts for live CTP reconciliation."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from src.trading.types import TradingStatus
from src.trading.vnpy_gateway import VnpyGateway


def _transport_ready_gateway() -> VnpyGateway:
    gateway = VnpyGateway()
    gateway.status = TradingStatus.CONNECTED
    gateway._td_connected = True
    gateway._md_connected = True
    gateway._contracts_ready = True
    return gateway


def _complete(gateway: VnpyGateway, kind: str, *, item_count: int = 0) -> None:
    request_id = 1
    gateway._arm_reconciliation_request(kind, request_id)
    gateway._on_reconciliation_event(
        SimpleNamespace(
            data={
                "kind": kind,
                "request_id": request_id,
                "error_id": 0,
                "error_msg": "",
                "item_count": item_count,
            }
        )
    )


def _broker_snapshot(
    gateway: VnpyGateway,
    calls: dict[str, int],
    release: threading.Event | None = None,
    started: threading.Event | None = None,
):
    class BrokerSnapshot:
        def query_orders_snapshot(self):
            calls["orders"] += 1
            if started is not None:
                started.set()
            if release is not None:
                release.wait(timeout=1.0)
            _complete(gateway, "orders")
            return 0

        def query_trades_snapshot(self):
            calls["trades"] += 1
            _complete(gateway, "trades")
            return 0

        def query_positions_snapshot(self):
            calls["positions"] += 1
            _complete(gateway, "positions")
            return 0

        def query_account_snapshot(self):
            calls["account"] += 1
            gateway._on_vnpy_account(
                SimpleNamespace(
                    data=SimpleNamespace(
                        accountid="ACC001",
                        balance=500000,
                        available=480000,
                        frozen=20000,
                    )
                )
            )
            _complete(gateway, "account", item_count=1)
            return 0

    return BrokerSnapshot()


def test_concurrent_reconciliation_calls_share_one_broker_snapshot():
    gateway = _transport_ready_gateway()
    calls = {kind: 0 for kind in ("orders", "trades", "positions", "account")}
    release = threading.Event()
    started = threading.Event()
    gateway._main_engine = SimpleNamespace(
        get_gateway=lambda _name: _broker_snapshot(gateway, calls, release, started)
    )

    results: list[dict[str, object]] = []
    first = threading.Thread(
        target=lambda: results.append(gateway.refresh_reconciliation(timeout_seconds=1.0))
    )
    second = threading.Thread(
        target=lambda: results.append(gateway.refresh_reconciliation(timeout_seconds=1.0))
    )
    first.start()
    # The first query is intentionally held so the second call overlaps it.
    assert started.wait(timeout=1.0)
    second.start()
    release.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == {"orders": 1, "trades": 1, "positions": 1, "account": 1}
    assert [result["ok"] for result in results] == [True, True]
    assert gateway.connection_snapshot()["order_entry_ready"] is True


def test_recent_successful_reconciliation_is_cached_without_closing_order_entry_gate():
    gateway = _transport_ready_gateway()
    calls = {kind: 0 for kind in ("orders", "trades", "positions", "account")}
    gateway._main_engine = SimpleNamespace(
        get_gateway=lambda _name: _broker_snapshot(gateway, calls)
    )

    first = gateway.refresh_reconciliation(timeout_seconds=0.5)
    assert first["ok"] is True
    assert gateway.connection_snapshot()["order_entry_ready"] is True

    second = gateway.refresh_reconciliation(timeout_seconds=0.5)

    assert second == first
    assert calls == {"orders": 1, "trades": 1, "positions": 1, "account": 1}
    assert gateway.connection_snapshot()["order_entry_ready"] is True

    gateway.refresh_reconciliation_for_resume(timeout_seconds=0.5)

    assert calls == {"orders": 2, "trades": 2, "positions": 2, "account": 2}


def test_recovery_retries_with_backoff_then_completes_once_without_real_sleep(monkeypatch):
    gateway = _transport_ready_gateway()
    gateway.status = TradingStatus.ERROR
    gateway._connection_outage = True
    gateway._reconnecting = True
    gateway._connection_generation = 7
    gateway._main_engine = object()
    gateway._reconciliation_retry_attempts = 3
    gateway._reconciliation_retry_base_delay_seconds = 0.1
    delays: list[float] = []
    gateway._reconciliation_sleep = delays.append
    attempts = 0

    def snapshot(_timeout_seconds):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {"ok": False, "fresh": False, "failure_code": "broker_snapshot_timeout"}
        return {"ok": True, "fresh": True, "failure_code": ""}

    monkeypatch.setattr(gateway, "_refresh_reconciliation_snapshot", snapshot)

    gateway._run_recovery_reconciliation(7)
    gateway._run_recovery_reconciliation(7)

    assert attempts == 3
    assert delays == [0.1, 0.2]
    assert gateway.connection_snapshot()["reconnecting"] is False
    assert gateway.connection_snapshot()["reconnect_count"] == 1


def test_recovery_schedules_later_batches_with_capped_backoff_until_success(monkeypatch):
    gateway = _transport_ready_gateway()
    gateway.status = TradingStatus.ERROR
    gateway._connection_outage = True
    gateway._reconnecting = True
    gateway._connection_generation = 11
    gateway._main_engine = object()
    gateway._reconciliation_retry_attempts = 2
    gateway._reconciliation_retry_base_delay_seconds = 0.1
    gateway._reconciliation_retry_max_delay_seconds = 0.2
    delays: list[float] = []
    gateway._reconciliation_sleep = delays.append
    attempts = 0

    def snapshot(_timeout_seconds):
        nonlocal attempts
        attempts += 1
        if attempts < 5:
            return {"ok": False, "fresh": False, "failure_code": "broker_snapshot_timeout"}
        return {"ok": True, "fresh": True, "failure_code": ""}

    monkeypatch.setattr(gateway, "_refresh_reconciliation_snapshot", snapshot)

    gateway._run_recovery_reconciliation(11)

    # Two failures exhaust the first batch, but the same outage generation
    # continues with later batches.  The wait is capped, so it cannot grow
    # without bound during a prolonged broker-side outage.
    assert attempts == 5
    assert delays == [0.1, 0.2, 0.2, 0.2]
    assert gateway.connection_snapshot()["reconnecting"] is False
    assert gateway.connection_snapshot()["reconnect_count"] == 1


def test_recovery_stops_retrying_when_its_generation_is_replaced(monkeypatch):
    gateway = _transport_ready_gateway()
    gateway.status = TradingStatus.ERROR
    gateway._connection_outage = True
    gateway._reconnecting = True
    gateway._connection_generation = 3
    gateway._main_engine = object()
    gateway._reconciliation_retry_attempts = 3
    attempts = 0
    delays: list[float] = []
    gateway._reconciliation_sleep = delays.append

    def snapshot(_timeout_seconds):
        nonlocal attempts
        attempts += 1
        with gateway._connection_lock:
            gateway._connection_generation += 1
            gateway._main_engine = None
        return {"ok": False, "fresh": False, "failure_code": "broker_connection_changed"}

    monkeypatch.setattr(gateway, "_refresh_reconciliation_snapshot", snapshot)

    gateway._run_recovery_reconciliation(3)

    assert attempts == 1
    assert delays == []
    assert gateway.connection_snapshot()["order_entry_ready"] is False


def test_recovery_stops_after_disconnect_during_retry_wait(monkeypatch):
    gateway = _transport_ready_gateway()
    gateway.status = TradingStatus.ERROR
    gateway._connection_outage = True
    gateway._reconnecting = True
    gateway._connection_generation = 13
    gateway._main_engine = SimpleNamespace(close=lambda: None)
    gateway._reconciliation_retry_attempts = 1
    attempts = 0
    delays: list[float] = []

    def snapshot(_timeout_seconds):
        nonlocal attempts
        attempts += 1
        return {"ok": False, "fresh": False, "failure_code": "broker_snapshot_timeout"}

    def sleep_then_disconnect(delay_seconds):
        delays.append(delay_seconds)
        gateway.disconnect()

    gateway._reconciliation_sleep = sleep_then_disconnect
    monkeypatch.setattr(gateway, "_refresh_reconciliation_snapshot", snapshot)

    gateway._run_recovery_reconciliation(13)

    assert attempts == 1
    assert delays == [0.25]
    assert gateway.status == TradingStatus.STOPPED
