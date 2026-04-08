"""
数据库管理模块
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Tuple, Optional

import pandas as pd

from ..common.exceptions import retry
from .errors import DatabaseError

logger = logging.getLogger(__name__)


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: str = "data/historical/quotes.db",
                 max_retries: int = 3, timeout: float = 30.0):
        self.db_path = db_path
        self.max_retries = max_retries
        self.timeout = timeout
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
            raise DatabaseError(f"加载数据失败: {e}")
        except Exception as e:
            logger.error(f"加载数据异常: {e}")
            raise DatabaseError(f"加载数据异常: {e}")

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
