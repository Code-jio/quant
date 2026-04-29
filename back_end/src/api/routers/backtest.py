"""回测路由：strategies / run。"""

from __future__ import annotations

import asyncio
import logging
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..backtest_service import STRATEGY_CATALOG, run_backtest_sync
from ..schemas import BacktestRunRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["回测"])


@router.get("/backtest/strategies")
async def bt_list_strategies(request: Request):
    return JSONResponse({"strategies": STRATEGY_CATALOG})


@router.post("/backtest/run")
async def bt_run(body: BacktestRunRequest, request: Request):
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
