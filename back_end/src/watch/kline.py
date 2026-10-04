"""
K线数据与技术指标模块

数据层策略：
  - 所有周期读取 SQLite 中该周期的已导入数据，缺失时返回明确的空结果。
  - 不生成模拟行情；来源随每条记录返回。

缓存策略（内存 TTL Cache，行为对齐 Redis）：
  - TTL 随周期变化：1m=10s / 5m=30s / 1h=120s / 1d=600s
  - 支持 since 参数实现增量更新（只返回新 bar）
  - 最大 200 条缓存 key，LRU 淘汰

技术指标：MA / EMA / MACD / RSI / KDJ / BOLL / VOL_MA
"""

from __future__ import annotations

import re
import time
import threading
from typing import Any, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 内存 TTL 缓存（无需 Redis，行为等价）
# ---------------------------------------------------------------------------


class _KlineCache:
    """
    线程安全的内存 TTL 缓存。
    每个 key 存储 (value, expire_monotonic) 元组。
    超出 maxsize 时先淘汰过期项，再 LRU 淘汰最旧的 10%。
    """

    def __init__(self, maxsize: int = 200):
        self._store: dict[str, tuple[Any, float]] = {}
        self._access: dict[str, float] = {}  # LRU 时间戳
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_ts = entry
            if time.monotonic() > expire_ts:
                del self._store[key]
                self._access.pop(key, None)
                return None
            self._access[key] = time.monotonic()
            return value

    def set(self, key: str, value: Any, ttl: int = 60) -> None:
        with self._lock:
            if len(self._store) >= self._maxsize:
                now = time.monotonic()
                # 先淘汰过期
                expired = [k for k, (_, exp) in self._store.items() if exp <= now]
                for k in expired[:20]:
                    self._store.pop(k, None)
                    self._access.pop(k, None)
                # 再 LRU 淘汰 10%
                if len(self._store) >= self._maxsize:
                    lru = sorted(self._access, key=lambda k: self._access[k])
                    for k in lru[: max(1, self._maxsize // 10)]:
                        self._store.pop(k, None)
                        self._access.pop(k, None)
            self._store[key] = (value, time.monotonic() + ttl)
            self._access[key] = time.monotonic()

    def invalidate(self, prefix: str = "") -> int:
        """使指定前缀的缓存失效，返回删除条数。"""
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
                self._access.pop(k, None)
            return len(keys)

    @property
    def size(self) -> int:
        return len(self._store)


kline_cache = _KlineCache(maxsize=200)

# ---------------------------------------------------------------------------
# 周期定义
# ---------------------------------------------------------------------------

_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}

# 各周期缓存 TTL（秒）
_INTERVAL_TTL: dict[str, int] = {
    "1m": 10,
    "5m": 30,
    "15m": 60,
    "30m": 90,
    "1h": 120,
    "4h": 300,
    "1d": 600,
    "1w": 1800,
}

_TRADING_MINUTES_PER_DAY = 240  # 期货日内交易时间约 4 小时


# ---------------------------------------------------------------------------
# 日线数据加载
# ---------------------------------------------------------------------------

# 技术指标计算
# ---------------------------------------------------------------------------


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _calc_kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = ((close - low_n) / (high_n - low_n).replace(0, np.nan) * 100).fillna(50)
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def _apply_indicators(df: pd.DataFrame, indicators: list[str]) -> pd.DataFrame:
    """
    根据 indicators 列表计算技术指标，追加到 df 并返回。

    支持格式：
      ma{N}           简单均线，如 ma20
      ema{N}          指数均线，如 ema20
      macd            MACD(12,26,9)：字段 macd / macd_signal / macd_hist
      rsi / rsi{N}    RSI，默认 14 周期
      kdj             KDJ(9,3,3)：字段 k / d / j
      boll / boll{N}  布林带，默认 20 周期：boll_{N}_upper/mid/lower
      vol_ma{N}       成交量均线，如 vol_ma5
      volume / vol    原始成交量（已内置，无需额外计算）
    """
    df = df.copy()
    close = df["close"]
    vol = df["volume"]

    for raw in indicators:
        ind = raw.strip().lower()
        if not ind:
            continue

        # ── MA ───────────────────────────────────────────────────────────────
        m = re.fullmatch(r"ma(\d+)", ind)
        if m:
            n = int(m.group(1))
            df[f"ma{n}"] = close.rolling(n, min_periods=1).mean().round(4)
            continue

        # ── EMA ──────────────────────────────────────────────────────────────
        m = re.fullmatch(r"ema(\d+)", ind)
        if m:
            n = int(m.group(1))
            df[f"ema{n}"] = close.ewm(span=n, adjust=False).mean().round(4)
            continue

        # ── MACD ─────────────────────────────────────────────────────────────
        if re.fullmatch(r"macd(\d*_?\d*_?\d*)?", ind):
            fast, slow, sig = 12, 26, 9
            # 支持 macd_fast_slow_sig 格式，如 macd_12_26_9
            parts = ind.split("_")
            if len(parts) == 4:
                try:
                    fast, slow, sig = int(parts[1]), int(parts[2]), int(parts[3])
                except ValueError:
                    pass
            ema_fast = close.ewm(span=fast, adjust=False).mean()
            ema_slow = close.ewm(span=slow, adjust=False).mean()
            macd_line = (ema_fast - ema_slow).round(4)
            signal = macd_line.ewm(span=sig, adjust=False).mean().round(4)
            df["macd"] = macd_line
            df["macd_signal"] = signal
            df["macd_hist"] = (macd_line - signal).round(4)
            continue

        # ── RSI ──────────────────────────────────────────────────────────────
        m = re.fullmatch(r"rsi(\d*)", ind)
        if m:
            n = int(m.group(1)) if m.group(1) else 14
            df[f"rsi{n}"] = _calc_rsi(close, n).round(3)
            continue

        # ── KDJ ──────────────────────────────────────────────────────────────
        if re.fullmatch(r"kdj(\d*)", ind):
            parts = ind.split("_")
            n, m1, m2 = 9, 3, 3
            if len(parts) == 4:
                try:
                    n, m1, m2 = int(parts[1]), int(parts[2]), int(parts[3])
                except ValueError:
                    pass
            k, d, j = _calc_kdj(df["high"], df["low"], close, n, m1, m2)
            df["k"] = k.round(3)
            df["d"] = d.round(3)
            df["j"] = j.round(3)
            continue

        # ── Bollinger Bands ───────────────────────────────────────────────────
        m = re.fullmatch(r"boll(\d*)", ind)
        if m:
            n = int(m.group(1)) if m.group(1) else 20
            mid = close.rolling(n, min_periods=1).mean()
            std = close.rolling(n, min_periods=1).std().fillna(0)
            df[f"boll{n}_upper"] = (mid + 2 * std).round(4)
            df[f"boll{n}_mid"] = mid.round(4)
            df[f"boll{n}_lower"] = (mid - 2 * std).round(4)
            continue

        # ── Volume MA ─────────────────────────────────────────────────────────
        m = re.fullmatch(r"vol_?ma(\d+)", ind)
        if m:
            n = int(m.group(1))
            df[f"vol_ma{n}"] = vol.rolling(n, min_periods=1).mean().round(0)
            continue

        # volume / vol → 已内置，忽略

    return df


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def get_kline(
    symbol: str, interval: str = "1d", limit: int = 100, indicators: str = "", since=None, before=None
) -> dict:
    """Read exact-period history; an exclusive before cursor retrieves older bars."""
    from ..data import DataManager
    from ..data.governance import data_provenance, validate_bars
    from ..analysis.round_trips import finite_json

    if interval not in _INTERVAL_MINUTES:
        return {"code": 1, "msg": "Unsupported interval", "data": []}
    limit = max(1, min(int(limit), 1000))
    ind_list = [x.strip().lower() for x in indicators.split(",") if x.strip()]
    if len(ind_list) > 20 or any(any(int(n) < 1 or int(n) > 2000 for n in re.findall(r"\d+", x)) for x in ind_list):
        return {"code": 1, "msg": "Indicator period must be 1..2000", "data": []}
    dm = DataManager()
    # Query count is bounded; keeping row provenance avoids cache-policy ambiguity.
    df = dm.db.load_recent_bars(symbol, interval, min(5000, limit + 2000), before=before, since=since)
    result = {
        "code": 0,
        "symbol": symbol,
        "interval": interval,
        "data": [],
        "total": 0,
        "cached": False,
        "data_source": "missing",
        "synthetic_data_used": False,
        "has_more": False,
        "next_before": None,
        "timezone": "Asia/Shanghai",
    }
    if df.empty:
        result["msg"] = "未导入该周期历史数据"
        return result
    validate_bars(df)
    result.update(data_provenance(df.tail(limit)))
    result["has_more"] = len(df) > limit
    if ind_list:
        df = _apply_indicators(df, ind_list)
    df = df.tail(limit)
    rows = df.reset_index().rename(columns={"datetime": "timestamp"})
    rows["timestamp"] = rows["timestamp"].map(lambda v: v.isoformat())
    result["data"] = finite_json(rows.to_dict("records"))
    result["total"] = len(rows)
    result["next_before"] = result["data"][0]["timestamp"]
    return result
