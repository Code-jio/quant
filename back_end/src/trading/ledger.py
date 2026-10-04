"""Account-scoped durable execution journal. SQLite is the deduplication authority."""
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from ..strategy import Trade, Direction, OffsetFlag


def encode(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


class ExecutionLedger:
    def __init__(self, path, account_id):
        self.account_id = account_id
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.lock = threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS fills(account TEXT, day TEXT, exchange TEXT, id TEXT, payload TEXT,
                PRIMARY KEY(account, day, exchange, id));
            CREATE TABLE IF NOT EXISTS order_journal(account TEXT, id TEXT, payload TEXT, PRIMARY KEY(account,id));
            CREATE TABLE IF NOT EXISTS intents(account TEXT, id TEXT PRIMARY KEY, state TEXT, order_id TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS baselines(account TEXT, day TEXT, balance REAL, PRIMARY KEY(account,day));
            CREATE TABLE IF NOT EXISTS settings(account TEXT, key TEXT, payload TEXT, PRIMARY KEY(account,key));
        ''')

    def record_trade(self, trade):
        if not trade.trade_id:
            raise ValueError('Broker trade ID is required')
        day = trade.trading_day or trade.trade_time.strftime('%Y%m%d')
        with self.lock, self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO fills VALUES(?,?,?,?,?)',
                (self.account_id, day, trade.exchange, trade.trade_id, json.dumps(asdict(trade), default=encode, allow_nan=False)))
            return cursor.rowcount == 1

    def trades(self, limit=500):
        with self.lock:
            rows = self.db.execute('SELECT payload FROM fills WHERE account=? ORDER BY rowid DESC LIMIT ?',
                (self.account_id, max(1, min(limit, 10000)))).fetchall()
        result = []
        for row in rows:
            data = json.loads(row[0])
            data['direction'] = Direction(data['direction'])
            data['offset'] = OffsetFlag(data['offset']) if data.get('offset') else None
            data['trade_time'] = datetime.fromisoformat(data['trade_time'])
            result.append(Trade(**data))
        return result

    def record_order(self, order):
        with self.lock, self.db:
            self.db.execute('INSERT INTO order_journal VALUES(?,?,?) ON CONFLICT(account,id) DO UPDATE SET payload=excluded.payload',
                (self.account_id, order.order_id, json.dumps(asdict(order), default=encode, allow_nan=False)))

    def begin_intent(self, signal):
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO intents VALUES(?,?,?,?,?)', (self.account_id, identity, 'pending', '',
                json.dumps(asdict(signal), default=encode, allow_nan=False)))
        return identity

    def complete_intent(self, identity, order_id):
        with self.lock, self.db:
            self.db.execute('UPDATE intents SET state=?,order_id=? WHERE id=? AND account=?',
                ('sent' if order_id else 'rejected', order_id, identity, self.account_id))

    def unresolved(self):
        with self.lock:
            return self.db.execute("SELECT id FROM intents WHERE account=? AND state='pending'", (self.account_id,)).fetchall()

    def day_baseline(self, day, balance):
        if not day or balance <= 0:
            return 0
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO baselines VALUES(?,?,?)', (self.account_id, day, balance))
            return self.db.execute('SELECT balance FROM baselines WHERE account=? AND day=?', (self.account_id, day)).fetchone()[0]

    def save_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO settings VALUES(?,?,?) ON CONFLICT(account,key) DO UPDATE SET payload=excluded.payload',
                (self.account_id, key, json.dumps(value, allow_nan=False)))

    def load_setting(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT payload FROM settings WHERE account=? AND key=?', (self.account_id,key)).fetchone()
            return json.loads(row[0]) if row else default

    def close(self):
        self.db.close()
