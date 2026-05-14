import pandas as pd
from src.strategy.strategies.verify import VerifyStrategy


def _bar(close, symbol="rb2510"):
    return pd.Series({
        "symbol": symbol, "datetime": pd.Timestamp.now(),
        "open": close - 2, "high": close + 2, "low": close - 3, "close": close, "volume": 100,
    })


def test_warmup_phase_no_signal():
    """Strategy should not emit signals during warmup."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(15):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0
    assert s._bar_count == 15


def test_buy_signal_after_warmup():
    """Strategy emits a buy signal after warmup bars."""
    s = VerifyStrategy("verify", {"warmup_bars": 5, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(4):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].volume == 1


def test_sell_signal_after_hold():
    """Strategy emits close signal after holding for hold_bars."""
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 5, "volume": 1})
    s.on_init()
    for _ in range(3):
        s.on_bar(_bar(3128))
    # Buy at bar 3
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s._bought is True
    # Hold for 5 bars
    for _ in range(5):
        s.on_bar(_bar(3135))
    # Sell at bar 5 of hold
    assert len(s.signals) == 2
    assert s.signals[1].direction.value == "short"
    assert s.signals[1].offset.value == "close"
    assert s._closed is True


def test_no_duplicate_buy():
    """Strategy only buys once."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    s.on_bar(_bar(3135))
    s.on_bar(_bar(3132))
    assert len(s.signals) == 1  # No more buy signals


def test_no_signals_after_close():
    """Strategy emits nothing after close."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))  # buy
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3135))  # sell
    count_after_sell = len(s.signals)
    s.on_bar(_bar(3140))
    s.on_bar(_bar(3138))
    assert len(s.signals) == count_after_sell  # No new signals
