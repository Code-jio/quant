import json
import pandas as pd
import pytest
from datetime import datetime
from src.backtest import BacktestConfig, BacktestEngine
from src.strategy import StrategyBase, Direction, OrderType, OrderStatus, Trade, OffsetFlag
from src.analysis import Analyzer
from test_backtest_accounting import SingleSymbolDataManager


class BuyOnce(StrategyBase):
    def on_init(self):
        self.symbol = "A"
        self.seen = []

    def on_bar(self, bar):
        self.seen.append(self.get_position("A").volume)
        if not self.get_position("A").volume:
            self.buy("A", 100, 1, OrderType.LIMIT)


def run(strategy, prices=(100, 100, 100, 100)):
    df = pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices, "volume": [100] * len(prices)},
        index=pd.date_range("2026-01-01", periods=len(prices)),
    )
    e = BacktestEngine(BacktestConfig(start_date="2026-01-01", end_date="2026-01-10", commission_rate=0, slip_rate=0))
    e.set_strategy(strategy)
    e.set_data_manager(SingleSymbolDataManager(df))
    e.run()
    return e


def test_fills_on_next_bar_and_updates_strategy_position():
    s = BuyOnce("probe", {"symbol": "A"})
    e = run(s)
    assert s.seen == [0, 1, 1, 1]
    assert len(e.trades) == 1
    t = e.trades[0]
    assert t.trade_time == pd.Timestamp("2026-01-02")
    assert t.order_id and e.orders[t.order_id].status == OrderStatus.FILLED
    assert e.orders[t.order_id].traded_volume == 1


def test_limit_that_never_crosses_does_not_fill():
    e = run(BuyOnce("probe", {"symbol": "A"}), prices=(200, 200, 200, 200))
    assert e.trades == []


def test_drawdown_unit_and_short_sample_json():
    a = Analyzer(initial_capital=100)
    a.set_data([{"date": "2026-01-01", "capital": 100}, {"date": "2026-01-02", "capital": 90}], [])
    raw = a.analyze().to_dict()
    assert raw["risk"]["max_drawdown_pct"] == pytest.approx(0.1)
    json.dumps(raw, allow_nan=False)
    assert "10.00%" in a.generate_report()


def test_round_trip_net_of_both_fees_is_one_trade():
    a = Analyzer(initial_capital=100)
    a.set_data(
        [{"date": "2026-01-01", "capital": 100}, {"date": "2026-01-02", "capital": 108}],
        [
            Trade("1", "1", "A", Direction.LONG, 100, 1, commission=1, offset=OffsetFlag.OPEN),
            Trade("2", "2", "A", Direction.SHORT, 110, 1, commission=1, pnl=9, offset=OffsetFlag.CLOSE),
        ],
    )
    p = a.analyze().performance
    assert p.total_trades == 1 and p.win_rate == 1 and p.avg_win == 8


def test_strategy_error_does_not_report_complete_backtest():
    class Broken(BuyOnce):
        def on_bar(self, bar):
            raise RuntimeError("broken strategy")

    e = run(Broken("probe", {"symbol": "A"}))
    assert e.result.status == "failed" and e.result.errors


def test_reported_strategy_errors_are_not_silent_success():
    class Reported(BuyOnce):
        def on_bar(self, bar):
            self.on_error(RuntimeError("reported failure"))

    e = run(Reported("probe", {"symbol": "A"}))
    assert e.result.status == "failed" and e.result.errors


def test_multiple_orders_share_each_bars_volume_budget():
    class Multiple(BuyOnce):
        def on_bar(self, bar):
            if not self.signals:
                self.buy("A", 100, 150, OrderType.LIMIT)
                self.buy("A", 100, 150, OrderType.LIMIT)

    e = run(Multiple("probe", {"symbol": "A"}), prices=(100, 100))
    assert sum(t.volume for t in e.trades) == 100


def test_bankrupt_equity_has_no_undefined_annualization_warning():
    a = Analyzer(initial_capital=100)
    a.set_data([{"date": "2026-01-01", "capital": 100}, {"date": "2026-01-02", "capital": -10}], [])
    raw = a.analyze().to_dict()
    assert raw["performance"]["annual_return"] is None
    assert raw["risk"]["sharpe_ratio"] is None
    json.dumps(raw, allow_nan=False)


def test_full_synthetic_research_request_is_finite_json(tmp_path, monkeypatch):
    from src.api import BacktestRunRequest
    from src.api.backtest_service import run_backtest_sync

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUANT_ALLOW_SYNTHETIC_DATA", "1")
    result = run_backtest_sync(
        BacktestRunRequest(
            strategy_name="ma_cross",
            strategy_params={"symbol": "IF9999", "fast_period": 10, "slow_period": 20, "position_ratio": 0.8},
            start_date="2023-01-01",
            end_date="2024-12-31",
            allow_synthetic_data=True,
            sample_days=700,
        )
    )
    assert result["success"] and result["synthetic_data_used"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_capital": 0},
        {"margin_rate": 0},
        {"commission_rate": -1},
        {"start_date": "2026-01-02", "end_date": "2026-01-01"},
        {"contract_multiplier": float("nan")},
    ],
)
def test_invalid_backtest_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(**kwargs)
