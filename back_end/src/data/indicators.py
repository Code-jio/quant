"""
技术指标计算模块
"""

import logging
from typing import Tuple, List

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """添加技术指标"""
    if df is None or df.empty:
        logger.warning("数据为空，无法计算技术指标")
        return df

    try:
        df = df.copy()

        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['sma_60'] = df['close'].rolling(window=60).mean()

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        df['ema_12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['ema_26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']

        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + 2 * bb_std
        df['bb_lower'] = df['bb_middle'] - 2 * bb_std

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
