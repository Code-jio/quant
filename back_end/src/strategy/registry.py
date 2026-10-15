"""
策略注册与工厂函数
"""

from typing import Dict, Any
import math

from .base import StrategyBase
from .errors import StrategyError
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy

STRATEGY_REGISTRY: Dict[str, type] = {
    "ma_cross": MACrossStrategy,
    "rsi": RSIStrategy,
    "breakout": BreakoutStrategy,
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
    params = dict(params or {})
    fields = {
        "ma_cross": {"fast_period", "slow_period"},
        "rsi": {"rsi_period", "oversold", "overbought"},
        "breakout": {"lookback_period"},
    }
    if strategy_name in fields:
        allowed = fields[strategy_name] | {"symbol", "symbols", "position_ratio", "max_errors", "timeframe"}
        if set(params) - allowed:
            raise ValueError(f"Unsupported strategy parameters: {sorted(set(params) - allowed)}")
        for key, value in params.items():
            if key.endswith("period") or key == "max_errors":
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2000:
                    raise ValueError(f"{key} must be an integer in [1, 2000]")
            if key in {"position_ratio", "oversold", "overbought"}:
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"Invalid {key}")
                if value > (1 if key == "position_ratio" else 100):
                    raise ValueError(f"Invalid {key}")
        if "symbols" in params:
            if not isinstance(params["symbols"], list) or len(params["symbols"]) != 1:
                raise ValueError("Built-in strategies require exactly one symbol")
            params["symbol"] = params["symbols"][0]
        if "symbol" in params and (not isinstance(params["symbol"], str) or not params["symbol"].strip()):
            raise ValueError("A non-empty symbol is required")
        if params.get("timeframe", "1d") not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}:
            raise ValueError("Unsupported timeframe")
    return STRATEGY_REGISTRY[strategy_name](strategy_name, params)
