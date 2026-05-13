"""Unit tests for VnpyGateway static methods, converters, and callbacks."""

import sys
from datetime import datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from src.strategy import Direction, OffsetFlag, OrderStatus, OrderType, Signal
from src.trading.types import MarketData, TradingStatus
from src.trading.vnpy_gateway import (
    PRODUCT_EXCHANGE,
    VnpyGateway,
    _extract_product,
)


# ── Mock vnpy enums (must be real Enum subclasses for hashability + iteration) ─

class _MockExchange(Enum):
    CFFEX = "CFFEX"
    SHFE = "SHFE"
    DCE = "DCE"
    CZCE = "CZCE"
    INE = "INE"
    GFEX = "GFEX"


class _MockDirection(Enum):
    LONG = "long"
    SHORT = "short"
    NET = "net"


class _MockOrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class _MockOffset(Enum):
    OPEN = "open"
    CLOSE = "close"
    CLOSETODAY = "close_today"
    CLOSEYESTERDAY = "close_yesterday"
    NONE = "none"


class _MockStatus(Enum):
    SUBMITTING = "submitting"
    NOTTRADED = "not_traded"
    PARTTRADED = "part_traded"
    ALLTRADED = "all_traded"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


def _install_mock_vnpy_constants(monkeypatch):
    """Install fake vnpy.trader.constant into sys.modules for converter tests."""
    mock = SimpleNamespace()
    mock.Exchange = _MockExchange
    mock.Direction = _MockDirection
    mock.OrderType = _MockOrderType
    mock.Offset = _MockOffset
    mock.Status = _MockStatus
    monkeypatch.setitem(sys.modules, "vnpy.trader.constant", mock)
    return mock


def _install_mock_vnpy_sys():
    """Install mock vnpy constants directly into sys.modules (returns the mock module)."""
    mock = SimpleNamespace()
    mock.Exchange = _MockExchange
    mock.Direction = _MockDirection
    mock.OrderType = _MockOrderType
    mock.Offset = _MockOffset
    mock.Status = _MockStatus
    sys.modules["vnpy.trader.constant"] = mock
    return mock


def _make_market_data(symbol="rb2505", last_price=3880.0):
    return MarketData(
        symbol=symbol,
        last_price=last_price,
        bid_price_1=0.0,
        ask_price_1=0.0,
        bid_volume_1=0,
        ask_volume_1=0,
        volume=0,
        turnover=0.0,
    )


# ── PRODUCT_EXCHANGE & _extract_product ──────────────────────────────────────

class TestProductExchange:
    def test_known_products(self):
        assert PRODUCT_EXCHANGE["IF"] == "CFFEX"
        assert PRODUCT_EXCHANGE["IC"] == "CFFEX"
        assert PRODUCT_EXCHANGE["RB"] == "SHFE"
        assert PRODUCT_EXCHANGE["CU"] == "SHFE"
        assert PRODUCT_EXCHANGE["SC"] == "INE"
        assert PRODUCT_EXCHANGE["M"] == "DCE"
        assert PRODUCT_EXCHANGE["TA"] == "CZCE"
        assert PRODUCT_EXCHANGE["SI"] == "GFEX"

    def test_extract_product_from_full_symbol(self):
        assert _extract_product("IF2506") == "IF"
        assert _extract_product("rb2505") == "RB"
        assert _extract_product("SC2406") == "SC"
        assert _extract_product("m2501") == "M"

    def test_extract_product_no_letters(self):
        assert _extract_product("2501") == "2501"

    def test_extract_product_empty(self):
        assert _extract_product("") == ""


# ── _is_connect_error ─────────────────────────────────────────────────────────

class TestIsConnectError:
    def test_chinese_error_keywords(self):
        assert VnpyGateway._is_connect_error("连接失败") is True
        assert VnpyGateway._is_connect_error("请求被拒绝") is True
        assert VnpyGateway._is_connect_error("解码错误") is True
        assert VnpyGateway._is_connect_error("网络断开") is True

    def test_english_error_keywords(self):
        assert VnpyGateway._is_connect_error("decode err") is True
        assert VnpyGateway._is_connect_error("shake hand err") is True

    def test_success_messages_not_errors(self):
        assert VnpyGateway._is_connect_error("结算信息确认成功") is False
        assert VnpyGateway._is_connect_error("合约信息查询成功") is False
        assert VnpyGateway._is_connect_error("行情推送正常") is False

    def test_empty_string(self):
        assert VnpyGateway._is_connect_error("") is False


# ── _price_field ──────────────────────────────────────────────────────────────

class TestPriceField:
    def test_basic_access(self):
        data = SimpleNamespace(open_price=3880.5)
        assert VnpyGateway._price_field(data, "open_price") == 3880.5

    def test_missing_field_uses_fallback(self):
        data = SimpleNamespace()
        assert VnpyGateway._price_field(data, "close", 100.0) == 100.0

    def test_null_field_uses_fallback(self):
        data = SimpleNamespace(price=None)
        assert VnpyGateway._price_field(data, "price", 999.0) == 999.0

    def test_non_numeric_field_uses_fallback(self):
        data = SimpleNamespace(price="abc")
        assert VnpyGateway._price_field(data, "price", 50.0) == 50.0


# ── _tick_cache_keys ──────────────────────────────────────────────────────────

class TestTickCacheKeys:
    def test_minimal_keys(self):
        data = SimpleNamespace(vt_symbol="", exchange="")
        keys = VnpyGateway._tick_cache_keys(data, "rb2505")
        assert "rb2505" in keys

    def test_vt_symbol(self):
        data = SimpleNamespace(vt_symbol="rb2505.SHFE", exchange="")
        keys = VnpyGateway._tick_cache_keys(data, "rb2505")
        assert "rb2505.SHFE" in keys

    def test_exchange_value_keys(self):
        exchange = SimpleNamespace(value="SHFE", name="")
        data = SimpleNamespace(vt_symbol="", exchange=exchange)
        keys = VnpyGateway._tick_cache_keys(data, "rb2505")
        assert "rb2505.SHFE" in keys
        assert "SHFE.rb2505" in keys

    def test_exchange_name_keys(self):
        exchange = SimpleNamespace(value="", name="SHFE")
        data = SimpleNamespace(vt_symbol="", exchange=exchange)
        keys = VnpyGateway._tick_cache_keys(data, "rb2505")
        assert "rb2505.SHFE" in keys
        assert "SHFE.rb2505" in keys

    def test_no_falsy_keys(self):
        exchange = SimpleNamespace(value="SHFE", name="")
        data = SimpleNamespace(vt_symbol="", exchange=exchange)
        keys = VnpyGateway._tick_cache_keys(data, "")
        assert "" not in keys


# ── _tick_to_snapshot ─────────────────────────────────────────────────────────

class TestTickToSnapshot:
    def test_basic_snapshot(self):
        tick = MarketData(
            symbol="rb2505",
            last_price=3880.0,
            bid_price_1=3879.0,
            ask_price_1=3881.0,
            bid_volume_1=50,
            ask_volume_1=30,
            volume=10000,
            turnover=38800000,
        )
        data = SimpleNamespace(
            open_price=3850.0,
            high_price=3900.0,
            low_price=3840.0,
            pre_close=3860.0,
            open_interest=50000,
            bid_price_1=3879.0,
            ask_price_1=3881.0,
            bid_volume_1=50,
            ask_volume_1=30,
            bid_price_2=3878.0,
            ask_price_2=3882.0,
            bid_volume_2=40,
            ask_volume_2=20,
            bid_price_3=3877.0,
            ask_price_3=3883.0,
            bid_volume_3=30,
            ask_volume_3=10,
            bid_price_4=3876.0,
            ask_price_4=3884.0,
            bid_volume_4=20,
            ask_volume_4=5,
            bid_price_5=3875.0,
            ask_price_5=3885.0,
            bid_volume_5=10,
            ask_volume_5=5,
        )

        snap = VnpyGateway._tick_to_snapshot(data, tick)

        assert snap["symbol"] == "rb2505"
        assert snap["last"] == 3880.0
        assert snap["open"] == 3850.0
        assert snap["high"] == 3900.0
        assert snap["low"] == 3840.0
        assert snap["pre_close"] == 3860.0
        assert snap["volume"] == 10000
        assert snap["turnover"] == 38800000
        assert snap["open_interest"] == 50000
        assert snap["change"] == 20.0
        assert snap["change_rate"] == pytest.approx(0.5181, abs=0.01)
        assert snap["source"] == "vnpy"
        assert snap["type"] == "tick"
        assert snap["bid1"] == 3879.0
        assert snap["ask1"] == 3881.0
        assert snap["bid1_vol"] == 50
        assert snap["ask1_vol"] == 30
        assert snap["bid5"] == 3875.0
        assert snap["ask5_vol"] == 5

    def test_pre_close_zero_yields_zero_change(self):
        tick = _make_market_data(last_price=100.0)
        data = SimpleNamespace(pre_close=0.0)
        snap = VnpyGateway._tick_to_snapshot(data, tick)
        assert snap["change"] == 0.0
        assert snap["change_rate"] == 0.0

    def test_missing_prices_default_to_zero(self):
        tick = _make_market_data(last_price=0.0)
        data = SimpleNamespace()
        snap = VnpyGateway._tick_to_snapshot(data, tick)
        assert snap["open"] == 0.0
        assert snap["high"] == 0.0
        assert snap["bid1"] == 0.0
        assert snap["bid1_vol"] == 0


# ── Converter tests (with mocked vnpy) ───────────────────────────────────────

class TestDirectionConverters:
    def test_to_vnpy_long(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_direction(Direction.LONG) == _MockDirection.LONG

    def test_to_vnpy_short(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_direction(Direction.SHORT) == _MockDirection.SHORT

    def test_from_vnpy_long(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_direction(_MockDirection.LONG) == Direction.LONG

    def test_from_vnpy_short(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_direction(_MockDirection.SHORT) == Direction.SHORT

    def test_from_vnpy_none_falls_back_to_net(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_direction(None) == Direction.NET


class TestOrderTypeConverters:
    def test_to_vnpy_limit(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_order_type(OrderType.LIMIT) == _MockOrderType.LIMIT

    def test_to_vnpy_market(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_order_type(OrderType.MARKET) == _MockOrderType.MARKET

    def test_to_vnpy_stop(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_order_type(OrderType.STOP) == _MockOrderType.STOP

    def test_from_vnpy_limit(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_order_type(_MockOrderType.LIMIT) == OrderType.LIMIT

    def test_from_vnpy_unknown_defaults_to_market(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_order_type(None) == OrderType.MARKET


class TestOffsetConverters:
    def test_to_vnpy_open(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_offset(OffsetFlag.OPEN) == _MockOffset.OPEN

    def test_to_vnpy_close(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_offset(OffsetFlag.CLOSE) == _MockOffset.CLOSE

    def test_to_vnpy_close_today(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._to_vnpy_offset(OffsetFlag.CLOSE_TODAY) == _MockOffset.CLOSETODAY

    def test_from_vnpy_open(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_offset(_MockOffset.OPEN) == OffsetFlag.OPEN

    def test_from_vnpy_unknown_defaults_to_open(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_offset(None) == OffsetFlag.OPEN


class TestStatusConverter:
    def test_from_vnpy_status_mapping(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_status(_MockStatus.SUBMITTING) == OrderStatus.SUBMITTING
        assert gw._from_vnpy_status(_MockStatus.NOTTRADED) == OrderStatus.SUBMITTED
        assert gw._from_vnpy_status(_MockStatus.PARTTRADED) == OrderStatus.PARTFILLED
        assert gw._from_vnpy_status(_MockStatus.ALLTRADED) == OrderStatus.FILLED
        assert gw._from_vnpy_status(_MockStatus.CANCELLED) == OrderStatus.CANCELLED
        assert gw._from_vnpy_status(_MockStatus.REJECTED) == OrderStatus.REJECTED

    def test_from_vnpy_unknown_defaults_to_submitting(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        assert gw._from_vnpy_status(None) == OrderStatus.SUBMITTING


# ── _split_symbol (with mocked vnpy Exchange) ────────────────────────────────

class TestSplitSymbol:
    def test_simple_symbol_uses_product_lookup(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        symbol, exchange = gw._split_symbol("IF2506")
        assert symbol == "IF2506"
        assert exchange == _MockExchange.CFFEX

    def test_dotted_cffex_format(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        symbol, exchange = gw._split_symbol("CFFEX.IF2506")
        assert symbol == "IF2506"
        assert exchange == _MockExchange.CFFEX

    def test_dotted_value_format(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        symbol, exchange = gw._split_symbol("rb2505.SHFE")
        assert symbol == "rb2505"
        assert exchange == _MockExchange.SHFE

    def test_unknown_product_defaults_to_shfe(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        symbol, exchange = gw._split_symbol("ZZ9999")
        assert symbol == "ZZ9999"
        assert exchange == _MockExchange.SHFE


# ── Callback handler tests ────────────────────────────────────────────────────

class TestCallbacks:
    def test_on_vnpy_log_sets_connected_on_success(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTING
        gw._connected_event.clear()
        event = SimpleNamespace(data=SimpleNamespace(msg="结算信息确认成功"))
        gw._on_vnpy_log(event)
        assert gw._connected_event.is_set()

    def test_on_vnpy_log_sets_error_on_failure(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTING
        gw._connected_event.clear()
        gw._error_event.clear()
        event = SimpleNamespace(data=SimpleNamespace(msg="连接失败：网络不可达"))
        gw._on_vnpy_log(event)
        assert gw._error_event.is_set()
        assert not gw._connected_event.is_set()

    def test_on_vnpy_log_ignores_errors_when_not_connecting(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTED
        gw._error_event.clear()
        event = SimpleNamespace(data=SimpleNamespace(msg="断开连接"))
        gw._on_vnpy_log(event)
        assert not gw._error_event.is_set()

    def test_on_vnpy_account_sets_account_and_connected(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTING
        gw._connected_event.clear()
        calls = []
        gw.on_account_callback = lambda acct: calls.append(acct)
        event = SimpleNamespace(data=SimpleNamespace(
            accountid="ACC001", balance="500000", available="480000", frozen="20000"
        ))
        gw._on_vnpy_account(event)
        assert gw.account.account_id == "ACC001"
        assert gw.account.balance == 500000.0
        assert gw.account.available == 480000.0
        assert gw.account.margin == 20000.0
        assert gw._connected_event.is_set()
        assert len(calls) == 1

    def test_on_vnpy_position_creates_correct_position(self):
        mc = _install_mock_vnpy_sys()
        gw = VnpyGateway()
        position_calls = []
        gw.on_position_callback = lambda pos: position_calls.append(pos)
        event = SimpleNamespace(data=SimpleNamespace(
            symbol="rb2505",
            direction=mc.Direction.LONG,
            volume=5,
            frozen=0,
            price=3880.0,
            pnl=500.0,
        ))
        gw._on_vnpy_position(event)
        key = "rb2505_long"
        assert key in gw.positions
        assert gw.positions[key].symbol == "rb2505"
        assert gw.positions[key].volume == 5
        assert gw.positions[key].pnl == 500.0
        assert len(position_calls) == 1

    def test_on_vnpy_order_stores_and_forwards(self):
        mc = _install_mock_vnpy_sys()
        gw = VnpyGateway()
        order_calls = []
        gw.on_order_callback = lambda o: order_calls.append(o)
        now = datetime.now()
        event = SimpleNamespace(data=SimpleNamespace(
            vt_orderid="ORD001",
            symbol="rb2505",
            direction=mc.Direction.LONG,
            type=mc.OrderType.LIMIT,
            price=3880.0,
            volume=2,
            traded=0,
            status=mc.Status.SUBMITTING,
            offset=mc.Offset.OPEN,
            datetime=now,
        ))
        gw._on_vnpy_order(event)
        assert "ORD001" in gw.orders
        assert gw.orders["ORD001"].symbol == "rb2505"
        assert gw.orders["ORD001"].price == 3880.0
        assert gw.orders["ORD001"].volume == 2
        assert gw.orders["ORD001"].status == OrderStatus.SUBMITTING
        assert len(order_calls) == 1

    def test_on_vnpy_trade_creates_trade_record(self):
        mc = _install_mock_vnpy_sys()
        gw = VnpyGateway()
        trade_calls = []
        gw.on_trade_callback = lambda t: trade_calls.append(t)
        now = datetime.now()
        event = SimpleNamespace(data=SimpleNamespace(
            vt_tradeid="TRD001",
            vt_orderid="ORD001",
            symbol="rb2505",
            direction=mc.Direction.LONG,
            price=3880.0,
            volume=2,
            commission=15.0,
            pnl=100.0,
            datetime=now,
        ))
        gw._on_vnpy_trade(event)
        assert len(trade_calls) == 1
        assert trade_calls[0].trade_id == "TRD001"
        assert trade_calls[0].price == 3880.0
        assert trade_calls[0].commission == 15.0
        assert trade_calls[0].pnl == 100.0

    def test_on_vnpy_tick_caches_and_forwards(self):
        gw = VnpyGateway()
        tick_calls = []
        gw.on_tick_callback = lambda t: tick_calls.append(t)
        exchange = SimpleNamespace(value="SHFE", name="SHFE")
        event = SimpleNamespace(data=SimpleNamespace(
            symbol="rb2505",
            vt_symbol="rb2505.SHFE",
            exchange=exchange,
            last_price=3880.0,
            bid_price_1=3879.0,
            ask_price_1=3881.0,
            bid_volume_1=50,
            ask_volume_1=30,
            volume=10000,
            turnover=38800000,
            datetime=datetime.now(),
        ))
        gw._on_vnpy_tick(event)
        assert len(tick_calls) == 1
        assert tick_calls[0].symbol == "rb2505"
        assert tick_calls[0].last_price == 3880.0
        assert "rb2505" in gw.latest_ticks
        assert "rb2505" in gw.latest_tick_snapshots


# ── Connection flow tests ─────────────────────────────────────────────────────

class TestConnectionFlow:
    def test_connect_rejects_missing_credentials(self):
        gw = VnpyGateway()
        with pytest.raises(Exception):
            gw.connect({"username": "", "password": "", "broker_id": "", "td_server": ""})

    def test_disconnect_clears_state(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTED
        gw._main_engine = SimpleNamespace(close=lambda: None)
        gw.disconnect()
        assert gw.status == TradingStatus.STOPPED
        assert gw._main_engine is None

    def test_send_order_refused_when_not_connected(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.STOPPED
        signal = Signal(symbol="rb2505", datetime=datetime.now(), direction=Direction.LONG, price=3880, volume=1)
        assert gw.send_order(signal) == ""
