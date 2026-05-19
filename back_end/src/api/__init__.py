"""
REST API 模块 - FastAPI + WebSocket + 身份认证

端点总览：
  POST /auth/login            — CTP 账户登录（连接交易/行情前置）
  POST /auth/logout           — 断开连接并注销会话
  GET  /auth/status           — 当前连接状态（无需鉴权）
  GET  /auth/servers          — 预设服务器列表
  GET  /system/status         — 系统健康状态
  GET  /strategies            — 策略列表
  GET  /positions             — 持仓列表
  GET  /dashboard/metrics     — 全局仪表盘指标（PnL/收益率/夏普/回撤/仓位）
  GET  /orders                — 全部委托单列表（最近500条）
  GET  /trades                — 全部成交记录（最近500条）
  POST /orders                — 手动下单（开仓/平仓）
  DELETE /orders/{id}         — 撤销委托单
  POST /orders/cancel-all     — 一键撤销所有活跃委托
  POST /positions/{sym}/close — 快捷平仓指定合约
  POST /strategy/{id}/action  — 启停策略
  GET  /backtest/strategies   — 可用策略列表
  POST /backtest/run          — 运行回测，返回资金曲线/标记/指标/热力图
  GET  /system/logs           — 查询系统日志（?level=&q=&limit=）
  WS   /ws/logs               — 实时日志推送
  GET  /watch/search          — 期货品种/合约搜索（?query=&exchange=）
  GET  /watch/kline           — K线数据+技术指标（?symbol=&interval=&limit=&indicators=）
  WS   /ws/system             — 系统状态实时推送（每秒）
  WS   /ws/orders             — 订单 & 成交事件推送
  WS   /ws/positions          — 持仓快照推送（每2秒）
  WS   /ws/dashboard          — 仪表盘指标推送（每2秒）
  WS   /ws/watch              — 盯盘系统实时行情（tick + kline_update，subscribe/unsubscribe 协议）
"""

import asyncio
import json as _json
import logging
import os
import threading
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd
import psutil
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..strategy import Direction, StrategyBase
from ..trading import AccountInfo, TradingEngine, TradingStatus
from ..strategy import Signal, OrderType, OffsetFlag
from ..observability import audit_log, metrics, new_request_id, structured_json
from ..settings import (
    ctp_defaults,
    ctp_server_presets,
    runtime_risk_defaults,
    secure_session_cookie_enabled,
    websocket_query_token_enabled,
)
from .backtest_service import STRATEGY_CATALOG, run_backtest_sync
from .security import SESSION_COOKIE_MAX_AGE, SESSION_COOKIE_NAME, is_open_path, session_store

logger = logging.getLogger(__name__)
_CTP_DEFAULTS = ctp_defaults()
_DEFAULT_RUNTIME_RISK = runtime_risk_defaults()

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,"
    "http://127.0.0.1:5173,"
    "http://localhost:5174,"
    "http://127.0.0.1:5174"
)


def _cors_origins() -> List[str]:
    raw = os.getenv("QUANT_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    return [item.strip() for item in raw.split(",") if item.strip()]

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username:   str
    password:   str
    broker_id:  str = _CTP_DEFAULTS["broker_id"]
    td_server:  str = _CTP_DEFAULTS["td_server"]
    md_server:  str = _CTP_DEFAULTS["md_server"]
    app_id:     str = _CTP_DEFAULTS["app_id"]
    auth_code:  str = _CTP_DEFAULTS["auth_code"]
    gateway_type: str = "vnpy"   # "vnpy" | "ctp"
    environment: str = _CTP_DEFAULTS["vnpy_environment"]     # vn.py CTP 柜台环境："实盘" | "测试"
    auto_start_strategy: bool = False
    strategy_name: str = "ma_cross"
    strategy_params: Dict[str, Any] = Field(default_factory=dict)
    risk: Dict[str, Any] = Field(default_factory=dict)


class LoginResponse(BaseModel):
    success:        bool
    message:        str
    gateway_status: str
    account_id:     str = ""
    balance:        float = 0.0
    strategy_started: bool = False
    strategy_id:      str = ""


class AuthStatusResponse(BaseModel):
    logged_in:         bool
    gateway_connected: bool
    gateway_status:    str
    gateway_name:      str
    account_id:        str
    connect_log:       List[str]


class SystemStatusResponse(BaseModel):
    timestamp:        str
    market_connected: bool
    gateway_status:   str
    gateway_name:     str
    cpu_percent:      float
    memory_percent:   float
    active_strategies: int
    account:          Optional[Dict[str, Any]] = None


class PositionInfo(BaseModel):
    symbol:     str
    direction:  str
    volume:     int
    frozen:     int
    cost_price: float
    pnl:        float


class StrategyInfo(BaseModel):
    strategy_id: str
    name:        str
    status:      str
    symbol:      Optional[str]
    pnl:         float
    positions:   List[PositionInfo]
    trade_count: int
    error_count: int


class ActionRequest(BaseModel):
    action: str   # "start" | "stop"


class ActionResponse(BaseModel):
    success:     bool
    strategy_id: str
    action:      str
    message:     str


class SignalSchema(BaseModel):
    symbol:     str
    time:       str
    direction:  str
    price:      float
    volume:     int
    comment:    str
    order_type: str


class StrategyDetailResponse(BaseModel):
    strategy_id:    str
    name:           str
    status:         str
    symbol:         Optional[str]
    pnl:            float
    trade_count:    int
    error_count:    int
    positions:      List[PositionInfo]
    weight:         float
    params:         Dict[str, Any]
    recent_signals: List[SignalSchema]


class ParamsUpdateRequest(BaseModel):
    params:  Dict[str, Any]
    restart: bool = False   # 如果策略正在运行，是否重启使新参数生效


class WeightRequest(BaseModel):
    weights: Dict[str, float]  # {strategy_id: 0.0~1.0}


class BacktestRunRequest(BaseModel):
    strategy_name:   str            = "ma_cross"
    strategy_params: Dict[str, Any] = Field(default_factory=dict)
    start_date:      str            = "2023-01-01"
    end_date:        str            = "2024-12-31"
    initial_capital: float          = 1_000_000
    commission_rate: float          = 0.0003
    slip_rate:       float          = 0.0001
    margin_rate:     float          = 0.12
    contract_multiplier: float      = 1.0
    max_errors:      int            = 100
    sample_days:     int            = 700   # 模拟数据天数
    allow_synthetic_data: bool      = False


class ManualOrderRequest(BaseModel):
    """手动下单请求"""
    symbol:     str
    direction:  str         # "long" | "short"
    offset:     str = "open"  # "open" | "close" | "close_today" | "close_yesterday"
    price:      float = 0   # 0 = 市价
    volume:     int = 1
    order_type: str = "market"  # "market" | "limit"


class ClosePositionRequest(BaseModel):
    """快捷平仓请求"""
    volume:     int = 0         # 0 = 全部平仓
    price:      float = 0       # 0 = 市价
    direction:  str = ""        # 可选："long" | "short"，用于锁定要平的持仓方向
    offset:     str = "close"   # "close" | "close_today" | "close_yesterday"
    order_type: str = ""        # "" 自动按 price 推断；也可传 "market" | "limit"


class EmergencyStopRequest(BaseModel):
    """交易急停请求。"""
    reason: str = ""
    cancel_orders: bool = True
    stop_strategies: bool = False


class RiskConfigRequest(BaseModel):
    """运行时风控配置更新请求。"""
    risk: Dict[str, Any] = Field(default_factory=dict)


_MANUAL_DIRECTION_MAP = {"long": Direction.LONG, "short": Direction.SHORT}
_MANUAL_OFFSET_MAP = {
    "open": OffsetFlag.OPEN,
    "close": OffsetFlag.CLOSE,
    "close_today": OffsetFlag.CLOSE_TODAY,
    "close_yesterday": OffsetFlag.CLOSE_YESTERDAY,
}
_MANUAL_CLOSE_OFFSET_MAP = {
    "close": OffsetFlag.CLOSE,
    "close_today": OffsetFlag.CLOSE_TODAY,
    "close_yesterday": OffsetFlag.CLOSE_YESTERDAY,
}
_MANUAL_ORDER_TYPE_MAP = {"market": OrderType.MARKET, "limit": OrderType.LIMIT}


def _clean_manual_symbol(symbol: str) -> str:
    clean_symbol = str(symbol or "").strip()
    if not clean_symbol:
        raise HTTPException(status_code=400, detail="合约代码不能为空")
    return clean_symbol


def _normalize_choice(value: str, mapping: Dict[str, Any], field_label: str) -> tuple[str, Any]:
    normalized = str(value or "").strip().lower()
    if normalized not in mapping:
        supported = " / ".join(mapping.keys())
        raise HTTPException(status_code=400, detail=f"{field_label}无效: {value}，支持: {supported}")
    return normalized, mapping[normalized]


def _positive_volume(value: int, *, allow_zero: bool = False) -> int:
    try:
        volume = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="委托数量必须是整数")
    if allow_zero and volume == 0:
        return 0
    if volume <= 0:
        raise HTTPException(status_code=400, detail="委托数量必须大于 0")
    return volume


def _manual_order_price(order_type: OrderType, price: float) -> float:
    try:
        parsed_price = float(price or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="委托价格必须是数字")
    if order_type == OrderType.MARKET:
        return 0.0
    if parsed_price <= 0:
        raise HTTPException(status_code=400, detail="限价单价格必须大于 0")
    return parsed_price


def _manual_position_direction(pos) -> str:
    direction = getattr(pos, "direction", "")
    if hasattr(direction, "value"):
        direction = direction.value
    direction = str(direction or "").strip().lower()
    if direction in {"long", "short"}:
        return direction

    try:
        volume = float(getattr(pos, "volume", 0))
    except (TypeError, ValueError):
        volume = 0
    if volume > 0:
        return "long"
    if volume < 0:
        return "short"
    return ""


def _find_close_position(positions: Dict[str, Any], symbol: str, direction: str = ""):
    target_symbol = symbol.strip().lower()
    target_direction = direction.strip().lower()
    candidates = []
    for key, pos in positions.items():
        pos_symbol = str(getattr(pos, "symbol", key) or "").strip()
        if pos_symbol.lower() != target_symbol and str(key).strip().lower() != target_symbol:
            continue
        try:
            volume = abs(int(getattr(pos, "volume", 0) or 0))
        except (TypeError, ValueError):
            volume = 0
        if volume <= 0:
            continue
        pos_direction = _manual_position_direction(pos)
        if target_direction and pos_direction != target_direction:
            continue
        candidates.append(pos)

    if not candidates:
        return None
    if len(candidates) > 1 and not target_direction:
        raise HTTPException(status_code=400, detail=f"{symbol} 同时存在多个方向持仓，请指定 direction")
    return candidates[0]


def _close_direction_for_position(pos) -> Direction:
    pos_direction = _manual_position_direction(pos)
    if pos_direction == "long":
        return Direction.SHORT
    if pos_direction == "short":
        return Direction.LONG
    raise HTTPException(status_code=400, detail="无法识别持仓方向，已取消快捷平仓")


# ---------------------------------------------------------------------------
# 预设服务器列表从环境变量读取：
# QUANT_CTP_TD_PRESETS="label=tcp://host:port,label2=tcp://host:port"
# QUANT_CTP_MD_PRESETS="label=tcp://host:port,label2=tcp://host:port"
# ---------------------------------------------------------------------------

_PRESET_TD = ctp_server_presets("td")
_PRESET_MD = ctp_server_presets("md")

# ---------------------------------------------------------------------------
# WebSocket 连接管理器
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self, channel: str, send_timeout: float = 1.0):
        self.channel = channel
        self.send_timeout = send_timeout
        self._connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        metrics.record_ws_connect(self.channel)
        logger.info(f"[WS:{self.channel}] 新连接，当前: {len(self._connections)} 条")

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.discard(ws)
            metrics.record_ws_disconnect(self.channel)
            logger.info(f"[WS:{self.channel}] 连接断开，剩余: {len(self._connections)} 条")

    async def broadcast(self, payload: dict):
        if not self._connections:
            return
        text = _json.dumps(payload, ensure_ascii=False, default=str)
        dead: Set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=self.send_timeout)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)
        metrics.record_ws_broadcast(self.channel, dropped=len(dead))

    def broadcast_sync(self, payload: dict, loop: asyncio.AbstractEventLoop):
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), loop)

    @property
    def count(self) -> int:
        return len(self._connections)


system_manager    = ConnectionManager("system")
orders_manager    = ConnectionManager("orders")
dashboard_manager = ConnectionManager("dashboard")
positions_manager = ConnectionManager("positions")
log_manager       = ConnectionManager("logs")

_event_loop: Optional[asyncio.AbstractEventLoop] = None

# ── 网络速率差分计算 ──────────────────────────────────────────────────────────
_last_net_io: Optional[tuple] = None   # (bytes_sent, bytes_recv, monotonic_ts)


def _get_network_speed() -> tuple:
    """返回 (send_bps, recv_bps)，单位 字节/秒。"""
    global _last_net_io
    try:
        io  = psutil.net_io_counters()
        now = time.monotonic()
        if _last_net_io:
            sent, recv, ts = _last_net_io
            dt       = max(now - ts, 0.001)
            send_bps = (io.bytes_sent - sent) / dt
            recv_bps = (io.bytes_recv - recv) / dt
        else:
            send_bps = recv_bps = 0.0
        _last_net_io = (io.bytes_sent, io.bytes_recv, now)
        return round(max(send_bps, 0), 1), round(max(recv_bps, 0), 1)
    except Exception:
        return 0.0, 0.0

# ---------------------------------------------------------------------------
# 全局状态注册表
# ---------------------------------------------------------------------------

class _StrategyEntry:
    def __init__(self, strategy_id, strategy, engine, config):
        self.strategy_id = strategy_id
        self.strategy    = strategy
        self.engine      = engine
        self.config      = config

    @property
    def status(self) -> str:
        es = self.engine.status
        if es == TradingStatus.TRADING:    return "running"
        if es == TradingStatus.ERROR:      return "error"
        if es == TradingStatus.CONNECTING: return "connecting"
        return "stopped"


class TradingState:
    """
    全局单例：持有所有引擎/策略引用，供 API 路由及 WebSocket 推送读取。

    登录后调用 set_main_engine() 注册主引擎（CTP 网关）；
    策略注册后调用 register() 绑定到具体策略。
    """

    EQUITY_MAXLEN = 300   # 保留最近 300 个快照（1s 间隔 ≈ 5 分钟窗口）

    def __init__(self):
        self._lock = threading.RLock()
        self._entries:             Dict[str, _StrategyEntry]  = {}
        self._main_engine:         Optional[TradingEngine]    = None
        self._main_config:         Dict[str, Any]             = {}
        self._connect_log:         List[str]                  = []
        self._equity_curve:        deque                      = deque(maxlen=self.EQUITY_MAXLEN)
        self._day_open_balance:    float                      = 0.0
        self._weights:             Dict[str, float]           = {}   # strategy_id → 0.0~1.0
        self._last_gw_callback_ts: float                      = 0.0  # time.monotonic() 上次网关回调时间

    # ── 主引擎（登录产生的 CTP 引擎）────────────────────────────────────────
    def set_main_engine(self, engine: TradingEngine, config: Dict[str, Any] = None):
        with self._lock:
            self._main_engine = engine
            self._main_config = config or {}
        # 安装订单广播钩子
        if _event_loop and not _event_loop.is_closed():
            _install_hook_on_engine(engine)
        logger.info("[API] 主引擎已设置")

    def clear_main(self):
        """断开并清理主引擎"""
        with self._lock:
            engine = self._main_engine
            self._main_engine = None
            self._main_config = {}
            self._entries.clear()
            self._weights.clear()
        if engine:
            try:
                engine.stop()
            except Exception:
                pass

    # ── 连接日志 ──────────────────────────────────────────────────────────────
    def add_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        with self._lock:
            self._connect_log.append(entry)
            if len(self._connect_log) > 200:
                self._connect_log = self._connect_log[-100:]
        logger.info(f"[ConnLog] {msg}")

    def clear_log(self):
        with self._lock:
            self._connect_log = []

    def get_log(self) -> List[str]:
        with self._lock:
            return list(self._connect_log)

    # ── 策略权重 ──────────────────────────────────────────────────────────────
    def set_weights(self, weights: Dict[str, float]):
        with self._lock:
            for sid, w in weights.items():
                self._weights[sid] = max(0.0, min(1.0, float(w)))

    def get_weight(self, strategy_id: str) -> float:
        with self._lock:
            if strategy_id in self._weights:
                return self._weights[strategy_id]
            n = max(len(self._entries), 1)
            return round(1.0 / n, 4)

    def all_weights(self) -> Dict[str, float]:
        with self._lock:
            ids = list(self._entries.keys())
            if not ids:
                return {}
            if self._weights:
                return {sid: self._weights.get(sid, round(1.0 / len(ids), 4)) for sid in ids}
            w = round(1.0 / len(ids), 4)
            return {sid: w for sid in ids}

    # ── 权益曲线历史 ──────────────────────────────────────────────────────────
    def push_equity(self, pnl: float, balance: float):
        """记录一个权益快照（每秒由广播循环调用）。"""
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._equity_curve.append({"ts": ts, "p": round(pnl, 2), "b": round(balance, 2)})
            # 记录当天首次有效余额作为日内基准
            if self._day_open_balance == 0.0 and balance > 0:
                self._day_open_balance = balance

    def get_equity_data(self) -> list:
        with self._lock:
            return list(self._equity_curve)

    # ── 策略注册 ──────────────────────────────────────────────────────────────
    def register(self, strategy_id, strategy, engine, config=None):
        entry = _StrategyEntry(strategy_id, strategy, engine, config or {})
        with self._lock:
            self._entries[strategy_id] = entry
        if _event_loop and not _event_loop.is_closed():
            _install_order_hook(entry)
        logger.info(f"[API] 策略已注册: {strategy_id}")

    def unregister(self, strategy_id: str):
        with self._lock:
            self._entries.pop(strategy_id, None)

    def get(self, strategy_id: str) -> Optional[_StrategyEntry]:
        with self._lock:
            return self._entries.get(strategy_id)

    def all_entries(self) -> List[_StrategyEntry]:
        with self._lock:
            return list(self._entries.values())

    def primary_engine(self) -> Optional[TradingEngine]:
        with self._lock:
            if self._main_engine:
                return self._main_engine
            for entry in self._entries.values():
                return entry.engine
            return None


trading_state = TradingState()


def _request_id(request: Optional[Request]) -> str:
    return getattr(getattr(request, "state", None), "request_id", "") if request else ""


def _websocket_session_token(ws: WebSocket) -> str:
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    cookie_token = ws.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie_token:
        return cookie_token
    if websocket_query_token_enabled():
        return ws.query_params.get("token", "")
    return ""


async def _require_websocket_session(ws: WebSocket) -> bool:
    token = _websocket_session_token(ws)
    if token and session_store.is_valid(token):
        return True
    await ws.close(code=1008)
    return False


def _record_audit(
    event_type: str,
    action: str,
    status: str,
    *,
    actor: str = "system",
    resource: str = "",
    request: Optional[Request] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    audit_log.record(
        event_type,
        action,
        status,
        actor=actor,
        resource=resource,
        request_id=_request_id(request),
        detail=detail,
    )
    metrics.record_audit(event_type)


# ---------------------------------------------------------------------------
# 订单广播钩子
# ---------------------------------------------------------------------------

def _order_to_dict(order) -> dict:
    return {
        "type":          "order_update",
        "timestamp":     datetime.now().isoformat(),
        "order_id":      order.order_id,
        "symbol":        order.symbol,
        "direction":     order.direction.value if hasattr(order.direction, "value") else str(order.direction),
        "order_type":    order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type),
        "offset":        order.offset.value if hasattr(order, "offset") and hasattr(order.offset, "value") else "open",
        "price":         order.price,
        "volume":        order.volume,
        "traded_volume": order.traded_volume,
        "status":        order.status.value if hasattr(order.status, "value") else str(order.status),
        "error_msg":     getattr(order, "error_msg", ""),
    }


def _install_order_hook(entry: _StrategyEntry):
    """在策略入口的网关回调链末尾挂载订单广播。"""
    _install_hook_on_engine(entry.engine)


def _trade_to_dict(trade) -> dict:
    ts = getattr(trade, "trade_time", None)
    if hasattr(ts, "strftime"):
        time_str  = ts.strftime("%H:%M:%S")
        ts_iso    = ts.isoformat()
    else:
        time_str  = str(ts)[-8:] if ts else "--"
        ts_iso    = datetime.now().isoformat()
    return {
        "type":       "trade_event",
        "timestamp":  ts_iso,
        "trade_id":   getattr(trade, "trade_id",   ""),
        "order_id":   getattr(trade, "order_id",   ""),
        "symbol":     getattr(trade, "symbol",     ""),
        "direction":  trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction),
        "price":      getattr(trade, "price",      0.0),
        "volume":     getattr(trade, "volume",     0),
        "commission": round(getattr(trade, "commission", 0.0), 4),
        "pnl":        round(getattr(trade, "pnl",  0.0), 2),
        "trade_time": time_str,
    }


def _install_hook_on_engine(engine: TradingEngine):
    """在引擎的网关回调链末尾挂载订单 & 成交广播钩子。"""
    gw = engine.gateway
    if getattr(gw, "_quant_api_hooks_installed", False):
        return

    # ── 订单钩子 ────────────────────────────────────────────────────────────
    _orig_order = gw.on_order_callback

    def _order_chained(order):
        trading_state._last_gw_callback_ts = time.monotonic()
        _record_audit("order", "gateway_order_update", "received", resource=getattr(order, "order_id", ""))
        if _orig_order:
            _orig_order(order)
        loop = _event_loop
        if loop and not loop.is_closed():
            orders_manager.broadcast_sync(_order_to_dict(order), loop)

    gw.on_order_callback = _order_chained

    # ── 成交钩子（通过同一 orders_manager 频道推送）──────────────────────────
    _orig_trade = gw.on_trade_callback

    def _trade_chained(trade):
        trading_state._last_gw_callback_ts = time.monotonic()
        _record_audit("trade", "gateway_trade_update", "received", resource=getattr(trade, "trade_id", ""))
        if _orig_trade:
            _orig_trade(trade)
        loop = _event_loop
        if loop and not loop.is_closed():
            orders_manager.broadcast_sync(_trade_to_dict(trade), loop)

    gw.on_trade_callback = _trade_chained
    setattr(gw, "_quant_api_hooks_installed", True)
    logger.info(f"[API] 订单/成交广播钩子已安装: 网关={gw.name}")

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _position_to_schema(pos) -> PositionInfo:
    return PositionInfo(
        symbol=pos.symbol,
        direction=pos.direction.value if isinstance(pos.direction, Direction) else str(pos.direction),
        volume=pos.volume,
        frozen=getattr(pos, "frozen", 0),
        cost_price=getattr(pos, "cost", 0.0),
        pnl=getattr(pos, "pnl", 0.0),
    )


def _signal_to_schema(sig) -> SignalSchema:
    ts = sig.datetime
    if hasattr(ts, "strftime"):
        time_str = ts.strftime("%H:%M:%S")
    else:
        time_str = str(ts)[-8:] if ts else "--"
    return SignalSchema(
        symbol    = sig.symbol,
        time      = time_str,
        direction = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction),
        price     = float(sig.price),
        volume    = int(sig.volume),
        comment   = sig.comment or "",
        order_type= sig.order_type.value if hasattr(sig.order_type, "value") else "market",
    )


def _calc_strategy_pnl(entry: _StrategyEntry) -> float:
    return sum(getattr(p, "pnl", 0.0) for p in entry.strategy.positions.values())


def _account_to_dict(account: AccountInfo) -> Dict[str, Any]:
    return {
        "account_id":   account.account_id,
        "balance":      account.balance,
        "available":    account.available,
        "margin":       account.margin,
        "commission":   account.commission,
        "position_pnl": account.position_pnl,
        "total_pnl":    account.total_pnl,
    }


def _tick_cache_lookup(raw: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    keys = [symbol, symbol.strip(), symbol.upper(), symbol.lower()]
    for key in keys:
        if key in raw:
            value = raw[key]
            return dict(value) if isinstance(value, dict) else None

    normalized = symbol.strip().lower()
    for key, value in raw.items():
        if str(key).strip().lower() == normalized and isinstance(value, dict):
            return dict(value)
    return None


def _market_data_tick_snapshot(tick: Any, requested_symbol: str) -> Dict[str, Any]:
    ts = getattr(tick, "timestamp", None) or datetime.now()
    last = float(getattr(tick, "last_price", 0) or 0)
    return {
        "type": "tick",
        "source": "vnpy",
        "symbol": requested_symbol,
        "last": last,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "pre_close": 0.0,
        "volume": int(getattr(tick, "volume", 0) or 0),
        "turnover": float(getattr(tick, "turnover", 0) or 0),
        "open_interest": 0,
        "bid1": float(getattr(tick, "bid_price_1", 0) or 0),
        "ask1": float(getattr(tick, "ask_price_1", 0) or 0),
        "bid1_vol": int(getattr(tick, "bid_volume_1", 0) or 0),
        "ask1_vol": int(getattr(tick, "ask_volume_1", 0) or 0),
        "change": 0.0,
        "change_rate": 0.0,
        "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
    }


def _gateway_tick_snapshot(gateway: Any, symbol: str) -> Optional[Dict[str, Any]]:
    snapshots = getattr(gateway, "latest_tick_snapshots", {})
    snapshot = _tick_cache_lookup(snapshots, symbol)
    if snapshot:
        snapshot["symbol"] = symbol
        return snapshot

    latest_ticks = getattr(gateway, "latest_ticks", {})
    tick = latest_ticks.get(symbol)
    if tick is None:
        normalized = symbol.strip().lower()
        for key, value in latest_ticks.items():
            if str(key).strip().lower() == normalized:
                tick = value
                break
    if tick is None:
        return None
    return _market_data_tick_snapshot(tick, symbol)


def _subscribe_market_ticks(engine: Optional[TradingEngine], symbols: List[str]) -> None:
    if engine is None:
        return
    subscribe = getattr(engine.gateway, "subscribe_market_data", None)
    if not callable(subscribe):
        return
    unique_symbols = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))
    if not unique_symbols:
        return
    try:
        subscribe(unique_symbols)
    except Exception as exc:
        logger.warning("[watch] 行情订阅失败: %s", exc)


async def _wait_for_gateway_ticks(
    engine: TradingEngine,
    symbols: List[str],
    timeout: float = 1.2,
) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    _subscribe_market_ticks(engine, symbols)
    deadline = time.monotonic() + timeout
    result: Dict[str, Dict[str, Any]] = {}

    while True:
        for symbol in symbols:
            if symbol in result:
                continue
            snapshot = _gateway_tick_snapshot(engine.gateway, symbol)
            if snapshot and float(snapshot.get("last") or 0) > 0:
                result[symbol] = snapshot

        missing = [symbol for symbol in symbols if symbol not in result]
        if not missing or time.monotonic() >= deadline:
            return result, missing
        await asyncio.sleep(0.1)


def _build_system_snapshot() -> dict:
    engine = trading_state.primary_engine()
    market_connected = False
    td_connected     = False
    gateway_status   = TradingStatus.STOPPED.value
    gateway_name     = "N/A"
    total_pnl        = 0.0
    balance          = 0.0
    initial_capital  = 1_000_000.0

    if engine is not None:
        gw = engine.gateway
        gateway_name     = gw.name
        gateway_status   = gw.status.value if isinstance(gw.status, TradingStatus) else str(gw.status)
        td_connected     = gw.status in (TradingStatus.CONNECTED, TradingStatus.TRADING)
        market_connected = td_connected   # CTP 单网关：行情与交易共享状态
        try:
            account = engine.get_account()
            if not account.error_msg:
                total_pnl = account.total_pnl
                balance   = account.balance
        except Exception:
            pass

    if total_pnl == 0.0:
        total_pnl = sum(_calc_strategy_pnl(e) for e in trading_state.all_entries())

    for entry in trading_state.all_entries():
        initial_capital = entry.config.get("initial_capital", 1_000_000.0)
        break
    if trading_state._main_config:
        initial_capital = trading_state._main_config.get("initial_capital", initial_capital)

    return_rate  = (total_pnl / initial_capital * 100) if initial_capital else 0.0
    active_count = sum(1 for e in trading_state.all_entries() if e.status == "running")

    # 网关延迟：上次回调距今毫秒数（0 = 尚未收到任何回调）
    if trading_state._last_gw_callback_ts > 0:
        gateway_latency_ms = round((time.monotonic() - trading_state._last_gw_callback_ts) * 1000)
    else:
        gateway_latency_ms = -1

    # 网络速率
    send_bps, recv_bps = _get_network_speed()

    return {
        "type":                "system_status",
        "timestamp":           datetime.now().isoformat(),
        "market_connected":    market_connected,
        "td_connected":        td_connected,
        "md_connected":        market_connected,
        "gateway_status":      gateway_status,
        "gateway_name":        gateway_name,
        "gateway_latency_ms":  gateway_latency_ms,
        "cpu_percent":         psutil.cpu_percent(interval=None),
        "memory_percent":      psutil.virtual_memory().percent,
        "network_send_bps":    send_bps,
        "network_recv_bps":    recv_bps,
        "active_strategies":   active_count,
        "total_pnl":           round(total_pnl, 2),
        "return_rate":         round(return_rate, 4),
        "balance":             round(balance, 2),
    }

# ---------------------------------------------------------------------------
# 后台广播任务
# ---------------------------------------------------------------------------

async def _system_broadcast_loop():
    while True:
        try:
            snapshot = _build_system_snapshot()
            # 同步记录到权益历史（夏普/回撤计算用）
            trading_state.push_equity(snapshot["total_pnl"], snapshot["balance"])
            if system_manager.count > 0:
                await system_manager.broadcast(snapshot)
        except Exception as exc:
            logger.warning(f"[WS:system] 广播异常: {exc}")
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# 全局仪表盘指标构建（含夏普比率、最大回撤、权益曲线）
# ---------------------------------------------------------------------------

def _build_dashboard_metrics() -> dict:
    """计算实时 PnL、收益率、夏普比率、最大回撤、仓位概览。"""
    engine          = trading_state.primary_engine()
    total_pnl       = 0.0
    balance         = 0.0
    available       = 0.0
    margin          = 0.0
    account_id      = ""
    initial_capital = trading_state._main_config.get("initial_capital", 1_000_000.0)

    if engine:
        try:
            account = engine.get_account()
            if not account.error_msg:
                total_pnl  = account.total_pnl
                balance    = account.balance
                available  = account.available
                margin     = getattr(account, "margin", 0.0)
                account_id = account.account_id
        except Exception:
            pass

    # 如果网关无数据，降级为策略层汇总
    if total_pnl == 0.0:
        total_pnl = sum(_calc_strategy_pnl(e) for e in trading_state.all_entries())

    # 收益率
    return_rate  = total_pnl / initial_capital * 100 if initial_capital else 0.0
    day_open     = trading_state._day_open_balance or initial_capital
    today_return = (balance - day_open) / day_open * 100 if day_open > 0 else return_rate

    # 持仓列表
    positions_list: list = []
    if engine:
        for symbol, pos in engine.gateway.positions.items():
            if getattr(pos, "volume", 0) != 0:
                positions_list.append({
                    "symbol":    symbol,
                    "direction": pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction),
                    "volume":    abs(pos.volume),
                    "cost":      round(getattr(pos, "cost", 0.0), 2),
                    "pnl":       round(getattr(pos, "pnl", 0.0), 2),
                })

    total_exposure = sum(p["cost"] * p["volume"] for p in positions_list)
    exposure_pct   = total_exposure / balance * 100 if balance > 0 else 0.0

    # 夏普比率 / 最大回撤（滚动权益历史）
    sharpe_ratio     = 0.0
    max_drawdown_pct = 0.0
    equity_curve_data: list = []

    curve = trading_state.get_equity_data()
    n     = len(curve)
    if n >= 10:
        bal_arr = pd.Series([c["b"] for c in curve], dtype=float)

        # 夏普：日内年化（252 交易日 × 14400 秒交易时段）
        bal_returns = bal_arr.pct_change().dropna()
        if len(bal_returns) > 1 and bal_returns.std() > 1e-12:
            ann_factor   = np.sqrt(252 * 14400)
            sharpe_ratio = float(
                np.clip(bal_returns.mean() / bal_returns.std() * ann_factor, -20.0, 20.0)
            )

        # 最大回撤
        peak             = bal_arr.cummax()
        dd               = (bal_arr - peak) / peak.replace(0, np.nan) * 100
        max_drawdown_pct = float(abs(dd.min())) if not dd.empty else 0.0

        # 权益曲线降采样（前端图表用，≤60 点）
        step = max(1, n // 60)
        equity_curve_data = [
            {"ts": c["ts"], "v": c["p"]} for c in curve[::step]
        ][-60:]

    active_cnt = sum(1 for e in trading_state.all_entries() if e.status == "running")

    return {
        "type":             "dashboard_metrics",
        "timestamp":        datetime.now().isoformat(),
        "account_id":       account_id,
        "total_pnl":        round(total_pnl, 2),
        "return_rate":      round(return_rate, 4),
        "today_return":     round(today_return, 4),
        "balance":          round(balance, 2),
        "available":        round(available, 2),
        "margin":           round(margin, 2),
        "initial_capital":  round(initial_capital, 2),
        "sharpe_ratio":     round(sharpe_ratio, 3),
        "max_drawdown_pct": round(max_drawdown_pct, 3),
        "total_exposure":   round(total_exposure, 2),
        "exposure_pct":     round(exposure_pct, 2),
        "positions":        positions_list,
        "equity_curve":     equity_curve_data,
        "active_strategies": active_cnt,
    }


async def _dashboard_broadcast_loop():
    while True:
        try:
            if dashboard_manager.count > 0:
                await dashboard_manager.broadcast(_build_dashboard_metrics())
        except Exception as exc:
            logger.warning(f"[WS:dashboard] 广播异常: {exc}")
        await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# 订单簿 / 持仓簿辅助
# ---------------------------------------------------------------------------

def _collect_all_orders() -> list:
    """从主引擎和各策略引擎收集所有订单，去重并按时间倒序。"""
    seen:   set  = set()
    result: list = []

    def _add(order):
        oid = getattr(order, "order_id", None)
        if oid and oid not in seen:
            seen.add(oid)
            # 补充 create_time 字段到 dict
            d = _order_to_dict(order)
            ct = getattr(order, "create_time", None)
            d["create_time"] = ct.strftime("%H:%M:%S") if hasattr(ct, "strftime") else str(ct)[-8:]
            d["create_ts"]   = ct.isoformat()          if hasattr(ct, "isoformat") else ""
            result.append(d)

    engine = trading_state.primary_engine()
    if engine:
        for o in engine.gateway.orders.values():
            _add(o)
    for entry in trading_state.all_entries():
        for o in entry.engine.gateway.orders.values():
            _add(o)

    result.sort(key=lambda x: x.get("create_ts", ""), reverse=True)
    return result


def _collect_all_trades() -> list:
    """从各策略的成交记录收集，去重并按时间倒序。"""
    seen:   set  = set()
    result: list = []
    for entry in trading_state.all_entries():
        for t in getattr(entry.strategy, "trades", []):
            tid = getattr(t, "trade_id", None)
            if tid and tid not in seen:
                seen.add(tid)
                result.append(_trade_to_dict(t))
    result.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return result


def _unique_engines() -> list:
    """Return connected engines without duplicate object references."""
    engines = []
    seen: set[int] = set()
    primary = trading_state.primary_engine()
    if primary:
        engines.append(primary)
        seen.add(id(primary))
    for entry in trading_state.all_entries():
        if id(entry.engine) not in seen:
            engines.append(entry.engine)
            seen.add(id(entry.engine))
    return engines


def _cancel_all_active_orders() -> dict:
    cancelled = 0
    failed = 0
    seen: set[str] = set()
    for engine in _unique_engines():
        for oid, order in list(engine.gateway.orders.items()):
            if oid in seen:
                continue
            seen.add(oid)
            if not order.is_active():
                continue
            try:
                if engine.gateway.cancel_order(oid):
                    cancelled += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
    return {"cancelled": cancelled, "failed": failed}


def _build_positions_snapshot() -> dict:
    """构建完整持仓快照，含市值估算。"""
    engine    = trading_state.primary_engine()
    positions = []
    seen: set = set()

    def _add_pos(pos):
        sym = pos.symbol
        if sym in seen or getattr(pos, "volume", 0) == 0:
            return
        seen.add(sym)
        cost_price   = round(getattr(pos, "cost",  0.0), 4)
        cur_price    = round(getattr(pos, "price", 0.0), 4)
        volume       = abs(pos.volume)
        pnl          = round(getattr(pos, "pnl",   0.0), 2)
        # 若有当前价则估算市值，否则用成本价
        market_price = cur_price if cur_price > 0 else cost_price
        market_value = round(market_price * volume, 2)
        cost_value   = round(cost_price   * volume, 2)
        pnl_pct      = round(pnl / cost_value * 100, 3) if cost_value else 0.0
        positions.append({
            "symbol":       sym,
            "direction":    pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction),
            "volume":       volume,
            "frozen":       getattr(pos, "frozen", 0),
            "cost_price":   cost_price,
            "cur_price":    cur_price,
            "market_value": market_value,
            "cost_value":   cost_value,
            "pnl":          pnl,
            "pnl_pct":      pnl_pct,
        })

    if engine:
        for pos in engine.gateway.positions.values():
            _add_pos(pos)
    for entry in trading_state.all_entries():
        for pos in entry.engine.gateway.positions.values():
            _add_pos(pos)

    return {
        "type":      "positions_update",
        "timestamp": datetime.now().isoformat(),
        "positions": positions,
    }


async def _positions_broadcast_loop():
    while True:
        try:
            if positions_manager.count > 0:
                await positions_manager.broadcast(_build_positions_snapshot())
        except Exception as exc:
            logger.warning(f"[WS:positions] 广播异常: {exc}")
        await asyncio.sleep(2)


async def _logs_broadcast_loop():
    """从 log_buffer._queue 消费新日志条目，实时广播到 /ws/logs 订阅者。"""
    # _queue 在 lifespan 中赋值，此处自旋等待直到可用
    while log_buffer._queue is None:
        await asyncio.sleep(0.1)
    while True:
        try:
            entry = await asyncio.wait_for(log_buffer._queue.get(), timeout=5.0)
            if log_manager.count > 0:
                await log_manager.broadcast({"type": "log_entry", **entry})
        except asyncio.TimeoutError:
            pass   # 无新日志，继续等待
        except Exception as exc:
            await asyncio.sleep(0.5)   # 短暂退避


# ---------------------------------------------------------------------------
# 日志缓冲区与实时推送
# ---------------------------------------------------------------------------

class _LogBuffer(logging.Handler):
    """拦截所有 Python 日志，存入环形缓冲并推送到 WS /ws/logs。"""
    MAX_ENTRIES = 500

    def __init__(self):
        super().__init__()
        self._buf: deque                       = deque(maxlen=self.MAX_ENTRIES)
        self._queue: Optional[asyncio.Queue]   = None   # 在 lifespan 中初始化

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "ts":      datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level":   record.levelname,
                "name":    record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", ""),
            }
            self._buf.append(entry)
            if self._queue is not None:
                try:
                    self._queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass   # 绝不让日志处理器自身抛异常

    def query(self, level: str = "", q: str = "", limit: int = 200) -> list:
        entries = list(self._buf)
        if level and level.upper() not in ("ALL", ""):
            entries = [e for e in entries if e["level"] == level.upper()]
        if q:
            ql = q.lower()
            entries = [e for e in entries if ql in e["message"].lower() or ql in e["name"].lower()]
        return entries[-limit:]


log_buffer = _LogBuffer()
log_buffer.setLevel(logging.DEBUG)
log_buffer.setFormatter(logging.Formatter("%(message)s"))


# ---------------------------------------------------------------------------
# 回测辅助
# ---------------------------------------------------------------------------
# Backtest helpers are implemented in api.backtest_service.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FastAPI 应用工厂
# ---------------------------------------------------------------------------

def create_app(title: str = "量化交易系统 API", version: str = "1.0.0") -> FastAPI:

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        global _event_loop
        _event_loop = asyncio.get_running_loop()
        for entry in trading_state.all_entries():
            _install_order_hook(entry)
        if trading_state._main_engine:
            _install_hook_on_engine(trading_state._main_engine)
        # 挂载日志缓冲 handler（异步队列必须在事件循环就绪后创建）
        log_buffer._queue = asyncio.Queue(maxsize=1000)
        logging.getLogger().addHandler(log_buffer)

        broadcast_task   = asyncio.create_task(_system_broadcast_loop())
        dashboard_task   = asyncio.create_task(_dashboard_broadcast_loop())
        positions_task   = asyncio.create_task(_positions_broadcast_loop())
        logs_task        = asyncio.create_task(_logs_broadcast_loop())
        logger.info("[API] WebSocket 广播任务已启动（system / dashboard / positions / logs）")
        yield
        logging.getLogger().removeHandler(log_buffer)
        for task in (broadcast_task, dashboard_task, positions_task, logs_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title=title,
        version=version,
        description="量化交易系统 API：CTP 登录 · REST 控制 · WebSocket 实时推送",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # ── 可观测性中间件：request id + 结构化访问日志 + 基础指标 ───────────────
    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            logger.exception(
                structured_json(
                    "http.request.error",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                )
            )
            raise
        finally:
            elapsed = time.perf_counter() - started
            metrics.record_http(request.method, request.url.path, status_code, elapsed)
            logger.info(
                structured_json(
                    "http.request",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status=status_code,
                    elapsed_ms=round(elapsed * 1000, 3),
                )
            )
        if response is not None:
            response.headers["X-Request-ID"] = request_id
        return response

    # ── 鉴权中间件 ────────────────────────────────────────────────────────────
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        # 开放路径（登录、状态查询、文档、WebSocket 握手）
        if is_open_path(path):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get(SESSION_COOKIE_NAME, "")
        if not token:
            return JSONResponse(
                {"detail": "未登录，请先连接交易账户"},
                status_code=401,
            )
        if not session_store.is_valid(token):
            return JSONResponse(
                {"detail": "会话已过期，请重新登录"},
                status_code=401,
            )
        response = await call_next(request)
        request_id = getattr(request.state, "request_id", "")
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    # ==================================================================
    # Auth 端点
    # ==================================================================

    @app.get("/health", summary="健康检查", tags=["运维"])
    def health():
        engine = trading_state.primary_engine()
        gateway_status = "stopped"
        if engine:
            gateway_status = engine.gateway.status.value if hasattr(engine.gateway.status, "value") else str(engine.gateway.status)
        return {
            "status": "ok",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "gateway_status": gateway_status,
            "active_sessions": session_store.active_count(),
            "websockets": {
                "system": system_manager.count,
                "orders": orders_manager.count,
                "dashboard": dashboard_manager.count,
                "positions": positions_manager.count,
                "logs": log_manager.count,
            },
        }

    @app.get("/metrics", summary="Prometheus 指标", tags=["运维"])
    def prometheus_metrics():
        return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")

    @app.get("/audit/events", summary="交易事件审计", tags=["运维"])
    def audit_events(event_type: str = "", limit: int = 200):
        return {"events": audit_log.query(event_type=event_type, limit=limit)}

    @app.get("/auth/servers", summary="获取预设服务器列表", tags=["认证"])
    def get_servers():
        """返回 CTP 交易/行情前置预设地址列表，供前端选择器使用。"""
        return {"td_servers": _PRESET_TD, "md_servers": _PRESET_MD}

    @app.get(
        "/auth/status",
        response_model=AuthStatusResponse,
        summary="当前连接状态（无需鉴权）",
        tags=["认证"],
    )
    def get_auth_status():
        """返回当前 CTP 网关的连接状态及连接日志，可在登录页面轮询。"""
        engine = trading_state.primary_engine()
        connected    = False
        gw_status    = TradingStatus.STOPPED.value
        gw_name      = "N/A"
        account_id   = ""

        if engine:
            gw         = engine.gateway
            gw_name    = gw.name
            gw_status  = gw.status.value if isinstance(gw.status, TradingStatus) else str(gw.status)
            connected  = gw.status in (TradingStatus.CONNECTED, TradingStatus.TRADING)
            account_id = gw.account.account_id if hasattr(gw, "account") else ""

        return AuthStatusResponse(
            logged_in         = session_store.has_active_sessions(),
            gateway_connected = connected,
            gateway_status    = gw_status,
            gateway_name      = gw_name,
            account_id        = account_id,
            connect_log       = trading_state.get_log(),
        )

    @app.post(
        "/auth/login",
        response_model=LoginResponse,
        summary="CTP 账户登录",
        tags=["认证"],
    )
    async def do_login(body: LoginRequest, response: Response, request: Request):
        """
        接受 CTP 账户配置，连接交易前置和行情前置。
        连接过程最长等待 35 秒。成功后返回会话 token。
        当前仅支持 vn.py/CTP 网关；登录成功后返回会话 token。
        """
        from ..trading import create_gateway

        trading_state.clear_log()
        trading_state.add_log(f"开始连接账户: {body.username}")
        _record_audit(
            "auth",
            "login_attempt",
            "started",
            actor=body.username,
            request=request,
            detail={"gateway_type": body.gateway_type},
        )

        # 若已有连接先断开
        if trading_state._main_engine:
            trading_state.add_log("检测到已有连接，正在断开…")
            trading_state.clear_main()

        # 确定网关类型：ctp 作为 vn.py CTP 网关别名保留，便于兼容旧配置。
        requested_gateway = (body.gateway_type or "vnpy").lower()
        if requested_gateway not in {"vnpy", "ctp"}:
            trading_state.add_log(f"不支持的网关类型: {body.gateway_type}")
            raise HTTPException(status_code=400, detail="仅支持 vn.py/CTP 网关")
        gateway_type = "vnpy"
        trading_state.add_log("使用 vn.py CTP 网关登录")

        config: Dict[str, Any] = {
            "gateway":    gateway_type,
            "username":   body.username,
            "password":   body.password,
            "broker_id":  body.broker_id,
            "td_server":  body.td_server,
            "md_server":  body.md_server,
            "app_id":     body.app_id,
            "auth_code":  body.auth_code,
            "vnpy_environment": body.environment,
            "connect_timeout": 25,
            "initial_capital": 0.0,
            "risk": {**_DEFAULT_RUNTIME_RISK, **dict(body.risk or {})},
            "log_callback": trading_state.add_log,
        }

        trading_state.add_log(f"交易前置: {body.td_server}")
        trading_state.add_log(f"行情前置: {body.md_server}")

        gateway = None
        try:
            gateway = create_gateway(gateway_type)
            loop    = asyncio.get_running_loop()
            trading_state.add_log("正在连接，请稍候（最长 35 秒）…")

            success = await asyncio.wait_for(
                loop.run_in_executor(None, gateway.connect, config),
                timeout=35,
            )
        except asyncio.TimeoutError:
            if gateway:
                try:
                    gateway.disconnect()
                except Exception:
                    logger.exception("[API] 登录超时后断开网关异常")
            trading_state.add_log("✘ 连接超时（35s）")
            logger.error("[API] 登录超时（35s），请检查网络和服务器地址")
            raise HTTPException(status_code=408, detail="连接超时，请检查服务器地址或网络")
        except Exception as exc:
            if gateway:
                try:
                    gateway.disconnect()
                except Exception:
                    logger.exception("[API] 登录异常后断开网关异常")
            trading_state.add_log(f"✘ 连接异常: {exc}")
            raise HTTPException(status_code=500, detail=f"连接异常: {exc}")

        if not success:
            if gateway:
                try:
                    gateway.disconnect()
                except Exception:
                    logger.exception("[API] 登录失败后断开网关异常")
            error_summary = ""
            if gateway and hasattr(gateway, "connection_error_summary"):
                error_summary = gateway.connection_error_summary()
            if error_summary:
                detail = f"vn.py/CTP 连接失败：{error_summary}"
                trading_state.add_log(f"✘ {detail}")
                raise HTTPException(status_code=502, detail=detail)

            trading_state.add_log("✘ 登录失败，请检查账户信息")
            raise HTTPException(status_code=401, detail="登录失败，请检查账户/密码/经纪商ID")

        # 构建 TradingEngine 并注册
        engine = TradingEngine(gateway)

        # 查询真实账户信息，用当前余额作为初始资金基准
        try:
            account    = gateway.query_account()
            account_id = account.account_id if account else ""
            balance    = account.balance    if account else 0.0
        except Exception:
            account_id, balance = "", 0.0

        # 用真实余额覆盖 initial_capital，并重置当日基准
        if balance > 0:
            config["initial_capital"] = balance
        engine.configure_risk(config)
        trading_state.set_main_engine(engine, config)
        trading_state._day_open_balance = balance  # 以登录时余额作为日内基准
        engine.risk_manager.set_day_open_balance(balance)

        strategy_started = False
        strategy_id = ""
        if body.auto_start_strategy:
            from ..strategy import create_strategy

            strategy_params = dict(body.strategy_params or {})
            if not strategy_params.get("symbol") and not strategy_params.get("symbols"):
                raise HTTPException(status_code=400, detail="自动启动策略需要 strategy_params.symbol 或 symbols")
            strategy = create_strategy(body.strategy_name, strategy_params)
            strategy.initial_capital = config.get("initial_capital", 0.0) or balance or 1_000_000.0
            engine.set_strategy(strategy)

            strategy_config = {
                **config,
                "strategy_name": body.strategy_name,
                "strategy_params": strategy_params,
            }
            if not engine.start(strategy_config):
                raise HTTPException(status_code=500, detail="策略运行时启动失败")

            strategy_id = f"{body.strategy_name}_main"
            trading_state.register(strategy_id, strategy, engine, strategy_config)
            symbols = strategy_params.get("symbols") or [strategy_params.get("symbol")]
            _subscribe_market_ticks(engine, [s for s in symbols if s])
            strategy_started = True
            trading_state.add_log(f"策略已自动启动: {strategy_id}")

        trading_state.add_log(f"✔ 登录成功，账户: {account_id}，当前余额: ¥{balance:,.2f}（用作初始资金基准）")

        token = session_store.create()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=secure_session_cookie_enabled(),
        )

        _record_audit(
            "auth",
            "login",
            "success",
            actor=body.username,
            request=request,
            detail={"gateway_type": gateway_type, "account_id": account_id},
        )

        return LoginResponse(
            success        = True,
            message        = "登录成功，策略已启动" if strategy_started else "登录成功",
            gateway_status = gateway.status.value,
            account_id     = account_id,
            balance        = balance,
            strategy_started = strategy_started,
            strategy_id      = strategy_id,
        )

    @app.post("/auth/logout", summary="断开连接并注销会话", tags=["认证"])
    async def do_logout(request: Request):
        auth  = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if token:
            session_store.revoke(token)
        request_token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if request_token:
            session_store.revoke(request_token)
        response = JSONResponse({"success": True, "message": "已断开连接"})
        response.delete_cookie(SESSION_COOKIE_NAME)
        trading_state.add_log("用户主动断开连接")
        _record_audit("auth", "logout", "success", request=request)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, trading_state.clear_main)
        return response

    # ==================================================================
    # REST 端点
    # ==================================================================

    @app.get(
        "/system/status",
        response_model=SystemStatusResponse,
        summary="系统健康状态",
        tags=["系统"],
    )
    def get_system_status():
        engine          = trading_state.primary_engine()
        market_connected = False
        gateway_status   = TradingStatus.STOPPED.value
        gateway_name     = "N/A"
        account_dict     = None

        if engine:
            gw             = engine.gateway
            gateway_name   = gw.name
            gateway_status = gw.status.value if isinstance(gw.status, TradingStatus) else str(gw.status)
            market_connected = gw.status in (TradingStatus.CONNECTED, TradingStatus.TRADING)
            try:
                account = engine.get_account()
                if not account.error_msg:
                    account_dict = _account_to_dict(account)
            except Exception as exc:
                logger.warning(f"查询账户信息失败: {exc}")

        active_count = sum(1 for e in trading_state.all_entries() if e.status == "running")
        return SystemStatusResponse(
            timestamp        = datetime.now().isoformat(),
            market_connected = market_connected,
            gateway_status   = gateway_status,
            gateway_name     = gateway_name,
            cpu_percent      = psutil.cpu_percent(interval=None),
            memory_percent   = psutil.virtual_memory().percent,
            active_strategies = active_count,
            account          = account_dict,
        )

    @app.get("/strategies", response_model=List[StrategyInfo], summary="策略列表", tags=["策略"])
    def list_strategies():
        result: List[StrategyInfo] = []
        for entry in trading_state.all_entries():
            s         = entry.strategy
            positions = [_position_to_schema(p) for p in s.positions.values() if p.volume != 0]
            result.append(StrategyInfo(
                strategy_id = entry.strategy_id,
                name        = s.name,
                status      = entry.status,
                symbol      = getattr(s, "symbol", None),
                pnl         = _calc_strategy_pnl(entry),
                positions   = positions,
                trade_count = len(s.trades),
                error_count = s._error_count,
            ))
        return result

    @app.get(
        "/strategies/{strategy_id}",
        response_model=StrategyDetailResponse,
        summary="策略详情（含信号、参数、权重）",
        tags=["策略"],
    )
    def get_strategy_detail(strategy_id: str):
        entry = trading_state.get(strategy_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")
        s         = entry.strategy
        positions = [_position_to_schema(p) for p in s.positions.values() if p.volume != 0]
        signals   = [_signal_to_schema(sig) for sig in list(s.signals)[-10:]]
        return StrategyDetailResponse(
            strategy_id    = entry.strategy_id,
            name           = s.name,
            status         = entry.status,
            symbol         = getattr(s, "symbol", None),
            pnl            = _calc_strategy_pnl(entry),
            trade_count    = len(s.trades),
            error_count    = s._error_count,
            positions      = positions,
            weight         = trading_state.get_weight(strategy_id),
            params         = dict(s.params),
            recent_signals = signals,
        )

    @app.put(
        "/strategies/{strategy_id}/params",
        summary="更新策略参数",
        tags=["策略"],
    )
    def update_strategy_params(strategy_id: str, body: ParamsUpdateRequest):
        """
        热更新策略参数。如果 restart=true 且策略正在运行，则先停止再启动。
        不需要 restart 的参数（如 volume、threshold）会立刻生效。
        """
        entry = trading_state.get(strategy_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")

        s = entry.strategy
        # 更新 params 字典
        s.params.update(body.params)
        entry.config.update(body.params)

        # 尝试热更新策略实例属性
        for key, val in body.params.items():
            if hasattr(s, key):
                try:
                    setattr(s, key, type(getattr(s, key))(val))
                except Exception:
                    setattr(s, key, val)

        was_running = entry.status == "running"
        if body.restart and was_running:
            try:
                entry.engine.stop()
                entry.engine.start(entry.config)
                logger.info(f"[API] 策略 {strategy_id} 参数更新后重启")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"重启失败: {exc}")

        return {
            "success":     True,
            "strategy_id": strategy_id,
            "params":      dict(s.params),
            "restarted":   body.restart and was_running,
        }

    @app.put(
        "/strategies/weights",
        summary="批量更新策略权重",
        tags=["策略"],
    )
    def update_weights(body: WeightRequest):
        """
        更新所有策略的权重分配（0.0~1.0）。权重用于后续资金分配计算。
        """
        # 验证策略 ID
        unknown = [sid for sid in body.weights if trading_state.get(sid) is None]
        if unknown:
            raise HTTPException(status_code=404, detail=f"未知策略: {unknown}")
        trading_state.set_weights(body.weights)
        return {
            "success": True,
            "weights": trading_state.all_weights(),
        }

    @app.get("/positions", response_model=List[PositionInfo], summary="当前持仓", tags=["持仓"])
    def list_positions():
        merged: Dict[str, PositionInfo] = {}
        engine = trading_state.primary_engine()
        if engine:
            for symbol, pos in engine.gateway.positions.items():
                if pos.volume != 0:
                    merged[symbol] = _position_to_schema(pos)
        for entry in trading_state.all_entries():
            for symbol, pos in entry.engine.gateway.positions.items():
                if pos.volume != 0 and symbol not in merged:
                    merged[symbol] = _position_to_schema(pos)
        return list(merged.values())

    @app.get("/orders", summary="全部委托单列表", tags=["订单簿"])
    def list_orders():
        """返回所有委托单（含历史），按时间倒序，最多 500 条。"""
        return _collect_all_orders()[:500]

    @app.get("/trades", summary="全部成交记录", tags=["订单簿"])
    def list_trades():
        """返回所有已成交记录，按时间倒序，最多 500 条。"""
        return _collect_all_trades()[:500]

    @app.get("/risk/status", summary="风控状态", tags=["风控"])
    def risk_status():
        engine = trading_state.primary_engine()
        if engine is None:
            return {"connected": False, "risk": None}
        return {
            "connected": True,
            "gateway_status": engine.gateway.status.value if hasattr(engine.gateway.status, "value") else str(engine.gateway.status),
            "risk": engine.risk_manager.status(),
            "last_reject_reason": getattr(engine, "last_reject_reason", ""),
        }

    @app.put("/risk/config", summary="更新运行时风控配置", tags=["风控"])
    def update_risk_config(body: RiskConfigRequest, request: Request):
        engines = _unique_engines()
        if not engines:
            raise HTTPException(status_code=503, detail="交易引擎未连接")
        merged = {**_DEFAULT_RUNTIME_RISK, **dict(body.risk or {})}
        for engine in engines:
            engine.configure_risk({"risk": merged})
        if trading_state._main_config is not None:
            trading_state._main_config["risk"] = merged
        _record_audit("risk", "update_config", "success", request=request, detail={"risk": merged})
        return {"success": True, "risk": engines[0].risk_manager.status()}

    @app.post("/risk/emergency-stop", summary="交易急停", tags=["风控"])
    def emergency_stop(body: EmergencyStopRequest, request: Request):
        engines = _unique_engines()
        if not engines:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        reason = body.reason.strip() or "operator emergency stop"
        for engine in engines:
            engine.risk_manager.set_emergency_stop(True, reason)

        cancel_result = _cancel_all_active_orders() if body.cancel_orders else {"cancelled": 0, "failed": 0}
        stopped = 0
        if body.stop_strategies:
            for entry in trading_state.all_entries():
                try:
                    entry.engine.stop()
                    stopped += 1
                except Exception:
                    logger.exception("[risk] 急停策略停止失败: %s", entry.strategy_id)

        trading_state.add_log(f"交易急停已触发: {reason}")
        _record_audit(
            "risk",
            "emergency_stop",
            "success",
            request=request,
            detail={**cancel_result, "stopped_strategies": stopped, "reason": reason},
        )
        return {
            "success": True,
            "emergency_stop": True,
            "reason": reason,
            **cancel_result,
            "stopped_strategies": stopped,
        }

    @app.post("/risk/resume", summary="解除交易急停", tags=["风控"])
    def resume_trading(request: Request):
        engines = _unique_engines()
        if not engines:
            raise HTTPException(status_code=503, detail="交易引擎未连接")
        for engine in engines:
            engine.risk_manager.set_emergency_stop(False, "")
        trading_state.add_log("交易急停已解除")
        _record_audit("risk", "resume", "success", request=request)
        return {"success": True, "emergency_stop": False}

    @app.get("/trading/reconcile", summary="账户/委托/持仓对账快照", tags=["风控"])
    def trading_reconcile():
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")
        account = engine.get_account()
        positions_snapshot = _build_positions_snapshot()
        orders = _collect_all_orders()
        active_orders = [order for order in orders if order.get("status") in {"submitting", "submitted", "partfilled"}]
        return {
            "timestamp": datetime.now().isoformat(),
            "connected": engine.gateway.status in (TradingStatus.CONNECTED, TradingStatus.TRADING),
            "gateway_status": engine.gateway.status.value if hasattr(engine.gateway.status, "value") else str(engine.gateway.status),
            "account": _account_to_dict(account) if not getattr(account, "error_msg", "") else {"error_msg": account.error_msg},
            "risk": engine.risk_manager.status(),
            "orders": {
                "active_count": len(active_orders),
                "total_count": len(orders),
                "active": active_orders[:100],
            },
            "positions": {
                "count": len(positions_snapshot.get("positions", [])),
                "items": positions_snapshot.get("positions", []),
            },
            "strategies": [
                {
                    "strategy_id": entry.strategy_id,
                    "name": entry.strategy.name,
                    "status": entry.status,
                    "signal_count": len(getattr(entry.strategy, "signals", [])),
                    "trade_count": len(getattr(entry.strategy, "trades", [])),
                    "error_count": getattr(entry.strategy, "_error_count", 0),
                }
                for entry in trading_state.all_entries()
            ],
        }

    @app.delete("/orders/{order_id}", summary="撤销委托单", tags=["订单簿"])
    def cancel_order(order_id: str, request: Request):
        """调用网关 cancel_order()，结果通过 /ws/orders 回调推送。"""
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")
        gw = engine.gateway
        # 先在主引擎找，再去各策略引擎
        order = gw.orders.get(order_id)
        if order is None:
            for entry in trading_state.all_entries():
                order = entry.engine.gateway.orders.get(order_id)
                if order:
                    gw = entry.engine.gateway
                    break
        if order is None:
            raise HTTPException(status_code=404, detail=f"委托单不存在: {order_id}")
        try:
            success = gw.cancel_order(order_id)
            _record_audit(
                "order",
                "cancel",
                "success" if success else "rejected",
                resource=order_id,
                request=request,
            )
            return {"success": bool(success), "order_id": order_id}
        except Exception as exc:
            _record_audit("order", "cancel", "error", resource=order_id, request=request, detail={"error": str(exc)})
            raise HTTPException(status_code=500, detail=f"撤单失败: {exc}")

    # ── 手动交易端点 ──────────────────────────────────────────────────────────

    @app.post("/orders", summary="手动下单", tags=["手动交易"])
    def place_manual_order(body: ManualOrderRequest, request: Request):
        """
        手动下达交易指令（开仓/平仓）。

        - direction: "long"（买入）或 "short"（卖出）
        - offset: "open"（开仓）、"close"（平仓）、"close_today"（平今）、"close_yesterday"（平昨）
        - price: 0 表示市价单
        - order_type: "market" 或 "limit"
        """
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        try:
            symbol = _clean_manual_symbol(body.symbol)
            direction_name, direction = _normalize_choice(body.direction, _MANUAL_DIRECTION_MAP, "买卖方向")
            offset_name, offset = _normalize_choice(body.offset, _MANUAL_OFFSET_MAP, "开平方向")
            order_type_name, order_type = _normalize_choice(body.order_type, _MANUAL_ORDER_TYPE_MAP, "订单类型")
            volume = _positive_volume(body.volume)
            price = _manual_order_price(order_type, body.price)
        except HTTPException as exc:
            _record_audit(
                "order",
                "manual_order",
                "rejected",
                resource=str(body.symbol or ""),
                request=request,
                detail={"reason": exc.detail, "volume": body.volume, "direction": body.direction},
            )
            raise

        signal = Signal(
            symbol=symbol,
            datetime=datetime.now(),
            direction=direction,
            price=price,
            volume=volume,
            order_type=order_type,
            offset=offset,
            comment=f"manual_{offset_name}",
        )

        try:
            order_id = engine.send_signal(signal)
            if not order_id:
                reason = getattr(engine, "last_reject_reason", "")
                if reason:
                    _record_audit(
                        "order",
                        "manual_order",
                        "rejected",
                        resource=body.symbol,
                        request=request,
                        detail={"reason": reason, "volume": volume, "direction": direction_name},
                    )
                    raise HTTPException(status_code=400, detail=f"风控拒单: {reason}")
                raise HTTPException(status_code=500, detail="下单失败，引擎未返回订单号")
            _record_audit(
                "order",
                "manual_order",
                "success",
                resource=order_id,
                request=request,
                detail={"symbol": symbol, "volume": volume, "direction": direction_name, "offset": offset_name},
            )
            return {
                "success": True,
                "order_id": order_id,
                "symbol": symbol,
                "direction": direction_name,
                "offset": offset_name,
                "price": price,
                "volume": volume,
                "order_type": order_type_name,
            }
        except HTTPException:
            raise
        except Exception as exc:
            _record_audit("order", "manual_order", "error", resource=symbol, request=request, detail={"error": str(exc)})
            raise HTTPException(status_code=500, detail=f"下单失败: {exc}")

    @app.post("/orders/cancel-all", summary="一键撤销所有活跃委托", tags=["手动交易"])
    def cancel_all_orders():
        """撤销所有活跃状态的委托单。"""
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        cancelled = 0
        failed = 0
        result = _cancel_all_active_orders()
        cancelled = result["cancelled"]
        failed = result["failed"]

        return {"success": True, "cancelled": cancelled, "failed": failed}

    @app.post("/positions/{symbol}/close", summary="快捷平仓", tags=["手动交易"])
    def close_position(symbol: str, request: Request, body: ClosePositionRequest = ClosePositionRequest()):
        """
        快捷平仓指定合约。自动推断持仓方向和数量。

        - volume: 0 表示全部平仓
        - price: 0 表示市价
        """
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        try:
            clean_symbol = _clean_manual_symbol(symbol)
            direction_name = str(body.direction or "").strip().lower()
            if direction_name:
                direction_name, _ = _normalize_choice(direction_name, _MANUAL_DIRECTION_MAP, "持仓方向")
            offset_name, offset = _normalize_choice(body.offset, _MANUAL_CLOSE_OFFSET_MAP, "平仓类型")
            requested_volume = _positive_volume(body.volume, allow_zero=True)
            inferred_type = "limit" if float(body.price or 0) > 0 else "market"
            order_type_name = str(body.order_type or inferred_type).strip().lower()
            order_type_name, order_type = _normalize_choice(order_type_name, _MANUAL_ORDER_TYPE_MAP, "订单类型")
            price = _manual_order_price(order_type, body.price)
            pos = _find_close_position(engine.gateway.positions, clean_symbol, direction_name)
        except HTTPException as exc:
            _record_audit(
                "order",
                "manual_close_position",
                "rejected",
                resource=symbol,
                request=request,
                detail={"reason": exc.detail, "volume": body.volume, "direction": body.direction},
            )
            raise

        if pos is None:
            _record_audit("order", "manual_close_position", "rejected", resource=symbol, request=request, detail={"reason": "position_not_found"})
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的持仓")

        available_volume = abs(int(getattr(pos, "volume", 0) or 0))
        close_volume = requested_volume if requested_volume > 0 else available_volume
        if close_volume > available_volume:
            _record_audit(
                "order",
                "manual_close_position",
                "rejected",
                resource=clean_symbol,
                request=request,
                detail={"reason": "close_volume_exceeds_position", "requested": close_volume, "available": available_volume},
            )
            raise HTTPException(status_code=400, detail=f"平仓数量 {close_volume} 超过当前持仓 {available_volume}")

        close_direction = _close_direction_for_position(pos)

        signal = Signal(
            symbol=clean_symbol,
            datetime=datetime.now(),
            direction=close_direction,
            price=price,
            volume=close_volume,
            order_type=order_type,
            offset=offset,
            comment="manual_close_position",
        )

        try:
            order_id = engine.send_signal(signal)
            if not order_id:
                reason = getattr(engine, "last_reject_reason", "")
                if reason:
                    _record_audit("order", "manual_close_position", "rejected", resource=clean_symbol, request=request, detail={"reason": reason})
                    raise HTTPException(status_code=400, detail=f"风控拒单: {reason}")
                raise HTTPException(status_code=500, detail="平仓下单失败")
            _record_audit(
                "order",
                "manual_close_position",
                "success",
                resource=order_id,
                request=request,
                detail={"symbol": clean_symbol, "volume": close_volume, "offset": offset_name, "direction": close_direction.value},
            )
            return {
                "success": True,
                "order_id": order_id,
                "symbol": clean_symbol,
                "direction": close_direction.value,
                "volume": close_volume,
                "price": price,
                "offset": offset_name,
                "order_type": order_type_name,
            }
        except HTTPException:
            raise
        except Exception as exc:
            _record_audit("order", "manual_close_position", "error", resource=symbol, request=request, detail={"error": str(exc)})
            raise HTTPException(status_code=500, detail=f"平仓失败: {exc}")

    @app.post(
        "/strategy/{strategy_id}/action",
        response_model=ActionResponse,
        summary="控制策略启停",
        tags=["策略"],
    )
    def strategy_action(strategy_id: str, body: ActionRequest):
        action = body.action.strip().lower()
        if action not in ("start", "stop"):
            raise HTTPException(status_code=400, detail=f"不支持的操作: '{action}'")
        entry = trading_state.get(strategy_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")
        try:
            if action == "start":
                if entry.status == "running":
                    return ActionResponse(success=True, strategy_id=strategy_id, action=action,
                                         message="策略已在运行中")
                if not entry.engine.start(entry.config):
                    raise HTTPException(status_code=500, detail="策略启动失败")
                return ActionResponse(success=True, strategy_id=strategy_id, action=action,
                                      message="策略已启动")
            else:
                if entry.status == "stopped":
                    return ActionResponse(success=True, strategy_id=strategy_id, action=action,
                                         message="策略已停止")
                entry.engine.stop()
                return ActionResponse(success=True, strategy_id=strategy_id, action=action,
                                      message="策略已停止")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(f"策略操作失败 [{strategy_id}] {action}")
            raise HTTPException(status_code=500, detail=str(exc))

    # ==================================================================
    # WebSocket 端点
    # ==================================================================

    @app.get(
        "/dashboard/metrics",
        summary="全局仪表盘指标快照",
        tags=["仪表盘"],
    )
    def get_dashboard_metrics():
        """一次性返回仪表盘所有关键指标（PnL、收益率、夏普比率、最大回撤、仓位概览）。"""
        return _build_dashboard_metrics()

    @app.get("/data/quality", summary="历史行情数据质量报告", tags=["数据治理"])
    def data_quality(symbol: str, start_date: str = "", end_date: str = "", timeframe: str = "1d"):
        from ..data import DataManager

        if not symbol:
            raise HTTPException(status_code=400, detail="symbol 不能为空")
        dm = DataManager()
        if not start_date or not end_date:
            first, last = dm.db.get_data_range(symbol, timeframe)
            start_date = start_date or first or ""
            end_date = end_date or last or ""
        if not start_date or not end_date:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "range": {"start": start_date, "end": end_date},
                "metadata": dm.db.get_metadata(symbol, timeframe),
                "gaps": {"has_gaps": False, "missing_count": 0},
                "quality": {"rows": 0},
                "cache": dm.cache.stats(),
            }
        return dm.inspect_data_quality(symbol, start_date, end_date, timeframe)

    # ==================================================================
    # WebSocket 端点
    # ==================================================================

    @app.websocket("/ws/system")
    async def ws_system(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await system_manager.connect(ws)
        try:
            await ws.send_text(_json.dumps(_build_system_snapshot(), ensure_ascii=False, default=str))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(f"[WS:system] 连接异常: {exc}")
        finally:
            system_manager.disconnect(ws)

    @app.websocket("/ws/orders")
    async def ws_orders(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await orders_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(f"[WS:orders] 连接异常: {exc}")
        finally:
            orders_manager.disconnect(ws)

    @app.websocket("/ws/positions")
    async def ws_positions(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await positions_manager.connect(ws)
        try:
            # 连接时立即推送当前持仓快照
            await ws.send_text(
                _json.dumps(_build_positions_snapshot(), ensure_ascii=False, default=str)
            )
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(f"[WS:positions] 连接异常: {exc}")
        finally:
            positions_manager.disconnect(ws)

    @app.websocket("/ws/dashboard")
    async def ws_dashboard(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await dashboard_manager.connect(ws)
        try:
            # 连接时立即推送一次快照
            await ws.send_text(
                _json.dumps(_build_dashboard_metrics(), ensure_ascii=False, default=str)
            )
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(f"[WS:dashboard] 连接异常: {exc}")
        finally:
            dashboard_manager.disconnect(ws)

    @app.get("/ws-demo", response_class=HTMLResponse, include_in_schema=False)
    def ws_demo_page():
        from pathlib import Path
        demo = Path(__file__).parent.parent.parent / "static" / "ws_demo.html"
        return demo.read_text(encoding="utf-8") if demo.exists() else "<h3>演示页面未找到</h3>"

    # ── 系统日志端点 ──────────────────────────────────────────────────────────
    @app.get("/system/logs")
    async def get_system_logs(
        request: Request,
        level: str = "",
        q:     str = "",
        limit: int = 200,
    ):
        """
        查询系统日志（内存缓冲，最多 500 条）。
        - level: DEBUG / INFO / WARNING / ERROR / CRITICAL / ALL（默认全部）
        - q:     关键词全文搜索
        - limit: 返回条数上限（默认 200）
        """
        entries = log_buffer.query(level=level, q=q, limit=min(limit, 500))
        return JSONResponse({"logs": entries, "total": len(entries)})

    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await log_manager.connect(ws)
        try:
            # 连接时先推送最近 200 条历史日志
            history = log_buffer.query(limit=200)
            await ws.send_text(
                _json.dumps(
                    {"type": "log_history", "logs": history},
                    ensure_ascii=False, default=str,
                )
            )
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(f"[WS:logs] 连接异常: {exc}")
        finally:
            log_manager.disconnect(ws)

    # ── 回测端点 ──────────────────────────────────────────────────────────────
    @app.get("/backtest/strategies")
    async def bt_list_strategies(request: Request):
        """返回可用策略列表及默认参数。"""
        return JSONResponse({"strategies": STRATEGY_CATALOG})

    @app.post("/backtest/run")
    async def bt_run(body: BacktestRunRequest, request: Request):
        """运行回测，返回完整结果（资金曲线、交易标记、指标、热力图数据）。"""
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, run_backtest_sync, body),
                timeout=120,
            )
        except asyncio.TimeoutError:
            return JSONResponse({"success": False, "error": "回测超时（>120s），请缩短日期范围"}, status_code=504)
        except Exception as exc:
            logger.error(f"[backtest] 运行失败: {exc}\n{traceback.format_exc()}")
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
        return JSONResponse(result)

    # ── K线数据 ───────────────────────────────────────────────────────────────
    @app.get("/watch/kline", summary="K线数据 + 技术指标", tags=["行情"])
    async def watch_kline(
        request:    Request,
        symbol:     str           = "rb2501",
        interval:   str           = "1d",
        limit:      int           = 100,
        indicators: str           = "",
        since:      Optional[str] = None,
    ):
        """
        获取 K 线数据并计算技术指标。

        **周期 interval**：1m / 5m / 15m / 30m / 1h / 4h / 1d / 1w

        **指标 indicators**（逗号分隔）：
        - `ma{N}`     简单移动均线，如 `ma20,ma60`
        - `ema{N}`    指数移动均线，如 `ema12`
        - `macd`      MACD(12,26,9)，返回 macd / macd_signal / macd_hist
        - `rsi` / `rsi{N}`   RSI，默认 14 周期
        - `kdj`       KDJ(9,3,3)，返回 k / d / j
        - `boll` / `boll{N}` 布林带，返回 boll{N}_upper/mid/lower
        - `vol_ma{N}` 成交量均线，如 `vol_ma5`

        **增量更新**：传入 `since=2024-01-15T09:30:00` 仅返回该时刻之后的 bar。

        缓存 TTL：1m=10s / 1h=120s / 1d=600s。
        """
        from ..watch.kline import get_kline
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: get_kline(
                    symbol     = symbol,
                    interval   = interval,
                    limit      = min(limit, 1000),
                    indicators = indicators,
                    since      = since,
                ),
            )
            if result.get("code") != 0:
                return JSONResponse(result, status_code=400)
            return JSONResponse(result)
        except Exception as exc:
            logger.error(f"[watch/kline] 获取失败: {exc}\n{traceback.format_exc()}")
            return JSONResponse({"code": 1, "msg": str(exc)}, status_code=500)

    @app.delete("/watch/kline/cache", summary="清除K线缓存", tags=["行情"])
    async def clear_kline_cache(request: Request, symbol: str = ""):
        """清除指定合约（或全部）的 K 线缓存。"""
        from ..watch.kline import kline_cache
        prefix = f"kline:{symbol}" if symbol else "kline:"
        n = kline_cache.invalidate(prefix)
        return JSONResponse({"code": 0, "cleared": n})

    # ── /watch/tick — 批量查询实时行情（REST轮询） ────────────────────────────
    #
    # 参数：symbols - 逗号分隔的合约代码，如 "IF2506,IC2506,rb2601"
    # 响应：{ code, ticks: { symbol: { ...tick_data } } }
    #
    @app.get("/watch/tick", summary="批量查询实时行情", tags=["行情"])
    async def get_ticks(symbols: str = ""):
        """批量查询多个合约的实时 tick 数据，用于前端轮询。"""
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not sym_list:
            return JSONResponse({"code": 1, "msg": "请提供 symbols 参数"}, status_code=400)

        engine = trading_state.primary_engine()
        if engine is None or engine.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
            return JSONResponse(
                {"code": 1, "msg": "行情网关未连接，请先登录 CTP 账户", "ticks": {}, "missing_symbols": sym_list},
                status_code=503,
            )

        ticks_result, missing = await _wait_for_gateway_ticks(engine, sym_list)
        msg = "" if not missing else f"已订阅但尚未收到实时行情: {', '.join(missing)}"
        return JSONResponse(
            {
                "code": 0,
                "ticks": ticks_result,
                "missing_symbols": missing,
                "msg": msg,
            }
        )

    # ── /ws/watch — 盯盘系统实时行情 WebSocket ────────────────────────────────
    #
    # 协议（JSON）：
    #   客户端 → 服务端
    #     { type: 'subscribe',   symbols: [...], channels: ['tick', 'kline_1m'] }
    #     { type: 'unsubscribe', symbols: [...] }
    #     纯文本 'ping' → 服务端回 'pong'
    #
    #   服务端 → 客户端
    #     { type: 'subscribed', symbols: [...] }
    #     { type: 'tick',       symbol, last, open, high, low, pre_close,
    #                           volume, turnover, open_interest,
    #                           bid1..bid5, ask1..ask5,
    #                           bid1_vol..bid5_vol, ask1_vol..ask5_vol,
    #                           change, change_rate, time }
    #     { type: 'kline_update', symbol, interval, bar: {...} }
    #     { type: 'error',        code, msg }

    # 每个连接独立维护自己的订阅集合；tick 生成器按已订阅品种模拟行情。
    @app.websocket("/ws/watch")
    async def ws_watch(ws: WebSocket):
        if not await _require_websocket_session(ws):
            return
        await ws.accept()
        logger.info("[WS:watch] 新连接")

        # 每个连接的订阅状态：symbol → set(channels)
        subscriptions: Dict[str, set] = {}
        last_sent_ts: Dict[str, str] = {}

        # 监听客户端消息（订阅 / 取消 / ping）
        async def _recv_loop():
            try:
                while True:
                    raw = await ws.receive_text()
                    if raw == "ping":
                        await ws.send_text("pong")
                        continue
                    try:
                        msg = _json.loads(raw)
                    except Exception:
                        continue

                    mtype = msg.get("type", "")
                    syms  = msg.get("symbols", [])
                    chs   = msg.get("channels", ["tick"])

                    if mtype == "subscribe" and syms:
                        engine = trading_state.primary_engine()
                        if engine is None or engine.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                            await ws.send_text(_json.dumps({
                                "type": "error",
                                "code": "gateway_not_connected",
                                "msg": "行情网关未连接，请先登录 CTP 账户",
                            }, ensure_ascii=False))
                            continue

                        for s in syms:
                            if s not in subscriptions:
                                subscriptions[s] = set()
                            subscriptions[s].update(chs)
                        _subscribe_market_ticks(engine, syms)
                        await ws.send_text(_json.dumps({
                            "type": "subscribed", "symbols": syms,
                        }, ensure_ascii=False))
                        logger.info(f"[WS:watch] 订阅: {syms} channels={chs}")

                    elif mtype == "unsubscribe" and syms:
                        for s in syms:
                            if msg.get("channels"):
                                for ch in msg["channels"]:
                                    subscriptions.get(s, set()).discard(ch)
                                if not subscriptions.get(s):
                                    subscriptions.pop(s, None)
                            else:
                                subscriptions.pop(s, None)
                        logger.info(f"[WS:watch] 取消订阅: {syms}")

            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.debug(f"[WS:watch] recv_loop 异常: {e}")

        # 推送 tick 循环（每 500ms 推送一次）
        async def _push_loop():
            try:
                while True:
                    await asyncio.sleep(0.5)
                    if not subscriptions:
                        continue
                    engine = trading_state.primary_engine()
                    if engine is None:
                        continue
                    for symbol, channels in list(subscriptions.items()):
                        if "tick" not in channels and not any(
                            ch.startswith("kline_") for ch in channels
                        ):
                            continue

                        snapshot = _gateway_tick_snapshot(engine.gateway, symbol)
                        if not snapshot or float(snapshot.get("last") or 0) <= 0:
                            _subscribe_market_ticks(engine, [symbol])
                            continue

                        if "tick" in channels:
                            timestamp = str(snapshot.get("timestamp") or snapshot.get("time") or "")
                            if last_sent_ts.get(symbol) != timestamp:
                                await ws.send_text(_json.dumps(snapshot, ensure_ascii=False, default=str))
                                last_sent_ts[symbol] = timestamp

                        # K 线更新（用当前真实 tick 生成当前周期的增量 bar）
                        for ch in channels:
                            if not ch.startswith("kline_"):
                                continue
                            iv = ch[6:]  # e.g. "1m"
                            last = float(snapshot.get("last") or 0)
                            bar_update = {
                                "type":     "kline_update",
                                "symbol":   symbol,
                                "interval": iv,
                                "bar": {
                                    "time":   datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "open":   snapshot.get("open") or last,
                                    "high":   max(float(snapshot.get("high") or last), last),
                                    "low":    min(float(snapshot.get("low") or last), last),
                                    "close":  last,
                                    "volume": snapshot.get("volume") or 0,
                                },
                            }
                            await ws.send_text(
                                _json.dumps(bar_update, ensure_ascii=False, default=str)
                            )
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.debug(f"[WS:watch] push_loop 异常: {e}")

        try:
            await asyncio.gather(_recv_loop(), _push_loop())
        except Exception as exc:
            logger.debug(f"[WS:watch] 连接结束: {exc}")
        finally:
            logger.info("[WS:watch] 连接关闭")

    # ── 期货品种搜索 ──────────────────────────────────────────────────────────
    @app.get("/watch/search", summary="期货品种/合约搜索", tags=["行情"])
    async def watch_search(
        query:    str           = "",
        exchange: Optional[str] = None,
        limit:    int           = 50,
    ):
        """
        搜索期货合约，支持：
        - **合约根码** 前缀匹配（`rb` → 所有螺纹钢合约）
        - **完整合约代码** 精确定位（`rb2501`）
        - **中文名称** 包含匹配（`螺纹` → 螺纹钢）
        - **拼音首字母** 前缀匹配（`lwg` → 螺纹钢，`hj` → 黄金）

        可选 `exchange` 按交易所过滤：SHFE / DCE / CZCE / CFFEX / INE / GFEX
        """
        from ..watch import search_contracts
        try:
            data = search_contracts(query=query, exchange=exchange, limit=min(limit, 200))
            return JSONResponse({"code": 0, "data": data, "total": len(data)})
        except Exception as exc:
            logger.error(f"[watch/search] 搜索失败: {exc}")
            return JSONResponse({"code": 1, "data": [], "total": 0, "msg": str(exc)}, status_code=500)

    return app


app = create_app()
