from datetime import datetime, timedelta

import pandas as pd
import pytest
from src.strategy import Direction, Order, OrderStatus, OrderType, Trade
from src.strategy.strategies.verify import VerifyStrategy
from src.trading.types import MarketData


class MonotonicClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _bar(close, symbol="rb2610"):
    return pd.Series({
        "symbol": symbol, "datetime": pd.Timestamp.now(),
        "open": close - 2, "high": close + 2, "low": close - 3, "close": close, "volume": 100,
    })


def _trade(direction, price=3130, symbol="rb2610", order_id=None):
    suffix = direction.value if hasattr(direction, "value") else str(direction)
    return Trade(
        trade_id=f"TRADE_{suffix}",
        order_id=order_id or f"ORDER_{suffix}",
        symbol=symbol,
        direction=direction,
        price=price,
        volume=1,
    )


def _order(order_id, status, direction=Direction.LONG, symbol="rb2610"):
    return Order(
        order_id=order_id,
        symbol=symbol,
        direction=direction,
        order_type=OrderType.LIMIT,
        price=3130,
        volume=1,
        status=status,
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


def test_signal_submission_returns_role_attempt_parent_and_snapshot_ownership():
    s = VerifyStrategy("verify", {"warmup_bars": 1, "volume": 1, "auto_arm": True})
    s.on_init()
    s.on_bar(_bar(3130))

    metadata = s.on_signal_submitted(s.signals[0], "ORDER_1")

    assert metadata == {
        "role": "entry",
        "attempt": 0,
        "parent_order_id": "",
        "symbol": "rb2610",
        "volume": 1,
        "direction": "long",
        "offset": "open",
        "price": s.signals[0].price,
        "submitted_monotonic": s._last_order_submitted_monotonic,
    }
    snapshot = s.snapshot()
    assert snapshot["current_order_id"] == "ORDER_1"
    assert snapshot["entry_order_id"] == "ORDER_1"
    assert snapshot["close_order_id"] == ""
    assert snapshot["active_order_role"] == "entry"
    assert snapshot["order_ownership"]["ORDER_1"]["role"] == "entry"


def test_replacement_submission_metadata_points_to_cancelled_parent():
    s = VerifyStrategy(
        "verify",
        {
            "warmup_bars": 1,
            "volume": 1,
            "auto_arm": True,
            "chase_interval_seconds": 1,
        },
    )
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")
    s._last_order_submitted_monotonic -= 2

    action = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=2)))
    assert action["action"] == "cancel"
    s.on_order(_order("ORDER_1", OrderStatus.CANCELLED))
    replacement_action = s.next_chase_action(
        _tick(3133, ts=datetime.now() + timedelta(seconds=3))
    )
    replacement_signal = replacement_action["signal"]

    metadata = s.on_signal_submitted(replacement_signal, "ORDER_2")

    assert metadata["role"] == "entry"
    assert metadata["attempt"] == 1
    assert metadata["parent_order_id"] == "ORDER_1"
    assert s.snapshot()["entry_order_id"] == "ORDER_2"
    assert s.snapshot()["order_ownership"]["ORDER_2"]["parent_order_id"] == "ORDER_1"


def test_chase_age_uses_injected_monotonic_clock_not_exchange_timestamp():
    clock = MonotonicClock()
    s = VerifyStrategy("verify", {
        "warmup_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 2,
        "monotonic_clock": clock,
    })
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")

    assert s.next_chase_action(_tick(3131, ts=datetime(1970, 1, 1)), now_monotonic=clock()) == {}
    clock.advance(2.1)
    assert s.next_chase_action(_tick(3131, ts=datetime(1970, 1, 1)), now_monotonic=clock()) == {
        "action": "cancel",
        "order_id": "ORDER_1",
    }


def test_unrelated_order_and_trade_callbacks_do_not_change_verify_state():
    s = VerifyStrategy("verify", {"warmup_bars": 1, "volume": 1, "auto_arm": True})
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")
    before = s.snapshot()

    s.on_order(_order("EXTERNAL", OrderStatus.FILLED))
    s.on_trade(_trade(Direction.LONG, order_id="EXTERNAL"))

    after = s.snapshot()
    assert after["state"] == before["state"]
    assert after["current_order_id"] == before["current_order_id"]
    assert s._bought is False


def test_owned_cancelled_entry_can_fill_late_and_enter_holding():
    s = VerifyStrategy("verify", {"warmup_bars": 1, "volume": 1, "auto_arm": True})
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")
    s.on_order(_order("ORDER_1", OrderStatus.CANCELLED))

    s.on_trade(_trade(Direction.LONG, order_id="ORDER_1"))

    assert s._bought is True
    assert s.snapshot()["state"] == "holding"


@pytest.mark.parametrize("status", [OrderStatus.REJECTED, OrderStatus.CANCELLED])
def test_terminal_entry_order_clears_strategy_current_order(status):
    s = VerifyStrategy("verify", {"warmup_bars": 1, "volume": 1, "auto_arm": True})
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")

    s.on_order(_order("ORDER_1", status))

    assert s.snapshot()["current_order_id"] == ""
    assert s.snapshot()["active_order_role"] == ""
    assert s.snapshot()["state"] == "error"


def test_owned_trade_with_wrong_direction_does_not_mutate_strategy():
    s = VerifyStrategy("verify", {"warmup_bars": 1, "volume": 1, "auto_arm": True})
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")

    s.on_trade(_trade(Direction.SHORT, order_id="ORDER_1"))

    assert s._bought is False
    assert s.completed is False
    assert s.snapshot()["order_ownership"]["ORDER_1"]["status"] == "submitting"


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

    s.on_signal_submitted(s.signals[0], "ORDER_long")
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
    s.on_signal_submitted(s.signals[0], "ORDER_long")
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

    s.on_signal_submitted(s.signals[1], "ORDER_short")
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

    s.on_signal_submitted(s.signals[0], "ORDER_long")
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
    s.on_signal_submitted(s.signals[0], "ORDER_long")
    s.on_trade(_trade(Direction.LONG, 3131))
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3135))
    count_after_sell = len(s.signals)
    assert count_after_sell == 2
    s.on_signal_submitted(s.signals[1], "ORDER_short")
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
    s.set_market_order_supported(True)
    # Auto-arm fires entry
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s.signals[0].order_type.value == "limit"
    assert s._chase_attempts == 0
    # Simulate engine notifying the strategy that the order was submitted
    s.on_signal_submitted(s.signals[0], "ORDER_1")
    s._last_order_submitted_monotonic -= 3

    # ── Attempt 1: unfilled for >2s → cancel ──
    a1 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=3)))
    assert a1.get("action") == "cancel", f"Expected cancel, got {a1}"
    s.on_order(_order("ORDER_1", OrderStatus.CANCELLED))
    assert s._chase_attempts == 1

    # ── Attempt 2: resubmit with extra tick ──
    a2 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=4)))
    assert a2.get("action") == "submit", f"Expected submit, got {a2}"
    assert a2["signal"].order_type.value == "limit"
    s.on_signal_submitted(a2["signal"], "ORDER_2")
    s._last_order_submitted_monotonic -= 3
    # _entry_order_sent is True again after _create_chase_signal

    # ── Attempt 2 unfilled → cancel (now attempts == max == 2) ──
    a3 = s.next_chase_action(_tick(3132, ts=datetime.now() + timedelta(seconds=7)))
    assert a3.get("action") == "cancel", f"Expected cancel, got {a3}"
    assert s._chase_attempts == 2

    # ── Max attempts reached → fallback to market ──
    s.on_order(_order("ORDER_2", OrderStatus.CANCELLED))
    a4 = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=8)))
    assert a4.get("action") == "submit", f"Expected market submit, got {a4}"
    assert a4["signal"].order_type.value == "market"
    s.on_signal_submitted(a4["signal"], "ORDER_3")
    s._last_order_submitted_monotonic -= 3
    final_cancel = s.next_chase_action(_tick(3135))
    assert final_cancel == {"action": "cancel", "order_id": "ORDER_3"}
    s.on_order(_order("ORDER_3", OrderStatus.CANCELLED))
    assert s.snapshot()["chase_state"] == "exhausted"
    assert s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=20))) == {}


def test_final_chase_replacement_is_aggressive_limit_when_market_is_unsupported():
    s = VerifyStrategy("verify", {
        "warmup_bars": 1, "volume": 1, "auto_arm": True,
        "chase_max_attempts": 1, "chase_interval_seconds": 2,
        "chase_fallback_to_market": True,
    })
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ORDER_1")
    s._last_order_submitted_monotonic -= 3
    action = s.next_chase_action(_tick(3132))
    assert action["action"] == "cancel"
    s.on_order(_order("ORDER_1", OrderStatus.CANCELLED))
    replacement = s.next_chase_action(_tick(3132))
    assert replacement["action"] == "submit"
    assert replacement["signal"].order_type.value == "limit"


def test_entry_chain_stops_after_five_replacements_and_close_chain_starts_at_zero():
    clock = MonotonicClock()
    s = VerifyStrategy("verify", {
        "warmup_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_max_attempts": 5,
        "chase_interval_seconds": 2,
        "monotonic_clock": clock,
    })
    s.on_init()
    s.on_bar(_bar(3130))
    s.on_signal_submitted(s.signals[0], "ENTRY_0")
    current_id = "ENTRY_0"

    for attempt in range(1, 6):
        clock.advance(2.1)
        cancel = s.next_chase_action(_tick(3130), now_monotonic=clock())
        assert cancel == {"action": "cancel", "order_id": current_id}
        s.on_order(_order(current_id, OrderStatus.CANCELLED))
        submit = s.next_chase_action(_tick(3130), now_monotonic=clock())
        assert submit["action"] == "submit"
        next_id = f"ENTRY_{attempt}"
        metadata = s.on_signal_submitted(submit["signal"], next_id)
        assert metadata["attempt"] == attempt
        assert metadata["parent_order_id"] == current_id
        current_id = next_id

    clock.advance(2.1)
    final_cancel = s.next_chase_action(_tick(3130), now_monotonic=clock())
    assert final_cancel == {"action": "cancel", "order_id": current_id}
    s.on_order(_order(current_id, OrderStatus.CANCELLED))
    assert s.snapshot()["chase_state"] == "exhausted"

    close_strategy = VerifyStrategy("verify", {
        "warmup_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "hold_bars": 1,
    })
    close_strategy.on_init()
    close_strategy.on_bar(_bar(3130))
    close_strategy.on_signal_submitted(close_strategy.signals[0], "REAL_ENTRY")
    close_strategy.on_trade(_trade(Direction.LONG, order_id="REAL_ENTRY"))
    close_strategy.on_bar(_bar(3131))
    close_metadata = close_strategy.on_signal_submitted(close_strategy.signals[-1], "REAL_CLOSE")
    assert close_metadata["role"] == "exit"
    assert close_metadata["attempt"] == 0


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
    s._last_order_submitted_monotonic -= 3

    # One chase
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=3)))
    assert action["action"] == "cancel"
    s.on_order(_order("ORDER_1", OrderStatus.CANCELLED))

    # After cancel, resubmit should send a limit order (not market)
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=4)))
    assert action["action"] == "submit"
    assert action["signal"].order_type.value == "limit"
    s.on_signal_submitted(action["signal"], "ORDER_2")

    # The final replacement is cancelled after its timeout, then the chain is exhausted.
    s._last_order_submitted_monotonic -= 3
    action = s.next_chase_action(_tick(3135, ts=datetime.now() + timedelta(seconds=7)))
    assert action == {"action": "cancel", "order_id": "ORDER_2"}
    s.on_order(_order("ORDER_2", OrderStatus.CANCELLED))
    assert s.snapshot()["chase_state"] == "exhausted"
    assert s._last_chase_reason == "max_attempts_exhausted"


def test_verify_strategy_defaults_to_five_chase_attempts():
    from src.strategy import create_strategy

    strategy = create_strategy("verify", {"symbol": "rb2510"})
    strategy.on_init()

    assert strategy.snapshot()["chase_max_attempts"] == 5
