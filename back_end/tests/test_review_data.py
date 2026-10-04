import pandas as pd
import pytest

from src.data import DataManager, DatabaseManager
from src.data.errors import DatabaseError
from src.data.governance import detect_bar_gaps
from src.watch import search_contracts
from src.watch.kline import get_kline, kline_cache


def bars():
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02 09:00", periods=6, freq="min"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10,
        }
    )


def test_date_end_includes_intraday_and_retains_row_provenance(tmp_path):
    db = DatabaseManager(str(tmp_path / "quotes.db"))
    db.save_bars(bars().iloc[:3], "rb", "1m", data_source="vendor-a")
    db.save_bars(bars().iloc[3:], "rb", "1m", data_source="vendor-b")
    result = db.load_bars("rb", "2024-01-02", "2024-01-02", "1m")
    assert len(result) == 6
    assert set(result.data_source) == {"vendor-a", "vendor-b"}
    assert db.get_metadata("rb", "1m")["data_source"] == "mixed"
    assert len(db.load_bars("rb", "2024-01-02", "2024-01-02", "1m", limit=3)) == 3


@pytest.mark.parametrize("column,value", [("close", float("inf")), ("volume", -1), ("high", 90)])
def test_invalid_data_rejected_before_storage(tmp_path, column, value):
    db = DatabaseManager(str(tmp_path / "quotes.db"))
    data = bars()
    data.loc[0, column] = value
    with pytest.raises(DatabaseError):
        db.save_bars(data, "rb", "1m")


def test_synthetic_is_ephemeral_and_respects_requested_period(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_ALLOW_SYNTHETIC_DATA", "true")
    dm = DataManager(str(tmp_path / "quotes.db"))
    df = dm.generate_sample_data("rb", days=6, timeframe="1m", end_date="2024-01-02 09:05")
    assert len(df) == 6 and df.datetime.max() == pd.Timestamp("2024-01-02 09:05")
    assert df.datetime.diff().dropna().eq(pd.Timedelta(minutes=1)).all()
    assert dm.db.get_metadata("rb", "1m") is None


def test_watch_uses_real_intraday_and_before_cursor(tmp_path, monkeypatch):
    dm = DataManager(str(tmp_path / "quotes.db"))
    dm.save_bars(bars(), "rb", "1m", data_source="vendor")
    monkeypatch.setattr("src.data.DataManager", lambda: dm)
    kline_cache.invalidate()
    current = get_kline("rb", "1m", limit=3)
    older = get_kline("rb", "1m", limit=3, before=current["data"][0]["timestamp"])
    assert len(current["data"]) == len(older["data"]) == 3
    assert older["data"][-1]["timestamp"] < current["data"][0]["timestamp"]
    assert current["data_source"] == "vendor"
    assert get_kline("rb", "5m")["data"] == []


def test_calendar_does_not_claim_heuristic_is_exchange_schedule():
    result = detect_bar_gaps(bars(), "1m").to_dict()
    assert result["calendar_verified"] is False
    expected = pd.DatetimeIndex(["2024-01-02 09:00", "2024-01-02 09:05"])
    result = detect_bar_gaps(bars().iloc[[0, 5]], "1m", expected_index=expected).to_dict()
    assert result["calendar_verified"] is True and result["missing_count"] == 0


def test_search_does_not_invent_unknown_contracts():
    assert search_contracts("rb9999") == []
    rows = search_contracts("rb", contracts={"rb2610": {"symbol": "rb2610", "name": "螺纹", "exchange": "SHFE"}})
    assert [r["symbol"] for r in rows] == ["rb2610"]
