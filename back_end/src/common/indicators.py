"""Indicator definitions shared by research, chart APIs and strategies."""
import pandas as pd


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Seed with the first N changes; warmup is unknown and a flat window is 50."""
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("RSI period must be a positive integer")
    values = [float("nan")] * len(close)
    if len(close) <= period:
        return pd.Series(values, index=close.index, dtype=float)
    changes = close.astype(float).diff().to_numpy()
    gain = sum(max(change, 0.0) for change in changes[1:period + 1]) / period
    loss = sum(max(-change, 0.0) for change in changes[1:period + 1]) / period
    for i in range(period, len(close)):
        if i > period:
            gain = (gain * (period - 1) + max(changes[i], 0.0)) / period
            loss = (loss * (period - 1) + max(-changes[i], 0.0)) / period
        values[i] = 100.0 * gain / (gain + loss) if gain + loss else 50.0
    return pd.Series(values, index=close.index, dtype=float)
