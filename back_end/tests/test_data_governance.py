import time
import sqlite3
from pathlib import Path
from uuid import uuid4

import pandas as pd

from src.data import DataCache, DataManager, DatabaseManager, detect_bar_gaps


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


def test_database_manager_upgrades_legacy_bars_table_with_ingested_at():
    artifact_dir = Path(".test-artifacts")
    artifact_dir.mkdir(exist_ok=True)
    db_path = artifact_dir / f"legacy-quotes-{uuid4().hex}.db"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            datetime TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            open_interest REAL DEFAULT 0,
            data_source TEXT DEFAULT 'unknown',
            adjustment TEXT DEFAULT 'raw',
            rollover_rule TEXT DEFAULT 'none',
            UNIQUE(symbol, timeframe, datetime)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bars (
            symbol, timeframe, datetime, open, high, low, close, volume, open_interest
        ) VALUES ('rb2505', '1d', '2024-01-02', 10, 11, 9, 10.5, 100, 1000)
        """
    )
    conn.commit()
    conn.close()

    try:
        DatabaseManager(str(db_path))

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(bars)").fetchall()}
        ingested_at = conn.execute("SELECT ingested_at FROM bars WHERE symbol = 'rb2505'").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        assert "ingested_at" in columns
        assert ingested_at
        assert user_version >= 3
    finally:
        db_path.unlink(missing_ok=True)


def test_generate_sample_data_marks_synthetic_source(monkeypatch):
    artifact_dir = Path(".test-artifacts")
    artifact_dir.mkdir(exist_ok=True)
    db_path = artifact_dir / f"synthetic-quotes-{uuid4().hex}.db"
    monkeypatch.delenv("QUANT_ENV", raising=False)
    monkeypatch.delenv("QUANT_ALLOW_SYNTHETIC_DATA", raising=False)
    manager = DataManager(str(db_path), cache_ttl_seconds=30)

    try:
        generated = manager.generate_sample_data("rb2505", days=10)
        metadata = manager.db.get_metadata("rb2505")

        assert len(generated) == 10
        assert metadata["data_source"] == "synthetic"
    finally:
        db_path.unlink(missing_ok=True)


def test_generate_sample_data_can_be_disabled_for_production(monkeypatch):
    artifact_dir = Path(".test-artifacts")
    artifact_dir.mkdir(exist_ok=True)
    db_path = artifact_dir / f"no-synthetic-quotes-{uuid4().hex}.db"
    monkeypatch.setenv("QUANT_ALLOW_SYNTHETIC_DATA", "false")
    manager = DataManager(str(db_path), cache_ttl_seconds=30)

    try:
        generated = manager.generate_sample_data("rb2505", days=10)

        assert generated.empty
        assert manager.db.get_metadata("rb2505") is None
    finally:
        db_path.unlink(missing_ok=True)
