from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from src.trading.ctp_reconcile import BrokerReconciliation
from src.trading.contract_state import trading_state
from src.trading.recovery import resolve_intent, report
from src.trading.ledger import ExecutionLedger
from src.trading.order_manager import OrderManager, PreOrder, PreOrderStatus, PreOrderType
from src.strategy import Direction, OrderStatus
from test_review_trading import connected_engine, signal


def row(**kw):
    return dict(InstrumentID="A", FrontID=1, SessionID=2, OrderRef="3", OrderSysID="SYS", ExchangeID="SHFE",
                OrderStatus="3", Direction="0", CombOffsetFlag="0", LimitPrice=100,
                VolumeTotalOriginal=1, VolumeTraded=0, OrderPriceType="2", InsertDate="20261019", InsertTime="10:00:00", **kw)


def complete(book, orders=()):
    td = NS(reqid=0, userid="fixture", brokerid="fixture", reqQryOrder=lambda *_: 0, reqQryTrade=lambda *_: 0)
    book.start("20261019")
    book.dispatch(td, "20261019")
    for data in orders:
        book.on_response(dict(query="orders", reqid=1, data=data, last=False), "20261019")
    book.on_response(dict(query="orders", reqid=1, data={}, last=True), "20261019")
    assert book.snapshot("20261019")["status"] == "pending"
    book.dispatch(td, "20261019")
    book.on_response(dict(query="trades", reqid=2, data={}, last=True), "20261019")
    return book.snapshot("20261019")


def test_full_query_requires_both_completions_and_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.trading.ctp_reconcile.time.monotonic", lambda: now[0])
    book = BrokerReconciliation()
    result = complete(book, [row(BrokerID="fixture", InvestorID="fixture")])
    assert result["status"] == "ready"
    assert "InvestorID" not in str(result)
    assert result["orders"][0]["order_id"] == "CTP.1_2_3"
    now[0] += 31
    assert book.snapshot("20261019")["status"] == "stale"


def test_query_error_wrong_request_and_day_never_complete():
    book = BrokerReconciliation()
    book.start("20261019")
    td = NS(reqid=5, userid="fixture", brokerid="fixture", reqQryOrder=lambda *_: 0)
    book.dispatch(td, "20261019")
    book.on_response(dict(query="orders", reqid=5, last=True), "20261019")
    assert not book.result["completed"]
    book.on_response(dict(query="orders", reqid=6, last=True, error_id=7), "20261019")
    assert book.snapshot("20261019")["status"] == "stale"
    complete(book)
    assert book.snapshot("20261020")["status"] == "stale"


def test_unknown_execution_requires_matching_evidence_and_stays_stopped():
    engine, gw = connected_engine()
    try:
        gw.ready, gw.trading_day = True, "20261019"
        gw.reconciliation = BrokerReconciliation()
        identity = engine.ledger.begin_intent(signal(), gw.trading_day)
        snapshot = complete(gw.reconciliation, [row()])
        assert not report(engine)["reconciled"]
        with pytest.raises(ValueError, match="急停"):
            resolve_intent(engine, identity, snapshot["id"], "CTP.1_2_3", "柜台订单核验凭证 FIXTURE-001")
        engine.set_emergency_stop(True, "review")
        with pytest.raises(ValueError, match="不一致"):
            resolve_intent(engine, identity, snapshot["id"], "MISSING", "柜台订单核验凭证 FIXTURE-001")
        resolve_intent(engine, identity, snapshot["id"], "CTP.1_2_3", "柜台订单核验凭证 FIXTURE-001")
        assert not engine.ledger.unresolved()
        assert engine.risk_manager.emergency_stop
        assert gw.orders["CTP.1_2_3"].status == OrderStatus.SUBMITTED
        assert not gw.sent_signals
        assert engine.ledger.db.execute("SELECT count(*) FROM resolutions").fetchone()[0] == 1
        assert report(engine)["reconciled"]
    finally:
        engine.close()


def test_empty_query_is_not_enough_to_clear_recent_or_historical_intent():
    engine, gw = connected_engine()
    try:
        gw.ready, gw.trading_day = True, "20261019"
        gw.reconciliation = BrokerReconciliation()
        engine.set_emergency_stop(True, "review")
        identity = engine.ledger.begin_intent(signal(), gw.trading_day)
        snapshot = complete(gw.reconciliation)
        with pytest.raises(ValueError, match="查询过早"):
            resolve_intent(engine, identity, snapshot["id"], "", "人工核对柜台凭证 FIXTURE-EMPTY")
        old = engine.ledger.begin_intent(signal(), "20261018")
        with pytest.raises(ValueError, match="历史"):
            resolve_intent(engine, old, snapshot["id"], "", "人工核对柜台凭证 FIXTURE-EMPTY")
        snapshot["requested_at"] = (datetime.now().astimezone() + timedelta(seconds=35)).isoformat()
        gw.reconciliation.result = snapshot
        resolve_intent(engine, identity, snapshot["id"], "", "人工核对柜台凭证 FIXTURE-EMPTY")
        assert len(engine.ledger.unresolved()) == 1
        assert not gw.sent_signals
    finally:
        engine.close()


def test_conditional_restart_is_paused_and_preserves_trailing_extreme(tmp_path):
    ledger = ExecutionLedger(str(tmp_path / "conditions.db"), "fixture")
    manager = OrderManager(NS())
    manager.attach_ledger(ledger)
    order = PreOrder(PreOrderType.TRAILING_STOP, "A", Direction.SHORT, 1, 95, trailing_percent=5)
    identity = manager.place_pre_order(order)
    manager._should_trigger(order, 120)
    restored = OrderManager(NS())
    restored.attach_ledger(ledger)
    assert not restored.active_pre_orders
    saved = restored.pre_orders[identity]
    assert saved.status == PreOrderStatus.PAUSED
    assert (saved.trail_best_price, saved.trigger_price) == (120, 114)
    restored.resume_pre_order(identity)
    assert identity in restored.active_pre_orders
    saved.status = PreOrderStatus.TRIGGERING
    ledger.save_pre_order(saved)
    crashed = OrderManager(NS())
    crashed.attach_ledger(ledger)
    assert crashed.pre_orders[identity].status == PreOrderStatus.UNKNOWN
    with pytest.raises(ValueError):
        crashed.resume_pre_order(identity)
    ledger.close()


def test_conditional_persistence_failure_cannot_dispatch_or_retry(monkeypatch):
    engine, gw = connected_engine()
    try:
        order = PreOrder(PreOrderType.STOP_ENTRY, "A", Direction.LONG, 1, 100)
        identity = engine.place_pre_order(order)
        def fail(_):
            raise OSError("fixture disk failure")
        monkeypatch.setattr(engine.ledger, "save_pre_order", fail)
        with pytest.raises(OSError):
            engine.order_manager._trigger_pre_order(order)
        assert identity not in engine.order_manager.active_pre_orders
        assert not gw.sent_signals
        assert engine.risk_manager.emergency_stop
    finally:
        engine.close()


@pytest.mark.parametrize("metadata,day,expected", [
    ({}, "20261019", None),
    ({"IsTrading": False}, "20261019", False),
    ({"IsTrading": True, "OpenDate": "20260101", "ExpireDate": "20261018"}, "20261019", False),
    ({"IsTrading": True, "OpenDate": "20260101", "ExpireDate": "20261231"}, "20261019", True),
    ({"IsTrading": True, "OpenDate": "20260101", "ExpireDate": "invalid"}, "20261019", None),
])
def test_lifecycle_unknown_or_expired_is_not_certified(metadata, day, expected):
    assert trading_state(metadata, day)["tradable"] is expected


def test_recovery_endpoints_require_session_generation_stop_and_evidence(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state as state
    from test_manual_trading import install_gateway, login
    gateway = install_gateway(monkeypatch)
    try:
        with TestClient(create_app()) as client:
            assert client.post("/trading/reconcile/query").status_code == 401
            login(client)
            gateway.ready, gateway.trading_day = True, "20261019"
            gateway.reconciliation = BrokerReconciliation()
            snapshot = complete(gateway.reconciliation)
            body = {"generation": state.generation - 1, "snapshot_id": snapshot["id"], "note": "柜台完整快照人工核验 FIXTURE-TEST"}
            assert client.post("/trading/reconcile/import", json=body).status_code == 409
            body["generation"] = state.generation
            assert client.post("/trading/reconcile/import", json=body).status_code == 409
            state.primary_engine().set_emergency_stop(True, "review")
            assert client.post("/trading/reconcile/import", json=body).status_code == 200
            assert state.primary_engine().risk_manager.emergency_stop
            assert not gateway.sent_signals
    finally:
        state.clear_main()
