"""
分析模块数据类型定义
"""

from dataclasses import dataclass
from typing import Any
from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class ITrade(Protocol):
    """交易记录协议 - 用于类型标注"""
    @property
    def symbol(self) -> str: ...
    @property
    def direction(self) -> Any: ...
    @property
    def price(self) -> float: ...
    @property
    def volume(self) -> int: ...
    @property
    def pnl(self) -> float: ...
    @property
    def commission(self) -> float: ...


@dataclass
class RiskMetrics:
    """风险指标"""
    volatility: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    downside_vol: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0


@dataclass
class PerformanceMetrics:
    """绩效指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    cumulative_return: float = 0.0

    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    avg_holding_period: float = 0.0


@dataclass
class AnalysisResult:
    """综合分析结果"""
    risk: RiskMetrics
    performance: PerformanceMetrics

    def to_dict(self) -> dict:
        return {
            'risk': {
                'volatility': self.risk.volatility,
                'var_95': self.risk.var_95,
                'cvar_95': self.risk.cvar_95,
                'max_drawdown': self.risk.max_drawdown,
                'max_drawdown_pct': self.risk.max_drawdown_pct,
                'sharpe_ratio': self.risk.sharpe_ratio,
                'sortino_ratio': self.risk.sortino_ratio,
                'calmar_ratio': self.risk.calmar_ratio,
                'downside_vol': self.risk.downside_vol,
                'skewness': self.risk.skewness,
                'kurtosis': self.risk.kurtosis,
            },
            'performance': {
                'total_return': self.performance.total_return,
                'annual_return': self.performance.annual_return,
                'cumulative_return': self.performance.cumulative_return,
                'win_rate': self.performance.win_rate,
                'profit_loss_ratio': self.performance.profit_loss_ratio,
                'avg_win': self.performance.avg_win,
                'avg_loss': self.performance.avg_loss,
                'total_trades': self.performance.total_trades,
                'winning_trades': self.performance.winning_trades,
                'losing_trades': self.performance.losing_trades,
                'max_consecutive_wins': self.performance.max_consecutive_wins,
                'max_consecutive_losses': self.performance.max_consecutive_losses,
            }
        }
