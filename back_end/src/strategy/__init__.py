"""
策略模块 - 统一导出
"""

from .errors import SignalError, OrderError, StrategyError
from .types import Direction, OrderType, OrderStatus, OffsetFlag, Signal, Order, Trade, Position
from .base import StrategyBase
from .registry import STRATEGY_REGISTRY, create_strategy
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy

__all__ = [
    "SignalError",
    "OrderError",
    "StrategyError",
    "Direction",
    "OrderType",
    "OrderStatus",
    "OffsetFlag",
    "Signal",
    "Order",
    "Trade",
    "Position",
    "StrategyBase",
    "STRATEGY_REGISTRY",
    "create_strategy",
    "MACrossStrategy",
    "RSIStrategy",
    "BreakoutStrategy",
]
