"""Offline regression cases from the October reliability review."""
from types import SimpleNamespace as NS

import pytest

from src.trading.risk import RiskManager
from src.trading.types import AccountInfo
from src.trading.vnpy_gateway import VnpyGateway


def account_event(**extra):
    return NS(data=NS(accountid="FIXTURE", balance=100000, extra={
        "Available": 100000, "CurrMargin": 0, "Commission": 0,
        "PositionProfit": 0, "CloseProfit": 0, "TradingDay": "20261019",
        "PreBalance": 100000, "Deposit": 0, "Withdraw": 0, **extra,
    }))


def test_positions_cannot_keep_old_account_ready(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("src.trading.vnpy_gateway.time.monotonic", lambda: now[0])
    gw = VnpyGateway()
    gw.connection_state = dict.fromkeys(gw.connection_state, True)
    gw._on_vnpy_account(account_event())
    gw._on_snapshot(NS(data={"kind": "positions", "keys": []}))
    assert gw.ready
    now[0] += 61
    gw._on_snapshot(NS(data={"kind": "positions", "keys": []}))
    assert not gw.ready
    gw._on_vnpy_account(account_event())
    assert gw.ready
    gw._on_snapshot(NS(data={"kind": "account", "ready": False}))
    assert not gw.ready


@pytest.mark.parametrize("balance,deposit,withdraw,allowed", [
    (90000, 0, 10000, True), (110000, 20000, 0, False),
    (95000, 0, 0, True), (80000, 0, 0, False),
])
def test_broker_daily_loss_excludes_cashflows(balance, deposit, withdraw, allowed):
    risk = RiskManager({"require_account": True, "max_daily_loss_ratio": .1})
    risk.set_day_open_balance(balance)  # An intraday first login is not the day's baseline.
    account = AccountInfo(balance=balance, source="ctp", pre_balance=100000,
                          deposit=deposit, withdraw=withdraw)
    assert risk._check_daily_loss(account).allowed is allowed


@pytest.mark.parametrize("fields", [{}, {"pre_balance": float("nan"), "deposit": 0, "withdraw": 0},
                                    {"pre_balance": 0, "deposit": 100000, "withdraw": 0}])
def test_native_daily_loss_never_guesses_missing_baseline(fields):
    risk = RiskManager({"require_account": True})
    risk.set_day_open_balance(100000)
    assert not risk._check_daily_loss(AccountInfo(balance=100000, source="ctp", **fields)).allowed


def test_adapter_preserves_cashflow_fields():
    gw = VnpyGateway()
    gw._on_vnpy_account(account_event(Deposit=2000, Withdraw=1000))
    assert (gw.account.pre_balance, gw.account.deposit, gw.account.withdraw) == (100000, 2000, 1000)
    assert gw.account.source == "ctp"


def test_rollover_invalidates_positions_and_rechecks_contract_expiry(monkeypatch):
    from src.strategy import OffsetFlag
    from test_review_trading import signal
    gw = VnpyGateway()
    gw.contract_specs["A"] = {"source": "ctp", "size": 10, "pricetick": 1, "min_volume": 1,
                              "is_trading": True, "listed_date": "20260101", "expiry_date": "20261019", "tradable": True}
    gw._on_vnpy_account(account_event())
    gw._on_snapshot(NS(data={"kind": "positions", "keys": []}))
    gw._on_vnpy_account(account_event(TradingDay="20261020", PreBalance=90000))
    assert not gw.connection_state["positions"]
    assert gw.account.pre_balance == 90000
    with pytest.raises(ValueError, match="inactive"):
        gw.validate_order(signal(offset=OffsetFlag.OPEN))


def test_reservation_is_removed_only_after_a_query_started_after_broker_ack():
    from src.strategy import OrderStatus
    gw = VnpyGateway()
    order = NS(order_id="CTP.1", status=OrderStatus.SUBMITTED)
    account = AccountInfo(query_started_at=200)
    assert gw.needs_margin_reservation(order, account)
    gw._broker_order_clocks[order.order_id] = 150
    assert not gw.needs_margin_reservation(order, account)
    gw._broker_order_clocks[order.order_id] = 210  # Fill/order update after that query.
    assert gw.needs_margin_reservation(order, account)
    account.query_started_at = 220
    assert not gw.needs_margin_reservation(order, account)
    order.status = OrderStatus.SUBMITTING
    assert gw.needs_margin_reservation(order, account)


def test_reconcile_queries_are_not_starved_by_dashboard_refresh(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.trading.vnpy_gateway.time.monotonic", lambda: now[0])
    gw = VnpyGateway()
    gw.connection_state.update(td=True, contracts=True)
    gw.trading_day = "20261019"
    calls = []
    def record(kind):
        calls.append(kind)
        return 0
    native = NS(td_api=NS(reqid=0, userid="fixture", brokerid="fixture",
                         reqQryOrder=lambda *_: record("orders"),
                         reqQryTrade=lambda *_: record("trades")),
                md_api=NS(update_date=lambda: None), query_account=lambda: calls.append("account"))
    gw._main_engine = NS(get_gateway=lambda _: native)
    gw.reconciliation.start(gw.trading_day)
    gw._on_timer(None)
    assert calls == ["orders"]
    gw._on_snapshot(NS(data={"kind": "reconcile", "query": "orders", "reqid": 1, "last": True}))
    now[0] += 2
    gw._on_timer(None)
    assert calls == ["orders", "trades"]
