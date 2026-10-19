"""Protected operator-only reconciliation; registered by the canonical app factory."""
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..trading.recovery import checked_snapshot, import_snapshot, resolve_intent


class RecoveryRequest(BaseModel):
    generation: int
    snapshot_id: str = Field(min_length=1, max_length=64)
    intent_id: str = Field(default="", max_length=64)
    order_id: str = Field(default="", max_length=128)
    note: str = Field(min_length=12, max_length=1000)


class ConditionalReview(BaseModel):
    generation: int
    action: str
    note: str = Field(min_length=12, max_length=1000)


def register_recovery_routes(app, state, audit):
    def engine_for(generation=None):
        engine = state.primary_engine()
        if engine is None:
            raise HTTPException(503, "交易引擎未连接")
        if generation is not None and generation != state.generation:
            raise HTTPException(409, "账户会话已切换，请重新查询")
        return engine

    @app.post("/trading/reconcile/query", tags=["风控"])
    def query(request: Request):
        engine = engine_for()
        book = getattr(engine.gateway, "reconciliation", None)
        if not book or not engine.gateway.ready:
            raise HTTPException(409, "网关尚未就绪或不支持完整查询")
        with engine.order_manager.lock:
            result = book.start(engine.gateway.trading_day)
        audit("reconciliation", "query", "success", request=request)
        return {"generation": state.generation, "broker_snapshot": result}

    @app.post("/trading/reconcile/import", tags=["风控"])
    def synchronize(body: RecoveryRequest, request: Request):
        engine = engine_for(body.generation)
        try:
            book = getattr(engine.gateway, "reconciliation", None)
            if book is None:
                raise ValueError("网关不支持对账")
            with engine.order_manager.lock, book.lock:
                result = import_snapshot(engine, checked_snapshot(engine, body.snapshot_id))
                if engine.ledger:
                    engine.ledger.save_setting("last_reconciliation", {**body.model_dump(), **result})
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from None
        audit("reconciliation", "import", "success", request=request, detail=body.model_dump())
        return {"success": True, **result}

    @app.post("/trading/reconcile/resolve", tags=["风控"])
    def resolve(body: RecoveryRequest, request: Request):
        engine = engine_for(body.generation)
        try:
            book = getattr(engine.gateway, "reconciliation", None)
            if book is None:
                raise ValueError("网关不支持对账")
            with engine.order_manager.lock, book.lock:
                resolve_intent(engine, body.intent_id, body.snapshot_id, body.order_id, body.note)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from None
        audit("reconciliation", "resolve", "success", request=request, detail=body.model_dump())
        return {"success": True, "emergency_stop": engine.risk_manager.emergency_stop}

    @app.post("/trading/conditional/{identity}/review", tags=["风控"])
    def review_condition(identity: str, body: ConditionalReview, request: Request):
        engine = engine_for(body.generation)
        try:
            with engine.order_manager.lock:
                if body.action == "resume":
                    if not getattr(engine.gateway, "ready", False) or engine.risk_manager.emergency_stop:
                        raise ValueError("请先完成柜台核对并解除急停，再启用条件单")
                    condition = engine.order_manager.pre_orders.get(identity)
                    if condition is None:
                        raise ValueError("条件单不存在")
                    engine.gateway.subscribe_market_data([condition.symbol])
                    engine.order_manager.resume_pre_order(identity)
                elif body.action == "cancel":
                    if not engine.order_manager.cancel_pre_order(identity):
                        raise ValueError("条件单不能取消；发送结果未知时请先核对关联执行意图")
                else:
                    raise ValueError("不支持的条件单操作")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        audit("conditional", body.action, "success", request=request, detail={"id": identity, "note": body.note})
        return {"success": True}
