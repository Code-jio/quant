"""行情路由：kline / tick / search / cache。"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...trading import TradingStatus
from ..deps import _wait_for_gateway_ticks
from ..state import trading_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["行情"])


@router.get("/watch/kline", summary="K线数据 + 技术指标")
async def watch_kline(
    request:    Request,
    symbol:     str           = "rb2501",
    interval:   str           = "1d",
    limit:      int           = 100,
    indicators: str           = "",
    since:      Optional[str] = None,
):
    from ...watch.kline import get_kline  # noqa: PLC0415
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


@router.delete("/watch/kline/cache", summary="清除K线缓存")
async def clear_kline_cache(request: Request, symbol: str = ""):
    from ...watch.kline import kline_cache  # noqa: PLC0415
    prefix = f"kline:{symbol}" if symbol else "kline:"
    n = kline_cache.invalidate(prefix)
    return JSONResponse({"code": 0, "cleared": n})


@router.get("/watch/tick", summary="批量查询实时行情")
async def get_ticks(symbols: str = ""):
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


@router.get("/watch/search", summary="期货品种/合约搜索")
async def watch_search(
    query:    str           = "",
    exchange: Optional[str] = None,
    limit:    int           = 50,
):
    from ...watch import search_contracts  # noqa: PLC0415
    try:
        data = search_contracts(query=query, exchange=exchange, limit=min(limit, 200))
        return JSONResponse({"code": 0, "data": data, "total": len(data)})
    except Exception as exc:
        logger.error(f"[watch/search] 搜索失败: {exc}")
        return JSONResponse({"code": 1, "data": [], "total": 0, "msg": str(exc)}, status_code=500)
