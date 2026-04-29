"""共享辅助函数：审计、序列化、快照构建、钩子安装、日志缓冲。"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import psutil
from fastapi import Request, WebSocket

from ..observability import audit_log, metrics
from ..trading import AccountInfo, TradingEngine, TradingStatus
from ..strategy import Direction
from .schemas import PositionInfo, SignalSchema
from .state import _StrategyEntry, _event_loop, trading_state

logger = logging.getLogger(__name__)


# ── 审计辅助 ──────────────────────────────────────────────────────────────

def _request_id(request: Optional[Request]) -> str:
    return getattr(getattr(request, "state", None), "request_id", "") if request else ""


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


# ── 网络速率差分计算 ──────────────────────────────────────────────────────

_last_net_io: Optional[tuple] = None


def _get_network_speed() -> tuple:
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


# ── 订单广播钩子 ──────────────────────────────────────────────────────────

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


def _install_order_hook(entry: _StrategyEntry):
    _install_hook_on_engine(entry.engine)


def _install_hook_on_engine(engine: TradingEngine):
    from .ws import orders_manager  # noqa: PLC0415 — 避免循环导入

    gw = engine.gateway

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
    logger.info(f"[API] 订单/成交广播钩子已安装: 网关={gw.name}")


# ── 数据转换辅助 ──────────────────────────────────────────────────────────

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


# ── 行情辅助 ──────────────────────────────────────────────────────────────

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


# ── 快照构建 ──────────────────────────────────────────────────────────────

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
        market_connected = td_connected
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

    if trading_state._last_gw_callback_ts > 0:
        gateway_latency_ms = round((time.monotonic() - trading_state._last_gw_callback_ts) * 1000)
    else:
        gateway_latency_ms = -1

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


def _build_dashboard_metrics() -> dict:
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

    if total_pnl == 0.0:
        total_pnl = sum(_calc_strategy_pnl(e) for e in trading_state.all_entries())

    return_rate  = total_pnl / initial_capital * 100 if initial_capital else 0.0
    day_open     = trading_state._day_open_balance or initial_capital
    today_return = (balance - day_open) / day_open * 100 if day_open > 0 else return_rate

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

    sharpe_ratio     = 0.0
    max_drawdown_pct = 0.0
    equity_curve_data: list = []

    curve = trading_state.get_equity_data()
    n     = len(curve)
    if n >= 10:
        bal_arr = pd.Series([c["b"] for c in curve], dtype=float)
        bal_returns = bal_arr.pct_change().dropna()
        if len(bal_returns) > 1 and bal_returns.std() > 1e-12:
            ann_factor   = np.sqrt(252 * 14400)
            sharpe_ratio = float(
                np.clip(bal_returns.mean() / bal_returns.std() * ann_factor, -20.0, 20.0)
            )
        peak             = bal_arr.cummax()
        dd               = (bal_arr - peak) / peak.replace(0, np.nan) * 100
        max_drawdown_pct = float(abs(dd.min())) if not dd.empty else 0.0
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


def _collect_all_orders() -> list:
    seen:   set  = set()
    result: list = []

    def _add(order):
        oid = getattr(order, "order_id", None)
        if oid and oid not in seen:
            seen.add(oid)
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


# ── 日志缓冲区 ───────────────────────────────────────────────────────────

class _LogBuffer(logging.Handler):
    MAX_ENTRIES = 500

    def __init__(self):
        super().__init__()
        self._buf: deque                       = deque(maxlen=self.MAX_ENTRIES)
        self._queue: Optional[asyncio.Queue]   = None

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
            pass

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
