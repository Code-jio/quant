"""策略管理路由：CRUD / weights / action。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ..deps import _calc_strategy_pnl, _position_to_schema, _signal_to_schema
from ..schemas import (
    ActionRequest,
    ActionResponse,
    ParamsUpdateRequest,
    StrategyDetailResponse,
    StrategyInfo,
    WeightRequest,
)
from ..state import trading_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["策略"])


@router.get("/strategies", response_model=List[StrategyInfo], summary="策略列表")
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


@router.get(
    "/strategies/{strategy_id}",
    response_model=StrategyDetailResponse,
    summary="策略详情（含信号、参数、权重）",
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


@router.put("/strategies/{strategy_id}/params", summary="更新策略参数")
def update_strategy_params(strategy_id: str, body: ParamsUpdateRequest):
    entry = trading_state.get(strategy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")

    s = entry.strategy
    s.params.update(body.params)
    entry.config.update(body.params)

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


@router.put("/strategies/weights", summary="批量更新策略权重")
def update_weights(body: WeightRequest):
    unknown = [sid for sid in body.weights if trading_state.get(sid) is None]
    if unknown:
        raise HTTPException(status_code=404, detail=f"未知策略: {unknown}")
    trading_state.set_weights(body.weights)
    return {
        "success": True,
        "weights": trading_state.all_weights(),
    }


@router.post(
    "/strategy/{strategy_id}/action",
    response_model=ActionResponse,
    summary="控制策略启停",
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
