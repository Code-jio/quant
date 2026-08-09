import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from src.strategy import Direction, OffsetFlag, Order, OrderStatus, OrderType, Position, Signal, StrategyBase, Trade
from src.strategy.strategies.verify import VerifyStrategy
from src.trading import TradingEngine
from src.trading.order_manager import OrderManager, PreOrder, PreOrderType
from src.trading.types import MarketData
from src.trading.trial_run_execution import (
    TrialRunExecutionError,
    TrialRunExecutionState,
    TrialRunOutcome,
)

from tests.helpers import RecordingGateway


class MonotonicClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class LiveDataSignalStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self._initialized = True

    def on_bar(self, bar: pd.Series):
        data = self.get_data(self.symbol)
        if data is None or len(data) < 2 or self.signals:
            return
        self.buy(self.symbol, float(bar["close"]), 1, OrderType.LIMIT)


class LiveBiasProbeStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self.observed_lengths = []
        self.current_seen = []

    def on_bar(self, bar: pd.Series):
        data = self.get_data(self.symbol)
        self.observed_lengths.append(0 if data is None else len(data))
        self.current_seen.append(False if data is None else self.current_date in data.index)


class RejectedSignalStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self.reject_reason = ""
        self._initialized = True

    def on_bar(self, bar: pd.Series):
        pass

    def mark_signal_rejected(self, reason: str = ""):
        self.reject_reason = reason


class SynchronousFillGateway(RecordingGateway):
    """Emits broker callbacks before send_order returns the broker order ID."""

    def send_order(self, signal):
        self.sent_signals.append(signal)
        order_id = f"ORDER_{len(self.sent_signals)}"
        self.on_order(Order(
            order_id=order_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            status=OrderStatus.SUBMITTED,
            offset=signal.offset,
        ))
        self.on_order(Order(
            order_id=order_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            traded_volume=signal.volume,
            status=OrderStatus.FILLED,
            offset=signal.offset,
        ))
        self.on_trade(Trade(
            trade_id="SYNC-T1",
            order_id=order_id,
            symbol=signal.symbol,
            direction=signal.direction,
            price=signal.price,
            volume=signal.volume,
        ))
        return order_id


class BlockingSendGateway(RecordingGateway):
    def __init__(self):
        super().__init__()
        self.first_send_entered = threading.Event()
        self.release_first_send = threading.Event()

    def send_order(self, signal):
        if not self.sent_signals:
            self.first_send_entered.set()
            self.release_first_send.wait(timeout=2)
        return super().send_order(signal)


def make_tick(symbol: str, price: float, timestamp: datetime) -> MarketData:
    return MarketData(
        symbol=symbol,
        last_price=price,
        bid_price_1=price - 1,
        ask_price_1=price + 1,
        bid_volume_1=10,
        ask_volume_1=10,
        volume=100,
        turnover=price * 100,
        timestamp=timestamp,
    )


def test_live_ticks_update_strategy_data_and_dispatch_new_signals_once():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveDataSignalStrategy("live_test", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    start = datetime(2026, 4, 29, 9, 30)
    # Bar 1 (9:30): ticks within same bar — no on_bar call yet
    engine.on_tick(make_tick("rb2505", 3800.0, start))
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=15)))
    assert list(strategy.data) == []
    assert len(strategy.signals) == 0

    # Cross into bar 2 (9:31) — bar 1 completes, on_bar fires (len=1, no signal yet)
    engine.on_tick(make_tick("rb2505", 3815.0, start + timedelta(minutes=1)))
    assert len(strategy.data["rb2505"]) == 1
    assert len(strategy.signals) == 0  # strategy wants len(data) >= 2

    # Cross into bar 3 (9:32) — bar 2 completes, on_bar fires (len=2, signal!)
    engine.on_tick(make_tick("rb2505", 3820.0, start + timedelta(minutes=2)))

    assert list(strategy.data) == ["rb2505"]
    assert len(strategy.data["rb2505"]) == 2  # two completed bars
    assert len(strategy.signals) == 1
    assert len(gateway.sent_signals) == 1
    assert gateway.sent_signals[0].symbol == "rb2505"
    assert gateway.sent_signals[0].price == 3815.0  # bar 2 close
    assert gateway.orders["ORDER_1"].status == OrderStatus.SUBMITTING


def test_live_on_bar_sees_only_prior_ticks_in_strategy_data():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveBiasProbeStrategy("live_bias", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    start = datetime(2026, 4, 29, 9, 30)
    # Bar 1: 2 ticks within same interval, no on_bar yet
    engine.on_tick(make_tick("rb2505", 3800.0, start))
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=1)))
    assert strategy.observed_lengths == []  # on_bar not called yet

    # Cross into bar 2 — bar 1 completes, on_bar called with 1 bar in data
    engine.on_tick(make_tick("rb2505", 3815.0, start + timedelta(minutes=1)))
    assert strategy.observed_lengths == [1]
    assert strategy.current_seen == [True]

    # Cross into bar 3 — bar 2 completes, on_bar called with 2 bars in data
    engine.on_tick(make_tick("rb2505", 3820.0, start + timedelta(minutes=2)))
    assert strategy.observed_lengths == [1, 2]
    assert strategy.current_seen == [True, True]


def test_verify_strategy_can_warm_up_from_first_tick():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "hold_bars": 3,
        "volume": 1,
        "auto_arm": True,
        "order_type": "limit",
    })
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True

    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    assert strategy.snapshot()["bar_count"] == 1
    assert strategy.snapshot()["state"] == "entry_pending"
    assert len(strategy.signals) == 1
    assert len(gateway.sent_signals) == 1
    assert gateway.sent_signals[0].symbol == "rb2505"
    assert gateway.sent_signals[0].price == 3802.0


def test_trial_execution_binding_records_owned_order_callbacks_and_ignores_external():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "order_type": "limit",
    })
    execution = TrialRunExecutionState(run_id="RUN-ENGINE", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)

    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    assert execution.current_order_id == "ORDER_1"
    assert execution.order_chain[0].status == "submitting"

    order = gateway.orders["ORDER_1"]
    order.status = OrderStatus.SUBMITTED
    engine._on_order(order)
    assert execution.order_chain[0].status == "submitted"

    external = Order(
        order_id="EXTERNAL",
        symbol="rb2505",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        price=3800,
        volume=1,
        status=OrderStatus.FILLED,
    )
    engine._on_order(external)
    engine._on_trade(Trade(
        trade_id="EXTERNAL-TRADE",
        order_id="EXTERNAL",
        symbol="rb2505",
        direction=Direction.LONG,
        price=3800,
        volume=1,
    ))
    assert len(execution.order_chain) == 1
    assert execution.real_trade_ids == []
    assert strategy._bought is False


def test_synchronous_broker_callbacks_are_replayed_after_order_chain_registration():
    gateway = SynchronousFillGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-SYNC", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True

    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    assert execution.order_chain[0].order_id == "ORDER_1"
    assert execution.order_chain[0].status == "filled"
    assert execution.real_trade_ids == ["SYNC-T1"]
    assert execution.current_order_id == ""
    assert strategy._bought is True
    assert gateway.orders["ORDER_1"].status is OrderStatus.FILLED


def test_trial_submission_registration_failure_cancels_broker_order_and_fails_run(monkeypatch):
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-FAIL", symbol="rb2505", volume=1)
    monkeypatch.setattr(
        execution,
        "record_real_submission",
        lambda **kwargs: (_ for _ in ()).throw(
            TrialRunExecutionError("invalid_trial_transition")
        ),
    )
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True

    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert execution.outcome is TrialRunOutcome.FAILED
    assert execution.failure_code == "order_chain_registration_failed"
    assert engine.unbind_trial_run_execution() is False

    gateway.on_order(gateway.orders["ORDER_1"])
    assert engine.unbind_trial_run_execution() is True


def test_trial_shutdown_flag_blocks_a_strategy_submission_before_unbind():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-STOP", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0}) is True
    engine.order_manager.stop()
    signal = Signal(
        symbol="rb2505",
        datetime=datetime.now(),
        direction=Direction.LONG,
        price=3800.0,
        volume=1,
        order_type=OrderType.LIMIT,
        offset=OffsetFlag.OPEN,
        comment="buy_open",
    )

    assert engine.begin_trial_run_shutdown(strategy) is True
    assert engine._submit_strategy_signal(signal) == ""
    assert gateway.sent_signals == []


def test_concurrent_open_submissions_cannot_bypass_rate_capacity():
    gateway = BlockingSendGateway()
    engine = TradingEngine(gateway)
    assert engine.start({
        "initial_capital": 100000.0,
        "risk": {"max_orders_per_minute": 2},
    }) is True
    engine.order_manager.stop()
    signals = [
        Signal(
            symbol="rb2505",
            datetime=datetime.now(),
            direction=Direction.LONG,
            price=3800.0 + index,
            volume=1,
            order_type=OrderType.LIMIT,
            offset=OffsetFlag.OPEN,
        )
        for index in range(2)
    ]
    results = []
    threads = [threading.Thread(target=lambda item=item: results.append(engine.send_signal(item))) for item in signals]

    threads[0].start()
    assert gateway.first_send_entered.wait(timeout=1)
    threads[1].start()
    time.sleep(0.05)
    gateway.release_first_send.set()
    for thread in threads:
        thread.join(timeout=2)

    assert len([order_id for order_id in results if order_id]) == 1
    assert len(gateway.sent_signals) == 1


def test_order_manager_refuses_restart_until_blocked_timer_thread_exits():
    manager = OrderManager(RecordingGateway())
    manager._monitor_join_timeout_seconds = 0.01
    entered = threading.Event()
    release = threading.Event()

    def blocked_timer(_now):
        entered.set()
        release.wait(timeout=2)

    manager.on_timer_callback = blocked_timer
    assert manager.start() is True
    assert entered.wait(timeout=1)
    assert manager.stop() is False
    assert manager.start() is False

    release.set()
    deadline = time.time() + 1
    while manager._monitor_thread is not None and manager._monitor_thread.is_alive() and time.time() < deadline:
        time.sleep(0.01)
    assert manager.start() is True
    assert manager.stop() is True


def test_generic_engine_stop_preserves_and_cancels_an_active_trial_order():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-GENERIC-STOP", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    assert engine.stop() is False
    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert engine.trial_run_execution is execution
    assert gateway.status.value != "stopped"


def test_generic_stop_marks_stopping_before_waiting_for_strategy_lock():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {"symbol": "rb2505", "volume": 1})
    execution = TrialRunExecutionState(run_id="RUN-ASYNC-STOP", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0}) is True
    lock_acquired = threading.Event()
    release_strategy_lock = threading.Event()

    def hold_strategy_lock():
        with engine._strategy_callback_lock:
            lock_acquired.set()
            release_strategy_lock.wait(timeout=2)

    holder = threading.Thread(target=hold_strategy_lock)
    holder.start()
    assert lock_acquired.wait(timeout=1)
    with engine._trial_submission_lock:
        engine._trial_submission_in_flight = True

    stop_results = []
    stopper = threading.Thread(target=lambda: stop_results.append(engine.stop()))
    stopper.start()
    deadline = time.time() + 1
    while not engine._trial_run_stopping and time.time() < deadline:
        time.sleep(0.01)
    assert engine._trial_run_stopping is True

    execution.record_real_submission(
        order_id="ORDER_ASYNC",
        role="entry",
        price=3800.0,
        direction=Direction.LONG,
        offset=OffsetFlag.OPEN,
        attempt=0,
        symbol="rb2505",
        volume=1,
    )
    engine._finish_trial_submission()
    assert gateway.cancelled_order_ids == ["ORDER_ASYNC"]

    release_strategy_lock.set()
    holder.join(timeout=2)
    stopper.join(timeout=2)
    assert stop_results == [False]


def test_binding_a_new_execution_cannot_overwrite_an_existing_run():
    engine = TradingEngine(RecordingGateway())
    strategy = VerifyStrategy("verify", {"symbol": "rb2505", "volume": 1})
    first = TrialRunExecutionState(run_id="RUN-FIRST", symbol="rb2505", volume=1)
    second = TrialRunExecutionState(run_id="RUN-SECOND", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(first)

    try:
        engine.bind_trial_run_execution(second)
    except RuntimeError as exc:
        assert str(exc) == "trial_execution_already_bound"
    else:
        raise AssertionError("second execution unexpectedly replaced the first")
    assert engine.trial_run_execution is first


def test_triggered_pre_order_uses_the_engine_risk_gate():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    assert engine.start({
        "initial_capital": 100000.0,
        "risk": {"max_order_volume": 1},
    }) is True
    engine.order_manager.stop()
    pre_order = PreOrder(
        type=PreOrderType.STOP_ENTRY,
        symbol="rb2505",
        direction=Direction.LONG,
        volume=2,
        trigger_price=3800.0,
        exec_price=3801.0,
        order_type=OrderType.LIMIT,
    )
    engine.place_pre_order(pre_order)

    engine.update_market_data("rb2505", {"last_price": 3801.0})

    assert gateway.sent_signals == []
    assert pre_order.related_order_id == ""
    assert "volume" in engine.last_reject_reason.lower()


def test_standalone_order_manager_pre_order_fails_closed_without_submission_callback():
    gateway = RecordingGateway()
    manager = OrderManager(gateway)
    pre_order = PreOrder(
        type=PreOrderType.STOP_ENTRY,
        symbol="rb2505",
        direction=Direction.LONG,
        volume=1,
        trigger_price=3800.0,
        exec_price=3801.0,
        order_type=OrderType.LIMIT,
    )
    manager.place_pre_order(pre_order)

    manager.update_market_data("rb2505", {"last_price": 3801.0})

    assert gateway.sent_signals == []
    assert pre_order.related_order_id == ""


def test_owned_trade_evidence_conflict_cancels_current_order_and_fails_run():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-CONFLICT", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    engine._on_trade(Trade(
        trade_id="BAD-T1",
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.SHORT,
        price=3800,
        volume=1,
    ))

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert execution.outcome is TrialRunOutcome.FAILED
    assert execution.failure_code == "trade_evidence_conflict"
    assert execution.real_trade_ids == []
    assert strategy._bought is False


def test_owned_order_evidence_conflict_cancels_current_order_and_fails_run():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
    })
    execution = TrialRunExecutionState(run_id="RUN-ORDER-CONFLICT", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    engine._on_order(Order(
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.SHORT,
        order_type=OrderType.LIMIT,
        price=3800,
        volume=1,
        status=OrderStatus.SUBMITTED,
    ))

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert execution.outcome is TrialRunOutcome.FAILED
    assert execution.failure_code == "order_evidence_conflict"
    assert execution.order_chain[0].status == "submitting"
    assert strategy.snapshot()["state"] == "error"


def test_late_fill_on_superseded_order_cancels_current_replacement():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 1,
    })
    execution = TrialRunExecutionState(run_id="RUN-LATE", symbol="rb2505", volume=1)
    engine.set_strategy(strategy)
    engine.bind_trial_run_execution(execution)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))

    chase_tick = make_tick("rb2505", 3802.0, datetime.now() + timedelta(seconds=2))
    strategy._last_order_submitted_monotonic -= 2
    engine._maybe_chase_strategy_order(chase_tick)
    gateway.on_order(gateway.orders["ORDER_1"])
    engine._maybe_chase_strategy_order(
        make_tick("rb2505", 3803.0, chase_tick.timestamp + timedelta(seconds=1))
    )
    assert execution.current_order_id == "ORDER_2"

    engine._on_trade(Trade(
        trade_id="LATE-T1",
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.LONG,
        price=3800,
        volume=1,
    ))
    gateway.on_order(gateway.orders["ORDER_2"])

    assert gateway.cancelled_order_ids == ["ORDER_1", "ORDER_2"]
    assert execution.order_chain[0].status == "filled"
    assert execution.order_chain[1].status == "cancelled"
    assert strategy._bought is True


def test_timer_chases_without_a_new_tick_and_waits_for_broker_cancel_ack():
    clock = MonotonicClock()
    gateway = RecordingGateway()
    engine = TradingEngine(gateway, monotonic_clock=clock)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 2,
    })
    engine.set_strategy(strategy)
    assert engine.start({
        "initial_capital": 100000.0,
        "emit_first_tick_bar": True,
        "risk": {"allowed_symbols": ["rb2505"], "max_orders_per_minute": 5},
    }) is True

    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))
    assert strategy.snapshot()["current_order_id"] == "ORDER_1"

    clock.advance(2.1)
    engine.on_timer(clock())

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert strategy.snapshot()["chase_state"] == "cancel_pending"
    assert strategy.snapshot()["current_order_id"] == "ORDER_1"

    # A quote received before the broker confirms cancellation is not eligible
    # for the replacement order.
    engine.on_tick(make_tick("rb2505", 3800.5, datetime.now()))
    assert len(gateway.sent_signals) == 1

    gateway.on_order(gateway.orders["ORDER_1"])
    assert strategy.snapshot()["chase_state"] == "cancelled"
    engine.on_timer(clock())
    assert len(gateway.sent_signals) == 1
    assert strategy.snapshot()["chase_state"] == "waiting_fresh_quote"

    engine.on_tick(make_tick("rb2505", 3801.0, datetime.now()))
    assert len(gateway.sent_signals) == 2
    assert strategy.snapshot()["chase_state"] == "resubmit_pending"
    engine.stop()


def test_cancelled_close_replacement_can_use_the_reserved_final_rate_slot():
    clock = MonotonicClock()
    gateway = RecordingGateway()
    engine = TradingEngine(gateway, monotonic_clock=clock)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "hold_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 2,
    })
    engine.set_strategy(strategy)
    assert engine.start({
        "initial_capital": 100000.0,
        "emit_first_tick_bar": True,
        "risk": {"allowed_symbols": ["rb2505"], "max_orders_per_minute": 5},
    }) is True
    engine.order_manager.stop()

    initial_tick = make_tick("rb2505", 3800.0, datetime.now())
    engine.on_tick(initial_tick)
    gateway.positions["rb2505"] = Position(
        symbol="rb2505",
        direction=Direction.NET,
        volume=1,
        price=3800.0,
    )
    gateway.on_trade(Trade(
        trade_id="ENTRY-T1",
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.LONG,
        price=3800.0,
        volume=1,
    ))

    strategy.on_bar(pd.Series({
        "symbol": "rb2505",
        "datetime": datetime.now(),
        "open": 3801.0,
        "high": 3801.0,
        "low": 3801.0,
        "close": 3801.0,
        "volume": 1,
    }))
    engine._dispatch_strategy_signals()
    assert len(gateway.sent_signals) == 2
    assert gateway.sent_signals[-1].offset == OffsetFlag.CLOSE

    # Four submissions are already counted, leaving the slot reserved for the
    # close replacement itself.
    engine.risk_manager.record_order(gateway.sent_signals[-1])
    engine.risk_manager.record_order(gateway.sent_signals[-1])
    assert engine.risk_manager.order_rate_snapshot()["remaining"] == 1

    clock.advance(2.1)
    engine.on_timer(clock())
    assert gateway.cancelled_order_ids[-1] == "ORDER_2"
    gateway.on_order(gateway.orders["ORDER_2"])

    engine.on_tick(make_tick("rb2505", 3802.0, datetime.now()))
    assert len(gateway.sent_signals) == 3
    assert gateway.sent_signals[-1].offset == OffsetFlag.CLOSE
    assert engine.risk_manager.order_rate_snapshot()["remaining"] == 0


def test_non_target_tick_does_not_replace_timer_chase_quote():
    clock = MonotonicClock()
    gateway = RecordingGateway()
    engine = TradingEngine(gateway, monotonic_clock=clock)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 2,
    })
    engine.set_strategy(strategy)
    assert engine.start({
        "initial_capital": 100000.0,
        "emit_first_tick_bar": True,
        "risk": {"allowed_symbols": ["rb2505"], "max_orders_per_minute": 5},
    }) is True

    target_tick = make_tick("rb2505", 3800.0, datetime.now())
    engine.on_tick(target_tick)
    engine.on_tick(make_tick("au2606", 800.0, datetime.now()))
    clock.advance(2.1)
    engine.on_timer(clock())

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert engine._last_strategy_tick is target_tick


def test_failed_cancel_stops_automatic_resubmission():
    clock = MonotonicClock()
    gateway = RecordingGateway()
    gateway.cancel_order = lambda order_id: False
    engine = TradingEngine(gateway, monotonic_clock=clock)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "volume": 1,
        "auto_arm": True,
        "chase_interval_seconds": 2,
    })
    engine.set_strategy(strategy)
    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("rb2505", 3800.0, datetime.now()))
    clock.advance(2.1)
    engine.on_timer(clock())
    assert strategy.snapshot()["chase_state"] == "cancel_failed"
    assert len(gateway.sent_signals) == 1


def test_verify_strategy_first_tick_accepts_vt_symbol_variants():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "au2606",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "hold_bars": 3,
        "volume": 1,
        "auto_arm": True,
        "order_type": "limit",
    })
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True

    engine.on_tick(make_tick("SHFE.au2606", 812.0, datetime.now()))

    snapshot = strategy.snapshot()
    assert snapshot["tick_count"] == 1
    assert snapshot["bar_count"] == 1
    assert snapshot["state"] == "entry_pending"
    assert len(gateway.sent_signals) == 1
    assert gateway.sent_signals[0].symbol == "au2606"
    assert gateway.sent_signals[0].price == 814.0


def test_verify_strategy_rejects_same_contract_on_wrong_exchange():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "au2606",
        "warmup_bars": 1,
        "readiness_bars": 1,
        "hold_bars": 3,
        "volume": 1,
        "auto_arm": True,
    })
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0, "emit_first_tick_bar": True}) is True
    engine.on_tick(make_tick("DCE.au2606", 812.0, datetime.now()))

    assert strategy.snapshot()["tick_count"] == 0
    assert strategy.snapshot()["bar_count"] == 0
    assert gateway.sent_signals == []


def test_stale_first_tick_never_emits_bar_or_order():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2610",
        "warmup_bars": 1,
        "hold_bars": 3,
        "volume": 1,
        "auto_arm": True,
        "order_type": "limit",
    })
    engine.set_strategy(strategy)

    risk_config = {"max_market_data_age_seconds": 5}
    assert engine.start({
        "initial_capital": 100000.0,
        "emit_first_tick_bar": True,
        "risk": risk_config,
    }) is True

    stale_ts = datetime.now() - timedelta(seconds=10)
    engine.on_tick(make_tick("rb2610", 3130.0, stale_ts))

    snapshot = strategy.snapshot()
    assert snapshot["tick_count"] == 0
    assert snapshot["bar_count"] == 0
    assert snapshot["state"] == "waiting_market_data"
    assert strategy.signals == []
    assert gateway.sent_signals == []

    engine.on_tick(make_tick("rb2610", 3140.0, datetime.now() - timedelta(seconds=8)))
    snapshot = strategy.snapshot()
    assert snapshot["tick_count"] == 0
    assert snapshot["bar_count"] == 0
    assert strategy.signals == []
    assert gateway.sent_signals == []


def test_broker_order_callback_updates_order_manager_books():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveDataSignalStrategy("live_test", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    order = Order(
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        price=3800,
        volume=1,
        status=OrderStatus.FILLED,
    )
    gateway.on_order(order)

    assert "ORDER_1" not in engine.order_manager.active_orders
    assert engine.order_manager.completed_orders["ORDER_1"].status == OrderStatus.FILLED


def test_strategy_is_notified_when_auto_signal_is_rejected_by_risk():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = RejectedSignalStrategy("reject_probe", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0, "allow_market_orders": False}) is True

    strategy.buy("rb2505", 0, 1, OrderType.MARKET)
    engine._dispatch_strategy_signals()

    assert gateway.sent_signals == []
    assert strategy.reject_reason == "Market orders are disabled by risk config"


def test_verify_strategy_uses_live_tick_for_readiness_and_entry_dispatch():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)

    from src.strategy.strategies.verify import VerifyStrategy

    strategy = VerifyStrategy("verify", {"symbol": "rb2505", "warmup_bars": 20, "readiness_bars": 1, "volume": 1})
    engine.set_strategy(strategy)

    assert engine.start({
        "initial_capital": 100000.0,
        "risk": {
            "allowed_symbols": ["rb2505"],
            "max_order_volume": 1,
            "max_position_volume": 1,
            "max_market_data_age_seconds": 30,
            "allow_market_orders": False,
        },
    }) is True

    start = datetime.now()
    engine.on_tick(make_tick("rb2505", 3800.0, start))

    assert strategy.snapshot()["state"] == "ready_to_start"
    assert strategy.snapshot()["market_ready"] is True
    assert strategy.snapshot()["bar_count"] == 0

    assert strategy.start_verification() is True
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=1)))

    assert len(strategy.signals) == 1
    assert len(gateway.sent_signals) == 1
    assert gateway.sent_signals[0].symbol == "rb2505"
    assert gateway.sent_signals[0].price == 3812.0


def test_verify_strategy_chases_unfilled_entry_after_cancel_ack():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = VerifyStrategy("verify", {
        "symbol": "rb2505",
        "warmup_bars": 20,
        "readiness_bars": 1,
        "volume": 1,
        "aggressive_ticks": 1,
        "chase_enabled": True,
        "chase_interval_seconds": 1,
        "chase_max_attempts": 3,
        "chase_step_ticks": 1,
    })
    engine.set_strategy(strategy)

    assert engine.start({
        "initial_capital": 100000.0,
        "risk": {
            "allowed_symbols": ["rb2505"],
            "max_order_volume": 1,
            "max_position_volume": 1,
            "max_active_orders": 2,
            "max_market_data_age_seconds": 30,
            "allow_market_orders": False,
        },
    }) is True

    start = datetime.now()
    engine.on_tick(make_tick("rb2505", 3800.0, start))
    assert strategy.start_verification() is True
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=1)))

    assert gateway.sent_signals[0].price == 3812.0
    assert gateway.cancelled_order_ids == []

    strategy._last_order_submitted_monotonic -= 2
    engine.on_tick(make_tick("rb2505", 3820.0, start + timedelta(seconds=3)))

    assert gateway.cancelled_order_ids == ["ORDER_1"]
    assert len(gateway.sent_signals) == 1
    assert strategy.snapshot()["chase_state"] == "cancel_pending"

    gateway.on_order(gateway.orders["ORDER_1"])
    assert strategy.snapshot()["chase_state"] == "cancelled"

    engine.on_tick(make_tick("rb2505", 3830.0, start + timedelta(seconds=4)))

    snapshot = strategy.snapshot()
    assert len(gateway.sent_signals) == 2
    assert gateway.sent_signals[1].price == 3833.0
    assert snapshot["chase_attempts"] == 1
    assert snapshot["last_chase_order_id"] == "ORDER_2"
    assert snapshot["last_chase_price"] == 3833.0
    assert snapshot["state"] == "entry_pending"
