"""
Bar aggregator — converts tick stream into OHLC bars per symbol.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BarAggregator:
    """Aggregate ticks into N-minute OHLC bars per symbol.

    Keeps an in-progress bar for each symbol.  When a bar interval
    completes, emits the finished bar via the on_bar callback and
    starts a new bar for the next interval.
    """

    def __init__(
        self,
        interval_minutes: int = 1,
        on_bar: Callable[[str, pd.Series], None] = None,
    ) -> None:
        self.interval_minutes = interval_minutes
        self.on_bar = on_bar
        self._current: Dict[str, Dict[str, Any]] = {}

    def push(self, tick: Any) -> Optional[pd.Series]:
        """Push a tick into the aggregator.

        If the tick completes a bar, returns the finished bar.
        Otherwise returns None.
        """
        symbol = tick.symbol
        ts = tick.timestamp

        bar_start = self._bar_start(ts)

        if symbol not in self._current:
            self._current[symbol] = self._new_bar(symbol, bar_start, tick)

        current = self._current[symbol]
        finished = None

        # New interval started — emit previous bar and start fresh
        if bar_start > current["bar_start"]:
            finished = self._finish_bar(current)
            self._current[symbol] = self._new_bar(symbol, bar_start, tick)
            current = self._current[symbol]
            if self.on_bar:
                self.on_bar(symbol, finished)

        # Update O/H/L/C/V for current bar (runs for every tick)
        price = float(tick.last_price)
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        current["volume"] += int(getattr(tick, "volume", 0) or 0)

        return finished

    def _bar_start(self, ts: datetime) -> int:
        """Floor timestamp to bar interval boundary (Unix seconds)."""
        if isinstance(ts, datetime):
            epoch = ts.timestamp()
        else:
            epoch = float(ts)
        interval_s = self.interval_minutes * 60
        return int(epoch // interval_s) * interval_s

    def _new_bar(
        self,
        symbol: str,
        bar_start: int,
        tick: Any = None,
    ) -> Dict[str, Any]:
        price = float(tick.last_price) if tick else 0.0
        return {
            "symbol": symbol,
            "bar_start": bar_start,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0,
        }

    def _finish_bar(self, state: Dict[str, Any]) -> pd.Series:
        dt = datetime.fromtimestamp(state["bar_start"] + self.interval_minutes * 60)
        return pd.Series({
            "symbol": state["symbol"],
            "datetime": dt,
            "open": state["open"],
            "high": state["high"],
            "low": state["low"],
            "close": state["close"],
            "volume": state["volume"],
        })

    def flush(self, symbol: str) -> Optional[pd.Series]:
        """Emit current incomplete bar (used on shutdown)."""
        if symbol in self._current:
            bar = self._finish_bar(self._current.pop(symbol))
            if self.on_bar:
                self.on_bar(symbol, bar)
            return bar
        return None
