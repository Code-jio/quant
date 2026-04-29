"""WebSocket 连接管理器、广播循环和端点。"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import datetime
from typing import Dict, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..observability import metrics
from ..trading import TradingStatus
from .deps import (
    _build_dashboard_metrics,
    _build_positions_snapshot,
    _build_system_snapshot,
    _gateway_tick_snapshot,
    _subscribe_market_ticks,
    log_buffer,
)
from .state import _event_loop, trading_state

logger = logging.getLogger(__name__)


# ── ConnectionManager ─────────────────────────────────────────────────────

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


# ── 全局 manager 实例 ─────────────────────────────────────────────────────

system_manager    = ConnectionManager("system")
orders_manager    = ConnectionManager("orders")
dashboard_manager = ConnectionManager("dashboard")
positions_manager = ConnectionManager("positions")
log_manager       = ConnectionManager("logs")


# ── 后台广播任务 ──────────────────────────────────────────────────────────

async def _system_broadcast_loop():
    while True:
        try:
            snapshot = _build_system_snapshot()
            trading_state.push_equity(snapshot["total_pnl"], snapshot["balance"])
            if system_manager.count > 0:
                await system_manager.broadcast(snapshot)
        except Exception as exc:
            logger.warning(f"[WS:system] 广播异常: {exc}")
        await asyncio.sleep(1)


async def _dashboard_broadcast_loop():
    while True:
        try:
            if dashboard_manager.count > 0:
                await dashboard_manager.broadcast(_build_dashboard_metrics())
        except Exception as exc:
            logger.warning(f"[WS:dashboard] 广播异常: {exc}")
        await asyncio.sleep(2)


async def _positions_broadcast_loop():
    while True:
        try:
            if positions_manager.count > 0:
                await positions_manager.broadcast(_build_positions_snapshot())
        except Exception as exc:
            logger.warning(f"[WS:positions] 广播异常: {exc}")
        await asyncio.sleep(2)


async def _logs_broadcast_loop():
    while log_buffer._queue is None:
        await asyncio.sleep(0.1)
    while True:
        try:
            entry = await asyncio.wait_for(log_buffer._queue.get(), timeout=5.0)
            if log_manager.count > 0:
                await log_manager.broadcast({"type": "log_entry", **entry})
        except asyncio.TimeoutError:
            pass
        except Exception:
            await asyncio.sleep(0.5)


# ── 注册 WebSocket 端点到 FastAPI app ─────────────────────────────────────

def register_ws_endpoints(app: FastAPI) -> None:
    """将所有 WebSocket 端点注册到 app 上。在 app.py 的 create_app() 中调用。"""

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

    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket):
        await log_manager.connect(ws)
        try:
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

    @app.websocket("/ws/watch")
    async def ws_watch(ws: WebSocket):
        await ws.accept()
        logger.info("[WS:watch] 新连接")

        subscriptions: Dict[str, set] = {}
        last_sent_ts: Dict[str, str] = {}

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

                        for ch in channels:
                            if not ch.startswith("kline_"):
                                continue
                            iv = ch[6:]
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
