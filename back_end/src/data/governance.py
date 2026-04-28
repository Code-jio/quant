"""Data governance helpers for historical bar data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class BarDataMetadata:
    symbol: str
    timeframe: str
    data_source: str = "unknown"
    adjustment: str = "raw"
    rollover_rule: str = "none"
    ingested_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GapReport:
    timeframe: str
    expected_count: int
    actual_count: int
    missing_count: int
    first_missing: str | None
    last_missing: str | None
    sample_missing: list[str]

    @property
    def has_gaps(self) -> bool:
        return self.missing_count > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"has_gaps": self.has_gaps}


def timeframe_to_pandas_freq(timeframe: str) -> str:
    tf = (timeframe or "1d").strip().lower()
    mapping = {
        "1m": "min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "h",
        "4h": "4h",
        "1d": "B",
        "1w": "W-FRI",
    }
    return mapping.get(tf, tf)


def _datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    if df.empty:
        return pd.DatetimeIndex([])
    if isinstance(df.index, pd.DatetimeIndex):
        values = df.index
    elif "datetime" in df.columns:
        values = pd.to_datetime(df["datetime"])
    else:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(values).dropna().sort_values().unique()


def detect_bar_gaps(df: pd.DataFrame, timeframe: str = "1d", sample_limit: int = 20) -> GapReport:
    """Detect missing timestamps using a deterministic baseline calendar.

    Daily bars use business days. Intraday bars use a continuous interval
    baseline; exchange session calendars can be layered on later without
    changing this report contract.
    """
    actual = _datetime_index(df)
    if len(actual) == 0:
        return GapReport(timeframe, 0, 0, 0, None, None, [])

    expected = pd.date_range(actual[0], actual[-1], freq=timeframe_to_pandas_freq(timeframe))
    missing = expected.difference(actual)
    return GapReport(
        timeframe=timeframe,
        expected_count=len(expected),
        actual_count=len(actual),
        missing_count=len(missing),
        first_missing=missing[0].isoformat() if len(missing) else None,
        last_missing=missing[-1].isoformat() if len(missing) else None,
        sample_missing=[ts.isoformat() for ts in missing[:sample_limit]],
    )


def summarize_ohlcv_quality(df: pd.DataFrame) -> dict[str, Any]:
    """Return basic reproducibility checks for OHLCV data."""
    if df.empty:
        return {
            "rows": 0,
            "duplicate_timestamps": 0,
            "null_cells": 0,
            "invalid_ohlc_rows": 0,
        }

    indexed = df if isinstance(df.index, pd.DatetimeIndex) else df.set_index(pd.to_datetime(df["datetime"]))
    required = ["open", "high", "low", "close", "volume"]
    present = [col for col in required if col in indexed.columns]
    null_cells = int(indexed[present].isnull().sum().sum()) if present else 0
    invalid_ohlc = 0
    if {"open", "high", "low", "close"}.issubset(indexed.columns):
        invalid_ohlc = int(
            (
                (indexed["high"] < indexed[["open", "close", "low"]].max(axis=1))
                | (indexed["low"] > indexed[["open", "close", "high"]].min(axis=1))
            ).sum()
        )
    return {
        "rows": int(len(indexed)),
        "duplicate_timestamps": int(indexed.index.duplicated().sum()),
        "null_cells": null_cells,
        "invalid_ohlc_rows": invalid_ohlc,
    }


def normalize_metadata(
    symbol: str,
    timeframe: str,
    *,
    data_source: str = "unknown",
    adjustment: str = "raw",
    rollover_rule: str = "none",
) -> BarDataMetadata:
    return BarDataMetadata(
        symbol=symbol,
        timeframe=timeframe,
        data_source=(data_source or "unknown").strip() or "unknown",
        adjustment=(adjustment or "raw").strip() or "raw",
        rollover_rule=(rollover_rule or "none").strip() or "none",
    )
