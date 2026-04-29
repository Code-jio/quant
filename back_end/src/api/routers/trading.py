"""交易路由：orders / positions / cancel / manual trading。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from ...strategy import Direction, OffsetFlag, OrderType, Signal
from ..deps import (
    _collect_all_orders,
    _collect_all_trades,
    _position_to_schema,
    _record_audit,
)
from ..schemas import ClosePositionRequest, ManualOrderRequest, PositionInfo
from ..state import trading_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["订单簿"])

# ── 手动下单辅助 ──────────────────────────────────────────────────────────

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


# ── 路由 ──────────────────────────────────────────────────────────────────

@router.get("/positions", response_model=List[PositionInfo], summary="当前持仓", tags=["持仓"])
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


@router.get("/orders", summary="全部委托单列表")
def list_orders():
    return _collect_all_orders()[:500]


@router.get("/trades", summary="全部成交记录")
def list_trades():
    return _collect_all_trades()[:500]


@router.delete("/orders/{order_id}", summary="撤销委托单")
def cancel_order(order_id: str, request: Request):
    engine = trading_state.primary_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="交易引擎未连接")
    gw = engine.gateway
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
            "order", "cancel", "success" if success else "rejected",
            resource=order_id, request=request,
        )
        return {"success": bool(success), "order_id": order_id}
    except Exception as exc:
        _record_audit("order", "cancel", "error", resource=order_id, request=request, detail={"error": str(exc)})
        raise HTTPException(status_code=500, detail=f"撤单失败: {exc}")


# ── 手动交易 ──────────────────────────────────────────────────────────────

@router.post("/orders", summary="手动下单", tags=["手动交易"])
def place_manual_order(body: ManualOrderRequest, request: Request):
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
            "order", "manual_order", "rejected",
            resource=str(body.symbol or ""), request=request,
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
                    "order", "manual_order", "rejected",
                    resource=body.symbol, request=request,
                    detail={"reason": reason, "volume": volume, "direction": direction_name},
                )
                raise HTTPException(status_code=400, detail=f"风控拒单: {reason}")
            raise HTTPException(status_code=500, detail="下单失败，引擎未返回订单号")
        _record_audit(
            "order", "manual_order", "success",
            resource=order_id, request=request,
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


@router.post("/orders/cancel-all", summary="一键撤销所有活跃委托", tags=["手动交易"])
def cancel_all_orders():
    engine = trading_state.primary_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="交易引擎未连接")

    cancelled = 0
    failed = 0

    for oid, order in list(engine.gateway.orders.items()):
        if order.is_active():
            try:
                if engine.gateway.cancel_order(oid):
                    cancelled += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

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


@router.post("/positions/{symbol}/close", summary="快捷平仓", tags=["手动交易"])
def close_position(symbol: str, request: Request, body: ClosePositionRequest = ClosePositionRequest()):
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
            "order", "manual_close_position", "rejected",
            resource=symbol, request=request,
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
            "order", "manual_close_position", "rejected",
            resource=clean_symbol, request=request,
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
            "order", "manual_close_position", "success",
            resource=order_id, request=request,
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
