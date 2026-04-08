"""
数据缓存模块
"""

import logging
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataCache:
    """数据缓存"""

    def __init__(self, max_cache_size: int = 10):
        self.cache: Dict[str, pd.DataFrame] = {}
        self.max_cache_size = max_cache_size

    def get(self, key: str) -> Optional[pd.DataFrame]:
        """获取缓存数据"""
        return self.cache.get(key)

    def put(self, key: str, df: pd.DataFrame) -> None:
        """存入缓存"""
        if len(self.cache) >= self.max_cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = df

    def clear(self) -> None:
        """清空缓存"""
        self.cache.clear()
        logger.info("数据缓存已清空")

    def remove(self, key: str) -> None:
        """移除指定缓存"""
        self.cache.pop(key, None)
