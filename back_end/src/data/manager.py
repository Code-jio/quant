"""
数据管理器模块
"""

import logging
from datetime import datetime
from typing import Dict

import pandas as pd
import numpy as np

from .db import DatabaseManager
from .cache import DataCache
from .errors import DatabaseError, DataLoadError
from .indicators import add_technical_indicators, validate_data

logger = logging.getLogger(__name__)


class DataManager:
    """数据管理器 - 统一数据访问接口"""

    def __init__(self, db_path: str = "data/historical/quotes.db",
                 max_retries: int = 3, max_cache_size: int = 10):
        self.db = DatabaseManager(db_path, max_retries=max_retries)
        self.cache = DataCache(max_cache_size=max_cache_size)

    def get_bars(self, symbol: str, start_date: str, end_date: str,
                 timeframe: str = "1d", use_cache: bool = True) -> pd.DataFrame:
        """获取K线数据"""
        cache_key = f"{symbol}_{timeframe}_{start_date}_{end_date}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.debug(f"从缓存获取数据: {cache_key}")
                return cached

        try:
            df = self.db.load_bars(symbol, start_date, end_date, timeframe)

            if use_cache and not df.empty:
                self.cache.put(cache_key, df)

            return df

        except DataLoadError as e:
            logger.error(f"加载数据失败: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"获取数据异常: {e}")
            return pd.DataFrame()

    def save_bars(self, df: pd.DataFrame, symbol: str, timeframe: str = "1d") -> bool:
        """保存K线数据"""
        try:
            result = self.db.save_bars(df, symbol, timeframe)
            self.cache.clear()
            return result
        except DatabaseError as e:
            logger.error(f"保存数据失败: {e}")
            return False

    def generate_sample_data(self, symbol: str, days: int = 500,
                             timeframe: str = "1d") -> pd.DataFrame:
        """生成模拟K线数据用于测试"""
        try:
            np.random.seed(42)
            dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

            initial_price = 100.0
            returns = np.random.randn(days) * 0.02
            close_prices = initial_price * np.exp(np.cumsum(returns))

            high_mult = 1 + np.abs(np.random.randn(days)) * 0.015 + 0.003
            low_mult = 1 - np.abs(np.random.randn(days)) * 0.015 - 0.003
            high_prices = close_prices * high_mult
            low_prices = close_prices * low_mult

            open_prices = close_prices * (1 + np.random.randn(days) * 0.008)
            open_prices = np.clip(open_prices, low_prices, high_prices)

            high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
            low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))
            same_mask = (open_prices == close_prices)
            open_prices[same_mask] *= 1.001

            df = pd.DataFrame({
                'datetime': dates,
                'open': open_prices,
                'high': high_prices,
                'low': low_prices,
                'close': close_prices,
                'volume': np.random.randint(1000, 10000, days),
                'open_interest': np.random.randint(5000, 50000, days)
            })

            self.save_bars(df, symbol, timeframe)
            return df

        except Exception as e:
            logger.error(f"生成模拟数据失败: {e}")
            return pd.DataFrame()

    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标"""
        return add_technical_indicators(df)

    def validate_data(self, df: pd.DataFrame):
        """验证数据质量"""
        return validate_data(df)

    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
