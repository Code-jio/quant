"""
分析报告格式化模块
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import AnalysisResult


class IReportFormatter(ABC):
    """报告格式化器接口"""

    @abstractmethod
    def format(self, result: 'AnalysisResult') -> str:
        """格式化分析结果"""
        pass

    @abstractmethod
    def format_header(self) -> str:
        """格式化报告头部"""
        pass

    @abstractmethod
    def format_risk_section(self, result: 'AnalysisResult') -> str:
        """格式化风险指标部分"""
        pass

    @abstractmethod
    def format_performance_section(self, result: 'AnalysisResult') -> str:
        """格式化绩效指标部分"""
        pass


class TextReportFormatter(IReportFormatter):
    """文本格式报告"""

    def format(self, result: 'AnalysisResult') -> str:
        sections = [
            self.format_header(),
            self.format_risk_section(result),
            self.format_performance_section(result),
            "=" * 60,
        ]
        return "\n".join(sections)

    def format_header(self) -> str:
        return "\n".join([
            "=" * 60,
            "                    量化策略绩效报告",
            "=" * 60,
        ])

    def format_risk_section(self, result: 'AnalysisResult') -> str:
        r = result.risk
        return "\n".join([
            "\n【风险指标】",
            f"  年化波动率:     {r.volatility:.2%}",
            f"  VaR (95%):     {r.var_95:.2%}",
            f"  CVaR (95%):    {r.cvar_95:.2%}",
            f"  最大回撤:       {r.max_drawdown_pct:.2%}",
            f"  夏普比率:       {r.sharpe_ratio:.2f}",
            f"  索提诺比率:     {r.sortino_ratio:.2f}",
            f"  卡玛比率:       {r.calmar_ratio:.2f}",
        ])

    def format_performance_section(self, result: 'AnalysisResult') -> str:
        p = result.performance
        return "\n".join([
            "\n【绩效指标】",
            f"  总收益率:       {p.total_return:.2%}",
            f"  年化收益率:     {p.annual_return:.2%}",
            f"  胜率:          {p.win_rate:.2%}",
            f"  盈亏比:        {p.profit_loss_ratio:.2f}",
            f"  平均盈利:       {p.avg_win:.2f}",
            f"  平均亏损:       {p.avg_loss:.2f}",
            f"  总交易次数:     {p.total_trades}",
            f"  盈利次数:       {p.winning_trades}",
            f"  亏损次数:       {p.losing_trades}",
        ])


class JsonReportFormatter(IReportFormatter):
    """JSON格式报告"""

    def format(self, result: 'AnalysisResult') -> str:
        import json
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)

    def format_header(self) -> str:
        return ""

    def format_risk_section(self, result: 'AnalysisResult') -> str:
        return ""

    def format_performance_section(self, result: 'AnalysisResult') -> str:
        return ""
