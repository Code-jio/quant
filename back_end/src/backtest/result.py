"""
回测结果模块
"""

from dataclasses import dataclass, field
from typing import List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from ..strategy import Trade


@dataclass
class BacktestResult:
    """回测结果"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    equity_curve: List[Dict] = field(default_factory=list)
    trades: List['Trade'] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)
