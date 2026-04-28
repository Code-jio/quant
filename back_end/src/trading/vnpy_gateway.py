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


PRODUCT_EXCHANGE = {
    "IF": "CFFEX", "IC": "CFFEX", "IH": "CFFEX", "IM": "CFFEX",
    "T": "CFFEX", "TF": "CFFEX", "TS": "CFFEX", "TL": "CFFEX",
    "CU": "SHFE", "AU": "SHFE", "AG": "SHFE", "RB": "SHFE",
    "AL": "SHFE", "ZN": "SHFE", "PB": "SHFE", "NI": "SHFE",
    "SN": "SHFE", "FU": "SHFE", "BU": "SHFE", "HC": "SHFE",
    "RU": "SHFE", "SP": "SHFE", "SS": "SHFE", "AO": "SHFE",
    "SC": "INE", "NR": "INE", "BC": "INE", "LU": "INE",
    "A": "DCE", "B": "DCE", "C": "DCE", "CS": "DCE",
    "M": "DCE", "Y": "DCE", "P": "DCE", "L": "DCE",
    "V": "DCE", "PP": "DCE", "J": "DCE", "JM": "DCE",
    "I": "DCE", "EG": "DCE", "EB": "DCE", "PG": "DCE",
    "LH": "DCE",
    "CF": "CZCE", "SR": "CZCE", "TA": "CZCE", "MA": "CZCE",
    "OI": "CZCE", "RM": "CZCE", "ZC": "CZCE", "FG": "CZCE",
    "SA": "CZCE", "UR": "CZCE", "AP": "CZCE", "CJ": "CZCE",
    "PK": "CZCE", "PF": "CZCE", "PX": "CZCE", "SH": "CZCE",
    "SI": "GFEX", "LC": "GFEX",
}


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
        self._event_engine: Any = None
        self._main_engine: Any = None
        self._connected_event = threading.Event()
        self._error_event = threading.Event()
        self._connect_errors: List[str] = []
        self._vn_orders: Dict[str, Any] = {}
        self._order_meta: Dict[str, Tuple[str, Any]] = {}
        self.latest_ticks: Dict[str, MarketData] = {}

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect to CTP through vn.py."""
        self.status = TradingStatus.CONNECTING
        self._connected_event.clear()
        self._error_event.clear()
        self._connect_errors.clear()

        try:
            _ensure_vnpy_runtime_dir()
            from vnpy.event import EventEngine
            from vnpy.trader.engine import MainEngine
            from vnpy.trader.event import (
                EVENT_ACCOUNT,
                EVENT_LOG,
                EVENT_ORDER,
                EVENT_POSITION,
                EVENT_TICK,
                EVENT_TRADE,
            )
            from vnpy_ctp import CtpGateway
        except ImportError as exc:
            self.status = TradingStatus.ERROR
            raise ImportError(
                "vn.py CTP 依赖未安装，请执行: pip install vnpy vnpy_ctp"
            ) from exc

        self._event_engine = EventEngine()
        self._event_engine.register(EVENT_LOG, self._on_vnpy_log)
        self._event_engine.register(EVENT_ACCOUNT, self._on_vnpy_account)
        self._event_engine.register(EVENT_POSITION, self._on_vnpy_position)
        self._event_engine.register(EVENT_ORDER, self._on_vnpy_order)
        self._event_engine.register(EVENT_TRADE, self._on_vnpy_trade)
        self._event_engine.register(EVENT_TICK, self._on_vnpy_tick)

        self._main_engine = MainEngine(self._event_engine)
        self._main_engine.add_gateway(CtpGateway)

        setting = {
            "用户名": config.get("username", ""),
            "密码": config.get("password", ""),
            "经纪商代码": config.get("broker_id", ""),
            "交易服务器": config.get("td_server", ""),
            "行情服务器": config.get("md_server", ""),
            "产品名称": config.get("app_id", ""),
            "授权编码": config.get("auth_code", ""),
            "柜台环境": config.get("vnpy_environment", config.get("environment", "测试")),
        }

        if not all([setting["用户名"], setting["密码"], setting["经纪商代码"], setting["交易服务器"]]):
            self.status = TradingStatus.ERROR
            raise GatewayError("vn.py CTP 连接参数不完整")

        logger.info("[vn.py] connecting CTP gateway")
        self._main_engine.connect(setting, self._gateway_name)

        timeout = float(config.get("connect_timeout", 25))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connected_event.wait(timeout=0.2):
                self.status = TradingStatus.CONNECTED
                return True
            if self._error_event.is_set():
                self.status = TradingStatus.ERROR
                return False

        self.status = TradingStatus.ERROR
        return False

    def disconnect(self) -> None:
        """Disconnect CTP and stop vn.py event engine."""
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
        if self.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
            logger.warning("vn.py CTP 未连接，无法发送订单")
            return ""
        if not self._main_engine:
            return ""

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
        order = Order(
            order_id=vt_orderid,
            symbol=symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            status=OrderStatus.SUBMITTING,
            offset=signal.offset,
        )
        self.orders[vt_orderid] = order
        self.on_order(order)
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
        return self.account

    def query_positions(self) -> List[Position]:
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
            req = SubscribeRequest(symbol=symbol, exchange=exchange)
            self._main_engine.subscribe(req, self._gateway_name)

    def _on_vnpy_log(self, event: Any) -> None:
        log = event.data
        msg = getattr(log, "msg", str(log))
        logger.info("[vn.py] %s", msg)

        if self.status == TradingStatus.CONNECTING:
            if "结算信息确认成功" in msg or "合约信息查询成功" in msg:
                self._connected_event.set()
            elif "失败" in msg or "拒绝" in msg:
                self._connect_errors.append(msg)
                self._error_event.set()

    def _on_vnpy_account(self, event: Any) -> None:
        data = event.data
        account = AccountInfo(
            account_id=getattr(data, "accountid", ""),
            balance=float(getattr(data, "balance", 0) or 0),
            available=float(getattr(data, "available", 0) or 0),
            margin=float(getattr(data, "frozen", 0) or 0),
        )
        self.account = account
        self.on_account(account)
        if self.status == TradingStatus.CONNECTING:
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
            frozen=int(getattr(data, "frozen", 0) or 0),
            price=float(getattr(data, "price", 0) or 0),
            cost=float(getattr(data, "price", 0) or 0) * volume,
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
        self.orders[vt_orderid] = order
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
            trade_time=getattr(data, "datetime", None) or datetime.now(),
        )
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
        )
        self.latest_ticks[symbol] = tick
        self.on_tick(tick)

    @staticmethod
    def _split_symbol(symbol: str) -> Tuple[str, Any]:
        from vnpy.trader.constant import Exchange

        if "." in symbol:
            left, right = symbol.split(".", 1)
            if left.upper() in Exchange.__members__:
                return right, Exchange[left.upper()]
            for exchange in Exchange:
                if right.upper() == exchange.value:
                    return left, exchange

        product = _extract_product(symbol)
        exchange_code = PRODUCT_EXCHANGE.get(product, "SHFE")
        return symbol, Exchange(exchange_code)

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
        return mapping.get(offset, OffsetFlag.OPEN)

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
