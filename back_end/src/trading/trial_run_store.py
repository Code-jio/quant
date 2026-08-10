"""SQLite checkpoint storage for trial-run execution evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .trial_run_execution import TrialRunExecutionState, TrialRunOutcome


def _default_path() -> Path:
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "data" / "runtime" / "trial_run_state.db"


class TrialRunCheckpointStore:
    """Append-only checkpoint store with terminal-state protection."""

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        configured = str(path or os.getenv("QUANT_TRIAL_RUN_STATE_DB", "")).strip()
        self.path = Path(configured) if configured else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trial_run_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, state: TrialRunExecutionState) -> None:
        payload = state.serialize()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO trial_run_checkpoints (run_id, saved_at, outcome, payload) VALUES (?, ?, ?, ?)",
                (
                    state.run_id,
                    datetime.now(timezone.utc).isoformat(),
                    state.final_outcome.value,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            connection.commit()

    def load_latest(self) -> Optional[TrialRunExecutionState]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM trial_run_checkpoints ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return TrialRunExecutionState.deserialize(row["payload"])

    def mark_aborted(self, run_id: str, reason: str) -> None:
        """Abort the named run without redirecting to another checkpoint."""
        if not str(run_id or "").strip():
            return
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM trial_run_checkpoints WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
        if row is None:
            return
        state = TrialRunExecutionState.deserialize(row["payload"])
        if state.final_outcome is not TrialRunOutcome.RUNNING:
            return
        state.mark_aborted(str(reason or "backend_restarted"))
        self.save(state)

    def abort_non_terminal(self, reason: str = "backend_restarted") -> Optional[TrialRunExecutionState]:
        state = self.load_latest()
        if state is None:
            return None
        if state.final_outcome is TrialRunOutcome.RUNNING:
            self.mark_aborted(state.run_id, reason)
            state = self.load_latest()
        return state
