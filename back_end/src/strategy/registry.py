"""
策略注册与工厂函数
"""

from typing import Dict, Any

from .base import StrategyBase
from .errors import StrategyError
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy

STRATEGY_REGISTRY: Dict[str, type] = {
    'ma_cross': MACrossStrategy,
    'rsi': RSIStrategy,
    'breakout': BreakoutStrategy,
}


def register_strategy(name: str, strategy_class: type):
    """注册策略类"""
    if not issubclass(strategy_class, StrategyBase):
        raise TypeError(f"{strategy_class} must be a subclass of StrategyBase")
    STRATEGY_REGISTRY[name] = strategy_class


def create_strategy(strategy_name: str, params: Dict[str, Any] = None) -> StrategyBase:
    """工厂函数：创建策略实例"""
    if strategy_name not in STRATEGY_REGISTRY:
        available = list(STRATEGY_REGISTRY.keys())
        raise ValueError(f"未知策略: {strategy_name}, 可用: {available}")
    return STRATEGY_REGISTRY[strategy_name](strategy_name, params)
