"""FastAPI 应用工厂：lifespan、中间件、路由注册。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..observability import new_request_id, structured_json, metrics
from .security import is_open_path, session_store, SESSION_COOKIE_NAME
from .state import _event_loop, trading_state
from .deps import _install_hook_on_engine, _install_order_hook, log_buffer
from .routers import auth, backtest, dashboard, strategy, system, trading, watch
from . import ws

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


def create_app(title: str = "量化交易系统 API", version: str = "1.0.0") -> FastAPI:

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from . import state as state_mod  # noqa: PLC0415
        state_mod._event_loop = asyncio.get_running_loop()

        for entry in trading_state.all_entries():
            _install_order_hook(entry)
        if trading_state._main_engine:
            _install_hook_on_engine(trading_state._main_engine)

        log_buffer._queue = asyncio.Queue(maxsize=1000)
        logging.getLogger().addHandler(log_buffer)

        broadcast_task   = asyncio.create_task(ws._system_broadcast_loop())
        dashboard_task   = asyncio.create_task(ws._dashboard_broadcast_loop())
        positions_task   = asyncio.create_task(ws._positions_broadcast_loop())
        logs_task        = asyncio.create_task(ws._logs_broadcast_loop())
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

    # ── 可观测性中间件 ────────────────────────────────────────────────────────
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

    # ── 注册路由 ──────────────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(system.router)
    app.include_router(strategy.router)
    app.include_router(trading.router)
    app.include_router(dashboard.router)
    app.include_router(backtest.router)
    app.include_router(watch.router)

    # ── WebSocket 端点 ────────────────────────────────────────────────────────
    ws.register_ws_endpoints(app)

    # ── 其他 ──────────────────────────────────────────────────────────────────
    @app.get("/data/quality", summary="历史行情数据质量报告", tags=["数据治理"])
    def data_quality(symbol: str, start_date: str = "", end_date: str = "", timeframe: str = "1d"):
        from ..data import DataManager  # noqa: PLC0415
        if not symbol:
            from fastapi import HTTPException  # noqa: PLC0415
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

    return app
