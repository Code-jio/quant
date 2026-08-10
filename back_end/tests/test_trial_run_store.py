from src.trading.trial_run_execution import TrialRunExecutionState, TrialRunOutcome
from src.trading.trial_run_store import TrialRunCheckpointStore


def test_checkpoint_round_trip_and_restart_aborts_non_terminal(tmp_path):
    store = TrialRunCheckpointStore(tmp_path / "trial-run.db")
    running = TrialRunExecutionState(run_id="RUN-RUNNING", symbol="rb2610", volume=1)
    store.save(running)

    second_store = TrialRunCheckpointStore(tmp_path / "trial-run.db")
    loaded = second_store.load_latest()
    assert loaded is not None
    assert loaded.run_id == "RUN-RUNNING"

    second_store.abort_non_terminal("backend_restarted")
    aborted = second_store.load_latest()
    assert aborted is not None
    assert aborted.outcome is TrialRunOutcome.ABORTED
    assert aborted.failure_code == "backend_restarted"


def test_checkpoint_keeps_terminal_run_unchanged(tmp_path):
    store = TrialRunCheckpointStore(tmp_path / "terminal.db")
    terminal = TrialRunExecutionState(run_id="RUN-DONE", symbol="rb2610", volume=1)
    terminal.mark_failed("manual_failure", "operator")
    store.save(terminal)

    store.abort_non_terminal("backend_restarted")
    loaded = store.load_latest()
    assert loaded is not None
    assert loaded.outcome is TrialRunOutcome.FAILED
    assert loaded.failure_code == "manual_failure"


def test_mark_aborted_is_idempotent_for_the_selected_run(tmp_path):
    store = TrialRunCheckpointStore(tmp_path / "selected.db")
    state = TrialRunExecutionState(run_id="RUN-SELECTED", symbol="rb2610", volume=1)
    store.save(state)

    store.mark_aborted("RUN-SELECTED", "backend_restarted")
    store.mark_aborted("RUN-SELECTED", "different_reason")
    loaded = store.load_latest()
    assert loaded.outcome is TrialRunOutcome.ABORTED
    assert loaded.failure_code == "backend_restarted"


def test_mark_aborted_never_redirects_an_unknown_run_to_the_latest(tmp_path):
    store = TrialRunCheckpointStore(tmp_path / "multiple.db")
    latest = TrialRunExecutionState(run_id="RUN-LATEST", symbol="rb2610", volume=1)
    store.save(latest)

    store.mark_aborted("RUN-MISSING", "backend_restarted")

    loaded = store.load_latest()
    assert loaded is not None
    assert loaded.run_id == "RUN-LATEST"
    assert loaded.outcome is TrialRunOutcome.RUNNING
