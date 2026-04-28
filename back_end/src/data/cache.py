"""
数据缓存模块
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    data: pd.DataFrame
    created_at: float
    expires_at: float


class DataCache:
    """DataFrame 内存缓存，带容量上限和 TTL。"""

    def __init__(self, max_cache_size: int = 10, ttl_seconds: int = 300):
        self.cache: Dict[str, _CacheEntry] = {}
        self.max_cache_size = max_cache_size
        self.ttl_seconds = max(1, int(ttl_seconds))

    def get(self, key: str) -> Optional[pd.DataFrame]:
        """获取缓存数据"""
        entry = self.cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self.cache.pop(key, None)
            logger.debug("数据缓存已过期: %s", key)
            return None
        return entry.data.copy()

    def put(self, key: str, df: pd.DataFrame, ttl_seconds: int | None = None) -> None:
        """存入缓存"""
        self._prune_expired()
        if len(self.cache) >= self.max_cache_size:
            oldest_key = min(self.cache, key=lambda k: self.cache[k].created_at)
            self.cache.pop(oldest_key, None)
        now = time.monotonic()
        ttl = max(1, int(ttl_seconds or self.ttl_seconds))
        self.cache[key] = _CacheEntry(
            data=df.copy(),
            created_at=now,
            expires_at=now + ttl,
        )

    def clear(self) -> None:
        """清空缓存"""
        self.cache.clear()
        logger.info("数据缓存已清空")

    def remove(self, key: str) -> None:
        """移除指定缓存"""
        self.cache.pop(key, None)

    def stats(self) -> dict:
        """返回缓存状态，用于健康检查和调试。"""
        self._prune_expired()
        return {
            "entries": len(self.cache),
            "max_entries": self.max_cache_size,
            "ttl_seconds": self.ttl_seconds,
        }

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self.cache.items() if entry.expires_at <= now]
        for key in expired:
            self.cache.pop(key, None)
