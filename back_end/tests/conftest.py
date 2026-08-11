import os

import pytest

# Disable rate limiting during tests so that multiple test cases sharing the
# same module-level slowapi Limiter do not exhaust each other's quotas.
os.environ.setdefault("QUANT_RATE_LIMIT_ENABLED", "false")


@pytest.fixture(autouse=True)
def isolate_trial_run_checkpoint_db(tmp_path, monkeypatch):
    """Keep app-lifespan checkpoint recovery deterministic between tests."""
    monkeypatch.setenv(
        "QUANT_TRIAL_RUN_STATE_DB",
        str(tmp_path / "trial-run-state.db"),
    )
    monkeypatch.setenv(
        "QUANT_AUDIT_LOG_DIR",
        str(tmp_path / "audit-logs"),
    )
