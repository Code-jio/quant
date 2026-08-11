"""Unit tests for VnpyGateway static methods, converters, and callbacks."""

import sys
from datetime import datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from src.strategy import Direction, OffsetFlag, Order, OrderStatus, OrderType, Position, Signal
from src.trading.types import AccountInfo
from src.trading.types import MarketData, TradingStatus
from src.trading.vnpy_gateway import (
    PRODUCT_EXCHANGE,
    VnpyGateway,
    _build_reconciliation_ctp_gateway,
    _ctp_contracts_ready,
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


def test_partial_contract_table_is_not_reconciliation_ready():
    assert _ctp_contracts_ready(
        SimpleNamespace(contract_inited=False),
        {"IF2506": object()},
    ) is False
    assert _ctp_contracts_ready(
        SimpleNamespace(contract_inited=True),
        {"IF2506": object()},
    ) is True


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

    def test_unknown_product_fails_closed(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        with pytest.raises(Exception):
            gw._split_symbol("ZZ9999")

    def test_known_product_rejects_conflicting_exchange(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        gw = VnpyGateway()
        with pytest.raises(Exception):
            gw._split_symbol("au2606.DCE")


# ── Callback handler tests ────────────────────────────────────────────────────

class TestCallbacks:
    def test_contract_success_log_alone_does_not_mark_order_entry_ready(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTING
        gw._connected_event.clear()
        event = SimpleNamespace(data=SimpleNamespace(msg="合约信息查询成功"))
        gw._on_vnpy_log(event)
        assert not gw._connected_event.is_set()

    def test_settlement_confirmation_does_not_precede_contract_readiness(self):
        gw = VnpyGateway()
        gw.status = TradingStatus.CONNECTING
        gw._connected_event.clear()
        event = SimpleNamespace(data=SimpleNamespace(msg="结算信息确认成功"))

        gw._on_vnpy_log(event)

        assert not gw._connected_event.is_set()

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

    def test_on_vnpy_account_sets_account_but_waits_for_contracts(self):
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
        assert not gw._connected_event.is_set()
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
    @staticmethod
    def _install_synchronous_connect_runtime(
        monkeypatch,
        gateway,
        refresh_result,
        reconciliation_ready,
        trading_day="2026-08-12",
    ):
        """Replace vn.py with a synchronous three-channel-ready runtime."""
        class EventEngine:
            def register(self, *_args):
                return None

        class MainEngine:
            def __init__(self, _event_engine):
                pass

            def add_gateway(self, _gateway_class):
                return None

            def connect(self, _setting, _gateway_name):
                gateway._td_connected = True
                gateway._md_connected = True
                gateway._contracts_ready = True
                gateway.trading_day = trading_day
                gateway._connected_event.set()

        monkeypatch.setitem(sys.modules, "vnpy.event", SimpleNamespace(EventEngine=EventEngine))
        monkeypatch.setitem(sys.modules, "vnpy.trader.engine", SimpleNamespace(MainEngine=MainEngine))
        monkeypatch.setitem(sys.modules, "vnpy.trader.event", SimpleNamespace(
            EVENT_ACCOUNT="account", EVENT_LOG="log", EVENT_ORDER="order",
            EVENT_POSITION="position", EVENT_TICK="tick", EVENT_TRADE="trade",
        ))
        monkeypatch.setattr("src.trading.vnpy_gateway._ensure_vnpy_runtime_dir", lambda: None)
        monkeypatch.setattr(
            "src.trading.vnpy_gateway._build_reconciliation_ctp_gateway",
            lambda _adapter: object,
        )

        refresh_calls = []

        def refresh_reconciliation(*, timeout_seconds):
            refresh_calls.append(timeout_seconds)
            gateway._reconciliation_ready = reconciliation_ready
            return dict(refresh_result)

        monkeypatch.setattr(gateway, "refresh_reconciliation", refresh_reconciliation)
        return refresh_calls

    @staticmethod
    def _live_connect_config():
        return {
            "username": "live-user",
            "password": "live-password",
            "broker_id": "9999",
            "td_server": "tcp://td.example:1234",
            "md_server": "tcp://md.example:1234",
            "connect_timeout": 0.1,
            "reconciliation_timeout": 0.1,
        }

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

    def test_connect_rejects_transport_ready_session_when_broker_reconciliation_is_not_fresh(self, monkeypatch):
        gateway = VnpyGateway()
        refresh_calls = self._install_synchronous_connect_runtime(
            monkeypatch,
            gateway,
            {"ok": False, "fresh": False, "failure_code": "broker_snapshot_timeout"},
            reconciliation_ready=False,
        )

        assert gateway.connect(self._live_connect_config()) is False
        assert refresh_calls == [0.1]
        assert gateway.status == TradingStatus.ERROR

    def test_connect_accepts_transport_ready_session_only_after_fresh_reconciliation_sets_the_gate(self, monkeypatch):
        gateway = VnpyGateway()
        refresh_calls = self._install_synchronous_connect_runtime(
            monkeypatch,
            gateway,
            {"ok": True, "fresh": True, "failure_code": ""},
            reconciliation_ready=True,
        )

        assert gateway.connect(self._live_connect_config()) is True
        assert refresh_calls == [0.1]
        assert gateway.status == TradingStatus.CONNECTED

    def test_connect_rejects_fresh_reconciliation_when_broker_trading_day_is_missing(self, monkeypatch):
        gateway = VnpyGateway()
        refresh_calls = self._install_synchronous_connect_runtime(
            monkeypatch,
            gateway,
            {"ok": True, "fresh": True, "failure_code": ""},
            reconciliation_ready=True,
            trading_day="",
        )

        assert gateway.connect(self._live_connect_config()) is False
        assert refresh_calls == [0.1]
        assert gateway.status == TradingStatus.ERROR


class TestChannelConnectionHealth:
    @staticmethod
    def _log(gateway, message):
        gateway._on_vnpy_log(SimpleNamespace(data=SimpleNamespace(msg=message)))

    def test_td_or_md_disconnect_immediately_degrades_that_channel(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "\u4ea4\u6613\u670d\u52a1\u5668\u767b\u5f55\u6210\u529f")
        self._log(gateway, "\u884c\u60c5\u670d\u52a1\u5668\u767b\u5f55\u6210\u529f")

        self._log(gateway, "\u4ea4\u6613\u670d\u52a1\u5668\u8fde\u63a5\u65ad\u5f00")
        td_down = gateway.connection_snapshot()
        assert td_down["td_connected"] is False
        assert td_down["md_connected"] is True
        assert td_down["fully_connected"] is False
        assert gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING)

        self._log(gateway, "\u884c\u60c5\u670d\u52a1\u5668\u8fde\u63a5\u65ad\u5f00")
        md_down = gateway.connection_snapshot()
        assert md_down["td_connected"] is False
        assert md_down["md_connected"] is False
        assert md_down["fully_connected"] is False

    def test_relogin_waits_for_fresh_contracts_and_reconciliation_before_restoring_health(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        self._log(gateway, "合约信息查询成功")
        self._log(gateway, "交易服务器连接断开")
        self._log(gateway, "行情服务器连接断开")

        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        waiting = gateway.connection_snapshot()

        assert waiting["td_connected"] is True
        assert waiting["md_connected"] is True
        assert waiting["fully_connected"] is True
        assert waiting["contracts_ready"] is False
        assert waiting["order_entry_ready"] is False
        assert waiting["reconnecting"] is True
        assert gateway.status == TradingStatus.ERROR
        assert waiting["reconnect_count"] == 0

        self._log(gateway, "合约信息查询成功")
        recovered = gateway.connection_snapshot()
        assert recovered["contracts_ready"] is True
        assert recovered["reconciliation_ready"] is False
        assert recovered["order_entry_ready"] is False
        assert recovered["reconnecting"] is True
        assert recovered["reconnect_count"] == 0
        assert gateway.status == TradingStatus.ERROR

    def test_native_login_flags_degrade_health_when_disconnect_log_is_missing(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        native = SimpleNamespace(
            td_api=SimpleNamespace(login_status=True),
            md_api=SimpleNamespace(login_status=True),
        )
        gateway._main_engine = SimpleNamespace(get_gateway=lambda _name: native)

        native.md_api.login_status = False
        degraded = gateway.connection_snapshot()

        assert degraded["td_connected"] is True
        assert degraded["md_connected"] is False
        assert degraded["reconnecting"] is True
        assert gateway.status == TradingStatus.ERROR

        native.md_api.login_status = True
        restored = gateway.connection_snapshot()
        assert restored["fully_connected"] is True
        assert restored["reconnecting"] is True
        assert restored["reconnect_count"] == 0
        assert gateway.status == TradingStatus.ERROR


class TestBrokerReconciliation:
    @staticmethod
    def _complete(gateway, kind, error_id=0, item_count=None, request_id=None):
        if item_count is None:
            item_count = 1 if kind == "account" else 0
        if request_id is None:
            request_id = gateway._reconciliation_expected_reqids.get(kind, 1)
            gateway._arm_reconciliation_request(kind, request_id)
        gateway._on_reconciliation_event(SimpleNamespace(data={
            "kind": kind,
            "request_id": request_id,
            "error_id": error_id,
            "error_msg": "query failed" if error_id else "",
            "item_count": item_count,
        }))

    def test_ctp_runtime_exposes_order_query_api(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "vnpy.trader.constant", raising=False)
        monkeypatch.delitem(sys.modules, "vnpy_ctp", raising=False)
        monkeypatch.delitem(sys.modules, "vnpy_ctp.gateway", raising=False)
        monkeypatch.delitem(sys.modules, "vnpy_ctp.gateway.ctp_gateway", raising=False)
        from vnpy_ctp.gateway.ctp_gateway import CtpTdApi

        assert hasattr(CtpTdApi, "reqQryOrder")

    def test_refresh_reconciliation_replaces_stale_caches_after_all_fences(self, monkeypatch):
        constants = _install_mock_vnpy_constants(monkeypatch)
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        gateway._td_connected = True
        gateway._md_connected = True
        gateway._contracts_ready = True
        gateway.orders["STALE"] = Order(
            order_id="STALE",
            symbol="rb2505",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3800,
            volume=1,
            status=OrderStatus.SUBMITTED,
        )
        gateway.positions["STALE"] = Position(
            symbol="rb2505",
            direction=Direction.LONG,
            volume=9,
        )

        class BrokerSnapshot:
            def query_orders_snapshot(self):
                gateway._on_vnpy_order(SimpleNamespace(data=SimpleNamespace(
                    vt_orderid="FRESH",
                    symbol="rb2505",
                    direction=constants.Direction.LONG,
                    type=constants.OrderType.LIMIT,
                    price=3880.0,
                    volume=1,
                    traded=1,
                    status=constants.Status.ALLTRADED,
                    offset=constants.Offset.OPEN,
                    datetime=datetime.now(),
                )))
                TestBrokerReconciliation._complete(gateway, "orders")
                return 0

            def query_positions_snapshot(self):
                gateway._on_vnpy_position(SimpleNamespace(data=SimpleNamespace(
                    symbol="rb2505",
                    direction=constants.Direction.LONG,
                    volume=1,
                    frozen=0,
                    price=3880.0,
                    pnl=0.0,
                )))
                TestBrokerReconciliation._complete(gateway, "positions")
                return 0

            def query_account_snapshot(self):
                gateway._on_vnpy_account(SimpleNamespace(data=SimpleNamespace(
                    accountid="ACC001",
                    balance=500000,
                    available=480000,
                    frozen=20000,
                )))
                TestBrokerReconciliation._complete(gateway, "account")
                return 0

        broker_snapshot = BrokerSnapshot()
        gateway._main_engine = SimpleNamespace(get_gateway=lambda name: broker_snapshot)

        result = gateway.refresh_reconciliation(timeout_seconds=0.5)

        assert result["ok"] is True
        assert result["fresh"] is True
        assert set(gateway.orders) == {"FRESH"}
        assert gateway.orders["FRESH"].status == OrderStatus.FILLED
        assert set(gateway.positions) == {"rb2505_long"}
        assert gateway.positions["rb2505_long"].volume == 1
        assert gateway.account == AccountInfo(
            account_id="ACC001",
            balance=500000.0,
            available=480000.0,
            margin=20000.0,
        )

    def test_refresh_reconciliation_timeout_keeps_previous_cache(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        stale_order = Order(
            order_id="STALE",
            symbol="rb2505",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3800,
            volume=1,
            status=OrderStatus.SUBMITTED,
        )
        gateway.orders["STALE"] = stale_order
        broker_snapshot = SimpleNamespace(
            query_orders_snapshot=lambda: 0,
            query_positions_snapshot=lambda: 0,
            query_account_snapshot=lambda: 0,
        )
        gateway._main_engine = SimpleNamespace(get_gateway=lambda name: broker_snapshot)

        result = gateway.refresh_reconciliation(timeout_seconds=0.01)

        assert result["ok"] is False
        assert result["fresh"] is False
        assert result["failure_code"] == "broker_snapshot_timeout"
        assert gateway.orders == {"STALE": stale_order}

    def test_refresh_reconciliation_requires_account_payload(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED

        class BrokerSnapshot:
            def query_orders_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "orders")
                return 0

            def query_positions_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "positions")
                return 0

            def query_account_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "account", item_count=0)
                return 0

        gateway._main_engine = SimpleNamespace(get_gateway=lambda name: BrokerSnapshot())

        result = gateway.refresh_reconciliation(timeout_seconds=0.5)

        assert result["ok"] is False
        assert result["fresh"] is False
        assert result["failure_code"] == "broker_account_unavailable"

    def test_delayed_completion_from_timed_out_request_cannot_unlock_retry(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        gateway._td_connected = True
        gateway._md_connected = True
        gateway._contracts_ready = True

        first_snapshot = SimpleNamespace(
            query_orders_snapshot=lambda: (
                gateway._arm_reconciliation_request("orders", 1) or 0
            ),
            query_positions_snapshot=lambda: 0,
            query_account_snapshot=lambda: 0,
        )
        gateway._main_engine = SimpleNamespace(get_gateway=lambda name: first_snapshot)
        first_result = gateway.refresh_reconciliation(timeout_seconds=0.01)
        assert first_result["failure_code"] == "broker_snapshot_timeout"

        class RetrySnapshot:
            def query_orders_snapshot(self):
                gateway._on_reconciliation_event(SimpleNamespace(data={
                    "kind": "orders",
                    "request_id": 1,
                    "error_id": 99,
                    "error_msg": "stale response",
                    "item_count": 0,
                }))
                gateway._arm_reconciliation_request("orders", 2)
                TestBrokerReconciliation._complete(gateway, "orders", request_id=2)
                return 0

            def query_positions_snapshot(self):
                gateway._arm_reconciliation_request("positions", 3)
                TestBrokerReconciliation._complete(gateway, "positions", request_id=3)
                return 0

            def query_account_snapshot(self):
                gateway._arm_reconciliation_request("account", 4)
                gateway._on_vnpy_account(SimpleNamespace(data=SimpleNamespace(
                    accountid="ACC001",
                    balance=500000,
                    available=480000,
                    frozen=20000,
                )))
                TestBrokerReconciliation._complete(
                    gateway,
                    "account",
                    item_count=1,
                    request_id=4,
                )
                return 0

        gateway._main_engine = SimpleNamespace(get_gateway=lambda name: RetrySnapshot())
        retry_result = gateway.refresh_reconciliation(timeout_seconds=0.5)

        assert retry_result["ok"] is True
        assert retry_result["failure_code"] == ""


class TestOrderEntryReadinessContract:
    @staticmethod
    def _log(gateway, message):
        gateway._on_vnpy_log(SimpleNamespace(data=SimpleNamespace(msg=message)))

    def test_connect_event_requires_td_md_and_contracts_in_any_log_order(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTING

        self._log(gateway, "行情服务器登录成功")
        self._log(gateway, "合约信息查询成功")
        assert not gateway._connected_event.is_set()

        self._log(gateway, "交易服务器登录成功")
        assert gateway._connected_event.is_set()

    def test_snapshot_separates_full_channel_connection_from_order_entry_readiness(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")

        channels_only = gateway.connection_snapshot()
        assert channels_only["fully_connected"] is True
        assert channels_only["contracts_ready"] is False
        assert channels_only["order_entry_ready"] is False

        self._log(gateway, "合约信息查询成功")
        ready = gateway.connection_snapshot()
        assert ready["contracts_ready"] is True
        assert ready["reconciliation_ready"] is False
        assert ready["order_entry_ready"] is False

    def test_duplicate_td_disconnect_remains_one_blocked_outage_until_reconciliation(self):
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        self._log(gateway, "合约信息查询成功")
        self._log(gateway, "交易服务器连接断开")
        self._log(gateway, "交易服务器连接断开")
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "合约信息查询成功")

        snapshot = gateway.connection_snapshot()
        assert snapshot["reconnecting"] is True
        assert snapshot["reconnect_count"] == 0

    def test_send_order_does_not_reach_main_engine_without_order_entry_readiness(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        gateway._main_engine = SimpleNamespace(
            send_order=lambda *args: sent.append(args) or "UNEXPECTED"
        )
        signal = Signal(
            symbol="rb2505", datetime=datetime.now(), direction=Direction.LONG,
            price=3880, volume=1,
        )

        assert gateway.send_order(signal) == ""
        assert sent == []


class TestLiveBrokerReconciliationGate:
    """Live CTP order entry must wait for a broker-authoritative snapshot."""

    @staticmethod
    def _log(gateway, message):
        gateway._on_vnpy_log(SimpleNamespace(data=SimpleNamespace(msg=message)))

    def _make_transport_ready(self, gateway):
        gateway.status = TradingStatus.CONNECTED
        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        self._log(gateway, "合约信息查询成功")

    def test_transport_and_contracts_are_not_order_entry_ready_before_fresh_broker_reconciliation(self):
        gateway = VnpyGateway()
        self._make_transport_ready(gateway)

        snapshot = gateway.connection_snapshot()
        assert snapshot["td_connected"] is True
        assert snapshot["md_connected"] is True
        assert snapshot["contracts_ready"] is True
        assert snapshot["reconciliation_ready"] is False
        assert snapshot["order_entry_ready"] is False

    def test_successful_authoritative_snapshot_unlocks_order_entry(self):
        gateway = VnpyGateway()
        self._make_transport_ready(gateway)

        class BrokerSnapshot:
            def query_orders_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "orders")
                return 0

            def query_positions_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "positions")
                return 0

            def query_account_snapshot(self):
                gateway._on_vnpy_account(SimpleNamespace(data=SimpleNamespace(
                    accountid="ACC001", balance=500000, available=480000, frozen=20000,
                )))
                TestBrokerReconciliation._complete(gateway, "account")
                return 0

        gateway._main_engine = SimpleNamespace(get_gateway=lambda _name: BrokerSnapshot())
        result = gateway.refresh_reconciliation(timeout_seconds=0.5)
        snapshot = gateway.connection_snapshot()

        assert result["ok"] is True
        assert result["fresh"] is True
        assert snapshot["reconciliation_ready"] is True
        assert snapshot["order_entry_ready"] is True

    def test_failed_reconciliation_keeps_send_order_blocked(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = VnpyGateway()
        self._make_transport_ready(gateway)
        gateway._main_engine = SimpleNamespace(
            get_gateway=lambda _name: SimpleNamespace(
                query_orders_snapshot=lambda: 0,
                query_positions_snapshot=lambda: 0,
                query_account_snapshot=lambda: 0,
            ),
            send_order=lambda *args: sent.append(args) or "UNEXPECTED",
        )
        result = gateway.refresh_reconciliation(timeout_seconds=0.01)
        signal = Signal(
            symbol="rb2505", datetime=datetime.now(), direction=Direction.LONG,
            price=3880, volume=1,
        )

        assert result["ok"] is False
        assert result["fresh"] is False
        assert gateway.send_order(signal) == ""
        assert sent == []

    def test_disconnect_during_snapshot_rejects_the_old_connection_result(self):
        gateway = VnpyGateway()
        self._make_transport_ready(gateway)

        class BrokerSnapshot:
            def query_orders_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "orders")
                return 0

            def query_positions_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "positions")
                return 0

            def query_account_snapshot(self):
                gateway._on_vnpy_account(SimpleNamespace(data=SimpleNamespace(
                    accountid="ACC001", balance=500000, available=480000, frozen=20000,
                )))
                TestLiveBrokerReconciliationGate._log(gateway, "交易服务器连接断开")
                TestBrokerReconciliation._complete(gateway, "account")
                return 0

        gateway._main_engine = SimpleNamespace(get_gateway=lambda _name: BrokerSnapshot())

        result = gateway.refresh_reconciliation(timeout_seconds=0.5)
        snapshot = gateway.connection_snapshot()

        assert result["ok"] is False
        assert result["fresh"] is False
        assert result["failure_code"] == "broker_connection_changed_during_snapshot"
        assert snapshot["reconciliation_ready"] is False
        assert snapshot["order_entry_ready"] is False

    def test_disconnect_and_relogin_invalidate_reconciliation_until_a_new_snapshot_succeeds(self):
        gateway = VnpyGateway()
        self._make_transport_ready(gateway)
        gateway._reconciliation_ready = True

        self._log(gateway, "交易服务器连接断开")
        disconnected = gateway.connection_snapshot()
        assert disconnected["reconciliation_ready"] is False
        assert disconnected["order_entry_ready"] is False

        self._log(gateway, "交易服务器登录成功")
        self._log(gateway, "行情服务器登录成功")
        self._log(gateway, "合约信息查询成功")
        relogged = gateway.connection_snapshot()
        assert relogged["reconciliation_ready"] is False
        assert relogged["order_entry_ready"] is False
        assert relogged["reconnecting"] is True
        assert gateway.status == TradingStatus.ERROR
        assert relogged["reconnect_count"] == 0

        class BrokerSnapshot:
            def query_orders_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "orders")
                return 0

            def query_positions_snapshot(self):
                TestBrokerReconciliation._complete(gateway, "positions")
                return 0

            def query_account_snapshot(self):
                gateway._on_vnpy_account(SimpleNamespace(data=SimpleNamespace(
                    accountid="ACC001", balance=500000, available=480000, frozen=20000,
                )))
                TestBrokerReconciliation._complete(gateway, "account")
                return 0

        gateway._main_engine = SimpleNamespace(get_gateway=lambda _name: BrokerSnapshot())
        result = gateway.refresh_reconciliation(timeout_seconds=0.5)
        restored = gateway.connection_snapshot()

        assert result["ok"] is True
        assert result["fresh"] is True
        assert restored["reconciliation_ready"] is True
        assert restored["order_entry_ready"] is True
        assert restored["reconnecting"] is False
        assert gateway.status == TradingStatus.CONNECTED
        assert restored["reconnect_count"] == 1


class TestLiveContractOrderCapabilityGate:
    """Raw CTP contract limits must be enforced before an order reaches vn.py."""

    @staticmethod
    def _ready_gateway():
        gateway = VnpyGateway()
        gateway.status = TradingStatus.CONNECTED
        gateway._td_connected = True
        gateway._md_connected = True
        gateway._contracts_ready = True
        gateway._reconciliation_ready = True
        return gateway

    @staticmethod
    def _record(gateway, symbol="rb2505", exchange="SHFE", **limits):
        raw_contract = {
            "InstrumentID": symbol,
            "ExchangeID": exchange,
            "PriceTick": limits.get("price_tick", 0.2),
            "MinLimitOrderVolume": limits.get("min_limit", 1),
            "MaxLimitOrderVolume": limits.get("max_limit", 3),
            "MinMarketOrderVolume": limits.get("min_market", 0),
            "MaxMarketOrderVolume": limits.get("max_market", 0),
        }
        gateway._record_contract_capability(raw_contract)

    @staticmethod
    def _signal(symbol="rb2505.SHFE", *, price=100.0, volume=1, order_type=OrderType.LIMIT, offset=OffsetFlag.OPEN):
        return Signal(
            symbol=symbol,
            datetime=datetime.now(),
            direction=Direction.LONG,
            price=price,
            volume=volume,
            order_type=order_type,
            offset=offset,
        )

    def test_records_exchange_aware_ctp_contract_limits_and_clears_them_on_td_disconnect(self):
        gateway = self._ready_gateway()
        self._record(gateway, price_tick=0.2, min_limit=1, max_limit=3, min_market=1, max_market=2)

        capability = gateway._contract_capabilities["rb2505.SHFE"]
        assert capability["price_tick"] == 0.2
        assert capability["min_limit_order_volume"] == 1
        assert capability["max_limit_order_volume"] == 3
        assert capability["min_market_order_volume"] == 1
        assert capability["max_market_order_volume"] == 2

        gateway._on_vnpy_log(SimpleNamespace(data=SimpleNamespace(msg="交易服务器连接断开")))
        assert gateway._contract_capabilities == {}

    def test_missing_contract_is_rejected_before_main_engine_submission(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "UNEXPECTED")

        assert gateway.send_order(self._signal()) == ""
        assert sent == []
        assert gateway.last_reject_reason

    @pytest.mark.parametrize(
        ("price", "volume"),
        [
            (100.1, 1),
            (100.0, 0),
            (100.0, 4),
        ],
    )
    def test_limit_order_requires_exact_tick_and_limit_volume_range(self, monkeypatch, price, volume):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway)
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "UNEXPECTED")

        assert gateway.send_order(self._signal(price=price, volume=volume)) == ""
        assert sent == []
        assert gateway.last_reject_reason

    def test_market_order_requires_explicit_market_volume_capability(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway, min_market=0, max_market=0)
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "UNEXPECTED")

        assert gateway.send_order(self._signal(order_type=OrderType.MARKET)) == ""
        assert sent == []
        assert gateway.last_reject_reason

    def test_market_order_with_explicit_supported_market_range_is_sent(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway, min_market=1, max_market=2)
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "MARKET-1")

        assert gateway.send_order(self._signal(order_type=OrderType.MARKET, volume=2)) == "MARKET-1"
        assert len(sent) == 1

    def test_close_today_and_close_yesterday_are_local_only_for_shfe_and_ine(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway, symbol="m2501", exchange="DCE")
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "UNEXPECTED")

        assert gateway.send_order(self._signal("m2501.DCE", offset=OffsetFlag.CLOSE_TODAY)) == ""
        assert sent == []
        assert gateway.last_reject_reason

    def test_unknown_offset_is_rejected_instead_of_falling_back_to_open(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway)
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "UNEXPECTED")
        signal = self._signal()
        signal.offset = "invalid-offset"

        assert gateway.send_order(signal) == ""
        assert sent == []
        assert gateway.last_reject_reason

        assert gateway.send_order(self._signal("m2501.DCE", offset=OffsetFlag.CLOSE_YESTERDAY)) == ""
        assert sent == []
        assert gateway.last_reject_reason

    def test_legal_limit_order_with_tick_tolerance_is_constructed_and_sent(self, monkeypatch):
        _install_mock_vnpy_constants(monkeypatch)
        sent = []
        gateway = self._ready_gateway()
        self._record(gateway)
        gateway._main_engine = SimpleNamespace(send_order=lambda *args: sent.append(args) or "ORDER-1")

        assert gateway.send_order(self._signal(price=100.00000000001, volume=2)) == "ORDER-1"
        assert len(sent) == 1


def test_ctp_trading_day_is_normalized_emitted_once_and_exposed_in_connection_snapshot():
    gateway = VnpyGateway()
    observed = []
    gateway.on_trading_day_callback = observed.append

    gateway._set_trading_day("20260812")
    gateway._set_trading_day("2026-08-12")

    assert observed == ["2026-08-12"]
    assert gateway.connection_snapshot()["trading_day"] == "2026-08-12"


def test_cancel_request_is_pending_until_a_broker_cancelled_order_callback_arrives():
    gateway = VnpyGateway()
    gateway._main_engine = SimpleNamespace(cancel_order=lambda *_args: None)
    gateway._vn_orders["OID-1"] = SimpleNamespace(create_cancel_request=lambda: object())

    assert gateway.cancel_order("OID-1") is True
    pending = gateway.wait_cancel_confirmation("OID-1", timeout=0)

    assert pending == {"requested": True, "confirmed": False, "pending": True, "failed": False}


def test_cancel_confirmation_requires_cancelled_callback_and_rejects_late_fill():
    constants = _install_mock_vnpy_sys()
    gateway = VnpyGateway()
    gateway._main_engine = SimpleNamespace(cancel_order=lambda *_args: None)
    for order_id, broker_status in (
        ("OID-CANCELLED", constants.Status.CANCELLED),
        ("OID-FILLED", constants.Status.ALLTRADED),
    ):
        gateway._vn_orders[order_id] = SimpleNamespace(create_cancel_request=lambda: object())
        assert gateway.cancel_order(order_id) is True
        gateway._on_vnpy_order(SimpleNamespace(data=SimpleNamespace(
            vt_orderid=order_id, symbol="rb2505", direction=constants.Direction.LONG,
            type=constants.OrderType.LIMIT, price=100.0, volume=1, traded=1,
            status=broker_status, offset=constants.Offset.OPEN, datetime=datetime.now(),
        )))

    confirmed = gateway.wait_cancel_confirmation("OID-CANCELLED", timeout=0)
    late_fill = gateway.wait_cancel_confirmation("OID-FILLED", timeout=0)

    assert confirmed == {
        "requested": True,
        "confirmed": True,
        "pending": False,
        "failed": False,
    }
    assert late_fill["confirmed"] is False
    assert late_fill["failed"] is True
    assert "filled" in late_fill["error_msg"].lower()


def test_rejected_ctp_order_callback_preserves_broker_error_for_ui_and_engine():
    constants = _install_mock_vnpy_sys()
    gateway = VnpyGateway()
    gateway._on_vnpy_order(SimpleNamespace(data=SimpleNamespace(
        vt_orderid="OID-REJECTED", symbol="rb2505", direction=constants.Direction.LONG,
        type=constants.OrderType.LIMIT, price=100.0, volume=1, traded=0,
        status=constants.Status.REJECTED, offset=constants.Offset.OPEN, datetime=datetime.now(),
        error_id=31, error_msg="CTP rejected: insufficient funds",
    )))

    assert gateway.orders["OID-REJECTED"].error_msg == "CTP rejected: insufficient funds"
    assert gateway.last_reject_reason == "CTP rejected: insufficient funds"


def test_ctp_order_error_mapping_preserves_error_id_and_original_message():
    constants = _install_mock_vnpy_sys()
    gateway = VnpyGateway()
    gateway._record_order_submission_error(
        {"FrontID": 1, "SessionID": 2, "OrderRef": "3"},
        {"ErrorID": 31, "ErrorMsg": "insufficient funds"},
    )

    gateway._on_vnpy_order(SimpleNamespace(data=SimpleNamespace(
        vt_orderid="CTP.1_2_3", symbol="rb2505", direction=constants.Direction.LONG,
        type=constants.OrderType.LIMIT, price=100.0, volume=1, traded=0,
        status=constants.Status.REJECTED, offset=constants.Offset.OPEN, datetime=datetime.now(),
    )))

    error_msg = gateway.orders["CTP.1_2_3"].error_msg
    assert "ErrorID=31" in error_msg
    assert "insufficient funds" in error_msg


def test_ctp_cancel_error_fails_confirmation_and_keeps_original_order_active():
    gateway = VnpyGateway()
    order_id = "CTP.1_2_3"
    gateway._main_engine = SimpleNamespace(cancel_order=lambda *_args: None)
    gateway._vn_orders[order_id] = SimpleNamespace(create_cancel_request=lambda: object())
    gateway.orders[order_id] = Order(
        order_id=order_id,
        symbol="rb2505",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        price=100.0,
        volume=1,
        status=OrderStatus.SUBMITTED,
    )

    assert gateway.cancel_order(order_id) is True
    gateway._record_cancel_error(
        {"FrontID": 1, "SessionID": 2, "OrderRef": "3"},
        {"ErrorID": 26, "ErrorMsg": "order already filled"},
    )
    outcome = gateway.wait_cancel_confirmation(order_id, timeout=0)

    assert outcome["failed"] is True
    assert outcome["confirmed"] is False
    assert "ErrorID=26" in outcome["error_msg"]
    assert "order already filled" in outcome["error_msg"]
    assert gateway.orders[order_id].status == OrderStatus.SUBMITTED
    assert gateway.orders[order_id].error_msg == outcome["error_msg"]


def test_reconciliation_td_api_forwards_rsp_and_err_rtn_broker_errors(monkeypatch):
    class FakeTdApi:
        def __init__(self, gateway):
            self.gateway = gateway
            self.frontid = 1
            self.sessionid = 2

        def onRspOrderInsert(self, *_args):
            return None

        def onRspOrderAction(self, *_args):
            return None

    class FakeCtpGateway:
        default_name = "CTP"

        def __init__(self, event_engine, gateway_name):
            self.event_engine = event_engine
            self.gateway_name = gateway_name

    fake_module = SimpleNamespace(
        CtpGateway=FakeCtpGateway,
        CtpTdApi=FakeTdApi,
        symbol_contract_map={},
    )
    monkeypatch.setitem(sys.modules, "vnpy_ctp.gateway.ctp_gateway", fake_module)
    gateway = VnpyGateway()
    gateway._cancel_requests["CTP.1_2_3"] = 1.0
    gateway.orders["CTP.1_2_3"] = Order(
        order_id="CTP.1_2_3", symbol="rb2505", direction=Direction.LONG,
        order_type=OrderType.LIMIT, price=100.0, volume=1, status=OrderStatus.SUBMITTED,
    )
    gateway_class = _build_reconciliation_ctp_gateway(gateway)
    td_api = gateway_class(SimpleNamespace(), "CTP").td_api

    td_api.onRspOrderAction(
        {"OrderRef": "3"},
        {"ErrorID": 26, "ErrorMsg": "cancel rejected by exchange"},
        1,
        True,
    )
    assert "cancel rejected by exchange" in gateway.wait_cancel_confirmation(
        "CTP.1_2_3", timeout=0,
    )["error_msg"]

    td_api.onErrRtnOrderInsert(
        {"OrderRef": "4"},
        {"ErrorID": 31, "ErrorMsg": "insufficient funds"},
    )
    assert "insufficient funds" in gateway._broker_order_errors["CTP.1_2_4"]
