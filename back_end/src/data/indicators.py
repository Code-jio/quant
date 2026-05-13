"""
技术指标计算模块 — 系统内唯一真值。

所有 RSI / MACD / KDJ / Bollinger / MA / EMA / VOL_MA 均从此模块引用，
保证回测、K线展示、策略内部使用的指标计算一致。

RSI 使用 Wilder's smoothing (EWM alpha=1/N)，与主流平台 (TradingView, vnpy) 一致。
"""

import logging
from typing import Tuple, List

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI — Wilder's smoothing (EWM with alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD — 返回 (macd_line, signal_line, histogram)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def calc_kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ — 返回 (k, d, j)."""
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = ((close - low_n) / (high_n - low_n).replace(0, np.nan) * 100).fillna(50)
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_bollinger(close: pd.Series, period: int = 20, nbdev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands — 返回 (upper, mid, lower)."""
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std().fillna(0)
    upper = mid + nbdev * std
    lower = mid - nbdev * std
    return upper, mid, lower


def calc_ma(close: pd.Series, period: int) -> pd.Series:
    """简单移动均线."""
    return close.rolling(period, min_periods=1).mean()


def calc_ema(close: pd.Series, period: int) -> pd.Series:
    """指数移动均线."""
    return close.ewm(span=period, adjust=False).mean()


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """批量添加常用技术指标（兼容旧接口）。"""
    if df is None or df.empty:
        logger.warning("数据为空，无法计算技术指标")
        return df

    try:
        df = df.copy()
        close = df["close"]

        df["sma_20"] = calc_ma(close, 20)
        df["sma_60"] = calc_ma(close, 60)
        df["rsi"] = calc_rsi(close, 14)
        df["ema_12"] = calc_ema(close, 12)
        df["ema_26"] = calc_ema(close, 26)

        macd_line, signal_line, hist = calc_macd(close)
        df["macd"] = macd_line
        df["macd_signal"] = signal_line
        df["macd_hist"] = hist

        upper, mid, lower = calc_bollinger(close, 20)
        df["bb_upper"] = upper
        df["bb_middle"] = mid
        df["bb_lower"] = lower

        return df

    except Exception as e:
        logger.error(f"计算技术指标失败: {e}")
        return df


def validate_data(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """验证数据质量"""
    errors = []

    if df is None or df.empty:
        errors.append("数据为空")
        return False, errors

    required_columns = ['open', 'high', 'low', 'close', 'volume']
    for col in required_columns:
        if col not in df.columns:
            errors.append(f"缺少必需列: {col}")

    if df.isnull().any().any():
        null_counts = df.isnull().sum()
        null_cols = null_counts[null_counts > 0]
        errors.append(f"存在空值: {null_cols.to_dict()}")

    if 'high' in df.columns and 'low' in df.columns:
        invalid = df[df['high'] < df['low']]
        if not invalid.empty:
            errors.append(f"存在 high < low 的异常数据: {len(invalid)} 条")

    if 'close' in df.columns and 'open' in df.columns:
        if (df['close'] <= 0).any() or (df['open'] <= 0).any():
            errors.append("存在价格为0的异常数据")

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning(f"数据验证失败: {errors}")

    return is_valid, errors
