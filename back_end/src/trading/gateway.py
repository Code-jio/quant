"""Trading gateway abstractions and vn.py gateway factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

from .errors import GatewayError
from .types import AccountInfo, MarketData, TradingStatus

if TYPE_CHECKING:
    from ..strategy import Order, Position, Signal, Trade

logger = logging.getLogger(__name__)


class GatewayBase(ABC):
    """Base class for broker gateway adapters."""

    def __init__(self, name: str):
        self.name = name
        self.status = TradingStatus.STOPPED
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.account = AccountInfo()

        self.on_order_callback: Any = None
        self.on_trade_callback: Any = None
        self.on_position_callback: Any = None
        self.on_account_callback: Any = None
        self.on_tick_callback: Any = None
        self.on_error_callback: Any = None

    @abstractmethod
    def connect(self, config: dict[str, Any]) -> bool:
        """Connect the gateway."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect the gateway."""

    @abstractmethod
    def send_order(self, signal: Signal) -> str:
        """Send an order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""

    @abstractmethod
    def query_account(self) -> AccountInfo:
        """Query account state."""

    @abstractmethod
    def query_positions(self) -> list[Position]:
        """Query current positions."""

    @abstractmethod
    def query_orders(self) -> list[Order]:
        """Query current orders."""

    def on_order(self, order: Order) -> None:
        """Order callback entrypoint."""
        self.orders[order.order_id] = order
        if self.on_order_callback:
            try:
                self.on_order_callback(order)
            except Exception as exc:
                logger.error("Order callback failed: %s", exc)

    def on_trade(self, trade: Trade) -> None:
        """Trade callback entrypoint."""
        if self.on_trade_callback:
            try:
                self.on_trade_callback(trade)
            except Exception as exc:
                logger.error("Trade callback failed: %s", exc)

    def on_position(self, position: Position) -> None:
        """Position callback entrypoint."""
        direction = position.direction.value if hasattr(position.direction, "value") else str(position.direction)
        key = f"{position.symbol}_{direction}"
        self.positions[key] = position
        if self.on_position_callback:
            try:
                self.on_position_callback(position)
            except Exception as exc:
                logger.error("Position callback failed: %s", exc)

    def on_account(self, account: AccountInfo) -> None:
        """Account callback entrypoint."""
        self.account = account
        if self.on_account_callback:
            try:
                self.on_account_callback(account)
            except Exception as exc:
                logger.error("Account callback failed: %s", exc)

    def on_tick(self, tick: MarketData) -> None:
        """Market data callback entrypoint."""
        if self.on_tick_callback:
            try:
                self.on_tick_callback(tick)
            except Exception as exc:
                logger.error("Tick callback failed: %s", exc)

    def on_error(self, error: Exception, context: str = "") -> None:
        """Gateway error callback entrypoint."""
        logger.error("Gateway error (%s): %s", context, error)
        if self.on_error_callback:
            try:
                self.on_error_callback(error, context)
            except Exception as exc:
                logger.error("Error callback failed: %s", exc)


GatewayFactory = Callable[[], GatewayBase]


def _create_vnpy_gateway() -> GatewayBase:
    from .vnpy_gateway import create_vnpy_gateway

    return create_vnpy_gateway()


GATEWAY_REGISTRY: dict[str, GatewayFactory] = {
    "ctp": _create_vnpy_gateway,
    "vnpy": _create_vnpy_gateway,
}


def create_gateway(gateway_type: str = "vnpy") -> GatewayBase:
    """Create a real trading gateway instance.

    Only vn.py/CTP is supported. Unknown gateway names are rejected instead of
    silently falling back to a paper-trading implementation.
    """
    normalized_type = (gateway_type or "vnpy").lower()
    factory = GATEWAY_REGISTRY.get(normalized_type)
    if not factory:
        supported = ", ".join(sorted(GATEWAY_REGISTRY))
        raise GatewayError(f"Unsupported gateway type: {gateway_type}. Supported: {supported}")
    return factory()
