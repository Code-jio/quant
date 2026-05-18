import pandas as pd
from src.strategy import Direction, Trade
from src.strategy.strategies.verify import VerifyStrategy


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


def test_warmup_complete_waits_for_authorization():
    """Strategy should become ready after warmup and wait for authorization."""
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(2):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0
    assert s.snapshot()["state"] == "warming"

    s.on_bar(_bar(3130))
    assert len(s.signals) == 0
    assert s.ready_to_arm is True
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "ready_to_arm"


def test_early_authorization_is_rejected():
    """Authorization before ready_to_arm should be rejected."""
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 10, "volume": 1})
    s.on_init()
    assert s.authorize_trading() is False
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "warming"


def test_buy_signal_on_next_bar_after_authorization():
    """Strategy emits a buy signal on the bar after authorization."""
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(3):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0

    assert s.authorize_trading() is True
    assert s.snapshot()["state"] == "armed"
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
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 5, "volume": 1})
    s.on_init()
    for _ in range(3):
        s.on_bar(_bar(3128))
    assert s.authorize_trading() is True
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


def test_revoke_authorization_returns_to_ready_state():
    """Authorization can be revoked before entry is sent."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))
    assert s.authorize_trading() is True
    s.revoke_authorization()
    assert s.trade_authorized is False
    assert s.snapshot()["state"] == "ready_to_arm"
    s.on_bar(_bar(3135))
    assert len(s.signals) == 0


def test_no_duplicate_buy():
    """Strategy only buys once."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))
    assert s.authorize_trading() is True
    s.on_bar(_bar(3135))
    assert len(s.signals) == 1
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3131))
    assert len(s.signals) == 1


def test_no_signals_after_close():
    """Strategy emits nothing after close."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))
    assert s.authorize_trading() is True
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
    s = VerifyStrategy("verify", {"warmup_bars": 1, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    assert s.authorize_trading() is True
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1

    s.mark_signal_rejected("risk denied")
    assert s.snapshot()["state"] == "error"
    assert s.snapshot()["last_reject_reason"] == "risk denied"
    s.on_bar(_bar(3132))
    assert len(s.signals) == 1
