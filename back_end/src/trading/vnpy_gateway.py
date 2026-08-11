"""
vn.py CTP gateway adapter.

This module keeps the project's internal GatewayBase contract while using
vn.py/vnpy_ctp for the real CTP connection.
"""

from __future__ import annotations

import logging
import math
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
from .symbols import PRODUCT_EXCHANGE, extract_product, is_supported_symbol, symbol_key
from .types import AccountInfo, MarketData, TradingStatus

logger = logging.getLogger(__name__)

_RECONCILIATION_EVENT = "eQuantTrialReconciliation"


def _ctp_contracts_ready(td_api: Any, contract_map: Dict[str, Any]) -> bool:
    """Require the CTP contract query to finish, not merely yield some rows."""
    return bool(getattr(td_api, "contract_inited", False) and contract_map)


def _build_reconciliation_ctp_gateway(adapter: "VnpyGateway") -> Any:
    """Build a CTP gateway that exposes query-completion fences to the adapter."""
    from vnpy.event import Event
    from vnpy_ctp.gateway.ctp_gateway import CtpGateway, CtpTdApi, symbol_contract_map

    class ReconciliationTdApi(CtpTdApi):
        def __init__(self, gateway: Any) -> None:
            super().__init__(gateway)
            self._snapshot_reqids: Dict[str, int] = {}
            self._snapshot_counts: Dict[str, int] = {}

        def onRspUserLogin(
            self,
            data: Dict[str, Any],
            error: Dict[str, Any],
            reqid: int,
            last: bool,
        ) -> None:
            if not int((error or {}).get("ErrorID", 0) or 0):
                adapter._set_trading_day(str((data or {}).get("TradingDay", "") or ""))
            super().onRspUserLogin(data, error, reqid, last)

        def _emit_completion(
            self,
            kind: str,
            error: Dict[str, Any],
            request_id: int,
        ) -> None:
            payload = {
                "kind": kind,
                "request_id": int(request_id),
                "error_id": int((error or {}).get("ErrorID", 0) or 0),
                "error_msg": str((error or {}).get("ErrorMsg", "") or ""),
                "item_count": self._snapshot_counts.pop(kind, 0),
            }
            self.gateway.event_engine.put(Event(_RECONCILIATION_EVENT, payload))

        def onRspQryInvestorPosition(
            self,
            data: Dict[str, Any],
            error: Dict[str, Any],
            reqid: int,
            last: bool,
        ) -> None:
            is_snapshot = self._snapshot_reqids.get("positions") == reqid
            if is_snapshot and data and data.get("InstrumentID"):
                self._snapshot_counts["positions"] += 1
            try:
                super().onRspQryInvestorPosition(data, error, reqid, last)
            finally:
                if last and is_snapshot:
                    self._snapshot_reqids.pop("positions", None)
                    self._emit_completion("positions", error, reqid)

        def onRspQryTradingAccount(
            self,
            data: Dict[str, Any],
            error: Dict[str, Any],
            reqid: int,
            last: bool,
        ) -> None:
            is_snapshot = self._snapshot_reqids.get("account") == reqid
            if is_snapshot and data and data.get("AccountID"):
                self._snapshot_counts["account"] += 1
            try:
                super().onRspQryTradingAccount(data, error, reqid, last)
            finally:
                if last and is_snapshot:
                    self._snapshot_reqids.pop("account", None)
                    self._emit_completion("account", error, reqid)

        def onRspQryOrder(
            self,
            data: Dict[str, Any],
            error: Dict[str, Any],
            reqid: int,
            last: bool,
        ) -> None:
            is_snapshot = self._snapshot_reqids.get("orders") == reqid
            if is_snapshot and data and data.get("InstrumentID"):
                self._snapshot_counts["orders"] += 1
            if data and data.get("InstrumentID") and not (error or {}).get("ErrorID"):
                self.onRtnOrder(data)
            if last and is_snapshot:
                self._snapshot_reqids.pop("orders", None)
                self._emit_completion("orders", error, reqid)

        def onRspQryInstrument(
            self,
            data: Dict[str, Any],
            error: Dict[str, Any],
            reqid: int,
            last: bool,
        ) -> None:
            adapter._begin_contract_capability_snapshot(reqid)
            if data and data.get("InstrumentID"):
                adapter._record_contract_capability(data)
            super().onRspQryInstrument(data, error, reqid, last)

        def query_positions_snapshot(self) -> int:
            if not _ctp_contracts_ready(self, symbol_contract_map):
                return -99
            request = {"BrokerID": self.brokerid, "InvestorID": self.userid}
            self.reqid += 1
            self._snapshot_reqids["positions"] = self.reqid
            self._snapshot_counts["positions"] = 0
            adapter._arm_reconciliation_request("positions", self.reqid)
            return int(self.reqQryInvestorPosition(request, self.reqid) or 0)

        def query_account_snapshot(self) -> int:
            self.reqid += 1
            self._snapshot_reqids["account"] = self.reqid
            self._snapshot_counts["account"] = 0
            adapter._arm_reconciliation_request("account", self.reqid)
            return int(self.reqQryTradingAccount({}, self.reqid) or 0)

        def query_orders_snapshot(self) -> int:
            if not _ctp_contracts_ready(self, symbol_contract_map):
                return -99
            request = {"BrokerID": self.brokerid, "InvestorID": self.userid}
            self.reqid += 1
            self._snapshot_reqids["orders"] = self.reqid
            self._snapshot_counts["orders"] = 0
            adapter._arm_reconciliation_request("orders", self.reqid)
            return int(self.reqQryOrder(request, self.reqid) or 0)

    class ReconciliationCtpGateway(CtpGateway):
        default_name = CtpGateway.default_name

        def __init__(self, event_engine: Any, gateway_name: str) -> None:
            super().__init__(event_engine, gateway_name)
            self.td_api = ReconciliationTdApi(self)

        def query_positions_snapshot(self) -> int:
            return self.td_api.query_positions_snapshot()

        def query_account_snapshot(self) -> int:
            return self.td_api.query_account_snapshot()

        def query_orders_snapshot(self) -> int:
            return self.td_api.query_orders_snapshot()

    ReconciliationCtpGateway.__name__ = "QuantReconciliationCtpGateway"
    adapter._reconciliation_gateway_class = ReconciliationCtpGateway
    return ReconciliationCtpGateway


def _ensure_vnpy_runtime_dir() -> None:
    """Make vn.py use the project-local runtime directory."""
    Path.cwd().joinpath(".vntrader").mkdir(exist_ok=True)


def _extract_product(symbol: str) -> str:
    return extract_product(symbol)


class VnpyGateway(GatewayBase):
    """CTP gateway implemented with vn.py and vnpy_ctp."""

    def __init__(self) -> None:
        super().__init__("VNPY_CTP")
        self.requires_persistent_risk_state = True
        self._gateway_name = "CTP"
        self._event_engine: Any = None
        self._main_engine: Any = None
        self._connected_event = threading.Event()
        self._error_event = threading.Event()
        self._connect_errors: List[str] = []
        self._connect_log_callback: Any = None
        self._connection_lock = threading.RLock()
        self._td_connected = False
        self._md_connected = False
        self._contracts_ready = False
        self._reconciliation_ready = False
        self._reconnecting = False
        self._reconnect_count = 0
        self._last_disconnect_reason = ""
        self._connection_changed_at = datetime.now().isoformat()
        self._restore_status = TradingStatus.CONNECTED
        self._connection_outage = False
        self._connection_generation = 0
        self._reconciliation_worker: threading.Thread | None = None
        self._reconciliation_timeout_seconds = 8.0
        self._contract_capabilities: Dict[str, Dict[str, Any]] = {}
        self._contract_capability_request_id: int | None = None
        self.last_reject_reason = ""
        self.trading_day = ""
        self.on_trading_day_callback: Any = None
        self._vn_orders: Dict[str, Any] = {}
        self._order_meta: Dict[str, Tuple[str, Any]] = {}
        self.latest_ticks: Dict[str, MarketData] = {}
        self.latest_tick_snapshots: Dict[str, Dict[str, Any]] = {}
        self._subscribed_symbols: set[str] = set()
        self._reconciliation_gateway_class: Any = None
        self._reconciliation_lock = threading.RLock()
        self._reconciliation_capture_lock = threading.RLock()
        self._reconciliation_capture: Dict[str, Any] | None = None
        self._reconciliation_events = {
            "orders": threading.Event(),
            "positions": threading.Event(),
            "account": threading.Event(),
        }
        self._reconciliation_results: Dict[str, Dict[str, Any]] = {}
        self._reconciliation_expected_reqids: Dict[str, int] = {}

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect to CTP through vn.py."""
        self.status = TradingStatus.CONNECTING
        with self._connection_lock:
            self._td_connected = False
            self._md_connected = False
            self._contracts_ready = False
            self._reconciliation_ready = False
            self._reconnecting = False
            self._connection_outage = False
            self._connection_generation += 1
            self._contract_capabilities.clear()
            self._contract_capability_request_id = None
            self.trading_day = ""
            self._last_disconnect_reason = ""
            self._connection_changed_at = datetime.now().isoformat()
        self._connected_event.clear()
        self._error_event.clear()
        self._connect_errors.clear()
        self._connect_log_callback = config.get("log_callback")
        self._reconciliation_timeout_seconds = max(
            0.1,
            float(config.get("reconciliation_timeout", 8.0)),
        )

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
            CtpGateway = _build_reconciliation_ctp_gateway(self)
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
        self._event_engine.register(_RECONCILIATION_EVENT, self._on_reconciliation_event)

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
                reconciliation = self.refresh_reconciliation(
                    timeout_seconds=self._reconciliation_timeout_seconds,
                )
                if reconciliation.get("ok") is True and reconciliation.get("fresh") is True:
                    with self._connection_lock:
                        reconciliation_ready = self._reconciliation_ready
                        trading_day = self.trading_day
                    if reconciliation_ready and trading_day:
                        return True
                    if reconciliation_ready:
                        failure_code = "broker_trading_day_unavailable"
                    else:
                        failure_code = str(
                            reconciliation.get("failure_code")
                            or "broker_snapshot_unavailable"
                        )
                else:
                    failure_code = str(
                        reconciliation.get("failure_code")
                        or "broker_snapshot_unavailable"
                    )
                self._remember_connect_error(f"券商权威对账失败: {failure_code}")
                self.status = TradingStatus.ERROR
                return False
            if self._error_event.is_set():
                self.status = TradingStatus.ERROR
                return False

        self.status = TradingStatus.ERROR
        return False

    def connection_error_summary(self) -> str:
        """Return a concise summary of CTP connection errors captured during login."""
        return "；".join(self._connect_errors[-5:])

    def connection_snapshot(self) -> Dict[str, Any]:
        """Return a thread-safe snapshot of the independent CTP channels."""
        with self._connection_lock:
            self._refresh_channel_health_from_vnpy()
            channels_ready = self._td_connected and self._md_connected
            return {
                "td_connected": self._td_connected,
                "md_connected": self._md_connected,
                "fully_connected": channels_ready,
                "contracts_ready": self._contracts_ready,
                "reconciliation_ready": self._reconciliation_ready,
                "trading_day": self.trading_day,
                "order_entry_ready": (
                    channels_ready
                    and self._contracts_ready
                    and self._reconciliation_ready
                    and not self._reconnecting
                    and self.status in (TradingStatus.CONNECTED, TradingStatus.TRADING)
                ),
                "reconnecting": self._reconnecting,
                "reconnect_count": self._reconnect_count,
                "last_disconnect_reason": self._last_disconnect_reason,
                "changed_at": self._connection_changed_at,
            }

    def _refresh_channel_health_from_vnpy(self) -> None:
        """Calibrate channel health from the live vn.py API login flags."""
        if not self._main_engine:
            return
        try:
            gateway = self._main_engine.get_gateway(self._gateway_name)
        except Exception:
            return
        if gateway is None:
            return

        observed: Dict[str, bool] = {}
        for channel, api_name in (("td", "td_api"), ("md", "md_api")):
            api = getattr(gateway, api_name, None)
            if api is None or not hasattr(api, "login_status"):
                continue
            try:
                observed[channel] = bool(api.login_status)
            except Exception:
                continue

        lost_channels = tuple(
            channel
            for channel, logged_in in observed.items()
            if not logged_in and getattr(self, f"_{channel}_connected")
        )
        if lost_channels:
            labels = "/".join(channel.upper() for channel in lost_channels)
            self._mark_channels_disconnected(
                lost_channels,
                f"CTP native login_status lost: {labels}",
            )

        restored = False
        for channel, logged_in in observed.items():
            if logged_in and not getattr(self, f"_{channel}_connected"):
                setattr(self, f"_{channel}_connected", True)
                restored = True
        if restored:
            self._connection_changed_at = datetime.now().isoformat()
            self._restore_connection_if_ready()

    def _set_channel_connected(self, channel: str) -> None:
        attr = f"_{channel}_connected"
        if not getattr(self, attr):
            setattr(self, attr, True)
            self._connection_changed_at = datetime.now().isoformat()
        self._restore_connection_if_ready()

    def _set_contracts_ready(self) -> None:
        if not self._contracts_ready:
            self._contracts_ready = True
            self._connection_changed_at = datetime.now().isoformat()
        self._restore_connection_if_ready()

    def _set_trading_day(self, trading_day: str) -> None:
        raw = str(trading_day or "").strip()
        compact = raw.replace("-", "")
        if len(compact) != 8 or not compact.isdigit():
            return
        try:
            normalized = datetime.strptime(compact, "%Y%m%d").date().isoformat()
        except ValueError:
            return

        callback = None
        with self._connection_lock:
            if normalized == self.trading_day:
                return
            self.trading_day = normalized
            self._connection_changed_at = datetime.now().isoformat()
            callback = self.on_trading_day_callback
        if callback:
            try:
                callback(normalized)
            except Exception:
                logger.exception("[vn.py] trading day callback failed")

    def _mark_channels_disconnected(self, channels: Tuple[str, ...], reason: str) -> None:
        if self.status in (TradingStatus.CONNECTED, TradingStatus.TRADING):
            self._restore_status = self.status
            self.status = TradingStatus.ERROR
        changed = False
        for channel in channels:
            attr = f"_{channel}_connected"
            if getattr(self, attr):
                setattr(self, attr, False)
                changed = True
        if "td" in channels and self._contracts_ready:
            self._contracts_ready = False
            changed = True
        if "td" in channels and self._contract_capabilities:
            self._contract_capabilities.clear()
            self._contract_capability_request_id = None
            changed = True
        self._connected_event.clear()
        new_outage = not self._connection_outage
        self._reconnecting = True
        self._connection_outage = True
        if changed or new_outage:
            self._connection_generation += 1
            self._reconciliation_ready = False
            self.last_reconciliation = {
                "ok": False,
                "fresh": False,
                "failure_code": "broker_connection_changed",
                "refreshed_monotonic": time.monotonic(),
            }
            self._last_disconnect_reason = reason
            self._connection_changed_at = datetime.now().isoformat()
            logger.warning("[vn.py] connection health degraded: %s", reason)

    def _restore_connection_if_ready(self) -> None:
        if not (self._td_connected and self._md_connected and self._contracts_ready):
            return
        if self.status == TradingStatus.CONNECTING:
            self._connected_event.set()
        elif self._connection_outage and self._main_engine:
            self._start_reconciliation_worker_locked()

    def _start_reconciliation_worker_locked(self) -> None:
        worker = self._reconciliation_worker
        if worker is not None and worker.is_alive():
            return
        generation = self._connection_generation
        worker = threading.Thread(
            target=self._run_recovery_reconciliation,
            args=(generation,),
            name="ctp-reconciliation-recovery",
            daemon=True,
        )
        self._reconciliation_worker = worker
        worker.start()

    def _run_recovery_reconciliation(self, generation: int) -> None:
        try:
            result = self.refresh_reconciliation(
                timeout_seconds=self._reconciliation_timeout_seconds,
            )
            if result.get("ok") is not True or result.get("fresh") is not True:
                logger.error(
                    "[vn.py] broker reconciliation after reconnect failed: %s",
                    result.get("failure_code", "broker_snapshot_unavailable"),
                )
        except Exception:
            logger.exception("[vn.py] broker reconciliation after reconnect crashed")
        finally:
            with self._connection_lock:
                if self._reconciliation_worker is threading.current_thread():
                    self._reconciliation_worker = None
                generation_changed = generation != self._connection_generation
                if (
                    generation_changed
                    and self._connection_outage
                    and self._td_connected
                    and self._md_connected
                    and self._contracts_ready
                    and self._main_engine
                ):
                    self._start_reconciliation_worker_locked()

    def _complete_connection_recovery_locked(self) -> None:
        if not self._connection_outage:
            return
        self._reconnect_count += 1
        self._connection_outage = False
        self._reconnecting = False
        if self.status == TradingStatus.ERROR:
            self.status = self._restore_status
        logger.info("[vn.py] CTP connection and broker reconciliation restored")

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
            with self._connection_lock:
                self._td_connected = False
                self._md_connected = False
                self._contracts_ready = False
                self._reconciliation_ready = False
                self._reconnecting = False
                self._connection_outage = False
                self._connection_generation += 1
                self._reconciliation_worker = None
                self._contract_capabilities.clear()
                self._contract_capability_request_id = None
                self.trading_day = ""
                self._connected_event.clear()
                self._connection_changed_at = datetime.now().isoformat()
            self.status = TradingStatus.STOPPED

    def send_order(self, signal: Signal) -> str:
        """Send an order through vn.py."""
        self.last_reject_reason = ""
        if self.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
            return self._reject_order("CTP gateway is not connected")
        if not self._main_engine:
            return self._reject_order("CTP main engine is unavailable")
        with self._connection_lock:
            self._refresh_channel_health_from_vnpy()
            order_entry_ready = (
                self._td_connected
                and self._md_connected
                and self._contracts_ready
                and self._reconciliation_ready
                and not self._reconnecting
            )
        if not order_entry_ready:
            return self._reject_order("CTP order entry is not ready")

        contract_error = self._validate_contract_order(signal)
        if contract_error:
            return self._reject_order(contract_error)

        from vnpy.trader.object import OrderRequest

        try:
            symbol, exchange = self._split_symbol(signal.symbol)
        except GatewayError as exc:
            return self._reject_order(str(exc))
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

    def _reject_order(self, reason: str) -> str:
        self.last_reject_reason = str(reason or "CTP order rejected locally")
        logger.warning("[vn.py] order rejected locally: %s", self.last_reject_reason)
        return ""

    @staticmethod
    def _raw_contract_field(raw_contract: Any, field: str, default: Any = None) -> Any:
        if isinstance(raw_contract, dict):
            return raw_contract.get(field, default)
        return getattr(raw_contract, field, default)

    def _begin_contract_capability_snapshot(self, request_id: int) -> None:
        with self._connection_lock:
            request_id = int(request_id)
            if self._contract_capability_request_id == request_id:
                return
            self._contract_capability_request_id = request_id
            self._contract_capabilities.clear()
            self._contracts_ready = False

    def _record_contract_capability(self, raw_contract: Any) -> None:
        symbol = str(self._raw_contract_field(raw_contract, "InstrumentID", "") or "").strip()
        exchange = str(self._raw_contract_field(raw_contract, "ExchangeID", "") or "").strip().upper()
        if not symbol or not exchange:
            return
        key = symbol_key(f"{symbol}.{exchange}")
        if not key:
            return

        def positive_float(field: str) -> float:
            try:
                value = float(self._raw_contract_field(raw_contract, field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0
            return value if math.isfinite(value) and value > 0 else 0.0

        capability = {
            "symbol": symbol,
            "exchange": exchange,
            "price_tick": positive_float("PriceTick"),
            "min_limit_order_volume": positive_float("MinLimitOrderVolume"),
            "max_limit_order_volume": positive_float("MaxLimitOrderVolume"),
            "min_market_order_volume": positive_float("MinMarketOrderVolume"),
            "max_market_order_volume": positive_float("MaxMarketOrderVolume"),
        }
        with self._connection_lock:
            self._contract_capabilities[key] = capability

    def _contract_capability(self, symbol: str) -> Dict[str, Any] | None:
        key = symbol_key(symbol)
        with self._connection_lock:
            capability = self._contract_capabilities.get(key)
            return dict(capability) if capability is not None else None

    @staticmethod
    def _volume_error(volume: Any, minimum: float, maximum: float, label: str) -> str:
        try:
            parsed = float(volume)
        except (TypeError, ValueError):
            return f"{label} volume must be an integer"
        if not math.isfinite(parsed) or parsed <= 0 or not parsed.is_integer():
            return f"{label} volume must be a positive integer"
        if minimum <= 0 or maximum < minimum:
            return f"{label} volume capability is unavailable"
        if parsed < minimum or parsed > maximum:
            return f"{label} volume {int(parsed)} is outside broker range {minimum:g}-{maximum:g}"
        return ""

    def _validate_contract_order(self, signal: Signal) -> str:
        capability = self._contract_capability(signal.symbol)
        if capability is None:
            return f"CTP contract does not exist in the broker table: {signal.symbol}"

        exchange = str(capability.get("exchange") or "").upper()
        valid_offsets = {
            OffsetFlag.OPEN,
            OffsetFlag.CLOSE,
            OffsetFlag.CLOSE_TODAY,
            OffsetFlag.CLOSE_YESTERDAY,
        }
        if signal.offset not in valid_offsets:
            return f"Unsupported CTP offset: {signal.offset}"
        if signal.direction not in (Direction.LONG, Direction.SHORT):
            return f"Unsupported CTP direction: {signal.direction}"
        if signal.offset in (OffsetFlag.CLOSE_TODAY, OffsetFlag.CLOSE_YESTERDAY):
            if exchange not in {"SHFE", "INE"}:
                return f"{signal.offset.value} is only valid for SHFE/INE contracts"

        if signal.order_type == OrderType.MARKET:
            return self._volume_error(
                signal.volume,
                float(capability.get("min_market_order_volume") or 0),
                float(capability.get("max_market_order_volume") or 0),
                "Market order",
            )

        if signal.order_type != OrderType.LIMIT:
            order_type = getattr(signal.order_type, "value", signal.order_type)
            return f"Unsupported CTP order type: {order_type}"

        price_tick = float(capability.get("price_tick") or 0)
        try:
            price = float(signal.price)
        except (TypeError, ValueError):
            return "Limit price must be numeric"
        if not math.isfinite(price) or price <= 0:
            return "Limit price must be positive"
        if price_tick <= 0:
            return "Broker PriceTick is unavailable"
        tick_units = price / price_tick
        if not math.isclose(tick_units, round(tick_units), rel_tol=0.0, abs_tol=1e-8):
            return f"Limit price {price:g} is not aligned to PriceTick {price_tick:g}"
        return self._volume_error(
            signal.volume,
            float(capability.get("min_limit_order_volume") or 0),
            float(capability.get("max_limit_order_volume") or 0),
            "Limit order",
        )

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

    def supports_market_order(self, symbol: str = "") -> bool:
        """Return true only when the raw CTP contract proves a market range."""
        capability = self._contract_capability(symbol)
        if capability is None:
            return False
        minimum = float(capability.get("min_market_order_volume") or 0)
        maximum = float(capability.get("max_market_order_volume") or 0)
        return minimum > 0 and maximum >= minimum

    def query_account(self) -> AccountInfo:
        return self.account

    def query_positions(self) -> List[Position]:
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        return list(self.orders.values())

    def refresh_reconciliation(self, timeout_seconds: float = 8.0) -> Dict[str, Any]:
        """Refresh broker state and update the fail-closed order-entry gate."""
        with self._reconciliation_lock:
            with self._connection_lock:
                generation = self._connection_generation
                self._reconciliation_ready = False

            result = self._refresh_reconciliation_snapshot(timeout_seconds)

            with self._connection_lock:
                transport_ready = (
                    self._td_connected and self._md_connected and self._contracts_ready
                )
                accepted = bool(
                    result.get("ok") is True
                    and result.get("fresh") is True
                    and generation == self._connection_generation
                    and transport_ready
                )
                if result.get("ok") is True and result.get("fresh") is True and not accepted:
                    result = {
                        **result,
                        "ok": False,
                        "fresh": False,
                        "failure_code": "broker_connection_changed_during_snapshot",
                    }
                    self.last_reconciliation = dict(result)
                self._reconciliation_ready = accepted
                if accepted:
                    self._complete_connection_recovery_locked()
            return result

    def _refresh_reconciliation_snapshot(self, timeout_seconds: float = 8.0) -> Dict[str, Any]:
        """Actively query orders, positions and account with completion fences."""
        started = time.monotonic()
        with self._connection_lock:
            recovery_query_allowed = bool(
                self._connection_outage
                and self._td_connected
                and self._md_connected
                and self._contracts_ready
            )
        if (
            self.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING)
            and not recovery_query_allowed
        ) or not self._main_engine:
            return self._finish_reconciliation(
                ok=False,
                failure_code="broker_gateway_not_connected",
                started=started,
            )

        timeout = max(0.1, float(timeout_seconds))
        deadline = started + timeout
        with self._reconciliation_lock:
            broker_gateway = self._main_engine.get_gateway(self._gateway_name)
            required_methods = {
                "orders": "query_orders_snapshot",
                "positions": "query_positions_snapshot",
                "account": "query_account_snapshot",
            }
            if broker_gateway is None or any(
                not callable(getattr(broker_gateway, method_name, None))
                for method_name in required_methods.values()
            ):
                return self._finish_reconciliation(
                    ok=False,
                    failure_code="broker_snapshot_unsupported",
                    started=started,
                )

            with self._reconciliation_capture_lock:
                self._reconciliation_capture = {
                    "orders": {},
                    "vn_orders": {},
                    "positions": {},
                    "account": None,
                }
            self._reconciliation_results.clear()

            for kind, method_name in required_methods.items():
                event = self._reconciliation_events[kind]
                event.clear()
                self._reconciliation_expected_reqids.pop(kind, None)
                method = getattr(broker_gateway, method_name)
                try:
                    request_code = self._submit_reconciliation_query(method, deadline)
                except Exception as exc:
                    return self._abort_reconciliation(
                        failure_code="broker_snapshot_query_failed",
                        started=started,
                        error_msg=str(exc),
                    )
                if request_code != 0:
                    return self._abort_reconciliation(
                        failure_code=(
                            "broker_contracts_unavailable"
                            if request_code == -99
                            else "broker_snapshot_query_rejected"
                        ),
                        started=started,
                        request_code=request_code,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not event.wait(remaining):
                    return self._abort_reconciliation(
                        failure_code="broker_snapshot_timeout",
                        started=started,
                    )
                result = self._reconciliation_results.get(kind, {})
                if int(result.get("error_id", 0) or 0):
                    return self._abort_reconciliation(
                        failure_code="broker_snapshot_query_failed",
                        started=started,
                        error_id=int(result.get("error_id", 0) or 0),
                        error_msg=str(result.get("error_msg", "") or ""),
                    )
                if kind == "account" and int(result.get("item_count", 0) or 0) < 1:
                    return self._abort_reconciliation(
                        failure_code="broker_account_unavailable",
                        started=started,
                    )

            with self._reconciliation_capture_lock:
                capture = self._reconciliation_capture or {}
                if capture.get("account") is None:
                    return self._abort_reconciliation(
                        failure_code="broker_account_unavailable",
                        started=started,
                    )
                self.orders = dict(capture.get("orders") or {})
                self._vn_orders = dict(capture.get("vn_orders") or {})
                self.positions = dict(capture.get("positions") or {})
                account = capture.get("account")
                if account is not None:
                    self.account = account
                self._reconciliation_capture = None
            return self._finish_reconciliation(ok=True, failure_code="", started=started)

    @staticmethod
    def _submit_reconciliation_query(method: Any, deadline: float) -> int:
        while time.monotonic() < deadline:
            code = int(method() or 0)
            if code == 0 or code == -99:
                return code
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return -1

    def _on_reconciliation_event(self, event: Any) -> None:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        kind = str(data.get("kind") or "")
        marker = self._reconciliation_events.get(kind)
        if marker is None:
            return
        expected_request_id = self._reconciliation_expected_reqids.get(kind)
        request_id = int(data.get("request_id", -1) or -1)
        if expected_request_id is None or request_id != expected_request_id:
            return
        self._reconciliation_results[kind] = dict(data)
        marker.set()

    def _arm_reconciliation_request(self, kind: str, request_id: int) -> None:
        self._reconciliation_expected_reqids[kind] = int(request_id)

    def _finish_reconciliation(
        self,
        *,
        ok: bool,
        failure_code: str,
        started: float,
        **details: Any,
    ) -> Dict[str, Any]:
        snapshot = {
            "ok": bool(ok),
            "fresh": bool(ok),
            "failure_code": str(failure_code),
            "refreshed_at": datetime.now().isoformat(),
            "refreshed_monotonic": time.monotonic(),
            "duration_seconds": round(time.monotonic() - started, 3),
            **details,
        }
        self.last_reconciliation = snapshot
        return dict(snapshot)

    def _abort_reconciliation(
        self,
        *,
        failure_code: str,
        started: float,
        **details: Any,
    ) -> Dict[str, Any]:
        with self._reconciliation_capture_lock:
            self._reconciliation_capture = None
        self._reconciliation_expected_reqids.clear()
        return self._finish_reconciliation(
            ok=False,
            failure_code=failure_code,
            started=started,
            **details,
        )

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
        with self._connection_lock:
            if "交易服务器登录成功" in msg:
                self._set_channel_connected("td")
            if "行情服务器登录成功" in msg:
                self._set_channel_connected("md")

            if "交易服务器" in msg and ("连接断开" in msg or "断开连接" in msg):
                self._mark_channels_disconnected(("td",), msg)
            elif "行情服务器" in msg and ("连接断开" in msg or "断开连接" in msg):
                self._mark_channels_disconnected(("md",), msg)
            elif "连接断开" in msg or "断开连接" in msg:
                self._mark_channels_disconnected(("td", "md"), msg)

            if "合约信息查询成功" in msg:
                self._set_contracts_ready()

        if self.status == TradingStatus.CONNECTING:
            if self._is_connect_error(msg):
                self._remember_connect_error(msg)
                self._error_event.set()

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
        account = AccountInfo(
            account_id=getattr(data, "accountid", ""),
            balance=float(getattr(data, "balance", 0) or 0),
            available=float(getattr(data, "available", 0) or 0),
            margin=float(getattr(data, "frozen", 0) or 0),
        )
        self.account = account
        with self._reconciliation_capture_lock:
            if self._reconciliation_capture is not None:
                self._reconciliation_capture["account"] = account
        self.on_account(account)

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
            cost=float(getattr(data, "price", 0) or 0),
            pnl=float(getattr(data, "pnl", 0) or 0),
        )
        self.positions[f"{symbol}_{direction.value}"] = pos
        with self._reconciliation_capture_lock:
            if self._reconciliation_capture is not None:
                key = f"{symbol}_{direction.value}"
                self._reconciliation_capture["positions"][key] = pos
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
        with self._reconciliation_capture_lock:
            if self._reconciliation_capture is not None:
                self._reconciliation_capture["orders"][vt_orderid] = order
                self._reconciliation_capture["vn_orders"][vt_orderid] = data
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

    @staticmethod
    def _split_symbol(symbol: str) -> Tuple[str, Any]:
        from vnpy.trader.constant import Exchange

        if not is_supported_symbol(symbol):
            raise GatewayError(f"Unknown or exchange-conflicting futures symbol: {symbol}")

        if "." in symbol:
            left, right = symbol.split(".", 1)
            if left.upper() in Exchange.__members__:
                return right, Exchange[left.upper()]
            for exchange in Exchange:
                if right.upper() == exchange.value:
                    return left, exchange

        product = _extract_product(symbol)
        exchange_code = PRODUCT_EXCHANGE[product]
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
