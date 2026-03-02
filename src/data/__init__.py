"""
数据管理模块 - 数据获取、存储、清洗
"""

import os
import sqlite3
import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from functools import wraps
import pandas as pd
import numpy as np

from ..common.exceptions import ExceptionHandler, retry

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """数据源抽象基类"""
    
    @abstractmethod
    def fetch_bars(self, symbol: str, start_date: str, end_date: str, 
                   timeframe: str = "1d") -> pd.DataFrame:
        """获取K线数据"""
        pass
    
    @abstractmethod
    def fetch_ticks(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取Tick数据"""
        pass


class DatabaseError(Exception):
    """数据库异常"""
    pass


class DataLoadError(Exception):
    """数据加载异常"""
    pass


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: str = "data/historical/quotes.db",
                 max_retries: int = 3, timeout: float = 30.0):
        self.db_path = db_path
        self.max_retries = max_retries
        self.timeout = timeout
        self.exception_handler = ExceptionHandler()
        self._init_database()

    @retry(max_retries=3, initial_delay=0.5, backoff_factor=1.5)
    def _init_database(self):
        """初始化数据库"""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    open_interest REAL DEFAULT 0,
                    UNIQUE(symbol, timeframe, datetime)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol_timeframe_datetime
                ON bars(symbol, timeframe, datetime)
            """)
            conn.commit()
            conn.close()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"数据库初始化失败: {e}")
            raise DatabaseError(f"数据库初始化失败: {e}")

    @retry(max_retries=3, initial_delay=0.5, backoff_factor=1.5)
    def save_bars(self, df: pd.DataFrame, symbol: str, timeframe: str) -> bool:
        """保存K线数据"""
        if df.empty:
            logger.warning(f"数据为空，跳过保存: {symbol}")
            return False

        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            df = df.copy()
            df['symbol'] = symbol
            df['timeframe'] = timeframe
            df['datetime'] = df['datetime'].astype(str)

            df.to_sql('bars', conn, if_exists='append', index=False)
            conn.close()
            logger.info(f"保存 {symbol} {timeframe} 数据 {len(df)} 条")
            return True

        except sqlite3.IntegrityError as e:
            logger.warning(f"数据重复，跳过: {symbol} {timeframe}")
            return False
        except sqlite3.Error as e:
            logger.error(f"保存数据失败: {e}")
            raise DatabaseError(f"保存数据失败: {e}")

    @retry(max_retries=3, initial_delay=0.5, backoff_factor=1.5)
    def load_bars(self, symbol: str, start_date: str, end_date: str,
                  timeframe: str = "1d") -> pd.DataFrame:
        """加载K线数据"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            query = """
                SELECT datetime, open, high, low, close, volume, open_interest
                FROM bars
                WHERE symbol = ? AND timeframe = ? AND datetime >= ? AND datetime <= ?
                ORDER BY datetime
            """
            df = pd.read_sql_query(query, conn, params=(symbol, timeframe, start_date, end_date))
            conn.close()

            if not df.empty:
                df['datetime'] = pd.to_datetime(df['datetime'])
                df.set_index('datetime', inplace=True)

            logger.debug(f"加载 {symbol} {timeframe} 数据 {len(df)} 条")
            return df

        except sqlite3.Error as e:
            logger.error(f"加载数据失败: {e}")
            raise DataLoadError(f"加载数据失败: {e}")
        except Exception as e:
            logger.error(f"加载数据异常: {e}\n{traceback.format_exc()}")
            raise DataLoadError(f"加载数据异常: {e}")

    @retry(max_retries=3, initial_delay=0.5, backoff_factor=1.5)
    def get_available_symbols(self) -> List[str]:
        """获取可用合约列表"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT symbol FROM bars")
            symbols = [row[0] for row in cursor.fetchall()]
            conn.close()
            return symbols
        except sqlite3.Error as e:
            logger.error(f"查询合约列表失败: {e}")
            return []

    def get_data_range(self, symbol: str, timeframe: str = "1d") -> Tuple[Optional[str], Optional[str]]:
        """获取数据日期范围"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            query = """
                SELECT MIN(datetime), MAX(datetime)
                FROM bars
                WHERE symbol = ? AND timeframe = ?
            """
            cursor = conn.cursor()
            cursor.execute(query, (symbol, timeframe))
            result = cursor.fetchone()
            conn.close()
            return result[0], result[1]
        except sqlite3.Error as e:
            logger.error(f"查询数据范围失败: {e}")
            return None, None


class DataManager:
    """数据管理器 - 统一数据访问接口"""
    
    def __init__(self, db_path: str = "data/historical/quotes.db",
                 max_retries: int = 3):
        self.db = DatabaseManager(db_path, max_retries=max_retries)
        self.cache: Dict[str, pd.DataFrame] = {}
        self.max_cache_size = 10
    
    def get_bars(self, symbol: str, start_date: str, end_date: str, 
                 timeframe: str = "1d", use_cache: bool = True) -> pd.DataFrame:
        """获取K线数据"""
        cache_key = f"{symbol}_{timeframe}_{start_date}_{end_date}"
        
        if use_cache and cache_key in self.cache:
            logger.debug(f"从缓存获取数据: {cache_key}")
            return self.cache[cache_key]
        
        try:
            df = self.db.load_bars(symbol, start_date, end_date, timeframe)
            
            if use_cache and not df.empty:
                if len(self.cache) >= self.max_cache_size:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[cache_key] = df
            
            return df
            
        except DataLoadError as e:
            logger.error(f"加载数据失败: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"获取数据异常: {e}\n{traceback.format_exc()}")
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
            
            high_prices = close_prices * (1 + np.abs(np.random.randn(days)) * 0.01)
            low_prices = close_prices * (1 - np.abs(np.random.randn(days)) * 0.01)
            open_prices = close_prices + np.random.randn(days) * 0.005
            
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
            logger.error(f"生成模拟数据失败: {e}\n{traceback.format_exc()}")
            return pd.DataFrame()
    
    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
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
            logger.error(f"计算技术指标失败: {e}\n{traceback.format_exc()}")
            return df
    
    def validate_data(self, df: pd.DataFrame) -> Tuple[bool, List[str]]:
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
    
    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
        logger.info("数据缓存已清空")
