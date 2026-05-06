"""Backtest service helpers for API routes."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from ..analysis import Analyzer
from ..backtest import BacktestConfig, BacktestEngine
from ..data import DataManager
from ..settings import synthetic_data_enabled
from ..strategy import create_strategy


STRATEGY_CATALOG = [
    {
        "name": "ma_cross",
        "label": "双均线策略",
        "desc": "快慢均线金叉/死叉",
        "default_params": {
            "symbol": "IF9999",
            "fast_period": 10,
            "slow_period": 20,
            "position_ratio": 0.8,
        },
    },
    {
        "name": "rsi",
        "label": "RSI 均值回归",
        "desc": "RSI 超买超卖反转",
        "default_params": {
            "symbol": "IF9999",
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "position_ratio": 0.8,
        },
    },
    {
        "name": "breakout",
        "label": "突破策略",
        "desc": "N 日高低点突破",
        "default_params": {
            "symbol": "IF9999",
            "lookback_period": 20,
            "position_ratio": 0.8,
        },
    },
]


def run_backtest_sync(body: Any) -> Dict[str, Any]:
    """Run a backtest in a worker thread and return JSON-serializable data."""
    bt_cfg = BacktestConfig(
        start_date=body.start_date,
        end_date=body.end_date,
        initial_capital=body.initial_capital,
        commission_rate=body.commission_rate,
        slip_rate=body.slip_rate,
        margin_rate=body.margin_rate,
        contract_multiplier=body.contract_multiplier,
        max_errors=body.max_errors,
    )

    strategy = create_strategy(body.strategy_name, body.strategy_params or {})

    dm = DataManager()
    symbol = (body.strategy_params or {}).get("symbol", "IF9999")
    synthetic_data_used = False
    existing = dm.get_bars(symbol, body.start_date, body.end_date)
    if existing is None or existing.empty:
        if body.allow_synthetic_data and synthetic_data_enabled():
            generated = dm.generate_sample_data(symbol, days=body.sample_days)
            synthetic_data_used = not generated.empty
        else:
            return {
                "success": False,
                "error": "回测无历史数据，且模拟数据生成已禁用",
                "data_source": "missing",
                "synthetic_data_used": False,
            }

    engine = BacktestEngine(bt_cfg)
    engine.set_data_manager(dm)
    engine.set_strategy(strategy)
    engine.run()

    equity_list = sorted(engine.equity_curve.values(), key=lambda x: x["date"])
    if not equity_list:
        return {"success": False, "error": "回测无数据，请检查日期范围或合约代码"}

    eq_df = pd.DataFrame(equity_list)
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date")
    peak = eq_df["capital"].cummax()
    eq_df["dd_pct"] = (eq_df["capital"] - peak) / peak * 100

    equity_curve_out = [
        {
            "date": str(idx.date()),
            "capital": round(row["capital"], 2),
            "dd_pct": round(row["dd_pct"], 4),
            "cash": round(row.get("cash", 0), 2),
            "margin": round(row.get("margin", 0), 2),
            "unrealized_pnl": round(row.get("unrealized_pnl", 0), 2),
        }
        for idx, row in eq_df.iterrows()
    ]

    daily_ret_pct = (eq_df["capital"].pct_change().dropna() * 100).round(4).tolist()

    monthly_ret = eq_df["capital"].resample("ME").last().pct_change().dropna()
    years_list = sorted({str(dt.year) for dt in monthly_ret.index})
    yr_idx_map = {y: i for i, y in enumerate(years_list)}
    heatmap_data = []
    for dt, ret in monthly_ret.items():
        heatmap_data.append([dt.month - 1, yr_idx_map[str(dt.year)], round(ret * 100, 3)])

    cap_map = {item["date"]: item["capital"] for item in equity_curve_out}
    pos_tracker: dict[str, str | None] = {}
    trade_markers = []

    for trade in sorted(engine.result.trades, key=lambda x: x.trade_time):
        symbol = trade.symbol
        direction = trade.direction.value
        current_position = pos_tracker.get(symbol)

        if direction == "long":
            if current_position == "short":
                marker_type = "cover_close"
                pos_tracker[symbol] = None
            else:
                marker_type = "buy_open"
                pos_tracker[symbol] = "long"
        else:
            if current_position == "long":
                marker_type = "sell_close"
                pos_tracker[symbol] = None
            else:
                marker_type = "short_open"
                pos_tracker[symbol] = "short"

        timestamp = trade.trade_time
        date_str = timestamp.strftime("%Y-%m-%d") if hasattr(timestamp, "strftime") else str(timestamp)[:10]
        trade_markers.append(
            {
                "date": date_str,
                "capital": cap_map.get(date_str),
                "trade_price": round(trade.price, 4),
                "type": marker_type,
                "symbol": symbol,
                "volume": trade.volume,
                "pnl": round(getattr(trade, "pnl", 0.0), 2),
                "commission": round(trade.commission, 4),
            }
        )

    analyzer = Analyzer(bt_cfg.initial_capital)
    analyzer.set_data(list(engine.equity_curve.values()), engine.result.trades)
    raw = analyzer.analyze().to_dict()
    risk = raw.get("risk", {}) if raw else {}
    performance = raw.get("performance", {}) if raw else {}

    metrics = {
        "total_return": round(performance.get("total_return", 0) * 100, 3),
        "annual_return": round(performance.get("annual_return", 0) * 100, 3),
        "win_rate": round(performance.get("win_rate", 0) * 100, 3),
        "profit_loss_ratio": round(performance.get("profit_loss_ratio", 0), 4),
        "total_trades": performance.get("total_trades", 0),
        "winning_trades": performance.get("winning_trades", 0),
        "losing_trades": performance.get("losing_trades", 0),
        "avg_win": round(performance.get("avg_win", 0), 2),
        "avg_loss": round(performance.get("avg_loss", 0), 2),
        "max_consecutive_wins": performance.get("max_consecutive_wins", 0),
        "max_consecutive_losses": performance.get("max_consecutive_losses", 0),
        "sharpe_ratio": round(risk.get("sharpe_ratio", 0), 3),
        "sortino_ratio": round(risk.get("sortino_ratio", 0), 3),
        "calmar_ratio": round(risk.get("calmar_ratio", 0), 3),
        "max_drawdown_pct": round(risk.get("max_drawdown_pct", 0) * 100, 3),
        "volatility": round(risk.get("volatility", 0) * 100, 3),
        "var_95": round(risk.get("var_95", 0) * 100, 3),
        "cvar_95": round(risk.get("cvar_95", 0) * 100, 3),
        "downside_vol": round(risk.get("downside_vol", 0) * 100, 3),
        "skewness": round(risk.get("skewness", 0), 4),
        "kurtosis": round(risk.get("kurtosis", 0), 4),
    }

    return {
        "success": True,
        "config": {
            "strategy_name": strategy.name,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "initial_capital": body.initial_capital,
            "commission_rate": body.commission_rate,
            "slip_rate": body.slip_rate,
            "margin_rate": body.margin_rate,
            "contract_multiplier": body.contract_multiplier,
            "max_errors": body.max_errors,
        },
        "data_source": "synthetic" if synthetic_data_used else "historical",
        "synthetic_data_used": synthetic_data_used,
        "metrics": metrics,
        "equity_curve": equity_curve_out,
        "daily_returns": daily_ret_pct,
        "monthly_heatmap": {"years": years_list, "data": heatmap_data},
        "trade_markers": trade_markers,
    }
