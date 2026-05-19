import numpy as np
import pandas as pd
import pytest

from src.analysis import PerformanceAnalyzer, RiskAnalyzer


def test_risk_metrics_match_known_sample():
    returns = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    equity = pd.Series([100, 101, 98.98, 101.9494, 100.929906, 102.94850412])

    metrics = RiskAnalyzer.analyze(returns, equity)

    assert metrics.var_95 == pytest.approx(-0.018, abs=0.001)
    assert metrics.cvar_95 == pytest.approx(-0.02, abs=0.001)
    assert metrics.sharpe_ratio == pytest.approx(4.5932, abs=0.01)
    assert metrics.sortino_ratio == pytest.approx(13.47, abs=0.01)
    assert metrics.max_drawdown == pytest.approx(0.02, abs=0.001)


def test_performance_metrics_use_trade_pnl_values():
    equity = pd.Series([1000, 1010, 1005, 1020])
    trades = [{"pnl": 10}, {"pnl": -5}, {"pnl": 15}]

    metrics = PerformanceAnalyzer.analyze(equity, trades)

    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.profit_loss_ratio == pytest.approx(12.5 / 5)
    assert metrics.avg_win == pytest.approx(12.5)
    assert metrics.avg_loss == pytest.approx(-5)
