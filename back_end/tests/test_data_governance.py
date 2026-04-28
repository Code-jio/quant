import time
from pathlib import Path
from uuid import uuid4

import pandas as pd

from src.data import DataCache, DataManager, detect_bar_gaps


def _bars(dates):
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(dates),
            "open": [10.0 + i for i, _ in enumerate(dates)],
            "high": [11.0 + i for i, _ in enumerate(dates)],
            "low": [9.0 + i for i, _ in enumerate(dates)],
            "close": [10.5 + i for i, _ in enumerate(dates)],
            "volume": [100 + i for i, _ in enumerate(dates)],
            "open_interest": [1000 + i for i, _ in enumerate(dates)],
        }
    )


def test_cache_expires_entries():
    cache = DataCache(max_cache_size=2, ttl_seconds=1)

    cache.put("rb", _bars(["2024-01-02"]))
    assert cache.get("rb") is not None

    time.sleep(1.05)
    assert cache.get("rb") is None
    assert cache.stats()["entries"] == 0


def test_detect_daily_bar_gap():
    report = detect_bar_gaps(
        _bars(["2024-01-02", "2024-01-04"]),
        timeframe="1d",
    )

    assert report.has_gaps is True
    assert report.missing_count == 1
    assert report.sample_missing == ["2024-01-03T00:00:00"]


def test_save_bars_records_metadata_and_quality():
    artifact_dir = Path(".test-artifacts")
    artifact_dir.mkdir(exist_ok=True)
    db_path = artifact_dir / f"quotes-{uuid4().hex}.db"
    manager = DataManager(str(db_path), cache_ttl_seconds=30)

    try:
        saved = manager.save_bars(
            _bars(["2024-01-02", "2024-01-04"]),
            "rb2505",
            data_source="simulated",
            adjustment="raw",
            rollover_rule="main_contract",
        )

        assert saved is True
        report = manager.inspect_data_quality("rb2505", "2024-01-01", "2024-01-05")
        assert report["metadata"]["data_source"] == "simulated"
        assert report["metadata"]["rollover_rule"] == "main_contract"
        assert report["gaps"]["missing_count"] == 1
        assert report["quality"]["rows"] == 2
    finally:
        db_path.unlink(missing_ok=True)
