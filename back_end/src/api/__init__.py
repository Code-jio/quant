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
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import psutil
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..strategy import Direction, StrategyBase
from ..trading import AccountInfo, GatewayBase, TradingEngine, TradingStatus
from ..strategy import Signal, OrderType, OffsetFlag
from .security import SESSION_COOKIE_MAX_AGE, SESSION_COOKIE_NAME, is_open_path, session_store

logger = logging.getLogger(__name__)

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
    broker_id:  str = "2071"
    td_server:  str = "tcp://114.94.128.1:42205"
    md_server:  str = "tcp://114.94.128.1:42213"
    app_id:     str = "client_TraderMaster_v1.0.0"
    auth_code:  str = ""
    gateway_type: str = "vnpy"   # "vnpy" | "ctp" | "simulated"
    environment: str = "测试"     # vn.py CTP 柜台环境："实盘" | "测试"


class LoginResponse(BaseModel):
    success:        bool
    token:          str
    message:        str
    gateway_status: str
    account_id:     str = ""
    balance:        float = 0.0


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
    strategy_params: Dict[str, Any] = {}
    start_date:      str            = "2023-01-01"
    end_date:        str            = "2024-12-31"
    initial_capital: float          = 1_000_000
    commission_rate: float          = 0.0003
    slip_rate:       float          = 0.0001
    margin_rate:     float          = 0.12
    sample_days:     int            = 700   # 模拟数据天数


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
    volume: int = 0         # 0 = 全部平仓
    price:  float = 0       # 0 = 市价


# ---------------------------------------------------------------------------
# 预设服务器列表（西南期货 SimNow / 生产前置）
# ---------------------------------------------------------------------------

_PRESET_TD = [
    {"label": "电信1", "value": "tcp://114.94.128.1:42205"},
    {"label": "联通1", "value": "tcp://140.206.34.161:42205"},
    {"label": "电信2", "value": "tcp://114.94.128.5:42205"},
    {"label": "联通2", "value": "tcp://140.206.34.165:42205"},
    {"label": "电信3", "value": "tcp://114.94.128.6:42205"},
    {"label": "联通3", "value": "tcp://140.206.34.166:42205"},
]

_PRESET_MD = [
    {"label": "电信1", "value": "tcp://114.94.128.1:42213"},
    {"label": "联通1", "value": "tcp://140.206.34.161:42213"},
    {"label": "电信2", "value": "tcp://114.94.128.5:42213"},
    {"label": "联通2", "value": "tcp://140.206.34.165:42213"},
    {"label": "电信3", "value": "tcp://114.94.128.6:42213"},
    {"label": "联通3", "value": "tcp://140.206.34.166:42213"},
]

# ---------------------------------------------------------------------------
# WebSocket 连接管理器
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self, channel: str):
        self.channel = channel
        self._connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"[WS:{self.channel}] 新连接，当前: {len(self._connections)} 条")

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        logger.info(f"[WS:{self.channel}] 连接断开，剩余: {len(self._connections)} 条")

    async def broadcast(self, payload: dict):
        if not self._connections:
            return
        text = _json.dumps(payload, ensure_ascii=False, default=str)
        dead: Set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        self._connections -= dead

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
        self._main_engine = engine
        self._main_config = config or {}
        # 安装订单广播钩子
        if _event_loop and not _event_loop.is_closed():
            _install_hook_on_engine(engine)
        logger.info("[API] 主引擎已设置")

    def clear_main(self):
        """断开并清理主引擎"""
        if self._main_engine:
            try:
                self._main_engine.stop()
            except Exception:
                pass
        self._main_engine = None
        self._main_config = {}

    # ── 连接日志 ──────────────────────────────────────────────────────────────
    def add_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._connect_log.append(entry)
        logger.info(f"[ConnLog] {msg}")
        if len(self._connect_log) > 200:
            self._connect_log = self._connect_log[-100:]

    def clear_log(self):
        self._connect_log = []

    def get_log(self) -> List[str]:
        return list(self._connect_log)

    # ── 策略权重 ──────────────────────────────────────────────────────────────
    def set_weights(self, weights: Dict[str, float]):
        for sid, w in weights.items():
            self._weights[sid] = max(0.0, min(1.0, float(w)))

    def get_weight(self, strategy_id: str) -> float:
        if strategy_id in self._weights:
            return self._weights[strategy_id]
        n = max(len(self._entries), 1)
        return round(1.0 / n, 4)

    def all_weights(self) -> Dict[str, float]:
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
        self._equity_curve.append({"ts": ts, "p": round(pnl, 2), "b": round(balance, 2)})
        # 记录当天首次有效余额作为日内基准
        if self._day_open_balance == 0.0 and balance > 0:
            self._day_open_balance = balance

    def get_equity_data(self) -> list:
        return list(self._equity_curve)

    # ── 策略注册 ──────────────────────────────────────────────────────────────
    def register(self, strategy_id, strategy, engine, config=None):
        entry = _StrategyEntry(strategy_id, strategy, engine, config or {})
        self._entries[strategy_id] = entry
        if _event_loop and not _event_loop.is_closed():
            _install_order_hook(entry)
        logger.info(f"[API] 策略已注册: {strategy_id}")

    def unregister(self, strategy_id: str):
        self._entries.pop(strategy_id, None)

    def get(self, strategy_id: str) -> Optional[_StrategyEntry]:
        return self._entries.get(strategy_id)

    def all_entries(self) -> List[_StrategyEntry]:
        return list(self._entries.values())

    def primary_engine(self) -> Optional[TradingEngine]:
        if self._main_engine:
            return self._main_engine
        for entry in self._entries.values():
            return entry.engine
        return None


trading_state = TradingState()

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

    # ── 订单钩子 ────────────────────────────────────────────────────────────
    _orig_order = gw.on_order_callback

    def _order_chained(order):
        trading_state._last_gw_callback_ts = time.monotonic()
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
        if _orig_trade:
            _orig_trade(trade)
        loop = _event_loop
        if loop and not loop.is_closed():
            orders_manager.broadcast_sync(_trade_to_dict(trade), loop)

    gw.on_trade_callback = _trade_chained
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
    import numpy as np      # noqa: PLC0415
    import pandas as pd     # noqa: PLC0415

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

# 策略目录（供前端选择器使用）
_STRATEGY_CATALOG = [
    {
        "name":   "ma_cross",
        "label":  "双均线策略",
        "desc":   "快慢均线金叉/死叉",
        "default_params": {
            "symbol": "IF9999", "fast_period": 10,
            "slow_period": 20,  "position_ratio": 0.8,
        },
    },
    {
        "name":   "rsi",
        "label":  "RSI 均值回归",
        "desc":   "RSI 超买超卖反转",
        "default_params": {
            "symbol": "IF9999", "rsi_period": 14,
            "oversold": 30,     "overbought": 70,
            "position_ratio": 0.8,
        },
    },
    {
        "name":   "breakout",
        "label":  "突破策略",
        "desc":   "N 日高低点突破",
        "default_params": {
            "symbol": "IF9999", "lookback_period": 20,
            "position_ratio": 0.8,
        },
    },
]


def _run_backtest_sync(body: "BacktestRunRequest") -> dict:
    """在线程池中同步运行回测并返回序列化结果（阻塞操作）。"""
    import numpy as np
    import pandas as pd
    from ..backtest import BacktestEngine, BacktestConfig
    from ..data import DataManager
    from ..strategy import create_strategy, Direction
    from ..analysis import Analyzer

    bt_cfg = BacktestConfig(
        start_date      = body.start_date,
        end_date        = body.end_date,
        initial_capital = body.initial_capital,
        commission_rate = body.commission_rate,
        slip_rate       = body.slip_rate,
        margin_rate     = body.margin_rate,
    )

    strategy = create_strategy(body.strategy_name, body.strategy_params or {})

    dm = DataManager()
    symbol = (body.strategy_params or {}).get("symbol", "IF9999")
    # 若数据库无数据则生成模拟数据
    existing = dm.get_bars(symbol, body.start_date, body.end_date)
    if existing is None or existing.empty:
        dm.generate_sample_data(symbol, days=body.sample_days)

    engine = BacktestEngine(bt_cfg)
    engine.set_data_manager(dm)
    engine.set_strategy(strategy)
    engine.run()

    # ── 权益曲线 ─────────────────────────────────────────────────────────────
    equity_list = sorted(engine.equity_curve.values(), key=lambda x: x["date"])
    if not equity_list:
        return {"success": False, "error": "回测无数据，请检查日期范围或合约代码"}

    eq_df = pd.DataFrame(equity_list)
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date")
    peak  = eq_df["capital"].cummax()
    eq_df["dd_pct"] = (eq_df["capital"] - peak) / peak * 100

    equity_curve_out = [
        {
            "date":    str(idx.date()),
            "capital": round(row["capital"], 2),
            "dd_pct":  round(row["dd_pct"], 4),
            "cash":    round(row.get("cash", 0), 2),
        }
        for idx, row in eq_df.iterrows()
    ]

    # ── 日收益率 ─────────────────────────────────────────────────────────────
    daily_ret_pct = (eq_df["capital"].pct_change().dropna() * 100).round(4).tolist()

    # ── 月度热力图 ───────────────────────────────────────────────────────────
    monthly_ret = eq_df["capital"].resample("ME").last().pct_change().dropna()
    years_list  = sorted({str(dt.year) for dt in monthly_ret.index})
    yr_idx_map  = {y: i for i, y in enumerate(years_list)}
    heatmap_data = []
    for dt, ret in monthly_ret.items():
        heatmap_data.append([dt.month - 1, yr_idx_map[str(dt.year)], round(ret * 100, 3)])

    # ── 交易标记（含开/平仓类型推断）──────────────────────────────────────────
    cap_map      = {item["date"]: item["capital"] for item in equity_curve_out}
    pos_tracker: dict = {}
    trade_markers = []

    for t in sorted(engine.result.trades, key=lambda x: x.trade_time):
        sym       = t.symbol
        dirval    = t.direction.value   # "long" | "short"
        cur_pos   = pos_tracker.get(sym)

        if dirval == "long":
            if cur_pos == "short":
                mtype            = "cover_close"
                pos_tracker[sym] = None
            else:
                mtype            = "buy_open"
                pos_tracker[sym] = "long"
        else:
            if cur_pos == "long":
                mtype            = "sell_close"
                pos_tracker[sym] = None
            else:
                mtype            = "short_open"
                pos_tracker[sym] = "short"

        ts       = t.trade_time
        date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        trade_markers.append({
            "date":        date_str,
            "capital":     cap_map.get(date_str),
            "trade_price": round(t.price, 4),
            "type":        mtype,
            "symbol":      sym,
            "volume":      t.volume,
            "pnl":         round(getattr(t, "pnl", 0.0), 2),
            "commission":  round(t.commission, 4),
        })

    # ── 综合指标 ─────────────────────────────────────────────────────────────
    analyzer = Analyzer(bt_cfg.initial_capital)
    analyzer.set_data(list(engine.equity_curve.values()), engine.result.trades)
    raw      = analyzer.analyze()
    r        = raw.get("risk",        {}) if raw else {}
    p        = raw.get("performance", {}) if raw else {}

    metrics = {
        "total_return":           round(p.get("total_return",          0) * 100, 3),
        "annual_return":          round(p.get("annual_return",         0) * 100, 3),
        "win_rate":               round(p.get("win_rate",              0) * 100, 3),
        "profit_loss_ratio":      round(p.get("profit_loss_ratio",     0),       4),
        "total_trades":           p.get("total_trades",                0),
        "winning_trades":         p.get("winning_trades",              0),
        "losing_trades":          p.get("losing_trades",               0),
        "avg_win":                round(p.get("avg_win",               0),       2),
        "avg_loss":               round(p.get("avg_loss",              0),       2),
        "max_consecutive_wins":   p.get("max_consecutive_wins",        0),
        "max_consecutive_losses": p.get("max_consecutive_losses",      0),
        "sharpe_ratio":           round(r.get("sharpe_ratio",          0),       3),
        "sortino_ratio":          round(r.get("sortino_ratio",         0),       3),
        "calmar_ratio":           round(r.get("calmar_ratio",          0),       3),
        "max_drawdown_pct":       round(r.get("max_drawdown_pct",      0) * 100, 3),
        "volatility":             round(r.get("volatility",            0) * 100, 3),
        "var_95":                 round(r.get("var_95",                0) * 100, 3),
        "cvar_95":                round(r.get("cvar_95",               0) * 100, 3),
        "downside_vol":           round(r.get("downside_vol",          0) * 100, 3),
        "skewness":               round(r.get("skewness",              0),       4),
        "kurtosis":               round(r.get("kurtosis",              0),       4),
    }

    return {
        "success":      True,
        "config":       {
            "strategy_name":   strategy.name,
            "start_date":      body.start_date,
            "end_date":        body.end_date,
            "initial_capital": body.initial_capital,
            "commission_rate": body.commission_rate,
            "slip_rate":       body.slip_rate,
            "margin_rate":     body.margin_rate,
        },
        "metrics":      metrics,
        "equity_curve": equity_curve_out,
        "daily_returns": daily_ret_pct,
        "monthly_heatmap": {"years": years_list, "data": heatmap_data},
        "trade_markers": trade_markers,
    }

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
        return await call_next(request)

    # ==================================================================
    # Auth 端点
    # ==================================================================

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
    async def do_login(body: LoginRequest, response: Response):
        """
        接受 CTP 账户配置，连接交易前置和行情前置。
        连接过程最长等待 35 秒。成功后返回会话 token。
        若用户名为 '模拟' 或 'simulate'，则使用模拟网关快速登录。
        """
        from ..trading import create_gateway

        trading_state.clear_log()
        trading_state.add_log(f"开始连接账户: {body.username}")

        # 若已有连接先断开
        if trading_state._main_engine:
            trading_state.add_log("检测到已有连接，正在断开…")
            trading_state.clear_main()

        # 确定网关类型：ctp 作为 vn.py CTP 网关别名保留，便于兼容旧配置。
        requested_gateway = (body.gateway_type or "vnpy").lower()
        if body.username in ("simulate", "模拟", "模拟登录") or requested_gateway == "simulated":
            gateway_type = "simulated"
            trading_state.add_log("使用模拟网关登录")
        else:
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

        trading_state.add_log(f"✔ 登录成功，账户: {account_id}，当前余额: ¥{balance:,.2f}（用作初始资金基准）")

        token = session_store.create()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )

        return LoginResponse(
            success        = True,
            token          = token,
            message        = "登录成功",
            gateway_status = gateway.status.value,
            account_id     = account_id,
            balance        = balance,
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
            cpu_percent      = psutil.cpu_percent(interval=0.1),
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

    @app.delete("/orders/{order_id}", summary="撤销委托单", tags=["订单簿"])
    def cancel_order(order_id: str):
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
            return {"success": bool(success), "order_id": order_id}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"撤单失败: {exc}")

    # ── 手动交易端点 ──────────────────────────────────────────────────────────

    @app.post("/orders", summary="手动下单", tags=["手动交易"])
    def place_manual_order(body: ManualOrderRequest):
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

        # 解析方向
        dir_map = {"long": Direction.LONG, "short": Direction.SHORT}
        direction = dir_map.get(body.direction.lower())
        if direction is None:
            raise HTTPException(status_code=400, detail=f"无效方向: {body.direction}")

        # 解析开平
        offset_map = {
            "open": OffsetFlag.OPEN,
            "close": OffsetFlag.CLOSE,
            "close_today": OffsetFlag.CLOSE_TODAY,
            "close_yesterday": OffsetFlag.CLOSE_YESTERDAY,
        }
        offset = offset_map.get(body.offset.lower(), OffsetFlag.OPEN)

        # 解析订单类型
        ot = OrderType.MARKET if body.order_type.lower() == "market" else OrderType.LIMIT
        price = body.price if ot == OrderType.LIMIT else body.price

        signal = Signal(
            symbol=body.symbol,
            datetime=datetime.now(),
            direction=direction,
            price=price,
            volume=body.volume,
            order_type=ot,
            offset=offset,
            comment=f"manual_{body.offset}",
        )

        try:
            order_id = engine.send_signal(signal)
            if not order_id:
                reason = getattr(engine, "last_reject_reason", "")
                if reason:
                    raise HTTPException(status_code=400, detail=f"风控拒单: {reason}")
                raise HTTPException(status_code=500, detail="下单失败，引擎未返回订单号")
            return {
                "success": True,
                "order_id": order_id,
                "symbol": body.symbol,
                "direction": body.direction,
                "offset": body.offset,
                "price": price,
                "volume": body.volume,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"下单失败: {exc}")

    @app.post("/orders/cancel-all", summary="一键撤销所有活跃委托", tags=["手动交易"])
    def cancel_all_orders():
        """撤销所有活跃状态的委托单。"""
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        cancelled = 0
        failed = 0

        # 主引擎订单
        for oid, order in list(engine.gateway.orders.items()):
            if order.is_active():
                try:
                    if engine.gateway.cancel_order(oid):
                        cancelled += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

        # 各策略引擎订单
        for entry in trading_state.all_entries():
            for oid, order in list(entry.engine.gateway.orders.items()):
                if order.is_active():
                    try:
                        if entry.engine.gateway.cancel_order(oid):
                            cancelled += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

        return {"success": True, "cancelled": cancelled, "failed": failed}

    @app.post("/positions/{symbol}/close", summary="快捷平仓", tags=["手动交易"])
    def close_position(symbol: str, body: ClosePositionRequest = ClosePositionRequest()):
        """
        快捷平仓指定合约。自动推断持仓方向和数量。

        - volume: 0 表示全部平仓
        - price: 0 表示市价
        """
        engine = trading_state.primary_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="交易引擎未连接")

        # 查找持仓
        pos = None
        for key, p in engine.gateway.positions.items():
            if p.symbol == symbol and abs(p.volume) > 0:
                pos = p
                break

        if pos is None:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的持仓")

        close_volume = body.volume if body.volume > 0 else abs(pos.volume)
        if close_volume > abs(pos.volume):
            close_volume = abs(pos.volume)

        # 平仓方向：持多则卖出，持空则买入
        if pos.direction == Direction.LONG or pos.volume > 0:
            close_direction = Direction.SHORT
        else:
            close_direction = Direction.LONG

        ot = OrderType.MARKET if body.price == 0 else OrderType.LIMIT

        signal = Signal(
            symbol=symbol,
            datetime=datetime.now(),
            direction=close_direction,
            price=body.price,
            volume=close_volume,
            order_type=ot,
            offset=OffsetFlag.CLOSE,
            comment="manual_close_position",
        )

        try:
            order_id = engine.send_signal(signal)
            if not order_id:
                reason = getattr(engine, "last_reject_reason", "")
                if reason:
                    raise HTTPException(status_code=400, detail=f"风控拒单: {reason}")
                raise HTTPException(status_code=500, detail="平仓下单失败")
            return {
                "success": True,
                "order_id": order_id,
                "symbol": symbol,
                "direction": close_direction.value,
                "volume": close_volume,
                "price": body.price,
            }
        except HTTPException:
            raise
        except Exception as exc:
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

    # ==================================================================
    # WebSocket 端点
    # ==================================================================

    @app.websocket("/ws/system")
    async def ws_system(ws: WebSocket):
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
        return JSONResponse({"strategies": _STRATEGY_CATALOG})

    @app.post("/backtest/run")
    async def bt_run(body: BacktestRunRequest, request: Request):
        """运行回测，返回完整结果（资金曲线、交易标记、指标、热力图数据）。"""
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_backtest_sync, body),
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
        import random
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not sym_list:
            return JSONResponse({"code": 1, "msg": "请提供 symbols 参数"}, status_code=400)

        ticks_result = {}
        rng = random.Random()

        for sym in sym_list:
            ref_price = 3000.0
            try:
                from ..watch.kline import get_kline
                result = get_kline(symbol=sym, interval="1d", limit=2, indicators="")
                data = result.get("data", [])
                if isinstance(data, list) and data:
                    ref_price = float(data[-1].get("close", 3000.0))
                elif isinstance(data, dict):
                    bars = data.get("bars", [])
                    if bars:
                        ref_price = float(bars[-1].get("close", 3000.0))
            except Exception:
                pass

            ref_price = round(ref_price * (1 + rng.gauss(0, 0.0001)), 4)
            tick_size = max(round(ref_price * 0.0002, 2), 0.01)
            drift = rng.gauss(0, tick_size)
            last = round(ref_price + drift, 2)
            spread = tick_size * rng.uniform(1, 3)
            bid1 = round(last - spread * 0.5, 2)
            ask1 = round(last + spread * 0.5, 2)
            bids, asks = [], []
            b, a = bid1, ask1
            for i in range(5):
                bids.append(round(b - tick_size * i * rng.uniform(0.5, 1.5), 2))
                asks.append(round(a + tick_size * i * rng.uniform(0.5, 1.5), 2))
            bid_vols = [rng.randint(1, 200) for _ in range(5)]
            ask_vols = [rng.randint(1, 200) for _ in range(5)]
            pre_close = round(last * rng.uniform(0.99, 1.01), 2)
            open_p = round(pre_close * rng.uniform(0.998, 1.002), 2)
            high_p = round(max(open_p, last) * rng.uniform(1.0, 1.005), 2)
            low_p = round(min(open_p, last) * rng.uniform(0.995, 1.0), 2)
            volume = rng.randint(100, 5000)
            turnover = round(last * volume * rng.uniform(0.9, 1.1), 0)
            oi = rng.randint(10000, 500000)
            change = round(last - pre_close, 2)
            chg_rate = round(change / pre_close * 100, 3) if pre_close else 0.0

            ticks_result[sym] = {
                "symbol": sym,
                "last": last,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "pre_close": pre_close,
                "volume": volume,
                "turnover": turnover,
                "open_interest": oi,
                "bid1": bid1, "bid2": bids[1], "bid3": bids[2], "bid4": bids[3], "bid5": bids[4],
                "ask1": ask1, "ask2": asks[1], "ask3": asks[2], "ask4": asks[3], "ask5": asks[4],
                "bid1_vol": bid_vols[0], "bid2_vol": bid_vols[1], "bid3_vol": bid_vols[2], "bid4_vol": bid_vols[3], "bid5_vol": bid_vols[4],
                "ask1_vol": ask_vols[0], "ask2_vol": ask_vols[1], "ask3_vol": ask_vols[2], "ask4_vol": ask_vols[3], "ask5_vol": ask_vols[4],
                "change": change,
                "change_rate": chg_rate,
            }

        return JSONResponse({"code": 0, "ticks": ticks_result})

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
        await ws.accept()
        logger.info("[WS:watch] 新连接")

        import random
        import math

        # 每个连接的订阅状态：symbol → set(channels)
        subscriptions: Dict[str, set] = {}

        # 品种基础价格缓存（用于模拟价格漂移）
        base_prices: Dict[str, float] = {}

        def _get_base_price(symbol: str) -> float:
            """获取或初始化品种基础价格（取最近日线收盘价）。"""
            if symbol in base_prices:
                return base_prices[symbol]
            try:
                from ..watch.kline import get_kline
                result = get_kline(symbol=symbol, interval="1d", limit=2, indicators="")
                data = result.get("data", [])
                # 新格式：data 是扁平记录数组
                if isinstance(data, list) and data:
                    price = float(data[-1].get("close", 3000.0))
                # 旧格式兼容：data 是含 bars 字段的字典
                elif isinstance(data, dict):
                    raw_bars = data.get("bars", [])
                    price = float(raw_bars[-1]["close"]) if raw_bars else 3000.0
                else:
                    price = 3000.0
            except Exception as e:
                logger.debug(f"[WS:watch] _get_base_price({symbol}) 失败: {e}")
                price = 3000.0
            base_prices[symbol] = price
            return price

        def _build_tick(symbol: str, ref_price: float, rng: random.Random) -> dict:
            """
            基于参考价格模拟一个真实感的 tick 快照。
            振幅：±0.3%（期货盘中正常波动）。
            """
            tick_size  = max(round(ref_price * 0.0002, 2), 0.01)
            drift      = rng.gauss(0, tick_size)
            last       = round(ref_price + drift, 2)

            spread     = tick_size * rng.uniform(1, 3)
            bid1       = round(last - spread * 0.5, 2)
            ask1       = round(last + spread * 0.5, 2)

            # 5 档深度（价差递增）
            bids, asks = [], []
            b, a = bid1, ask1
            for i in range(5):
                bids.append(round(b - tick_size * i * rng.uniform(0.5, 1.5), 2))
                asks.append(round(a + tick_size * i * rng.uniform(0.5, 1.5), 2))
            bid_vols   = [rng.randint(1, 200) for _ in range(5)]
            ask_vols   = [rng.randint(1, 200) for _ in range(5)]

            pre_close  = round(last * rng.uniform(0.99, 1.01), 2)
            open_p     = round(pre_close * rng.uniform(0.998, 1.002), 2)
            high_p     = round(max(open_p, last) * rng.uniform(1.0, 1.005), 2)
            low_p      = round(min(open_p, last) * rng.uniform(0.995, 1.0), 2)
            volume     = rng.randint(100, 5000)
            turnover   = round(last * volume * rng.uniform(0.9, 1.1), 0)
            oi         = rng.randint(10000, 500000)
            change     = round(last - pre_close, 2)
            chg_rate   = round(change / pre_close * 100, 3) if pre_close else 0.0

            return {
                "type":         "tick",
                "symbol":       symbol,
                "last":         last,
                "open":         open_p,
                "high":         high_p,
                "low":          low_p,
                "pre_close":    pre_close,
                "volume":       volume,
                "turnover":     turnover,
                "open_interest": oi,
                "bid1": bids[0], "bid2": bids[1], "bid3": bids[2], "bid4": bids[3], "bid5": bids[4],
                "ask1": asks[0], "ask2": asks[1], "ask3": asks[2], "ask4": asks[3], "ask5": asks[4],
                "bid1_vol": bid_vols[0], "bid2_vol": bid_vols[1], "bid3_vol": bid_vols[2],
                "bid4_vol": bid_vols[3], "bid5_vol": bid_vols[4],
                "ask1_vol": ask_vols[0], "ask2_vol": ask_vols[1], "ask3_vol": ask_vols[2],
                "ask4_vol": ask_vols[3], "ask5_vol": ask_vols[4],
                "change":      change,
                "change_rate": chg_rate,
                "time":        datetime.now().strftime("%H:%M:%S"),
            }

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
                        for s in syms:
                            if s not in subscriptions:
                                subscriptions[s] = set()
                            subscriptions[s].update(chs)
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
            rng = random.Random()
            try:
                while True:
                    await asyncio.sleep(0.5)
                    if not subscriptions:
                        continue
                    for symbol, channels in list(subscriptions.items()):
                        if "tick" not in channels and not any(
                            ch.startswith("kline_") for ch in channels
                        ):
                            continue
                        # 初始化基础价
                        ref = base_prices.get(symbol)
                        if ref is None:
                            ref = await asyncio.get_event_loop().run_in_executor(
                                None, _get_base_price, symbol
                            )
                        # 价格随机游走（模拟真实波动）
                        ref = round(ref * (1 + rng.gauss(0, 0.0001)), 4)
                        base_prices[symbol] = ref

                        if "tick" in channels:
                            tick = _build_tick(symbol, ref, rng)
                            await ws.send_text(_json.dumps(tick, ensure_ascii=False, default=str))

                        # K 线更新（仅推送当前分钟 bar）
                        for ch in channels:
                            if not ch.startswith("kline_"):
                                continue
                            iv = ch[6:]  # e.g. "1m"
                            bar_update = {
                                "type":     "kline_update",
                                "symbol":   symbol,
                                "interval": iv,
                                "bar": {
                                    "time":   datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "open":   round(ref * rng.uniform(0.9995, 1.0005), 2),
                                    "high":   round(ref * rng.uniform(1.0, 1.001), 2),
                                    "low":    round(ref * rng.uniform(0.999, 1.0), 2),
                                    "close":  ref,
                                    "volume": rng.randint(50, 500),
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
