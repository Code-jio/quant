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
from .governance import BarDataMetadata, normalize_metadata

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 3

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise DatabaseError(f"Invalid SQL identifier: {name!r}")


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
                    data_source TEXT DEFAULT 'unknown',
                    adjustment TEXT DEFAULT 'raw',
                    rollover_rule TEXT DEFAULT 'none',
                    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, timeframe, datetime)
                )
            """)
            self._ensure_column(cursor, "bars", "data_source", "TEXT DEFAULT 'unknown'")
            self._ensure_column(cursor, "bars", "adjustment", "TEXT DEFAULT 'raw'")
            self._ensure_column(cursor, "bars", "rollover_rule", "TEXT DEFAULT 'none'")
            self._ensure_column(cursor, "bars", "ingested_at", "TEXT")
            cursor.execute(
                """
                UPDATE bars
                SET ingested_at = ?
                WHERE ingested_at IS NULL OR ingested_at = ''
                """,
                (datetime.now().isoformat(timespec="seconds"),),
            )
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol_timeframe_datetime
                ON bars(symbol, timeframe, datetime)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bar_metadata (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    data_source TEXT NOT NULL DEFAULT 'unknown',
                    adjustment TEXT NOT NULL DEFAULT 'raw',
                    rollover_rule TEXT NOT NULL DEFAULT 'none',
                    first_datetime TEXT,
                    last_datetime TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, timeframe)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                )
            """)
            cursor.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (
                    CURRENT_SCHEMA_VERSION,
                    datetime.now().isoformat(timespec="seconds"),
                    "bars governance metadata, safe ingested_at migration, and migration tracking",
                ),
            )
            cursor.execute("PRAGMA user_version = " + str(CURRENT_SCHEMA_VERSION))
            conn.commit()
            conn.close()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"数据库初始化失败: {e}")
            raise DatabaseError(f"数据库初始化失败: {e}")

    @staticmethod
    def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
        _validate_identifier(table)
        _validate_identifier(column)
        cursor.execute("PRAGMA table_info(" + table + ")")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            cursor.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + ddl)

    @retry(max_retries=3, initial_delay=0.5, backoff_factor=1.5)
    def save_bars(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        *,
        data_source: str = "unknown",
        adjustment: str = "raw",
        rollover_rule: str = "none",
    ) -> bool:
        """保存K线数据"""
        if df.empty:
            logger.warning(f"数据为空，跳过保存: {symbol}")
            return False

        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            df = df.copy()
            if "datetime" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "datetime"})
            metadata = normalize_metadata(
                symbol,
                timeframe,
                data_source=data_source,
                adjustment=adjustment,
                rollover_rule=rollover_rule,
            )
            df['symbol'] = symbol
            df['timeframe'] = timeframe
            df['datetime'] = df['datetime'].astype(str)
            df['data_source'] = metadata.data_source
            df['adjustment'] = metadata.adjustment
            df['rollover_rule'] = metadata.rollover_rule
            df['ingested_at'] = metadata.ingested_at

            columns = [
                "symbol", "timeframe", "datetime",
                "open", "high", "low", "close", "volume", "open_interest",
                "data_source", "adjustment", "rollover_rule", "ingested_at",
            ]
            for col in columns:
                if col not in df.columns:
                    df[col] = 0 if col == "open_interest" else None
            records = df[columns].to_dict("records")
            conn.executemany(
                """
                INSERT OR REPLACE INTO bars (
                    symbol, timeframe, datetime, open, high, low, close, volume,
                    open_interest, data_source, adjustment, rollover_rule, ingested_at
                ) VALUES (
                    :symbol, :timeframe, :datetime, :open, :high, :low, :close, :volume,
                    :open_interest, :data_source, :adjustment, :rollover_rule, :ingested_at
                )
                """,
                records,
            )
            self._upsert_metadata(conn, metadata)
            conn.commit()
            conn.close()
            logger.info(f"保存 {symbol} {timeframe} 数据 {len(df)} 条")
            return True

        except sqlite3.IntegrityError:
            logger.warning(f"数据重复，跳过: {symbol} {timeframe}")
            return False
        except sqlite3.Error as e:
            logger.error(f"保存数据失败: {e}")
            raise DatabaseError(f"保存数据失败: {e}")

    def _upsert_metadata(self, conn: sqlite3.Connection, metadata: BarDataMetadata) -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT MIN(datetime), MAX(datetime), COUNT(*)
            FROM bars
            WHERE symbol = ? AND timeframe = ?
            """,
            (metadata.symbol, metadata.timeframe),
        )
        row = cursor.fetchone()
        if row is None:
            return
        first_dt, last_dt, row_count = row
        cursor.execute(
            """
            INSERT INTO bar_metadata (
                symbol, timeframe, data_source, adjustment, rollover_rule,
                first_datetime, last_datetime, row_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                data_source = excluded.data_source,
                adjustment = excluded.adjustment,
                rollover_rule = excluded.rollover_rule,
                first_datetime = excluded.first_datetime,
                last_datetime = excluded.last_datetime,
                row_count = excluded.row_count,
                updated_at = excluded.updated_at
            """,
            (
                metadata.symbol,
                metadata.timeframe,
                metadata.data_source,
                metadata.adjustment,
                metadata.rollover_rule,
                first_dt,
                last_dt,
                int(row_count or 0),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

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

    def get_metadata(self, symbol: str, timeframe: str = "1d") -> Optional[dict]:
        """获取指定合约/周期的数据治理元信息。"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM bar_metadata
                WHERE symbol = ? AND timeframe = ?
                """,
                (symbol, timeframe),
            )
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"查询数据元信息失败: {e}")
            return None
