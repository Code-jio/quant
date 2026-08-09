import pytest

from src.trading.trial_run_execution import (
    TrialRunExecutionError,
    TrialRunExecutionState,
    TrialRunOutcome,
    TrialRunTrack,
)


def _state(**kwargs):
    return TrialRunExecutionState(run_id="RUN-1", symbol="rb2610", volume=1, **kwargs)


def _evaluate(state, *, position=0, active=None, reconcile=True):
    return state.evaluate(
        broker_position_volume=position,
        broker_active_order_ids=[] if active is None else active,
        reconcile_ok=reconcile,
    )


def test_enums_and_real_order_attempt_are_typed():
    state = _state()

    assert TrialRunOutcome.RUNNING.value == "running"
    assert TrialRunOutcome.PASSED_REAL.value == "passed_real"
    assert TrialRunOutcome.PASSED_SIMULATED.value == "passed_simulated"
    assert TrialRunOutcome.FAILED.value == "failed"
    assert TrialRunOutcome.ABORTED.value == "aborted"
    assert TrialRunTrack.REAL.value == "real"
    assert TrialRunTrack.SIMULATED.value == "simulated"
    assert state.current_track is TrialRunTrack.REAL

    attempt = state.record_real_submission("R1", "entry", 3120)
    assert attempt.track is TrialRunTrack.REAL
    assert attempt.volume == 1
    assert attempt.symbol == "rb2610"
    assert state.current_order_id == "R1"


def test_passed_real_requires_real_entry_and_close_trade_proof():
    state = _state()
    state.record_real_submission("R-E", "entry", 3120)
    state.record_real_trade("R-E", "T-E", role="entry", price=3120)
    state.record_real_submission("R-C", "close", 3121, offset="close")
    state.record_real_trade("R-C", "T-C", role="close", price=3121)

    assert _evaluate(state).outcome is TrialRunOutcome.PASSED_REAL
    assert state.success_basis


@pytest.mark.parametrize(
    ("order_role", "callback_role"),
    [("entry", "close"), ("close", "entry")],
)
def test_real_trade_role_must_match_owned_order(order_role, callback_role):
    state = _state()
    order_id = "R-E" if order_role == "entry" else "R-C"
    state.record_real_submission(
        order_id,
        order_role,
        3120,
        offset="close" if order_role == "close" else "open",
    )

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_real_trade(order_id, "T-MISMATCH", role=callback_role, price=3120)

    assert exc_info.value.failure_code == "invalid_trial_transition"
    assert state.real_trade_ids == []
    assert state.order_chain[0].status == "submitted"


def test_swapped_real_trade_roles_cannot_produce_passed_real():
    state = _state()
    state.record_real_submission("R-E", "entry", 3120)
    state.record_real_submission("R-C", "close", 3121, offset="close")

    with pytest.raises(TrialRunExecutionError):
        state.record_real_trade("R-E", "T-E", role="close", price=3120)
    with pytest.raises(TrialRunExecutionError):
        state.record_real_trade("R-C", "T-C", role="entry", price=3121)

    assert _evaluate(state).outcome is TrialRunOutcome.RUNNING


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("entry_trade", "running"),
        ("close_trade", "running"),
        ("broker_position", "running"),
        ("active_order", "running"),
        ("reconcile", "running"),
    ],
)
def test_passed_real_does_not_overclaim_missing_condition(missing, expected):
    state = _state()
    state.record_real_submission("R-E", "entry", 3120)
    if missing != "entry_trade":
        state.record_real_trade("R-E", "T-E", role="entry", price=3120)
    state.record_real_submission("R-C", "close", 3121, offset="close")
    if missing != "close_trade":
        state.record_real_trade("R-C", "T-C", role="close", price=3121)

    result = _evaluate(
        state,
        position=1 if missing == "broker_position" else 0,
        active=["OTHER"] if missing == "active_order" else [],
        reconcile=missing != "reconcile",
    )
    assert result.outcome.value == expected


def test_passed_simulated_requires_real_submission_and_clean_broker_state():
    state = _state()
    state.record_real_submission("R1", "entry", 3120)
    state.record_real_cancel("R1")
    state.record_simulated_entry("SIM-E1", "SIM-T1", 3120)
    state.record_simulated_close("SIM-C1", "SIM-T2", 3121)

    result = _evaluate(state)

    assert result.outcome is TrialRunOutcome.PASSED_SIMULATED
    assert result.current_track is TrialRunTrack.SIMULATED
    assert result.simulated_position_volume == 0


def test_real_cancel_does_not_claim_simulation_ready_before_reconciliation():
    state = _state()
    state.record_real_submission("R1", "entry", 3120)

    state.record_real_cancel("R1")

    assert state.simulation_state == "real_cancelled"


def test_real_trade_callback_proves_broker_acceptance():
    state = _state()
    state.record_real_submission("R1", "entry", 3120, status="submitting")

    state.record_real_trade("R1", "T1", role="entry", price=3120)

    assert state.real_submission_proof is True


def test_simulated_close_defaults_to_short_close():
    state = _state()
    state.record_real_submission("R1", "entry", 3120)
    state.record_real_cancel("R1")
    state.record_simulated_entry("SIM-E1", "SIM-T1", 3120)

    close = state.record_simulated_close("SIM-C1", "SIM-T2", 3121)

    assert close.direction == "short"
    assert close.offset == "close"


@pytest.mark.parametrize(
    "setup",
    [
        "missing_real_submission",
        "real_order_not_terminal",
        "broker_position",
        "active_order",
        "reconcile",
        "missing_simulated_entry",
        "missing_simulated_close",
        "simulated_position",
        "real_fill",
        "rejected_real_order",
    ],
)
def test_passed_simulated_does_not_overclaim_every_missing_condition(setup):
    state = _state()
    if setup != "missing_real_submission":
        state.record_real_submission(
            "R1",
            "entry",
            3120,
            status="rejected" if setup == "rejected_real_order" else "submitted",
        )
        if setup != "real_order_not_terminal":
            if setup == "real_fill":
                state.record_real_trade("R1", "REAL-T1", role="entry", price=3120)
            elif setup != "rejected_real_order":
                state.record_real_cancel("R1")
    if setup != "missing_simulated_entry":
        state.record_simulated_entry("SIM-E1", "SIM-T1", 3120)
    if setup != "missing_simulated_close" and setup != "missing_simulated_entry":
        state.record_simulated_close("SIM-C1", "SIM-T2", 3121)
    if setup == "simulated_position":
        state.simulated_position_volume = 1

    result = _evaluate(
        state,
        position=1 if setup == "broker_position" else 0,
        active=["R-OTHER"] if setup == "active_order" else [],
        reconcile=setup != "reconcile",
    )
    assert result.outcome is TrialRunOutcome.RUNNING


def test_failed_and_aborted_outcomes_are_sticky():
    state = _state()
    state.mark_failed("cancel_failed", basis="broker rejected cancellation")
    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_real_submission("R1", "entry", 3120)
    assert exc_info.value.failure_code == "invalid_trial_transition"
    assert _evaluate(state).outcome is TrialRunOutcome.FAILED
    assert state.failure_code == "cancel_failed"

    aborted = _state()
    aborted.mark_aborted("backend_restarted")
    assert _evaluate(aborted).outcome is TrialRunOutcome.ABORTED


@pytest.mark.parametrize(
    ("symbol", "volume", "failure_code"),
    [
        ("rb2611", 1, "invalid_trial_symbol"),
        ("rb2611.DCE", 1, "invalid_trial_symbol"),
        ("rb2610", 2, "invalid_trial_volume"),
    ],
)
def test_mutators_reject_wrong_symbol_or_volume(symbol, volume, failure_code):
    state = _state()

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_real_submission("R1", "entry", 3120, symbol=symbol, volume=volume)

    assert exc_info.value.failure_code == failure_code


def test_symbol_aliases_are_equivalent_without_conflating_contracts():
    state = _state()
    state.record_real_submission("R1", "entry", 3120, symbol="SHFE.rb2610")
    state.record_real_cancel("R1")
    state.record_simulated_entry("SIM-E1", "SIM-T1", 3120, symbol="rb2610.SHFE")
    assert state.simulated_position_volume == 1

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_simulated_close("SIM-C1", "SIM-T2", 3121, symbol="rb2611.SHFE")
    assert exc_info.value.failure_code == "invalid_trial_symbol"


def test_order_ownership_current_order_and_direction_volume_checks():
    state = _state()
    state.record_real_submission("R1", "entry", 3120, direction="long")

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.require_current_order("UNRELATED")
    assert exc_info.value.failure_code == "order_not_owned_by_trial_run"

    state.record_real_submission("R2", "entry", 3121, direction="short")
    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.require_current_order("R1")
    assert exc_info.value.failure_code == "order_not_current"

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.require_current_order("R2", direction="long")
    assert exc_info.value.failure_code == "invalid_trial_transition"

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.require_current_order("R2", volume=2)
    assert exc_info.value.failure_code == "invalid_trial_volume"


def test_record_real_cancel_rejects_unrelated_same_symbol_order():
    state = _state()
    state.record_real_submission("R1", "entry", 3120)

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_real_cancel("OTHER-SAME-SYMBOL")
    assert exc_info.value.failure_code == "order_not_owned_by_trial_run"

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_real_cancel("R1", symbol="rb2611", volume=2)
    assert exc_info.value.failure_code == "invalid_trial_symbol"


def test_serialization_round_trip_preserves_domain_state():
    state = _state()
    state.record_real_submission("R1", "entry", 3120, symbol="rb2610.SHFE")
    state.record_real_cancel("R1")
    state.record_simulated_entry("SIM-E1", "SIM-T1", 3120)
    state.record_simulated_close("SIM-C1", "SIM-T2", 3121)
    state.evaluate(0, [], True)

    restored = TrialRunExecutionState.deserialize(state.serialize())

    assert restored.serialize() == state.serialize()
    assert restored.outcome is TrialRunOutcome.PASSED_SIMULATED
    assert restored.order_chain[0].track is TrialRunTrack.REAL


def test_invalid_terminal_transition_is_reported_with_stable_code():
    state = _state()
    state.mark_failed("operator_failure")

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_simulated_entry("SIM-E1", "SIM-T1", 3120)

    assert exc_info.value.failure_code == "invalid_trial_transition"


@pytest.mark.parametrize("terminal_method", ["mark_failed", "mark_aborted"])
def test_terminal_outcomes_cannot_be_overwritten(terminal_method):
    state = _state()
    state.record_real_submission("R-E", "entry", 3120)
    state.record_real_trade("R-E", "T-E", role="entry", price=3120)
    state.record_real_submission("R-C", "close", 3121, offset="close")
    state.record_real_trade("R-C", "T-C", role="close", price=3121)
    state.evaluate(0, [], True)
    original_outcome = state.outcome

    with pytest.raises(TrialRunExecutionError) as exc_info:
        if terminal_method == "mark_failed":
            state.mark_failed("late_failure")
        else:
            state.mark_aborted("late_abort")

    assert exc_info.value.failure_code == "invalid_trial_transition"
    assert state.outcome is original_outcome


def test_failed_and_aborted_outcomes_cannot_be_overwritten_by_each_other():
    failed = _state().mark_failed("failure")
    with pytest.raises(TrialRunExecutionError) as exc_info:
        failed.mark_aborted("abort")
    assert exc_info.value.failure_code == "invalid_trial_transition"

    aborted = _state().mark_aborted("abort")
    with pytest.raises(TrialRunExecutionError) as exc_info:
        aborted.mark_failed("failure")
    assert exc_info.value.failure_code == "invalid_trial_transition"


def test_bare_contract_matches_only_its_known_exchange_alias():
    state = TrialRunExecutionState(run_id="RUN-AU", symbol="au2606", volume=1)
    state.record_real_submission("R1", "entry", 700, symbol="SHFE.au2606")
    state.record_real_cancel("R1", symbol="au2606.SHFE")

    assert state.order_chain[0].symbol == "SHFE.au2606"

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_simulated_entry("SIM-E1", "SIM-T1", 700, symbol="au2606.DCE")
    assert exc_info.value.failure_code == "invalid_trial_symbol"

    explicit_exchange_state = TrialRunExecutionState(
        run_id="RUN-AU-EXPLICIT", symbol="au2606.SHFE", volume=1
    )
    with pytest.raises(TrialRunExecutionError) as exc_info:
        explicit_exchange_state.record_real_submission(
            "R2", "entry", 700, symbol="au2606.DCE"
        )
    assert exc_info.value.failure_code == "invalid_trial_symbol"

    with pytest.raises(TrialRunExecutionError) as exc_info:
        state.record_simulated_entry("SIM-E2", "SIM-T2", 700, symbol="au2607.SHFE")
    assert exc_info.value.failure_code == "invalid_trial_symbol"
