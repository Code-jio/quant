"""Exercise the installed native package without connecting or submitting orders."""
import pytest

from src.trading.ctp_observer import install_observer


@pytest.fixture
def native_gateway(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".vntrader").mkdir()
    pytest.importorskip("vnpy")
    native = pytest.importorskip("vnpy_ctp")
    from vnpy.event import EventEngine

    return native.CtpGateway(EventEngine(), "NATIVE_FIXTURE")


@pytest.mark.parametrize("environment,production", [("实盘", True), ("测试", False)])
def test_native_package_maps_environment_to_both_api_factories(native_gateway, monkeypatch, environment, production):
    captured: dict[str, tuple] = {}
    monkeypatch.setattr(native_gateway.td_api, "connect", lambda *args: captured.update(td=args))
    monkeypatch.setattr(native_gateway.md_api, "connect", lambda *args: captured.update(md=args))
    native_gateway.connect({
        "用户名": "fixture", "密码": "fixture", "经纪商代码": "fixture",
        "交易服务器": "tcp://127.0.0.1:1", "行情服务器": "tcp://127.0.0.1:2",
        "产品名称": "fixture", "授权编码": "fixture", "柜台环境": environment,
    })
    assert captured["td"][-1] is production
    assert captured["md"][-1] is production


def test_observer_wraps_installed_native_callbacks(native_gateway):
    events: list[dict] = []
    install_observer(native_gateway, events.append)
    native_gateway.td_api.onRspQryInvestorPosition({"InstrumentID": ""}, {"ErrorID": 0}, 1, True)
    native_gateway.td_api.onFrontDisconnected(7)
    native_gateway.md_api.onFrontDisconnected(8)
    assert events == [
        {"kind": "positions", "keys": [], "ready": True},
        {"kind": "td", "ready": False},
        {"kind": "md", "ready": False},
    ]


def test_observer_captures_installed_margin_callbacks(native_gateway):
    events: list[dict] = []
    install_observer(native_gateway, events.append)
    payload = {"InstrumentID": "au2612", "LongMarginRatioByMoney": .12}
    native_gateway.td_api.onRspQryInstrumentMarginRate(payload, {"ErrorID": 0}, 8, False)
    native_gateway.td_api.onRspQryExchangeMarginRate({}, {"ErrorID": 7}, 9, True)
    assert events == [
        {"kind": "margin", "query": "instrument", "data": payload, "error_id": 0, "reqid": 8, "last": False},
        {"kind": "margin", "query": "exchange", "data": {}, "error_id": 7, "reqid": 9, "last": True},
    ]


def test_native_observer_preserves_instrument_lifecycle(native_gateway):
    contracts: list = []
    native_gateway.on_contract = contracts.append
    install_observer(native_gateway, lambda _: None)
    native_gateway.td_api.onRspQryInstrument({
        "ProductClass": "1", "InstrumentID": "au2612", "ExchangeID": "SHFE", "InstrumentName": "fixture",
        "VolumeMultiple": 1000, "PriceTick": .02, "MinLimitOrderVolume": 1, "MaxLimitOrderVolume": 500,
        "IsTrading": True, "OpenDate": "20260101", "ExpireDate": "20261215",
    }, {}, 1, False)
    assert contracts[0].extra == {"IsTrading": True, "OpenDate": "20260101", "ExpireDate": "20261215"}


def test_account_query_failure_and_reconcile_callbacks_are_structured(native_gateway):
    events: list[dict] = []
    install_observer(native_gateway, events.append)
    native_gateway.td_api.onRspQryTradingAccount({}, {"ErrorID": 7}, 1, True)
    native_gateway.td_api.onRspQryOrder({}, {}, 2, True)
    native_gateway.td_api.onRspQryTrade({}, {}, 3, True)
    assert events[0] == {"kind": "account", "ready": False}
    assert [event["query"] for event in events[1:]] == ["orders", "trades"]
