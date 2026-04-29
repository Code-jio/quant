"""系统/运维路由：health / metrics / audit / system-status / logs。"""

from __future__ import annotations

import logging
from datetime import datetime

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ...observability import audit_log, metrics
from ...trading import TradingStatus
from ..deps import _account_to_dict, log_buffer
from ..schemas import SystemStatusResponse
from ..security import session_store
from ..state import trading_state
from ..ws import dashboard_manager, log_manager, orders_manager, positions_manager, system_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["运维"])


@router.get("/health", summary="健康检查")
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


@router.get("/metrics", summary="Prometheus 指标")
def prometheus_metrics():
    return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")


@router.get("/audit/events", summary="交易事件审计")
def audit_events(event_type: str = "", limit: int = 200):
    return {"events": audit_log.query(event_type=event_type, limit=limit)}


@router.get(
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


@router.get("/system/logs", tags=["系统"])
async def get_system_logs(
    request: Request,
    level: str = "",
    q:     str = "",
    limit: int = 200,
):
    entries = log_buffer.query(level=level, q=q, limit=min(limit, 500))
    return JSONResponse({"logs": entries, "total": len(entries)})
