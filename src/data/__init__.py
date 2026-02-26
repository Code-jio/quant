"""
数据管理模块 - 数据获取、存储、清洗
"""

import os
import sqlite3
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

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


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = "data/historical/quotes.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
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
    
    def save_bars(self, df: pd.DataFrame, symbol: str, timeframe: str):
        if df.empty:
            return
        conn = sqlite3.connect(self.db_path)
        df = df.copy()
        df['symbol'] = symbol
        df['timeframe'] = timeframe
        df['datetime'] = df['datetime'].astype(str)
        df.to_sql('bars', conn, if_exists='append', index=False)
        conn.close()
        logger.info(f"保存 {symbol} {timeframe} 数据 {len(df)} 条")
    
    def load_bars(self, symbol: str, start_date: str, end_date: str, 
                  timeframe: str = "1d") -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
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
        return df
    
    def get_available_symbols(self) -> List[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM bars")
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        return symbols


class DataManager:
    """数据管理器 - 统一数据访问接口"""
    
    def __init__(self, db_path: str = "data/historical/quotes.db"):
        self.db = DatabaseManager(db_path)
        self.cache: Dict[str, pd.DataFrame] = {}
    
    def get_bars(self, symbol: str, start_date: str, end_date: str, 
                 timeframe: str = "1d", use_cache: bool = True) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}_{start_date}_{end_date}"
        
        if use_cache and cache_key in self.cache:
            return self.cache[cache_key]
        
        df = self.db.load_bars(symbol, start_date, end_date, timeframe)
        
        if use_cache and not df.empty:
            self.cache[cache_key] = df
        
        return df
    
    def save_bars(self, df: pd.DataFrame, symbol: str, timeframe: str = "1d"):
        self.db.save_bars(df, symbol, timeframe)
        self.cache.clear()
    
    def generate_sample_data(self, symbol: str, days: int = 500, 
                             timeframe: str = "1d") -> pd.DataFrame:
        """生成模拟K线数据用于测试"""
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
    
    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标"""
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
