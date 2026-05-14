from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.trading.bar_aggregator import BarAggregator


def _tick(symbol, ts, price, volume=10):
    return MagicMock(
        symbol=symbol,
        timestamp=ts,
        last_price=price,
        volume=volume,
    )


def test_single_bar_aggregation():
    """Ticks within the same interval update the same bar."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    assert agg.push(_tick("rb2510", t0, 3128)) is None
    assert agg.push(_tick("rb2510", t0.replace(second=15), 3132)) is None
    assert agg.push(_tick("rb2510", t0.replace(second=45), 3126)) is None

    current = agg._current["rb2510"]
    assert current["open"] == 3128
    assert current["high"] == 3132
    assert current["low"] == 3126
    assert current["close"] == 3126
    assert current["volume"] == 30


def test_bar_completion_emits():
    """Tick past the interval boundary emits a completed bar."""
    emitted = []
    agg = BarAggregator(interval_minutes=1, on_bar=lambda s, b: emitted.append((s, b)))
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.push(_tick("rb2510", t0.replace(minute=31), 3135))

    assert len(emitted) == 1
    sym, bar = emitted[0]
    assert sym == "rb2510"
    assert bar["open"] == 3128
    assert bar["close"] == 3128  # close was set by last tick of the single-tick bar
    # New bar started
    assert agg._current["rb2510"]["open"] == 3135


def test_bar_boundary_exact():
    """Tick exactly at bar boundary starts new bar."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.push(_tick("rb2510", t0.replace(minute=31, second=0), 3130))

    assert agg._current["rb2510"]["open"] == 3130
    assert agg._current["rb2510"]["bar_start"] == int(t0.replace(minute=31).timestamp())


def test_multi_symbol():
    """Two symbols aggregate independently."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.push(_tick("MA509", t0, 2450))

    assert len(agg._current) == 2
    assert agg._current["rb2510"]["open"] == 3128
    assert agg._current["MA509"]["open"] == 2450


def test_flush_emits_incomplete_bar():
    """flush() emits the current bar regardless of interval completion."""
    emitted = []
    agg = BarAggregator(interval_minutes=1, on_bar=lambda s, b: emitted.append((s, b)))
    t0 = datetime(2026, 5, 14, 9, 30, 15, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.flush("rb2510")

    assert len(emitted) == 1
    assert agg._current.get("rb2510") is None
