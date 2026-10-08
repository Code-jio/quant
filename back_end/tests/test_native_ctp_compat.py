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
