"""Runtime observability primitives for the trading service."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class AuditEvent:
    ts: str
    event_type: str
    action: str
    status: str
    actor: str = "system"
    resource: str = ""
    request_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class AuditEventLog:
    def __init__(self, max_entries: int = 1000, db_path: str = ":memory:"):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, payload TEXT)")
        self._events: deque[AuditEvent] = deque(maxlen=max_entries)
        self._lock = threading.RLock()

    def record(
        self,
        event_type: str,
        action: str,
        status: str,
        *,
        actor: str = "system",
        resource: str = "",
        request_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            ts=datetime.now().isoformat(timespec="milliseconds"),
            event_type=event_type,
            action=action,
            status=status,
            actor=actor or "system",
            resource=resource,
            request_id=request_id,
            detail=redact(detail or {}),
        )
        with self._lock:
            self._events.append(event)
            with self.db:
                self.db.execute(
                    "INSERT INTO audit(payload) VALUES(?)", (json.dumps(asdict(event), ensure_ascii=False),)
                )
        return event

    def query(self, event_type: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT payload FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)
            ).fetchall()
        events = [json.loads(row[0]) for row in reversed(rows)]
        return [event for event in events if not event_type or event["event_type"] == event_type]


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if any(word in k.lower() for word in ("password", "secret", "token", "auth_code"))
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def label(value):
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


class RuntimeMetrics:
    def __init__(self) -> None:
        self.started_at = time.time()
        self._lock = threading.RLock()
        self.http_requests: Counter[tuple[str, str, int]] = Counter()
        self.http_latency_sum: dict[tuple[str, str], float] = {}
        self.ws_connections: Counter[str] = Counter()
        self.ws_broadcasts: Counter[str] = Counter()
        self.ws_dropped: Counter[str] = Counter()
        self.audit_events: Counter[str] = Counter()

    def record_http(self, method: str, path: str, status_code: int, elapsed_seconds: float) -> None:
        method = (
            method.upper()
            if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
            else "OTHER"
        )
        with self._lock:
            if (method, path) not in self.http_latency_sum and len(self.http_latency_sum) >= 128:
                path = "/overflow"
        key = (method.upper(), path, int(status_code))
        latency_key = (method.upper(), path)
        with self._lock:
            self.http_requests[key] += 1
            self.http_latency_sum[latency_key] = self.http_latency_sum.get(latency_key, 0.0) + max(0.0, elapsed_seconds)

    def record_ws_connect(self, channel: str) -> None:
        with self._lock:
            self.ws_connections[channel] += 1

    def record_ws_disconnect(self, channel: str) -> None:
        with self._lock:
            self.ws_connections[channel] = max(0, self.ws_connections[channel] - 1)

    def record_ws_broadcast(self, channel: str, dropped: int = 0) -> None:
        with self._lock:
            self.ws_broadcasts[channel] += 1
            if dropped:
                self.ws_dropped[channel] += dropped

    def record_audit(self, event_type: str) -> None:
        with self._lock:
            self.audit_events[event_type] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "http_requests": [
                    {"method": m, "path": p, "status": s, "count": c} for (m, p, s), c in self.http_requests.items()
                ],
                "http_latency_seconds_sum": [
                    {"method": m, "path": p, "sum": round(v, 6)} for (m, p), v in self.http_latency_sum.items()
                ],
                "ws_connections": dict(self.ws_connections),
                "ws_broadcasts": dict(self.ws_broadcasts),
                "ws_dropped": dict(self.ws_dropped),
                "audit_events": dict(self.audit_events),
            }

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP quant_uptime_seconds Process uptime in seconds.",
            "# TYPE quant_uptime_seconds gauge",
            f"quant_uptime_seconds {snap['uptime_seconds']}",
            "# HELP quant_http_requests_total HTTP requests by method, path and status.",
            "# TYPE quant_http_requests_total counter",
        ]
        for item in snap["http_requests"]:
            lines.append(
                'quant_http_requests_total{method="%s",path="%s",status="%s"} %s'
                % (label(item["method"]), label(item["path"]), item["status"], item["count"])
            )
        lines.extend(
            [
                "# HELP quant_http_latency_seconds_sum HTTP request latency sum.",
                "# TYPE quant_http_latency_seconds_sum counter",
            ]
        )
        for item in snap["http_latency_seconds_sum"]:
            lines.append(
                'quant_http_latency_seconds_sum{method="%s",path="%s"} %s'
                % (label(item["method"]), label(item["path"]), item["sum"])
            )
        lines.extend(
            [
                "# HELP quant_ws_connections Active WebSocket connections.",
                "# TYPE quant_ws_connections gauge",
            ]
        )
        for channel, count in snap["ws_connections"].items():
            lines.append(f'quant_ws_connections{{channel="{label(channel)}"}} {count}')
        lines.extend(
            [
                "# HELP quant_ws_broadcasts_total WebSocket broadcast attempts.",
                "# TYPE quant_ws_broadcasts_total counter",
            ]
        )
        for channel, count in snap["ws_broadcasts"].items():
            lines.append(f'quant_ws_broadcasts_total{{channel="{label(channel)}"}} {count}')
        lines.extend(
            [
                "# HELP quant_ws_dropped_total WebSocket clients dropped during broadcast.",
                "# TYPE quant_ws_dropped_total counter",
            ]
        )
        for channel, count in snap["ws_dropped"].items():
            lines.append(f'quant_ws_dropped_total{{channel="{label(channel)}"}} {count}')
        return "\n".join(lines) + "\n"


def structured_json(event: str, **fields: Any) -> str:
    return json.dumps(
        {
            "event": event,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            **fields,
        },
        ensure_ascii=False,
        default=str,
    )


audit_log = AuditEventLog(
    db_path=os.getenv("QUANT_AUDIT_PATH", ":memory:" if os.getenv("QUANT_ENV") == "test" else "data/runtime/audit.db")
)
metrics = RuntimeMetrics()
