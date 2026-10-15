"""Installed CTP conversion/callback tests; every native send is intercepted.

No API is created, no network connection is opened, and prices are fixture data.
"""
from datetime import datetime
import time
from types import SimpleNamespace as NS

import pytest

from src.strategy import Direction, OffsetFlag, OrderStatus, OrderType, Signal
from src.trading.ctp_observer import install_observer
from src.trading.types import MarketData
from src.trading.vnpy_gateway import VnpyGateway


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".vntrader").mkdir()
    pytest.importorskip("vnpy")
    pytest.importorskip("vnpy_ctp")
    from vnpy.event import EventEngine
    from vnpy.trader.constant import Exchange, Product
    from vnpy.trader.object import ContractData
    from vnpy_ctp.gateway import ctp_gateway as ctp

    native = ctp.CtpGateway(EventEngine(), "CTP")
    calls = NS(insert=[], cancel=[], cancel_code=0, insert_code=0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Native API creation/connection is forbidden in tests")

    for api, factory in ((native.td_api, "createFtdcTraderApi"), (native.md_api, "createFtdcMdApi")):
        monkeypatch.setattr(api, factory, forbidden)
        monkeypatch.setattr(api, "connect", forbidden)

    def insert(request, reqid):
        calls.insert.append(dict(request))
        return calls.insert_code

    def cancel(request, reqid):
        calls.cancel.append(dict(request))
        return calls.cancel_code

    monkeypatch.setattr(native.td_api, "reqOrderInsert", insert)
    monkeypatch.setattr(native.td_api, "reqOrderAction", cancel)
    native.td_api.frontid, native.td_api.sessionid = 11, 22
    native.td_api.userid = native.td_api.brokerid = "FIXTURE"
    native.td_api.contract_inited = True
    contract = ContractData(
        symbol="au2612", exchange=Exchange.SHFE, name="fixture gold",
        product=Product.FUTURES, size=1000, pricetick=0.02, gateway_name="CTP",
    )
    monkeypatch.setitem(ctp.symbol_contract_map, contract.symbol, contract)

    adapter = VnpyGateway()
    adapter.contract_specs[contract.symbol] = {
        "exchange": "SHFE", "size": 1000, "pricetick": 0.02, "min_volume": 1,
    }
    adapter.connection_state = dict.fromkeys(adapter.connection_state, True)
    adapter.account.fields_known = True
    adapter.trading_day = "20261008"
    adapter._last_snapshot = time.monotonic()
    adapter._main_engine = NS(
        send_order=lambda request, _name: native.send_order(request),
        cancel_order=lambda request, _name: native.cancel_order(request),
    )
    monkeypatch.setattr(native, "on_order", lambda order: adapter._on_vnpy_order(NS(data=order)))
    monkeypatch.setattr(native, "on_tick", lambda tick: adapter._on_vnpy_tick(NS(data=tick)))
    install_observer(native, lambda _event: None)
    return NS(adapter=adapter, native=native, calls=calls, ctp=ctp)


def fixture_signal(**overrides):
    fields = dict(
        symbol="au2612", datetime=datetime(2026, 10, 8, 10), direction=Direction.LONG,
        offset=OffsetFlag.OPEN, order_type=OrderType.LIMIT, volume=1, price=100.02,
    )
    fields.update(overrides)
    return Signal(**fields)


def broker_receipt(bridge, status, submit_status="0", traded=0):
    return {
        **bridge.calls.insert[-1], "FrontID": 11, "SessionID": 22,
        "OrderStatus": status, "OrderSubmitStatus": submit_status,
        "InsertDate": "20261008", "InsertTime": "10:00:00",
        "VolumeTraded": traded, "OrderSysID": "FIXTURE_SYS", "StatusMsg": "已撤单",
    }


@pytest.mark.parametrize("direction,wire_direction", [(Direction.LONG, "0"), (Direction.SHORT, "1")])
@pytest.mark.parametrize("offset,wire_offset", [
    (OffsetFlag.OPEN, "0"), (OffsetFlag.CLOSE, "1"),
    (OffsetFlag.CLOSE_TODAY, "3"), (OffsetFlag.CLOSE_YESTERDAY, "4"),
])
def test_limit_request_and_cancel_wait_for_native_receipt(bridge, direction, wire_direction, offset, wire_offset):
    adapter, ctp = bridge.adapter, bridge.ctp
    order_id = adapter.send_order(fixture_signal(direction=direction, offset=offset))
    assert order_id == "CTP.11_22_1"
    request = bridge.calls.insert[0]
    assert (request["InstrumentID"], request["ExchangeID"]) == ("au2612", "SHFE")
    assert (request["Direction"], request["CombOffsetFlag"]) == (wire_direction, wire_offset)
    assert (request["LimitPrice"], request["VolumeTotalOriginal"]) == (100.02, 1)
    assert adapter.orders[order_id].status == OrderStatus.SUBMITTING

    bridge.native.td_api.onRtnOrder(broker_receipt(bridge, ctp.THOST_FTDC_OST_NoTradeQueueing))
    assert adapter.orders[order_id].status == OrderStatus.SUBMITTED
    assert adapter.cancel_order(order_id)
    assert adapter.orders[order_id].status == OrderStatus.SUBMITTED
    request = bridge.calls.cancel[0]
    assert (request["FrontID"], request["SessionID"], request["OrderRef"]) == (11, 22, "1")
    assert request["ActionFlag"] == ctp.THOST_FTDC_AF_Delete
    bridge.native.td_api.onRtnOrder(broker_receipt(bridge, ctp.THOST_FTDC_OST_Canceled))
    assert adapter.orders[order_id].status == OrderStatus.CANCELLED


@pytest.mark.parametrize("code", [-1, -2, -3])
def test_native_cancel_transport_failure_is_not_success(bridge, code):
    order_id = bridge.adapter.send_order(fixture_signal())
    bridge.calls.cancel_code = code
    with pytest.raises(RuntimeError, match=f"{code}"):
        bridge.adapter.cancel_order(order_id)
    assert bridge.adapter.orders[order_id].status == OrderStatus.SUBMITTING


def test_native_insert_transport_failure_has_no_order_id(bridge):
    bridge.calls.insert_code = -2
    assert bridge.adapter.send_order(fixture_signal()) == ""
    assert not bridge.adapter.orders


def test_native_insert_rejection_is_not_cancellation(bridge):
    order_id = bridge.adapter.send_order(fixture_signal())
    bridge.native.td_api.onRtnOrder(broker_receipt(
        bridge, bridge.ctp.THOST_FTDC_OST_Canceled, bridge.ctp.THOST_FTDC_OSS_InsertRejected,
    ))
    assert bridge.adapter.orders[order_id].status == OrderStatus.REJECTED


def test_native_cancel_rejection_keeps_order_active(bridge, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(bridge.native, "write_log", messages.append)
    order_id = bridge.adapter.send_order(fixture_signal())
    bridge.native.td_api.onRtnOrder(broker_receipt(bridge, bridge.ctp.THOST_FTDC_OST_NoTradeQueueing))
    assert bridge.adapter.cancel_order(order_id)
    bridge.native.td_api.onRspOrderAction({}, {"ErrorID": 26, "ErrorMsg": "fixture cancel rejection"}, 1, True)
    assert bridge.adapter.orders[order_id].status == OrderStatus.SUBMITTED
    assert any("fixture cancel rejection" in message for message in messages)


def test_native_tick_callback_updates_timestamp_and_cache(bridge):
    payload = {
        "InstrumentID": "au2612", "ActionDay": "20261008", "UpdateTime": "10:00:00",
        "UpdateMillisec": 500, "Volume": 10, "Turnover": 1000200, "OpenInterest": 20,
        "LastPrice": 100.02, "UpperLimitPrice": 110, "LowerLimitPrice": 90,
        "OpenPrice": 100, "HighestPrice": 101, "LowestPrice": 99, "PreClosePrice": 100,
        "BidPrice1": 100, "AskPrice1": 100.04, "BidVolume1": 2, "AskVolume1": 3,
        "BidVolume2": 0, "AskVolume2": 0,
    }
    observed: list[MarketData] = []
    bridge.adapter.on_tick_callback = observed.append
    bridge.native.md_api.onRtnDepthMarketData(payload)
    bridge.native.md_api.onRtnDepthMarketData({**payload, "UpdateTime": "10:00:01", "LastPrice": 100.04})
    assert len(observed) == 2 and observed[1].timestamp > observed[0].timestamp
    snapshot = bridge.adapter.latest_tick_snapshots["au2612.SHFE"]
    assert snapshot["last"] == 100.04
    assert snapshot["timestamp"] == "2026-10-08T10:00:01.500000+08:00"
