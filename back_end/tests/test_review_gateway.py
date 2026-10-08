from types import SimpleNamespace as NS

from src.trading.vnpy_gateway import VnpyGateway
from src.trading.types import TradingStatus
from src.strategy import Direction, Position


def test_log_or_account_event_cannot_claim_gateway_ready():
    gw = VnpyGateway()
    gw.status = TradingStatus.CONNECTING
    gw._on_vnpy_log(NS(data=NS(msg="结算信息确认成功")))
    assert not gw._connected_event.is_set()
    gw._on_vnpy_account(NS(data=NS(accountid="a", balance=100, available=90, frozen=7)))
    assert not gw.ready
    assert gw.account.margin == 0 and not gw.account.fields_known


def test_account_fields_come_from_broker_extras():
    gw = VnpyGateway()
    gw._on_vnpy_account(
        NS(
            data=NS(
                accountid="a",
                balance=100,
                available=70,
                frozen=5,
                extra={
                    "Available": 70,
                    "CurrMargin": 25,
                    "Commission": 2,
                    "PositionProfit": 3,
                    "CloseProfit": 4,
                    "TradingDay": "20261009",
                },
            )
        )
    )
    assert (gw.account.margin, gw.account.commission, gw.account.position_pnl, gw.account.total_pnl) == (25, 2, 3, 7)
    assert gw.account.fields_known and gw.account.trading_day == "20261009"


def test_empty_position_query_clears_old_positions():
    gw = VnpyGateway()
    gw.positions["rb_long"] = Position("rb", Direction.LONG, 3)
    gw._on_snapshot(NS(data={"kind": "positions", "keys": []}))
    assert gw.positions == {}
    assert gw.connection_state["positions"]


def test_disconnect_invalidates_snapshots_and_md_td_separately():
    gw = VnpyGateway()
    gw.connection_state.update(md=True, td=True, settlement=True, contracts=True, account=True, positions=True)
    gw._on_snapshot(NS(data={"kind": "td", "ready": False}))
    assert gw.connection_state["md"] and not gw.connection_state["td"]
    assert not gw.connection_state["positions"] and not gw.ready


def test_unknown_symbol_rejected_before_vnpy_import():
    gw = VnpyGateway()
    import pytest

    with pytest.raises(ValueError, match="contract"):
        gw._split_symbol("not-a-contract")


def test_native_observer_reports_empty_query_completion_and_login():
    from src.trading.ctp_observer import install_observer

    methods = [
        "onFrontDisconnected",
        "onRspUserLogin",
        "onRspSettlementInfoConfirm",
        "onRspQryInstrument",
        "onRspQryTradingAccount",
        "onRspQryInvestorPosition",
    ]
    gateway = NS(
        td_api=NS(reqOrderAction=lambda *_: 0, **{key: lambda *args: None for key in methods}),
        md_api=NS(**{key: lambda *args: None for key in methods}),
        on_account=lambda *_: None,
        on_position=lambda *_: None,
        on_trade=lambda *_: None,
        on_tick=lambda *_: None,
    )
    events: list[dict] = []
    install_observer(gateway, events.append)
    gateway.td_api.onRspUserLogin({"TradingDay": "20261009"}, {}, 1, True)
    gateway.td_api.onRspQryInvestorPosition({}, {}, 2, True)
    assert events == [
        {"kind": "td", "ready": True, "trading_day": "20261009"},
        {"kind": "positions", "keys": [], "ready": True},
    ]
    gateway.td_api.onFrontDisconnected(7)
    assert events[-1] == {"kind": "td", "ready": False}
