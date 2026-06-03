"""Test-environment helpers for simulating broker fills."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..strategy import Direction, OffsetFlag, Order, OrderStatus, Position, Trade


@dataclass(frozen=True)
class SimulatedFillResult:
    order: Order
    trade: Trade
    position: Position


def apply_simulated_fill(
    engine: Any,
    order_id: str,
    *,
    price: float | None = None,
    volume: int | None = None,
) -> SimulatedFillResult:
    """Mark an active order as filled and emit the normal gateway callbacks.

    This is intended for test/simulation environments where the broker accepts
    the order but does not produce an execution report.
    """
    if engine is None:
        raise ValueError("engine is required")
    if not order_id:
        raise ValueError("order_id is required")

    gateway = getattr(engine, "gateway", None)
    order_manager = getattr(engine, "order_manager", None)
    if gateway is None or order_manager is None:
        raise ValueError("engine must expose gateway and order_manager")

    order = _find_order(engine, order_id)
    if order is None:
        raise ValueError(f"order not found: {order_id}")
    if not order.is_active():
        raise ValueError(f"order is not active: {order_id}")

    remaining = max(0, int(order.volume or 0) - int(order.traded_volume or 0))
    fill_volume = remaining if volume is None else int(volume)
    if fill_volume <= 0:
        raise ValueError("fill volume must be positive")
    if fill_volume > remaining:
        raise ValueError("fill volume cannot exceed remaining order volume")

    fill_price = _fill_price(engine, order, price)
    now = datetime.now()
    order.traded_volume = int(order.traded_volume or 0) + fill_volume
    order.status = OrderStatus.FILLED if order.traded_volume >= order.volume else OrderStatus.PARTFILLED
    order.update_time = now

    trade = Trade(
        trade_id=f"SIM_{uuid.uuid4().hex[:12].upper()}",
        order_id=order.order_id,
        symbol=order.symbol,
        direction=order.direction,
        price=fill_price,
        volume=fill_volume,
        trade_time=now,
    )
    position = _apply_gateway_position(gateway, order, fill_price, fill_volume)

    gateway.on_order(order)
    gateway.on_trade(trade)
    gateway.on_position(position)
    return SimulatedFillResult(order=order, trade=trade, position=position)


def _find_order(engine: Any, order_id: str) -> Order | None:
    order_manager = getattr(engine, "order_manager", None)
    get_order = getattr(order_manager, "get_order", None)
    if callable(get_order):
        order = get_order(order_id)
        if order is not None:
            return order

    gateway = getattr(engine, "gateway", None)
    orders = getattr(gateway, "orders", {}) if gateway is not None else {}
    if isinstance(orders, dict):
        return orders.get(order_id)
    return None


def _fill_price(engine: Any, order: Order, price: float | None) -> float:
    if price is not None:
        fill_price = float(price)
    else:
        fill_price = float(order.price or 0.0)
        if fill_price <= 0:
            market_data_for_symbol = getattr(engine, "_market_data_for_symbol", None)
            market_data = market_data_for_symbol(order.symbol) if callable(market_data_for_symbol) else {}
            fill_price = float(
                market_data.get("last_price")
                or market_data.get("last")
                or market_data.get("ask_price_1")
                or market_data.get("bid_price_1")
                or 0.0
            )
    if fill_price <= 0:
        raise ValueError("fill price must be positive")
    return fill_price


def _apply_gateway_position(gateway: Any, order: Order, price: float, volume: int) -> Position:
    position_direction = _position_direction(order)
    key = f"{order.symbol}_{position_direction.value}"
    positions = getattr(gateway, "positions", {})
    current = positions.get(key) if isinstance(positions, dict) else None
    current_volume = int(getattr(current, "volume", 0) or 0)

    if order.offset == OffsetFlag.OPEN:
        next_volume = current_volume + volume
    else:
        next_volume = max(0, current_volume - volume)

    position = Position(
        symbol=order.symbol,
        direction=position_direction,
        volume=next_volume,
        frozen=0,
        price=price,
        cost=price,
        pnl=float(getattr(current, "pnl", 0.0) or 0.0),
    )
    if isinstance(positions, dict):
        positions[key] = position
    return position


def _position_direction(order: Order) -> Direction:
    if order.offset == OffsetFlag.OPEN:
        return order.direction
    if order.direction == Direction.LONG:
        return Direction.SHORT
    if order.direction == Direction.SHORT:
        return Direction.LONG
    return order.direction
