"""
Authentication and session helpers for the FastAPI layer.

The current implementation intentionally stays in-process because the project
does not yet have a persistent user/session store. Keeping it here isolates the
temporary choice from the route module and makes a Redis/JWT replacement local.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta
from typing import Dict, FrozenSet


OPEN_PATHS: FrozenSet[str] = frozenset(
    {
        "/auth/login",
        "/auth/logout",
        "/auth/status",
        "/auth/servers",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/ws-demo",
        "/system/logs",
        "/backtest/strategies",
        "/watch/search",
        "/watch/kline",
        "/watch/tick",
    }
)

SESSION_COOKIE_NAME = "quant_session"
SESSION_COOKIE_MAX_AGE = 24 * 60 * 60


class SessionStore:
    """Thread-safe expiring bearer token store."""

    def __init__(self, ttl: timedelta | None = None) -> None:
        self.ttl = ttl or timedelta(hours=24)
        self._sessions: Dict[str, datetime] = {}
        self._lock = threading.RLock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + self.ttl
        with self._lock:
            self._sessions[token] = expires_at
        return token

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def is_valid(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._sessions.get(token)
            if expires_at is None:
                return False
            if datetime.now() > expires_at:
                self._sessions.pop(token, None)
                return False
            return True

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
        with self._lock:
            expired = [token for token, expires_at in self._sessions.items() if now > expires_at]
            for token in expired:
                self._sessions.pop(token, None)


def is_open_path(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith("/ws")


session_store = SessionStore()
