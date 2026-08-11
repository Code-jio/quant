"""Durable state store for live pre-order risk controls."""

from __future__ import annotations

import json
import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_ACTIVE_WRITERS_GUARD = threading.Lock()
_ACTIVE_WRITERS: set[str] = set()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class LiveRiskWriterLease:
    """Process-lifetime ownership of one live account's risk state."""

    def __init__(self, store: "LiveRiskStateStore", handle: Any, key: str) -> None:
        self._store = store
        self._handle = handle
        self._key = key
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._store._release_file_lock(self._handle)
        finally:
            with _ACTIVE_WRITERS_GUARD:
                _ACTIVE_WRITERS.discard(self._key)

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


class LiveRiskStateStore:
    """Persist one current trading-day snapshot per anonymous account scope."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = _thread_lock_for(self.path)
        self._lock_timeout_seconds = 2.0

    def load(self, scope: str) -> Dict[str, Any] | None:
        normalized_scope = self._normalize_scope(scope)
        with self._lock:
            with self._exclusive_file_lock():
                document = self._read_document()
                record = document["scopes"].get(normalized_scope)
                return dict(record) if isinstance(record, dict) else None

    def save(self, scope: str, trading_day: str, state: Dict[str, Any]) -> None:
        normalized_scope = self._normalize_scope(scope)
        normalized_day = str(trading_day or "").strip()
        if not normalized_day:
            raise RuntimeError("risk state trading day is required")
        if not isinstance(state, dict):
            raise RuntimeError("risk state payload must be a mapping")

        with self._lock:
            with self._exclusive_file_lock():
                document = self._read_document()
                document["scopes"][normalized_scope] = {
                    "trading_day": normalized_day,
                    "state": dict(state),
                    "updated_at": datetime.now().isoformat(),
                }
                self._write_document(document)

    def acquire_writer_lease(self, scope: str) -> LiveRiskWriterLease:
        normalized_scope = self._normalize_scope(scope)
        scope_hash = hashlib.sha256(normalized_scope.encode("utf-8")).hexdigest()
        lock_path = self.path.with_name(f".{self.path.name}.{scope_hash}.writer.lock")
        key = os.path.normcase(os.path.abspath(str(lock_path)))
        with _ACTIVE_WRITERS_GUARD:
            if key in _ACTIVE_WRITERS:
                raise RuntimeError("active live risk writer already owns this account scope")
            _ACTIVE_WRITERS.add(key)
        try:
            handle = self._acquire_file_lock(lock_path, self._lock_timeout_seconds)
        except Exception as exc:
            with _ACTIVE_WRITERS_GUARD:
                _ACTIVE_WRITERS.discard(key)
            if isinstance(exc, RuntimeError):
                raise RuntimeError(
                    "active live risk writer already owns this account scope"
                ) from exc
            raise
        return LiveRiskWriterLease(self, handle, key)

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        normalized = str(scope or "").strip()
        if not normalized:
            raise RuntimeError("risk state scope is required")
        return normalized

    def _read_document(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "scopes": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"risk state file is invalid: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("scopes"), dict):
            raise RuntimeError("risk state file is invalid: scopes mapping is missing")
        return {"version": 1, "scopes": dict(raw["scopes"])}

    def _write_document(self, document: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            payload = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._sync_parent_directory()
        except (OSError, TypeError, ValueError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"risk state persistence failed: {exc}") from exc

    @contextmanager
    def _exclusive_file_lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        handle = self._acquire_file_lock(lock_path, self._lock_timeout_seconds)
        try:
            yield
        finally:
            self._release_file_lock(handle)

    def _acquire_file_lock(self, lock_path: Path, timeout_seconds: float) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = lock_path.open("a+b")
        except OSError as exc:
            raise RuntimeError(f"risk state lock is unavailable: {exc}") from exc

        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    handle.seek(0)
                    self._lock_handle(handle)
                    return handle
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("risk state lock acquisition timed out") from exc
                    time.sleep(0.05)
        except Exception:
            handle.close()
            raise

    def _release_file_lock(self, handle: Any) -> None:
        try:
            handle.seek(0)
            self._unlock_handle(handle)
        except OSError:
            pass
        finally:
            handle.close()

    @staticmethod
    def _lock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _sync_parent_directory(self) -> None:
        if os.name == "nt":
            return
        descriptor = None
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)
