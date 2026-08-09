"""Execution adapters used by the real and isolated trial-run tracks."""

from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol
from uuid import uuid4

from ..strategy import Direction, OffsetFlag, Order, OrderStatus, Position, Signal, Trade


class SimulationLedgerError(ValueError):
    """Stable failure raised by an isolated simulation ledger."""

    def __init__(self, failure_code: str, message: str = "") -> None:
        self.failure_code = failure_code
        super().__init__(message or failure_code)


class SignalExecutionAdapter(Protocol):
    def submit(self, signal: Signal) -> str:
        ...

    def cancel(self, order_id: str) -> bool:
        ...

    def get_order(self, order_id: str) -> Optional[Order]:
        ...


@dataclass(frozen=True)
class SimulationFillResult:
    order: Order
    trade: Trade
    position: Position


def _position_direction(order: Order) -> Direction:
    if order.offset == OffsetFlag.OPEN:
        return order.direction
    if order.direction == Direction.LONG:
        return Direction.SHORT
    if order.direction == Direction.SHORT:
        return Direction.LONG
    return order.direction


class TrialRunSimulationLedger:
    """In-memory synthetic broker state, deliberately detached from a gateway."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.orders: Dict[str, Order] = {}
        self.trades: Dict[str, Trade] = {}
        self.positions: Dict[str, Position] = {}
        self.current_order_id = ""
        self._order_sequence = 0

    @staticmethod
    def _validate_price(price: Any) -> float:
        try:
            value = float(price)
        except (TypeError, ValueError):
            value = 0.0
        if not (math.isfinite(value) and value > 0):
            raise ValueError("fill price must be finite and positive")
        return value

    def _next_order_id(self, prefix: str) -> str:
        self._order_sequence += 1
        return f"SIM-{prefix}-{self._order_sequence}"

    def create_entry_from_order(self, source_order: Order) -> Order:
        """Create one synthetic entry using only the canceled real order fields."""
        with self._lock:
            if self.orders or self.current_order_id:
                raise SimulationLedgerError("invalid_trial_transition")
            price = self._validate_price(getattr(source_order, "price", 0.0))
            if int(getattr(source_order, "volume", 0) or 0) != 1:
                raise SimulationLedgerError("invalid_trial_volume")
            if getattr(source_order, "offset", OffsetFlag.OPEN) != OffsetFlag.OPEN:
                raise SimulationLedgerError("invalid_trial_transition")
            order_id = self._next_order_id("E")
            order = Order(
                order_id=order_id,
                symbol=str(source_order.symbol),
                direction=source_order.direction,
                order_type=source_order.order_type,
                price=price,
                volume=1,
                status=OrderStatus.SUBMITTED,
                offset=OffsetFlag.OPEN,
                create_time=datetime.now(),
                update_time=datetime.now(),
            )
            self.orders[order_id] = order
            self.current_order_id = order_id
            return copy.deepcopy(order)

    def submit(self, signal: Signal) -> str:
        with self._lock:
            if self.current_order_id:
                raise SimulationLedgerError("active_simulation_order_exists")
            price = self._validate_price(getattr(signal, "price", 0.0))
            if int(getattr(signal, "volume", 0) or 0) != 1:
                raise SimulationLedgerError("invalid_trial_volume")
            prefix = "E" if signal.offset == OffsetFlag.OPEN else "C"
            order_id = self._next_order_id(prefix)
            now = datetime.now()
            self.orders[order_id] = Order(
                order_id=order_id,
                symbol=str(signal.symbol),
                direction=signal.direction,
                order_type=signal.order_type,
                price=price,
                volume=1,
                status=OrderStatus.SUBMITTED,
                offset=signal.offset,
                create_time=now,
                update_time=now,
            )
            self.current_order_id = order_id
            return order_id

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            order = self.orders.get(str(order_id))
            return copy.deepcopy(order) if order is not None else None

    def cancel(self, order_id: str) -> bool:
        with self._lock:
            order = self.orders.get(str(order_id))
            if order is None:
                raise SimulationLedgerError("order_not_owned_by_trial_run")
            if self.current_order_id != order.order_id:
                raise SimulationLedgerError("order_not_current")
            if not order.is_active():
                return False
            order.status = OrderStatus.CANCELLED
            order.update_time = datetime.now()
            self.current_order_id = ""
            return True

    def fill(self, order_id: str) -> SimulationFillResult:
        with self._lock:
            order = self.orders.get(str(order_id))
            if order is None:
                raise SimulationLedgerError("order_not_owned_by_trial_run")
            if self.current_order_id != order.order_id:
                raise SimulationLedgerError("order_not_current")
            if not order.is_active():
                raise SimulationLedgerError("invalid_trial_transition")
            price = self._validate_price(order.price)
            now = datetime.now()
            order.traded_volume = int(order.volume)
            order.status = OrderStatus.FILLED
            order.update_time = now
            trade = Trade(
                trade_id=f"SIM-T-{uuid4().hex[:12].upper()}",
                order_id=order.order_id,
                symbol=order.symbol,
                direction=order.direction,
                price=price,
                volume=1,
                trade_time=now,
            )
            direction = _position_direction(order)
            key = f"{order.symbol}_{direction.value}"
            current = self.positions.get(key)
            current_volume = int(getattr(current, "volume", 0) or 0)
            next_volume = (
                current_volume + 1
                if order.offset == OffsetFlag.OPEN
                else max(0, current_volume - 1)
            )
            position = Position(
                symbol=order.symbol,
                direction=direction,
                volume=next_volume,
                frozen=0,
                price=price,
                cost=price,
                pnl=float(getattr(current, "pnl", 0.0) or 0.0),
            )
            self.positions[key] = position
            self.trades[trade.trade_id] = trade
            self.current_order_id = ""
            return SimulationFillResult(
                order=copy.deepcopy(order),
                trade=copy.deepcopy(trade),
                position=copy.deepcopy(position),
            )

    def position_volume(self, symbol: str) -> int:
        with self._lock:
            return sum(
                abs(int(getattr(position, "volume", 0) or 0))
                for position in self.positions.values()
                if str(getattr(position, "symbol", "")) == str(symbol)
            )


class GatewayExecutionAdapter:
    """Default adapter preserving the gateway/OrderManager execution path."""

    is_simulation = False

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def submit(self, signal: Signal) -> str:
        return self.engine._send_gateway_signal(signal)

    def cancel(self, order_id: str) -> bool:
        return self.engine._cancel_gateway_order(order_id)

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.engine.order_manager.get_order(order_id)


class TrialRunSimulationAdapter:
    """Signal adapter backed solely by a TrialRunSimulationLedger."""

    is_simulation = True

    def __init__(
        self,
        ledger: Optional[TrialRunSimulationLedger] = None,
        *,
        on_fill: Optional[Callable[[SimulationFillResult], None]] = None,
        on_cancel: Optional[Callable[[Order], None]] = None,
    ) -> None:
        self.ledger = ledger or TrialRunSimulationLedger()
        self.on_fill = on_fill
        self.on_cancel = on_cancel

    @property
    def orders(self) -> Dict[str, Order]:
        return self.ledger.orders

    @property
    def positions(self) -> Dict[str, Position]:
        return self.ledger.positions

    def submit(self, signal: Signal) -> str:
        return self.ledger.submit(signal)

    def cancel(self, order_id: str) -> bool:
        cancelled = self.ledger.cancel(order_id)
        if cancelled and callable(self.on_cancel):
            order = self.ledger.get_order(order_id)
            if order is not None:
                self.on_cancel(order)
        return cancelled

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.ledger.get_order(order_id)

    def fill(self, order_id: str) -> SimulationFillResult:
        result = self.ledger.fill(order_id)
        if callable(self.on_fill):
            self.on_fill(result)
        return result
