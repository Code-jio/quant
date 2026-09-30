"""
Authentication and session helpers for the FastAPI layer.

Session tokens are persisted to SQLite for local continuity. Production
startup revokes them so a browser session can never cross a live-account
process generation. The in-memory cache is the primary read path; writes go
through to SQLite synchronously.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, FrozenSet


OPEN_PATHS: FrozenSet[str] = frozenset(
    {
        "/auth/login",
        "/auth/status",
        "/auth/servers",
        "/health",
        "/metrics",
        "/trial-run/config",
        "/trial-run/status",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/ws-demo",
        "/backtest/strategies",
        "/watch/search",
        "/watch/kline",
    }
)

SESSION_COOKIE_NAME = "quant_session"
SESSION_COOKIE_MAX_AGE = 24 * 60 * 60


def default_session_db() -> str:
    configured = os.getenv("QUANT_SESSION_DB", "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str((Path(__file__).resolve().parents[2] / "data" / "historical" / "sessions.db").resolve())


DEFAULT_SESSION_DB = default_session_db()


class SessionStore:
    """Thread-safe expiring bearer token store with SQLite persistence."""

    def __init__(
        self,
        ttl: timedelta | None = None,
        db_path: str = DEFAULT_SESSION_DB,
    ) -> None:
        self.ttl = ttl or timedelta(hours=24)
        self._sessions: Dict[str, datetime] = {}
        self._account_ids: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._db_path = db_path
        self._init_db()
        self._load_sessions()

    # ── SQLite helpers ───────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        import os
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                expires_at  TEXT NOT NULL,
                account_id  TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def _load_sessions(self) -> None:
        conn = self._get_conn()
        now = datetime.now()
        rows = conn.execute(
            "SELECT token, expires_at, account_id FROM sessions WHERE expires_at > ?",
            (now.isoformat(),),
        ).fetchall()
        conn.close()
        with self._lock:
            for token, expires_at_str, account_id in rows:
                try:
                    self._sessions[token] = datetime.fromisoformat(expires_at_str)
                    self._account_ids[token] = str(account_id or "")
                except (ValueError, TypeError):
                    pass

    # ── Public API ───────────────────────────────────────────────────────

    def create(self, account_id: str = "") -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + self.ttl
        with self._lock:
            self._sessions[token] = expires_at
            self._account_ids[token] = str(account_id or "")
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sessions (token, expires_at, account_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (token, expires_at.isoformat(), account_id, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return token

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)
            self._account_ids.pop(token, None)
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()

    def is_valid(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._sessions.get(token)
            if expires_at is None:
                return False
            if datetime.now() > expires_at:
                self._sessions.pop(token, None)
                self._account_ids.pop(token, None)
                self._prune_token_from_db(token)
                return False
            return True

    def is_valid_for_account(self, token: str, account_id: str) -> bool:
        if not self.is_valid(token):
            return False
        expected = str(account_id or "").strip()
        if not expected:
            return False
        with self._lock:
            return self._account_ids.get(token, "") == expected

    def revoke_all(self) -> None:
        """Invalidate every session before a live account generation changes."""
        with self._lock:
            self._sessions.clear()
            self._account_ids.clear()
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM sessions")
            conn.commit()
        finally:
            conn.close()

    def has_active_sessions(self) -> bool:
        self.prune_expired()
        with self._lock:
            return bool(self._sessions)

    def active_count(self) -> int:
        self.prune_expired()
        with self._lock:
            return len(self._sessions)

    def prune_expired(self) -> None:
        now = datetime.now()
        expired: list[str] = []
        with self._lock:
            for token, expires_at in list(self._sessions.items()):
                if now > expires_at:
                    expired.append(token)
            for token in expired:
                self._sessions.pop(token, None)
                self._account_ids.pop(token, None)
        for token in expired:
            self._prune_token_from_db(token)

    def _prune_token_from_db(self, token: str) -> None:
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            conn.close()
        except Exception:
            pass


def is_open_path(path: str) -> bool:
    return path in OPEN_PATHS


session_store = SessionStore()
