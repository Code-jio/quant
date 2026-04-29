from src.backtest import BacktestConfig, BacktestEngine
from src.strategy import Direction


def test_backtest_equity_uses_margin_multiplier_and_unrealized_pnl():
    config = BacktestConfig(
        initial_capital=1000,
        commission_rate=0.01,
        slip_rate=0,
        margin_rate=0.1,
        contract_multiplier=10,
    )
    engine = BacktestEngine(config)

    engine._open_position("rb2505", price=100, volume=2, direction=Direction.LONG)
    engine._update_positions({"rb2505": {"close": 110}})
    engine._record_equity("2024-01-02", {"rb2505": {"close": 110}})

    equity = engine.equity_curve["2024-01-02"]
    assert equity["cash"] == 780
    assert equity["margin"] == 200
    assert equity["unrealized_pnl"] == 200
    assert equity["capital"] == 1180

    engine._close_position("rb2505", price=110, volume=2, direction=Direction.LONG)

    assert engine.available_capital == 1158
    assert engine.position_margins["rb2505"] == 0
    assert engine.trades[-1].pnl == 178


def test_market_slippage_is_directional():
    config = BacktestConfig(slip_rate=0.01)
    engine = BacktestEngine(config)

    assert engine._apply_slippage(100, Direction.LONG) == 101
    assert engine._apply_slippage(100, Direction.SHORT) == 99
