"""
K线数据与技术指标模块

数据层策略：
  - 1d / 1w：从 SQLite 数据库加载历史日线，不足时自动生成模拟数据
  - 1m / 5m / 15m / 30m / 1h / 4h：基于日线合成仿真分钟线（可替换为 CTP Tick）

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
from datetime import date, datetime, timedelta
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
        self._access: dict[str, float] = {}          # LRU 时间戳
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
                    for k in lru[:max(1, self._maxsize // 10)]:
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
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}

# 各周期缓存 TTL（秒）
_INTERVAL_TTL: dict[str, int] = {
    "1m": 10, "5m": 30, "15m": 60, "30m": 90,
    "1h": 120, "4h": 300, "1d": 600, "1w": 1800,
}

_TRADING_MINUTES_PER_DAY = 240   # 期货日内交易时间约 4 小时


# ---------------------------------------------------------------------------
# 日线数据加载
# ---------------------------------------------------------------------------

def _load_daily(symbol: str, days_back: int) -> pd.DataFrame:
    """
    从 SQLite 加载日线数据；若不足则自动生成模拟数据后重新加载。
    返回按时间升序排列的 DataFrame，列：open/high/low/close/volume。
    """
    from ..data import DataManager

    dm    = DataManager()
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    df = dm.get_bars(symbol, start, end, "1d")

    if df is None or len(df) < 5:
        dm.generate_sample_data(symbol, days=max(days_back, 600))
        df = dm.get_bars(symbol, start, end, "1d")

    if df is None or df.empty:
        return pd.DataFrame()

    return df.sort_index()


# ---------------------------------------------------------------------------
# 仿真分钟线合成（日线 → 分钟线）
# ---------------------------------------------------------------------------

def _brownian_path(
    open_: float, close_: float, high: float, low: float,
    n: int, rng: np.random.Generator,
) -> np.ndarray:
    """生成 n+1 个价格点（首 = open，末 = close），约束在 [low, high]。"""
    t     = np.linspace(0, 1, n + 1)
    drift = open_ + (close_ - open_) * t
    sigma = (high - low) / 4
    noise = rng.normal(0, sigma * 0.25, n + 1)
    noise[0] = noise[-1] = 0
    path  = np.clip(drift + noise, low * 0.9995, high * 1.0005)
    path[0], path[-1] = open_, close_
    return path


def _volume_profile(n: int) -> np.ndarray:
    """U 型成交量分布（开盘和收盘成交量较高）。"""
    t = np.linspace(0, 1, n)
    w = 0.5 + (t - 0.5) ** 2 * 2.5
    w = w / w.sum()
    return w


def _synthesize_intraday(
    daily_df: pd.DataFrame,
    interval_minutes: int,
    bars_needed: int,
) -> pd.DataFrame:
    """将日线数据合成指定分钟周期的 K 线（仿真）。"""
    bars_per_day = max(1, _TRADING_MINUTES_PER_DAY // interval_minutes)
    days_needed  = (bars_needed // bars_per_day) + 3

    daily_df = daily_df.tail(days_needed)
    if daily_df.empty:
        return pd.DataFrame()

    rng      = np.random.default_rng(12345)   # 固定种子保证可重现
    all_bars = []

    for day_ts, row in daily_df.iterrows():
        d_open  = float(row["open"])
        d_high  = float(row["high"])
        d_low   = float(row["low"])
        d_close = float(row["close"])
        d_vol   = float(row["volume"])

        # 期货主交易时段：9:00
        if hasattr(day_ts, "year"):
            base_dt = datetime(day_ts.year, day_ts.month, day_ts.day, 9, 0)
        else:
            base_dt = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

        prices     = _brownian_path(d_open, d_close, d_high, d_low, bars_per_day, rng)
        vol_dist   = _volume_profile(bars_per_day)
        vol_bars   = np.maximum((d_vol * vol_dist).astype(int), 0)

        for i in range(bars_per_day):
            ts   = base_dt + timedelta(minutes=i * interval_minutes)
            o, c = prices[i], prices[i + 1]
            h    = min(max(o, c) * (1 + rng.exponential(0.0003)), d_high * 1.001)
            l    = max(min(o, c) * (1 - rng.exponential(0.0003)), d_low  * 0.999)
            all_bars.append({
                "datetime": ts,
                "open":     round(o, 2),
                "high":     round(h, 2),
                "low":      round(l, 2),
                "close":    round(c, 2),
                "volume":   int(vol_bars[i]),
            })

    if not all_bars:
        return pd.DataFrame()

    result = pd.DataFrame(all_bars).set_index("datetime")
    return result.tail(bars_needed)


# ---------------------------------------------------------------------------
# 技术指标计算
# ---------------------------------------------------------------------------

def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _calc_kdj(
    high: pd.Series, low: pd.Series, close: pd.Series,
    n: int = 9, m1: int = 3, m2: int = 3,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    low_n  = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv    = ((close - low_n) / (high_n - low_n).replace(0, np.nan) * 100).fillna(50)
    k      = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d      = k.ewm(com=m2 - 1, adjust=False).mean()
    j      = 3 * k - 2 * d
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
    df    = df.copy()
    close = df["close"]
    vol   = df["volume"]

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
            signal    = macd_line.ewm(span=sig, adjust=False).mean().round(4)
            df["macd"]        = macd_line
            df["macd_signal"] = signal
            df["macd_hist"]   = (macd_line - signal).round(4)
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
            n   = int(m.group(1)) if m.group(1) else 20
            mid = close.rolling(n, min_periods=1).mean()
            std = close.rolling(n, min_periods=1).std().fillna(0)
            df[f"boll{n}_upper"] = (mid + 2 * std).round(4)
            df[f"boll{n}_mid"]   = mid.round(4)
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
    symbol:     str,
    interval:   str  = "1d",
    limit:      int  = 100,
    indicators: str  = "",
    since:      Optional[str] = None,   # ISO datetime，仅返回该时刻之后的 bar
) -> dict:
    """
    获取 K 线数据并计算技术指标，结果带 TTL 缓存。

    Parameters
    ----------
    symbol     : 合约代码，如 rb2501
    interval   : 时间周期，1m/5m/15m/30m/1h/4h/1d/1w
    limit      : 最多返回条数（1~1000）
    indicators : 逗号分隔指标字符串，如 "ma20,ma60,macd,rsi14"
    since      : 增量更新起点，仅返回该 ISO datetime 之后的 bar

    Returns
    -------
    dict with keys: code / symbol / interval / data / total / cached
    """
    interval  = interval.lower().strip()
    if interval not in _INTERVAL_MINUTES:
        return {"code": 1, "msg": f"不支持的周期: {interval}，可选: {list(_INTERVAL_MINUTES)}"}

    limit     = max(1, min(limit, 1000))
    ind_list  = [x.strip().lower() for x in indicators.split(",") if x.strip()]
    ind_key   = ",".join(sorted(ind_list))

    # ── 缓存查询 ──────────────────────────────────────────────────────────────
    cache_key = f"kline:{symbol}:{interval}:{limit}:{ind_key}"
    cached    = kline_cache.get(cache_key)
    if cached is not None and since is None:
        data = cached
        return {
            "code": 0, "symbol": symbol, "interval": interval,
            "data": data, "total": len(data), "cached": True,
        }

    # ── 数据加载 ──────────────────────────────────────────────────────────────
    interval_min = _INTERVAL_MINUTES[interval]

    if interval == "1w":
        # 周线：从日线 resample
        days_back = limit * 7 + 30
        daily_df  = _load_daily(symbol, days_back)
        if daily_df.empty:
            return {"code": 1, "msg": f"无法加载 {symbol} 数据"}
        df = (daily_df
              .resample("W")
              .agg({"open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"})
              .dropna()
              .tail(limit))

    elif interval == "1d":
        days_back = limit + 100   # 多取以保证指标预热数据
        df = _load_daily(symbol, days_back)
        if df.empty:
            return {"code": 1, "msg": f"无法加载 {symbol} 数据"}
        df = df.tail(limit)

    else:
        # 分钟线：从日线合成
        bars_per_day = max(1, _TRADING_MINUTES_PER_DAY // interval_min)
        days_back    = (limit // bars_per_day + 5) + 100
        daily_df     = _load_daily(symbol, days_back)
        if daily_df.empty:
            return {"code": 1, "msg": f"无法加载 {symbol} 数据"}
        # 多取 2 倍用于指标预热
        df = _synthesize_intraday(daily_df, interval_min, limit * 2)
        if df.empty:
            return {"code": 1, "msg": "合成分钟线失败"}
        df = df.tail(limit)

    # 确保列存在
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return {"code": 1, "msg": f"数据缺少字段: {col}"}

    # ── 技术指标计算 ─────────────────────────────────────────────────────────
    if ind_list:
        df = _apply_indicators(df, ind_list)

    # ── 序列化 ────────────────────────────────────────────────────────────────
    df = df.replace({np.nan: None})   # NaN → None → JSON null

    records = []
    for ts, row in df.iterrows():
        entry: dict = {}
        # timestamp：统一输出 ISO 格式字符串
        if hasattr(ts, "isoformat"):
            entry["timestamp"] = ts.isoformat()
        else:
            entry["timestamp"] = str(ts)

        for col in df.columns:
            val = row[col]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                entry[col] = None
            elif isinstance(val, (np.integer,)):
                entry[col] = int(val)
            elif isinstance(val, (np.floating,)):
                entry[col] = float(val)
            else:
                entry[col] = val

        records.append(entry)

    # ── 增量过滤（since 参数）────────────────────────────────────────────────
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            records  = [r for r in records if datetime.fromisoformat(r["timestamp"]) > since_dt]
        except ValueError:
            pass

    # ── 写入缓存 ──────────────────────────────────────────────────────────────
    if since is None:
        ttl = _INTERVAL_TTL.get(interval, 60)
        kline_cache.set(cache_key, records, ttl=ttl)

    return {
        "code":     0,
        "symbol":   symbol,
        "interval": interval,
        "data":     records,
        "total":    len(records),
        "cached":   False,
    }
