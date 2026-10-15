"""
Authentication and session helpers for the FastAPI layer.

The current implementation intentionally stays in-process because the project
does not yet have a persistent user/session store. Keeping it here isolates the
temporary choice from the route module and makes a Redis/JWT replacement local.
"""

from __future__ import annotations

import secrets
import threading
import hashlib
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, FrozenSet


OPEN_PATHS: FrozenSet[str] = frozenset(
    {
        "/auth/login",
        "/auth/status",
        "/auth/servers",
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/ws-demo",
        "/backtest/strategies",
        "/watch/search",
        "/watch/kline",
        "/watch/tick",
    }
)

SESSION_COOKIE_NAME = "quant_session"
SESSION_COOKIE_MAX_AGE = 24 * 60 * 60


@dataclass(frozen=True, repr=False)
class AccountCredential:
    """Ephemeral proof of the password accepted by the current broker connection."""

    scope: bytes
    salt: bytes
    digest: bytes

    @staticmethod
    def scope_for(config: dict) -> bytes:
        fields = ('username', 'broker_id', 'td_server', 'md_server', 'app_id', 'auth_code', 'vnpy_environment')
        payload = json.dumps([str(config.get(key, '')) for key in fields], ensure_ascii=False).encode()
        return hashlib.sha256(payload).digest()

    @classmethod
    def from_config(cls, config: dict) -> 'AccountCredential':
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac('sha256', config['password'].encode(), salt, 600_000)
        return cls(cls.scope_for(config), salt, digest)

    def matches(self, config: dict) -> bool:
        return secrets.compare_digest(self.scope, self.scope_for(config))

    def verify(self, password: str) -> bool:
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, 600_000)
        return secrets.compare_digest(self.digest, digest)


class LoginThrottle:
    """Bounded per-peer failed-login window; forwarded headers are not trusted here."""

    def __init__(self) -> None:
        self.failures: OrderedDict[str, list[float]] = OrderedDict()

    def retry_after(self, peer: str) -> int:
        now = time.monotonic()
        attempts = [at for at in self.failures.get(peer, []) if now - at < 60]
        if not attempts:
            self.failures.pop(peer, None)
            return 0
        self.failures[peer] = attempts
        return max(1, math.ceil(60 - (now - attempts[0]))) if len(attempts) >= 5 else 0

    def record(self, peer: str, success: bool) -> None:
        if success:
            self.failures.pop(peer, None)
            return
        self.retry_after(peer)
        self.failures.setdefault(peer, []).append(time.monotonic())
        self.failures.move_to_end(peer)
        while len(self.failures) > 1024:
            self.failures.popitem(last=False)


class SessionStore:
    """Thread-safe expiring bearer token store."""

    def __init__(self, ttl: timedelta | None = None) -> None:
        self.ttl = ttl or timedelta(hours=24)
        self._sessions: Dict[str, datetime] = {}
        self._identities: dict[str, dict] = {}
        self.generation = 0
        self._lock = threading.RLock()
        self._credential: AccountCredential | None = None
        self.max_sessions = 32

    def create(self, account_id: str = "", role: str = "trading") -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + self.ttl
        with self._lock:
            self._sessions[token] = expires_at
            self._identities[token] = {"account_id": account_id, "role": role, "generation": self.generation}
        return token

    def revoke_all(self) -> None:
        with self._lock:
            self.generation += 1
            self._sessions.clear()
            self._identities.clear()
            self._credential = None

    def identity(self, token: str) -> dict:
        with self._lock:
            return dict(self._identities.get(token, {})) if self.is_valid(token) else {}

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)
            self._identities.pop(token, None)

    def is_valid(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._sessions.get(token)
            if expires_at is None:
                return False
            if datetime.now() > expires_at:
                self._sessions.pop(token, None)
                self._identities.pop(token, None)
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
                self._identities.pop(token, None)


def is_open_path(path: str) -> bool:
    if path == "/backtest/run":
        from ..settings import env_bool, is_production_env

        return env_bool("QUANT_ALLOW_PUBLIC_RESEARCH", not is_production_env())
    return path in OPEN_PATHS


session_store = SessionStore()
