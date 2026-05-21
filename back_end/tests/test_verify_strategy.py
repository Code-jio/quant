from datetime import datetime, timedelta

import pandas as pd
from src.strategy import Direction, Trade
from src.strategy.strategies.verify import VerifyStrategy
from src.trading.types import MarketData


def _bar(close, symbol="rb2510"):
    return pd.Series({
        "symbol": symbol, "datetime": pd.Timestamp.now(),
        "open": close - 2, "high": close + 2, "low": close - 3, "close": close, "volume": 100,
    })


def _trade(direction, price=3130, symbol="rb2510"):
    suffix = direction.value if hasattr(direction, "value") else str(direction)
    return Trade(
        trade_id=f"TRADE_{suffix}",
        order_id=f"ORDER_{suffix}",
        symbol=symbol,
        direction=direction,
        price=price,
        volume=1,
    )


def _tick(price=3130, symbol="rb2510", ts=None):
    return MarketData(
        symbol=symbol,
        last_price=price,
        bid_price_1=price - 1,
        ask_price_1=price + 1,
        bid_volume_1=10,
        ask_volume_1=10,
        volume=100,
        turnover=price * 100,
        timestamp=ts or datetime.now(),
    )


def test_first_valid_bar_marks_market_ready_without_legacy_warmup():
    """A fresh market bar is enough to make VerifyStrategy ready to start."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()

    s.on_bar(_bar(3130))
    assert len(s.signals) == 0
    assert s.market_ready is True
    assert s.ready_to_arm is True
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "ready_to_start"
    assert s.snapshot()["readiness_bars"] == 1


def test_first_valid_tick_marks_market_ready_without_waiting_for_completed_bar():
    """A live tick is enough for VerifyStrategy readiness; no completed bar is required."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()

    s.on_tick(_tick(3130))

    assert s.market_ready is True
    assert s.ready_to_arm is True
    assert s.snapshot()["state"] == "ready_to_start"
    assert s.snapshot()["tick_count"] == 1
    assert s.snapshot()["bar_count"] == 0


def test_started_strategy_buys_on_next_valid_tick():
    """After explicit start, VerifyStrategy sends the entry on the next valid tick."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_tick(_tick(3128, ts=datetime.now()))
    assert s.start_verification() is True

    s.on_tick(_tick(3130, ts=datetime.now() + timedelta(seconds=1)))

    assert len(s.signals) == 1
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].price == 3130
    assert s.snapshot()["state"] == "entry_pending"


def test_start_before_market_ready_is_rejected():
    """Starting before a valid market bar should be rejected."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    assert s.start_verification() is False
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "waiting_market_data"


def test_buy_signal_on_next_bar_after_start():
    """Strategy emits a buy signal on the bar after explicit verification start."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert len(s.signals) == 0

    assert s.start_verification() is True
    assert s.snapshot()["state"] == "started"
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].volume == 1
    assert s.signals[0].order_type.value == "limit"
    assert s._entry_order_sent is True
    assert s._bought is False
    assert s.snapshot()["state"] == "entry_pending"

    s.on_trade(_trade(Direction.LONG, 3130))
    assert s._bought is True
    assert s.snapshot()["state"] == "holding"


def test_sell_signal_after_hold():
    """Strategy emits close signal after holding for hold_bars."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 5, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.start_verification() is True
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s._bought is False
    s.on_trade(_trade(Direction.LONG, 3130))
    assert s._bought is True

    for _ in range(5):
        s.on_bar(_bar(3135))

    assert len(s.signals) == 2
    assert s.signals[1].direction.value == "short"
    assert s.signals[1].offset.value == "close"
    assert s._closed is False
    assert s.completed is False
    assert s.snapshot()["state"] == "closing"

    s.on_trade(_trade(Direction.SHORT, 3135))
    assert s._closed is True
    assert s.completed is True
    assert s.snapshot()["state"] == "completed"


def test_revoke_start_returns_to_ready_state():
    """Verification start can be revoked before entry is sent."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.start_verification() is True
    s.revoke_authorization()
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "ready_to_start"
    s.on_bar(_bar(3135))
    assert len(s.signals) == 0


def test_no_duplicate_buy():
    """Strategy only buys once."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.start_verification() is True
    s.on_bar(_bar(3135))
    assert len(s.signals) == 1
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3131))
    assert len(s.signals) == 1


def test_no_signals_after_close():
    """Strategy emits nothing after close."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.start_verification() is True
    s.on_bar(_bar(3131))
    s.on_trade(_trade(Direction.LONG, 3131))
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3135))
    count_after_sell = len(s.signals)
    assert count_after_sell == 2
    s.on_trade(_trade(Direction.SHORT, 3135))
    assert s.completed is True
    s.on_bar(_bar(3140))
    s.on_bar(_bar(3138))
    assert len(s.signals) == count_after_sell


def test_rejected_signal_enters_error_and_does_not_retry():
    """A rejected entry signal should not be retried as if it were pending forever."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.start_verification() is True
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1

    s.mark_signal_rejected("risk denied")
    assert s.snapshot()["state"] == "error"
    assert s.snapshot()["last_reject_reason"] == "risk denied"
    s.on_bar(_bar(3132))
    assert len(s.signals) == 1
