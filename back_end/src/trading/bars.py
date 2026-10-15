"""Event-time OHLCV aggregation shared by strategy execution and watch updates."""

from datetime import datetime, timedelta
from math import isfinite

INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


class BarAggregator:
    def __init__(self, interval="1d"):
        if interval not in INTERVALS:
            raise ValueError("Unsupported bar interval")
        self.interval = interval
        self.current = None
        self.last_timestamp = None
        self.last_volume = None
        self.last_day = None

    def update(self, tick):
        if not isfinite(tick.last_price) or tick.last_price <= 0:
            return None
        if self.last_timestamp is not None and tick.timestamp <= self.last_timestamp:
            return None
        day = getattr(tick, "trading_day", "") or tick.timestamp.strftime("%Y%m%d")
        if self.interval in ("1d", "1w"):
            bucket = datetime.strptime(day.replace("-", ""), "%Y%m%d").replace(tzinfo=tick.timestamp.tzinfo)
            if self.interval == "1w":
                bucket -= timedelta(days=bucket.weekday())
        else:
            minutes = INTERVALS[self.interval]
            bucket = tick.timestamp.replace(second=0, microsecond=0)
            bucket -= timedelta(minutes=(bucket.hour * 60 + bucket.minute) % minutes)
        completed = None
        # First observation has no baseline: do not assign the whole day's volume to one bar.
        delta = 0 if self.last_volume is None else max(0, tick.volume - self.last_volume)
        if self.last_day is not None and self.last_day != day:
            delta = max(0, tick.volume)
        if self.current is None or self.current["datetime"] != bucket:
            completed = dict(self.current) if self.current else None
            self.current = dict(
                symbol=tick.symbol,
                datetime=bucket,
                time=bucket.isoformat(),
                open=tick.last_price,
                high=tick.last_price,
                low=tick.last_price,
                close=tick.last_price,
                volume=delta,
                trading_day=day,
                source="vnpy",
            )
        else:
            self.current.update(
                high=max(self.current["high"], tick.last_price),
                low=min(self.current["low"], tick.last_price),
                close=tick.last_price,
                volume=self.current["volume"] + delta,
            )
        self.last_timestamp, self.last_volume, self.last_day = tick.timestamp, tick.volume, day
        return completed
