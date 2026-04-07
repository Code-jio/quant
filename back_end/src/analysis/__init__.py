"""
风险与绩效分析模块
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


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


class RiskAnalyzer:
    """风险分析器"""
    
    @staticmethod
    def calculate_var(returns: pd.Series, confidence: float = 0.95) -> float:
        """计算VaR (Value at Risk)"""
        if len(returns) == 0:
            return 0.0
        return np.percentile(returns, (1 - confidence) * 100)
    
    @staticmethod
    def calculate_cvar(returns: pd.Series, confidence: float = 0.95) -> float:
        """计算CVaR (Conditional VaR) / Expected Shortfall"""
        var = RiskAnalyzer.calculate_var(returns, confidence)
        return returns[returns <= var].mean()
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: pd.Series) -> tuple:
        """计算最大回撤"""
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = abs(drawdown.min())
        
        max_dd_idx = drawdown.idxmin()
        peak_idx = equity_curve[:max_dd_idx].idxmax()
        
        return max_dd, max_dd_idx, peak_idx
    
    @staticmethod
    def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """计算夏普比率"""
        if len(returns) == 0 or returns.std() == 0:
            return 0.0
        excess_returns = returns - risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / returns.std()
    
    @staticmethod
    def calculate_sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """计算索提诺比率"""
        if len(returns) == 0:
            return 0.0
        
        excess_returns = returns - risk_free_rate / 252
        downside_returns = returns[returns < 0]
        
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0
        
        return np.sqrt(252) * excess_returns.mean() / downside_returns.std()
    
    @staticmethod
    def calculate_calmar_ratio(annual_return: float, max_drawdown: float) -> float:
        """计算卡玛比率"""
        if max_drawdown == 0:
            return 0.0
        return annual_return / max_drawdown
    
    @staticmethod
    def analyze(returns: pd.Series, equity_curve: pd.Series) -> RiskMetrics:
        """综合风险分析"""
        metrics = RiskMetrics()
        
        metrics.volatility = returns.std() * np.sqrt(252)
        metrics.var_95 = RiskAnalyzer.calculate_var(returns, 0.95)
        metrics.cvar_95 = RiskAnalyzer.calculate_cvar(returns, 0.95)
        
        metrics.max_drawdown, _, _ = RiskAnalyzer.calculate_max_drawdown(equity_curve)
        metrics.max_drawdown_pct = metrics.max_drawdown
        
        metrics.sharpe_ratio = RiskAnalyzer.calculate_sharpe_ratio(returns)
        metrics.sortino_ratio = RiskAnalyzer.calculate_sortino_ratio(returns)
        
        annual_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (252 / len(equity_curve)) - 1 if len(equity_curve) > 0 else 0
        metrics.calmar_ratio = RiskAnalyzer.calculate_calmar_ratio(annual_return, metrics.max_drawdown)
        
        downside_returns = returns[returns < 0]
        metrics.downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        metrics.skewness = returns.skew()
        metrics.kurtosis = returns.kurtosis()
        
        return metrics


class PerformanceAnalyzer:
    """绩效分析器"""
    
    @staticmethod
    def calculate_win_rate(trades: List[Dict]) -> float:
        """计算胜率"""
        if not trades:
            return 0.0
        
        wins = sum(1 for t in trades if t.get('pnl', 0) > 0)
        return wins / len(trades)
    
    @staticmethod
    def calculate_profit_loss_ratio(trades: List[Dict]) -> float:
        """计算盈亏比"""
        if not trades:
            return 0.0
        
        wins = [t['pnl'] for t in trades if t.get('pnl', 0) > 0]
        losses = [abs(t['pnl']) for t in trades if t.get('pnl', 0) < 0]
        
        if not wins or not losses:
            return 0.0
        
        avg_win = np.mean(wins)
        avg_loss = np.mean(losses)
        
        return avg_win / avg_loss if avg_loss > 0 else 0.0
    
    @staticmethod
    def calculate_consecutive_trades(trades: List[Dict]) -> tuple:
        """计算最大连续盈亏"""
        if not trades:
            return 0, 0
        
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            if trade.get('pnl', 0) > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif trade.get('pnl', 0) < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        
        return max_wins, max_losses
    
    @staticmethod
    def analyze(equity_curve: pd.Series, trades: List[Dict], 
                initial_capital: float) -> PerformanceMetrics:
        """综合绩效分析"""
        metrics = PerformanceMetrics()
        
        if len(equity_curve) < 2:
            return metrics
        
        metrics.cumulative_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        
        days = len(equity_curve)
        years = days / 252
        metrics.annual_return = ((1 + metrics.cumulative_return) ** (1 / years) - 1) if years > 0 else 0
        metrics.total_return = metrics.cumulative_return
        
        metrics.win_rate = PerformanceAnalyzer.calculate_win_rate(trades)
        metrics.profit_loss_ratio = PerformanceAnalyzer.calculate_profit_loss_ratio(trades)
        
        wins = [t['pnl'] for t in trades if t.get('pnl', 0) > 0]
        losses = [t['pnl'] for t in trades if t.get('pnl', 0) < 0]
        
        metrics.avg_win = np.mean(wins) if wins else 0
        metrics.avg_loss = np.mean(losses) if losses else 0
        
        metrics.total_trades = len(trades)
        metrics.winning_trades = len(wins)
        metrics.losing_trades = len(losses)
        
        metrics.max_consecutive_wins, metrics.max_consecutive_losses = \
            PerformanceAnalyzer.calculate_consecutive_trades(trades)
        
        return metrics


class Analyzer:
    """综合分析器 - 整合风险与绩效分析"""
    
    def __init__(self, initial_capital: float = 1000000.0):
        self.initial_capital = initial_capital
        self.equity_curve: Optional[pd.Series] = None
        self.returns: Optional[pd.Series] = None
        self.trades: List[Dict] = []
    
    def set_data(self, equity_curve: List[Dict], trades: List[Any]):
        """设置数据"""
        if equity_curve:
            df = pd.DataFrame(equity_curve)
            if 'date' in df.columns:
                df.set_index('date', inplace=True)
            self.equity_curve = df['capital'] if 'capital' in df.columns else df.iloc[:, 0]
            
            self.returns = self.equity_curve.pct_change().dropna()
        
        if trades:
            self.trades = []
            for trade in trades:
                self.trades.append({
                    'symbol': trade.symbol,
                    'direction': trade.direction.value,
                    'price': trade.price,
                    'volume': trade.volume,
                    'pnl': getattr(trade, 'pnl', 0),
                    'commission': trade.commission
                })
    
    def analyze(self) -> Dict[str, Any]:
        """综合分析"""
        if self.equity_curve is None or len(self.equity_curve) == 0:
            return {}
        
        risk_metrics = RiskAnalyzer.analyze(self.returns, self.equity_curve)
        perf_metrics = PerformanceAnalyzer.analyze(
            self.equity_curve, 
            self.trades, 
            self.initial_capital
        )
        
        return {
            'risk': {
                'volatility': risk_metrics.volatility,
                'var_95': risk_metrics.var_95,
                'cvar_95': risk_metrics.cvar_95,
                'max_drawdown': risk_metrics.max_drawdown,
                'max_drawdown_pct': risk_metrics.max_drawdown_pct,
                'sharpe_ratio': risk_metrics.sharpe_ratio,
                'sortino_ratio': risk_metrics.sortino_ratio,
                'calmar_ratio': risk_metrics.calmar_ratio,
                'downside_vol': risk_metrics.downside_vol,
                'skewness': risk_metrics.skewness,
                'kurtosis': risk_metrics.kurtosis,
            },
            'performance': {
                'total_return': perf_metrics.total_return,
                'annual_return': perf_metrics.annual_return,
                'cumulative_return': perf_metrics.cumulative_return,
                'win_rate': perf_metrics.win_rate,
                'profit_loss_ratio': perf_metrics.profit_loss_ratio,
                'avg_win': perf_metrics.avg_win,
                'avg_loss': perf_metrics.avg_loss,
                'total_trades': perf_metrics.total_trades,
                'winning_trades': perf_metrics.winning_trades,
                'losing_trades': perf_metrics.losing_trades,
                'max_consecutive_wins': perf_metrics.max_consecutive_wins,
                'max_consecutive_losses': perf_metrics.max_consecutive_losses,
            }
        }
    
    def generate_report(self) -> str:
        """生成分析报告"""
        results = self.analyze()
        
        if not results:
            return "无足够数据进行绩效分析"
        
        report = []
        report.append("=" * 60)
        report.append("                    量化策略绩效报告")
        report.append("=" * 60)
        
        report.append("\n【风险指标】")
        report.append(f"  年化波动率:     {results['risk']['volatility']:.2%}")
        report.append(f"  VaR (95%):     {results['risk']['var_95']:.2%}")
        report.append(f"  CVaR (95%):    {results['risk']['cvar_95']:.2%}")
        report.append(f"  最大回撤:       {results['risk']['max_drawdown_pct']:.2%}")
        report.append(f"  夏普比率:       {results['risk']['sharpe_ratio']:.2f}")
        report.append(f"  索提诺比率:     {results['risk']['sortino_ratio']:.2f}")
        report.append(f"  卡玛比率:       {results['risk']['calmar_ratio']:.2f}")
        
        report.append("\n【绩效指标】")
        report.append(f"  总收益率:       {results['performance']['total_return']:.2%}")
        report.append(f"  年化收益率:     {results['performance']['annual_return']:.2%}")
        report.append(f"  胜率:          {results['performance']['win_rate']:.2%}")
        report.append(f"  盈亏比:        {results['performance']['profit_loss_ratio']:.2f}")
        report.append(f"  平均盈利:       {results['performance']['avg_win']:.2f}")
        report.append(f"  平均亏损:       {results['performance']['avg_loss']:.2f}")
        report.append(f"  总交易次数:     {results['performance']['total_trades']}")
        report.append(f"  盈利次数:       {results['performance']['winning_trades']}")
        report.append(f"  亏损次数:       {results['performance']['losing_trades']}")
        
        report.append("\n" + "=" * 60)
        
        return "\n".join(report)
