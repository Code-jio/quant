"""Compatibility helper for isolated trial-run simulation fills."""

from __future__ import annotations

import math
from typing import Any

from .execution_adapter import SimulationFillResult, SimulationLedgerError

__all__ = ["SimulationFillResult", "apply_simulated_fill"]


def apply_simulated_fill(
    engine: Any,
    order_id: str,
    *,
    price: float | None = None,
    volume: int | None = None,
) -> SimulationFillResult:
    """Fill the current synthetic order without touching the gateway.

    ``price`` and ``volume`` remain in the compatibility signature so older
    callers fail explicitly rather than silently changing the ledger contract.
    """
    if engine is None:
        raise ValueError("engine is required")
    if not order_id:
        raise ValueError("order_id is required")
    adapter = getattr(engine, "simulation_adapter", None)
    if not getattr(adapter, "is_simulation", False):
        raise SimulationLedgerError("simulation_not_ready")
    order = adapter.get_order(order_id)
    if order is None:
        raise SimulationLedgerError("order_not_owned_by_trial_run")
    if price is not None:
        _validate_price(price)
        raise ValueError("simulation fill price override is not supported")
    if volume is not None:
        if int(volume) <= 0:
            raise ValueError("fill volume must be positive")
        raise ValueError("simulation fill volume override is not supported")
    return adapter.fill(order_id)


def _validate_price(price: Any) -> float:
    try:
        value = float(price)
    except (TypeError, ValueError):
        value = 0.0
    if not (math.isfinite(value) and value > 0):
        raise ValueError("fill price must be finite and positive")
    return value
