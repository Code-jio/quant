"""
回测引擎模块 - 统一导出
"""

from .errors import BacktestError
from .config import BacktestConfig
from .result import BacktestResult
from .engine import BacktestEngine

__all__ = [
    "BacktestError",
    "BacktestConfig",
    "BacktestResult",
    "BacktestEngine",
]
