"""
数据管理模块 - 数据获取、存储、清洗
统一导出所有数据相关类
"""

from .errors import DatabaseError, DataLoadError
from .db import DatabaseManager
from .cache import DataCache
from .governance import BarDataMetadata, GapReport, detect_bar_gaps, summarize_ohlcv_quality
from .indicators import add_technical_indicators, validate_data
from .manager import DataManager

__all__ = [
    "DatabaseError",
    "DataLoadError",
    "DatabaseManager",
    "DataCache",
    "BarDataMetadata",
    "GapReport",
    "detect_bar_gaps",
    "summarize_ohlcv_quality",
    "add_technical_indicators",
    "validate_data",
    "DataManager",
]
