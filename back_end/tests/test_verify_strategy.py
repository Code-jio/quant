from datetime import datetime, timedelta

import pandas as pd
from src.strategy import Direction, Trade
from src.strategy.strategies.verify import VerifyStrategy
from src.trading.types import MarketData


def _bar(close, symbol="rb2610"):
    return pd.Series({
        "symbol": symbol, "datetime": pd.Timestamp.now(),
        "open": close - 2, "high": close + 2, "low": close - 3, "close": close, "volume": 100,
    })


def _trade(direction, price=3130, symbol="rb2610"):
    suffix = direction.value if hasattr(direction, "value") else str(direction)
    return Trade(
        trade_id=f"TRADE_{suffix}",
        order_id=f"ORDER_{suffix}",
        symbol=symbol,
        direction=direction,
        price=price,
        volume=1,
    )


def _tick(price=3130, symbol="rb2610", ts=None):
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
    assert s.signals[0].price == 3132
    assert s.snapshot()["state"] == "entry_pending"
    assert s.snapshot()["last_order_pricing_source"] == "ask_price_1+1ticks"


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
    assert s.signals[0].price == 3131
    assert s._entry_order_sent is True
    assert s._bought is False
    assert s.snapshot()["state"] == "entry_pending"

    s.on_trade(_trade(Direction.LONG, 3130))
    assert s._bought is True
    assert s.snapshot()["state"] == "holding"


def test_auto_arm_sends_entry_on_warmup_bar():
    """Auto-arm mode emits the verification entry as soon as warmup completes."""
    s = VerifyStrategy("verify", {"warmup_bars": 1, "hold_bars": 3, "volume": 1, "auto_arm": True})
    s.on_init()

    s.on_bar(_bar(3130))

    assert len(s.signals) == 1
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].price == 3131
    assert s.trade_authorized is True
    assert s.ready_to_arm is True
    assert s.snapshot()["state"] == "entry_pending"
    assert s.snapshot()["auto_arm"] is True


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
    assert s.signals[1].price == 3134
    assert s._closed is False
    assert s.completed is False
    assert s.snapshot()["state"] == "closing"

    s.on_trade(_trade(Direction.SHORT, 3135))
    assert s._closed is True
    assert s.completed is True
    assert s.snapshot()["state"] == "completed"


def test_trade_callback_accepts_vt_symbol_variant():
    """A fill with exchange-qualified symbol still advances the verification state."""
    s = VerifyStrategy("verify", {"symbol": "rb2610", "warmup_bars": 1, "hold_bars": 3, "volume": 1, "auto_arm": True})
    s.on_init()

    s.on_bar(_bar(812, symbol="SHFE.rb2610"))
    assert s.snapshot()["state"] == "entry_pending"

    s.on_trade(_trade(Direction.LONG, 812, symbol="rb2610.SHFE"))

    assert s._bought is True
    assert s.snapshot()["state"] == "holding"


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


def test_chase_fallback_to_market_after_max_attempts():
    """After max chase attempts, fallback sends a market order instead of giving up."""
    s = VerifyStrategy("verify", {
        "warmup_bars": 1, "hold_bars": 3, "volume": 1, "auto_arm": True,
        "order_type": "limit", "chase_enabled": True,
        "chase_max_attempts": 2, "chase_interval_seconds": 2,
        "chase_fallback_to_market": True,
    })
    s.on_init()
    # Auto-arm fires entry
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s.signals[0].order_type.value == "limit"
    assert s._chase_attempts == 0
    # Simulate engine notifying the strategy that the order was submitted
    s.on_signal_submitted(s.signals[0], "ORDER_1")

    # ── Attempt 1: unfilled for >2s → cancel ──
    a1 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=3)))
    assert a1.get("action") == "cancel", f"Expected cancel, got {a1}"
    s._chase_pending_cancel_order_id = ""
    s._chase_resubmit_ready = True
    s._entry_order_sent = False
    assert s._chase_attempts == 1

    # ── Attempt 2: resubmit with extra tick ──
    a2 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=4)))
    assert a2.get("action") == "submit", f"Expected submit, got {a2}"
    assert a2["signal"].order_type.value == "limit"
    # _entry_order_sent is True again after _create_chase_signal

    # ── Attempt 2 unfilled → cancel (now attempts == max == 2) ──
    a3 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=7)))
    assert a3.get("action") == "cancel", f"Expected cancel, got {a3}"
    s._chase_pending_cancel_order_id = ""
    s._chase_resubmit_ready = True
    s._entry_order_sent = False
    assert s._chase_attempts == 2

    # ── Max attempts reached → fallback to market ──
    a4 = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=8)))
    assert a4.get("action") == "submit", f"Expected market submit, got {a4}"
    signal = a4["signal"]
    assert signal.order_type.value == "market"


def test_chase_no_fallback_when_disabled():
    """When fallback is disabled, max attempts just stops chasing."""
    s = VerifyStrategy("verify", {
        "warmup_bars": 1, "hold_bars": 3, "volume": 1, "auto_arm": True,
        "order_type": "limit", "chase_enabled": True,
        "chase_max_attempts": 1, "chase_interval_seconds": 2,
        "chase_fallback_to_market": False,
    })
    s.on_init()
    s.on_bar(_bar(3130))
    assert s._chase_attempts == 0
    s.on_signal_submitted(s.signals[0], "ORDER_1")

    # One chase
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=3)))
    assert action["action"] == "cancel"
    s._chase_pending_cancel_order_id = ""
    s._chase_resubmit_ready = True
    s._entry_order_sent = False

    # After cancel, resubmit should send a limit order (not market)
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=4)))
    assert action["action"] == "submit"
    assert action["signal"].order_type.value == "limit"
    s._chase_resubmit_ready = False

    # Now max reached, next tick should give up (no fallback)
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=7)))
    assert action == {}
    assert s._last_chase_reason == "max_attempts_reached"


def test_verify_strategy_defaults_to_five_chase_attempts():
    from src.strategy import create_strategy

    strategy = create_strategy("verify", {"symbol": "rb2510"})
    strategy.on_init()

    assert strategy.snapshot()["chase_max_attempts"] == 5
