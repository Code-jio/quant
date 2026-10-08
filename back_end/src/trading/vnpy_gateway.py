"""
vn.py CTP gateway adapter.

This module keeps the project's internal GatewayBase contract while using
vn.py/vnpy_ctp for the real CTP connection.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from math import isfinite
from .ctp_observer import install_observer, EVENT_SNAPSHOT
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..strategy import (
    Direction,
    OffsetFlag,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    Trade,
)
from .errors import GatewayError
from .gateway import GatewayBase
from .types import AccountInfo, MarketData, TradingStatus

logger = logging.getLogger(__name__)


def _ensure_vnpy_runtime_dir() -> None:
    """Make vn.py use the project-local runtime directory."""
    Path.cwd().joinpath(".vntrader").mkdir(exist_ok=True)


def _extract_product(symbol: str) -> str:
    match = re.match(r"([A-Za-z]+)", symbol)
    return match.group(1).upper() if match else symbol.upper()


class VnpyGateway(GatewayBase):
    """CTP gateway implemented with vn.py and vnpy_ctp."""

    def __init__(self) -> None:
        super().__init__("VNPY_CTP")
        self._gateway_name = "CTP"
        self.connection_state = {key: False for key in ("md", "td", "settlement", "contracts", "account", "positions")}
        self.contract_specs = {}
        self._margin_rates = {}
        self.trading_day = ""
        self._cancel_connect = threading.Event()
        self._refresh_queue = deque()
        self._position_keys = set()
        self._last_query = 0.0
        self._last_snapshot = 0.0
        self._event_engine: Any = None
        self._main_engine: Any = None
        self._connected_event = threading.Event()
        self._error_event = threading.Event()
        self._connect_errors: List[str] = []
        self._connect_log_callback: Any = None
        self._vn_orders: Dict[str, Any] = {}
        self._order_meta: Dict[str, Tuple[str, Any]] = {}
        self.latest_ticks: Dict[str, MarketData] = {}
        self.latest_tick_snapshots: Dict[str, Dict[str, Any]] = {}
        self._subscribed_symbols: set[str] = set()

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect to CTP through vn.py."""
        self._cancel_connect.clear()
        self.connection_state = {key: False for key in self.connection_state}
        self._margin_rates = dict(config.get("contract_margin_rates", {}))
        self.status = TradingStatus.CONNECTING
        self._connected_event.clear()
        self._error_event.clear()
        self._connect_errors.clear()
        self._connect_log_callback = config.get("log_callback")

        try:
            _ensure_vnpy_runtime_dir()
            from vnpy.event import EventEngine, Event
            from vnpy.trader.engine import MainEngine
            from vnpy.trader.event import (
                EVENT_ACCOUNT,
                EVENT_CONTRACT,
                EVENT_TIMER,
                EVENT_LOG,
                EVENT_ORDER,
                EVENT_POSITION,
                EVENT_TICK,
                EVENT_TRADE,
            )
            from vnpy_ctp import CtpGateway
        except ImportError as exc:
            self.status = TradingStatus.ERROR
            raise ImportError("vn.py CTP 依赖未安装，请执行: pip install vnpy vnpy_ctp") from exc

        self._event_engine = EventEngine()
        self._event_engine.register(EVENT_LOG, self._on_vnpy_log)
        self._event_engine.register(EVENT_CONTRACT, self._on_vnpy_contract)
        self._event_engine.register(EVENT_TIMER, self._on_timer)
        self._event_engine.register(EVENT_SNAPSHOT, self._on_snapshot)
        self._event_engine.register(EVENT_ACCOUNT, self._on_vnpy_account)
        self._event_engine.register(EVENT_POSITION, self._on_vnpy_position)
        self._event_engine.register(EVENT_ORDER, self._on_vnpy_order)
        self._event_engine.register(EVENT_TRADE, self._on_vnpy_trade)
        self._event_engine.register(EVENT_TICK, self._on_vnpy_tick)

        self._main_engine = MainEngine(self._event_engine)
        self._main_engine.add_gateway(CtpGateway)
        native = self._main_engine.get_gateway(self._gateway_name)
        event_engine = self._event_engine
        install_observer(native, lambda data: event_engine.put(Event(EVENT_SNAPSHOT, data)))

        setting = {
            "用户名": config.get("username", ""),
            "密码": config.get("password", ""),
            "经纪商代码": config.get("broker_id", ""),
            "交易服务器": config.get("td_server", ""),
            "行情服务器": config.get("md_server", ""),
            "产品名称": config.get("app_id", ""),
            "授权编码": config.get("auth_code", ""),
            "柜台环境": config.get("vnpy_environment", config.get("environment", "实盘")),
        }

        if not all([setting["用户名"], setting["密码"], setting["经纪商代码"], setting["交易服务器"]]):
            self.status = TradingStatus.ERROR
            raise GatewayError("vn.py CTP 连接参数不完整")

        logger.info("[vn.py] connecting CTP gateway")
        self._main_engine.connect(setting, self._gateway_name)

        timeout = float(config.get("connect_timeout", 25))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._cancel_connect.is_set():
            if self._connected_event.wait(timeout=0.2) and self.ready:
                self.status = TradingStatus.CONNECTED
                return True
            if self._error_event.is_set():
                self.status = TradingStatus.ERROR
                return False

        self.disconnect()
        self.status = TradingStatus.ERROR
        return False

    def connection_error_summary(self) -> str:
        """Return a concise summary of CTP connection errors captured during login."""
        return "；".join(self._connect_errors[-5:])

    def disconnect(self) -> None:
        """Disconnect CTP and stop vn.py event engine."""
        self._cancel_connect.set()
        self.connection_state = {key: False for key in self.connection_state}
        self._connected_event.clear()
        try:
            if self._main_engine:
                self._main_engine.close()
        except Exception as exc:
            logger.error("[vn.py] disconnect failed: %s", exc)
        finally:
            self._main_engine = None
            self._event_engine = None
            self.status = TradingStatus.STOPPED

    def send_order(self, signal: Signal) -> str:
        """Send an order through vn.py."""
        if not self.ready:
            logger.warning("vn.py CTP 未连接，无法发送订单")
            return ""
        if not self._main_engine:
            return ""

        spec = self.contract_specs.get(signal.symbol.split(".")[0], {})
        tick = float(spec.get("pricetick") or 0)
        unit = int(spec.get("min_volume") or 1)
        if not spec or signal.order_type != OrderType.LIMIT or not signal.validate():
            raise ValueError("Only validated limit orders for known contracts are supported")
        if tick <= 0 or abs(signal.price / tick - round(signal.price / tick)) > 1e-7 or signal.volume % unit:
            raise ValueError("Invalid contract price tick or minimum volume")
        from vnpy.trader.object import OrderRequest

        symbol, exchange = self._split_symbol(signal.symbol)
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=self._to_vnpy_direction(signal.direction),
            type=self._to_vnpy_order_type(signal.order_type),
            volume=float(signal.volume),
            price=float(signal.price or 0),
            offset=self._to_vnpy_offset(signal.offset),
            reference="quant-api",
        )

        vt_orderid = self._main_engine.send_order(req, self._gateway_name)
        if not vt_orderid:
            return ""

        self._order_meta[vt_orderid] = (symbol, exchange)
        return vt_orderid

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order."""
        if not self._main_engine:
            return False

        from vnpy.trader.object import CancelRequest

        vn_order = self._vn_orders.get(order_id)
        if vn_order:
            req = vn_order.create_cancel_request()
        else:
            raw_order_id = order_id.split(".", 1)[1] if "." in order_id else order_id
            symbol, exchange = self._order_meta.get(order_id, (None, None))
            if not symbol:
                order = self.orders.get(order_id)
                if not order:
                    return False
                symbol, exchange = self._split_symbol(order.symbol)
            req = CancelRequest(orderid=raw_order_id, symbol=symbol, exchange=exchange)

        self._main_engine.cancel_order(req, self._gateway_name)
        return True

    def query_account(self) -> AccountInfo:
        self.request_refresh()
        return self.account

    def query_positions(self) -> List[Position]:
        self.request_refresh()
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        return list(self.orders.values())

    def subscribe_market_data(self, symbols: List[str]) -> None:
        """Subscribe ticks through vn.py CTP market data API."""
        if not self._main_engine:
            return

        from vnpy.trader.object import SubscribeRequest

        for item in symbols:
            symbol, exchange = self._split_symbol(item)
            vt_key = f"{symbol}.{getattr(exchange, 'value', exchange)}"
            if vt_key in self._subscribed_symbols:
                continue
            req = SubscribeRequest(symbol=symbol, exchange=exchange)
            self._main_engine.subscribe(req, self._gateway_name)
            self._subscribed_symbols.add(vt_key)

    def _on_vnpy_log(self, event: Any) -> None:
        log = event.data
        msg = getattr(log, "msg", str(log))
        logger.info("[vn.py] %s", msg)
        self._emit_connect_log(msg)

        if self.status == TradingStatus.CONNECTING and self._is_connect_error(msg):
            self._remember_connect_error(msg)

    @property
    def ready(self):
        return (
            all(self.connection_state.values())
            and self.account.fields_known
            and bool(self.trading_day)
            and time.monotonic() - self._last_snapshot < 60
            and not self._cancel_connect.is_set()
        )

    def request_refresh(self):
        if not self._refresh_queue and time.monotonic() - self._last_query > 3:
            self._refresh_queue.extend(("account", "position"))

    def _on_timer(self, _event):
        if self.connection_state["contracts"] and self.connection_state["td"]:
            if not self._refresh_queue and time.monotonic() - self._last_query > 15:
                self.request_refresh()
            if self._refresh_queue and time.monotonic() - self._last_query > 1:
                native = self._main_engine.get_gateway(self._gateway_name) if self._main_engine else None
                if native:
                    getattr(native, "query_" + self._refresh_queue.popleft())()
                    self._last_query = time.monotonic()

    def _on_snapshot(self, event):
        data = event.data
        kind = data["kind"]
        if data.get("trading_day"):
            self.trading_day = data["trading_day"]
        self.connection_state[kind] = data.get("ready", True)
        if kind == "positions" and data.get("ready", True):
            keys = set(data["keys"])
            for key in list(self.positions):
                if key not in keys:
                    self.positions.pop(key, None)
            self._last_snapshot = time.monotonic()
        elif kind in ("td", "md") and not data.get("ready"):
            self._connected_event.clear()
            self.connection_state["positions"] = False
            self.connection_state["account"] = False
            self._subscribed_symbols.clear()
            if kind == "td":
                self.connection_state["settlement"] = False
        if kind in ("contracts", "td", "settlement") and data.get("ready"):
            self.request_refresh()
        if self.ready:
            self._connected_event.set()

    def _on_vnpy_contract(self, event):
        data = event.data
        symbol = data.symbol
        margin = self._margin_rates.get(symbol)
        self.contract_specs[symbol] = {
            "symbol": symbol,
            "name": data.name,
            "exchange": getattr(data.exchange, "value", str(data.exchange)),
            "size": float(data.size),
            "pricetick": float(data.pricetick),
            "min_volume": int(getattr(data, "min_volume", 1) or 1),
            "margin_rate": float(margin) if margin else None,
            "source": "ctp",
            "tradable": True,
        }

    def _emit_connect_log(self, msg: str) -> None:
        if self._connect_log_callback:
            try:
                self._connect_log_callback(f"vn.py: {msg}")
            except Exception:
                logger.exception("[vn.py] connect log callback failed")

    def _remember_connect_error(self, msg: str) -> None:
        if msg and msg not in self._connect_errors:
            self._connect_errors.append(msg)

    @staticmethod
    def _is_connect_error(msg: str) -> bool:
        error_keywords = (
            "失败",
            "拒绝",
            "报错",
            "错误",
            "断开",
            "decode err",
            "shake hand err",
        )
        normalized_msg = msg.lower()
        return any(keyword.lower() in normalized_msg for keyword in error_keywords)

    def _on_vnpy_account(self, event: Any) -> None:
        data = event.data
        extra = getattr(data, "extra", None) or {}
        account = AccountInfo(
            account_id=getattr(data, "accountid", ""),
            balance=float(getattr(data, "balance", 0) or 0),
            available=float(extra.get("Available", getattr(data, "available", 0)) or 0),
            margin=float(extra.get("CurrMargin", 0)),
            commission=float(extra.get("Commission", 0)),
            position_pnl=float(extra.get("PositionProfit", 0)),
            total_pnl=float(extra.get("PositionProfit", 0)) + float(extra.get("CloseProfit", 0)),
            trading_day=str(extra.get("TradingDay") or self.trading_day),
            fields_known=all(
                key in extra for key in ("Available", "CurrMargin", "Commission", "PositionProfit", "CloseProfit")
            ),
        )
        self.account = account
        self.on_account(account)
        self.connection_state["account"] = account.fields_known
        self.trading_day = account.trading_day or self.trading_day
        if self.ready:
            self._connected_event.set()

    def _on_vnpy_position(self, event: Any) -> None:
        data = event.data
        direction = self._from_vnpy_direction(getattr(data, "direction", None))
        volume = int(getattr(data, "volume", 0) or 0)
        symbol = getattr(data, "symbol", "")
        pos = Position(
            symbol=symbol,
            direction=direction,
            volume=volume,
            yd_volume=int(getattr(data, "yd_volume", 0)),
            exchange=getattr(getattr(data, "exchange", None), "value", ""),
            frozen=int(getattr(data, "frozen", 0) or 0),
            price=float(getattr(data, "price", 0) or 0),
            cost=float(getattr(data, "price", 0) or 0),
            pnl=float(getattr(data, "pnl", 0) or 0),
        )
        self.positions[f"{symbol}_{direction.value}"] = pos
        self.on_position(pos)

    def _on_vnpy_order(self, event: Any) -> None:
        data = event.data
        vt_orderid = getattr(data, "vt_orderid", "")
        order = Order(
            order_id=vt_orderid,
            symbol=getattr(data, "symbol", ""),
            direction=self._from_vnpy_direction(getattr(data, "direction", None)),
            order_type=self._from_vnpy_order_type(getattr(data, "type", None)),
            price=float(getattr(data, "price", 0) or 0),
            volume=int(getattr(data, "volume", 0) or 0),
            traded_volume=int(getattr(data, "traded", 0) or 0),
            status=self._from_vnpy_status(getattr(data, "status", None)),
            offset=self._from_vnpy_offset(getattr(data, "offset", None)),
            create_time=getattr(data, "datetime", None) or datetime.now(),
            update_time=datetime.now(),
        )
        self._vn_orders[vt_orderid] = data
        self.on_order(order)

    def _on_vnpy_trade(self, event: Any) -> None:
        data = event.data
        trade = Trade(
            trade_id=getattr(data, "vt_tradeid", "") or getattr(data, "tradeid", ""),
            order_id=getattr(data, "vt_orderid", "") or getattr(data, "orderid", ""),
            symbol=getattr(data, "symbol", ""),
            direction=self._from_vnpy_direction(getattr(data, "direction", None)),
            price=float(getattr(data, "price", 0) or 0),
            volume=int(getattr(data, "volume", 0) or 0),
            commission=float(getattr(data, "commission", 0) or getattr(data, "fee", 0) or 0),
            pnl=float(getattr(data, "pnl", 0) or getattr(data, "profit", 0) or 0),
            trade_time=getattr(data, "datetime", None) or datetime.now(),
            offset=self._from_vnpy_offset(getattr(data, "offset", None)),
            account_id=self.account.account_id,
            trading_day=(getattr(data, "extra", None) or {}).get("TradingDay", self.trading_day),
            exchange=getattr(getattr(data, "exchange", None), "value", ""),
            pnl_known=hasattr(data, "pnl") or hasattr(data, "profit"),
            commission_known=hasattr(data, "commission") or hasattr(data, "fee"),
        )
        self.connection_state["positions"] = False
        self.request_refresh()
        self.on_trade(trade)

    def _on_vnpy_tick(self, event: Any) -> None:
        data = event.data
        symbol = getattr(data, "symbol", "")
        tick = MarketData(
            symbol=symbol,
            last_price=float(getattr(data, "last_price", 0) or 0),
            bid_price_1=float(getattr(data, "bid_price_1", 0) or 0),
            ask_price_1=float(getattr(data, "ask_price_1", 0) or 0),
            bid_volume_1=int(getattr(data, "bid_volume_1", 0) or 0),
            ask_volume_1=int(getattr(data, "ask_volume_1", 0) or 0),
            volume=int(getattr(data, "volume", 0) or 0),
            turnover=float(getattr(data, "turnover", 0) or 0),
            timestamp=getattr(data, "datetime", None) or datetime.now(),
            trading_day=(getattr(data, "extra", None) or {}).get("TradingDay", self.trading_day),
        )
        snapshot = self._tick_to_snapshot(data, tick)
        for key in self._tick_cache_keys(data, symbol):
            self.latest_ticks[key] = tick
            self.latest_tick_snapshots[key] = snapshot
        self.on_tick(tick)

    @staticmethod
    def _tick_cache_keys(data: Any, symbol: str) -> set[str]:
        keys = {symbol}
        vt_symbol = getattr(data, "vt_symbol", "")
        if vt_symbol:
            keys.add(str(vt_symbol))
        exchange = getattr(data, "exchange", "")
        exchange_value = getattr(exchange, "value", "")
        exchange_name = getattr(exchange, "name", "")
        if exchange_value:
            keys.add(f"{symbol}.{exchange_value}")
            keys.add(f"{exchange_value}.{symbol}")
        if exchange_name:
            keys.add(f"{symbol}.{exchange_name}")
            keys.add(f"{exchange_name}.{symbol}")
        return {key for key in keys if key}

    @staticmethod
    def _price_field(data: Any, field: str, fallback: float = 0.0) -> float:
        try:
            value = float(getattr(data, field, fallback) or fallback)
        except (TypeError, ValueError):
            value = fallback
        return value

    @classmethod
    def _tick_to_snapshot(cls, data: Any, tick: MarketData) -> Dict[str, Any]:
        last = tick.last_price
        pre_close = cls._price_field(data, "pre_close", 0.0)
        change = round(last - pre_close, 4) if pre_close else 0.0
        change_rate = round(change / pre_close * 100, 4) if pre_close else 0.0
        ts = tick.timestamp if hasattr(tick.timestamp, "isoformat") else datetime.now()

        snapshot: Dict[str, Any] = {
            "type": "tick",
            "source": "vnpy",
            "trading_day": tick.trading_day,
            "symbol": tick.symbol,
            "last": last,
            "open": cls._price_field(data, "open_price", 0.0),
            "high": cls._price_field(data, "high_price", 0.0),
            "low": cls._price_field(data, "low_price", 0.0),
            "pre_close": pre_close,
            "volume": tick.volume,
            "turnover": tick.turnover,
            "open_interest": int(getattr(data, "open_interest", 0) or 0),
            "change": change,
            "change_rate": change_rate,
            "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        }

        for level in range(1, 6):
            snapshot[f"bid{level}"] = cls._price_field(data, f"bid_price_{level}", 0.0)
            snapshot[f"ask{level}"] = cls._price_field(data, f"ask_price_{level}", 0.0)
            snapshot[f"bid{level}_vol"] = int(getattr(data, f"bid_volume_{level}", 0) or 0)
            snapshot[f"ask{level}_vol"] = int(getattr(data, f"ask_volume_{level}", 0) or 0)
        return snapshot

    def _split_symbol(self, symbol: str) -> Tuple[str, Any]:
        parts = symbol.split(".")
        code = next((part for part in parts if part in self.contract_specs), None)
        if not code:
            raise ValueError("Unknown contract: " + symbol)
        spec = self.contract_specs[code]
        if len(parts) > 1 and spec["exchange"] not in parts:
            raise ValueError("Mismatched contract exchange")
        from vnpy.trader.constant import Exchange

        return code, Exchange(spec["exchange"])

    @staticmethod
    def _to_vnpy_direction(direction: Direction) -> Any:
        from vnpy.trader.constant import Direction as VnDirection

        return VnDirection.LONG if direction == Direction.LONG else VnDirection.SHORT

    @staticmethod
    def _from_vnpy_direction(direction: Any) -> Direction:
        from vnpy.trader.constant import Direction as VnDirection

        if direction == VnDirection.LONG:
            return Direction.LONG
        if direction == VnDirection.SHORT:
            return Direction.SHORT
        return Direction.NET

    @staticmethod
    def _to_vnpy_order_type(order_type: OrderType) -> Any:
        from vnpy.trader.constant import OrderType as VnOrderType

        if order_type == OrderType.LIMIT:
            return VnOrderType.LIMIT
        if order_type == OrderType.STOP:
            return VnOrderType.STOP
        return VnOrderType.MARKET

    @staticmethod
    def _from_vnpy_order_type(order_type: Any) -> OrderType:
        from vnpy.trader.constant import OrderType as VnOrderType

        if order_type == VnOrderType.LIMIT:
            return OrderType.LIMIT
        if order_type == VnOrderType.STOP:
            return OrderType.STOP
        return OrderType.MARKET

    @staticmethod
    def _to_vnpy_offset(offset: OffsetFlag) -> Any:
        from vnpy.trader.constant import Offset

        mapping = {
            OffsetFlag.OPEN: Offset.OPEN,
            OffsetFlag.CLOSE: Offset.CLOSE,
            OffsetFlag.CLOSE_TODAY: Offset.CLOSETODAY,
            OffsetFlag.CLOSE_YESTERDAY: Offset.CLOSEYESTERDAY,
        }
        return mapping.get(offset, Offset.NONE)

    @staticmethod
    def _from_vnpy_offset(offset: Any) -> OffsetFlag:
        from vnpy.trader.constant import Offset

        mapping = {
            Offset.OPEN: OffsetFlag.OPEN,
            Offset.CLOSE: OffsetFlag.CLOSE,
            Offset.CLOSETODAY: OffsetFlag.CLOSE_TODAY,
            Offset.CLOSEYESTERDAY: OffsetFlag.CLOSE_YESTERDAY,
        }
        if offset not in mapping:
            raise ValueError("Unknown broker offset; reconciliation required")
        return mapping[offset]

    @staticmethod
    def _from_vnpy_status(status: Any) -> OrderStatus:
        from vnpy.trader.constant import Status

        mapping = {
            Status.SUBMITTING: OrderStatus.SUBMITTING,
            Status.NOTTRADED: OrderStatus.SUBMITTED,
            Status.PARTTRADED: OrderStatus.PARTFILLED,
            Status.ALLTRADED: OrderStatus.FILLED,
            Status.CANCELLED: OrderStatus.CANCELLED,
            Status.REJECTED: OrderStatus.REJECTED,
        }
        return mapping.get(status, OrderStatus.SUBMITTING)


def create_vnpy_gateway() -> VnpyGateway:
    return VnpyGateway()
