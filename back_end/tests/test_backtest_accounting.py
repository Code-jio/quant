import pandas as pd

from src.backtest import BacktestConfig, BacktestEngine
from src.strategy import Direction, OrderType, StrategyBase, Trade


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


def test_backtest_result_counts_realized_trade_pnl_after_commission():
    engine = BacktestEngine(BacktestConfig(initial_capital=1000))
    engine.equity_curve = {
        "2024-01-01": {"date": "2024-01-01", "capital": 1000},
        "2024-01-02": {"date": "2024-01-02", "capital": 995},
    }
    engine.trades = [
        Trade("T1", "O1", "rb2505", Direction.LONG, price=100, volume=1, pnl=0),
        Trade("T2", "O2", "rb2505", Direction.SHORT, price=100, volume=1, pnl=-5),
    ]

    engine._calculate_result(["2024-01-01", "2024-01-02"])

    assert engine.result.total_trades == 1
    assert engine.result.winning_trades == 0
    assert engine.result.losing_trades == 1
    assert engine.result.win_rate == 0


class BiasProbeStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self.observed_indices = []

    def on_bar(self, bar: pd.Series):
        data = self.get_data(self.symbol)
        self.observed_indices.append([] if data is None else list(data.index))


class SingleSymbolDataManager:
    def __init__(self, df):
        self.df = df

    def get_bars(self, symbol, start_date, end_date):
        return self.df


def test_backtest_on_bar_sees_only_prior_history():
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    df = pd.DataFrame(
        {
            "open": [10, 11, 12],
            "high": [10, 11, 12],
            "low": [10, 11, 12],
            "close": [10, 11, 12],
            "volume": [1, 1, 1],
        },
        index=dates,
    )
    strategy = BiasProbeStrategy("bias_probe", {"symbol": "rb2505"})
    engine = BacktestEngine(BacktestConfig(start_date="2024-01-01", end_date="2024-01-03"))
    engine.set_data_manager(SingleSymbolDataManager(df))
    engine.set_strategy(strategy)

    engine.run()

    assert strategy.observed_indices == [[], [dates[0]], [dates[0], dates[1]]]
