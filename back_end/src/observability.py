"""Runtime observability primitives for the trading service."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class AuditEvent:
    ts: str
    event_type: str
    action: str
    status: str
    category: str = "system"
    actor: str = "system"
    resource: str = ""
    request_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class AuditEventLog:
    _CATEGORIES = frozenset({"trade", "monitor", "error", "system"})

    def __init__(self, max_entries: int = 1000, persistence_dir: str | Path | None = None):
        self._events: deque[AuditEvent] = deque(maxlen=max_entries)
        self._lock = threading.RLock()
        self._persistence_dir = Path(persistence_dir) if persistence_dir is not None else None
        self._last_persistence_error = ""
        if self._persistence_dir is not None:
            try:
                self._persistence_dir.mkdir(parents=True, exist_ok=True)
                self._load_persisted_events()
            except OSError as exc:
                self._last_persistence_error = str(exc)

    def configure_persistence(self, persistence_dir: str | Path) -> None:
        """Switch to a runtime log directory and restore its latest events."""
        with self._lock:
            self._persistence_dir = Path(persistence_dir)
            self._events.clear()
            try:
                self._persistence_dir.mkdir(parents=True, exist_ok=True)
                self._load_persisted_events()
                self._last_persistence_error = ""
            except OSError as exc:
                self._last_persistence_error = str(exc)

    @classmethod
    def _resolve_category(cls, event_type: str, status: str, category: str = "") -> str:
        normalized_category = category.strip().lower()
        if normalized_category in cls._CATEGORIES:
            return normalized_category

        normalized_status = status.strip().lower()
        if normalized_status in {"error", "failed", "rejected", "failure"}:
            return "error"

        normalized_event_type = event_type.strip().lower()
        if normalized_event_type in {"order", "trade"}:
            return "trade"
        if normalized_event_type in {"risk", "connection", "monitor"}:
            return "monitor"
        return "system"

    @classmethod
    def _deserialize_event(cls, payload: Any) -> AuditEvent | None:
        if not isinstance(payload, dict):
            return None
        required = ("ts", "event_type", "action", "status")
        if any(not isinstance(payload.get(name), str) for name in required):
            return None
        detail = payload.get("detail", {})
        if not isinstance(detail, dict):
            return None
        event_type = payload["event_type"]
        status = payload["status"]
        return AuditEvent(
            ts=payload["ts"],
            event_type=event_type,
            action=payload["action"],
            status=status,
            category=cls._resolve_category(event_type, status, str(payload.get("category", ""))),
            actor=str(payload.get("actor") or "system"),
            resource=str(payload.get("resource") or ""),
            request_id=str(payload.get("request_id") or ""),
            detail=detail,
        )

    def _load_persisted_events(self) -> None:
        assert self._persistence_dir is not None
        loaded: list[AuditEvent] = []
        paths = sorted(self._persistence_dir.rglob("*.jsonl"), key=lambda path: path.as_posix())
        for path in paths:
            try:
                with path.open("rb") as handle:
                    for line in handle:
                        try:
                            event = self._deserialize_event(json.loads(line.decode("utf-8")))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if event is not None:
                            loaded.append(event)
            except OSError:
                continue
        loaded.sort(key=lambda event: event.ts)
        self._events.extend(loaded)

    def _persist_event(self, event: AuditEvent) -> None:
        if self._persistence_dir is None:
            return
        event_date = event.ts[:10]
        category_dir = self._persistence_dir / event.category
        category_dir.mkdir(parents=True, exist_ok=True)
        path = category_dir / f"{event_date}.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str))
            handle.write("\n")

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
        category: str = "",
    ) -> AuditEvent:
        resolved_category = self._resolve_category(event_type, status, category)
        event = AuditEvent(
            ts=datetime.now().isoformat(timespec="milliseconds"),
            event_type=event_type,
            action=action,
            status=status,
            category=resolved_category,
            actor=actor or "system",
            resource=resource,
            request_id=request_id,
            detail=detail or {},
        )
        with self._lock:
            self._events.append(event)
            try:
                self._persist_event(event)
                self._last_persistence_error = ""
            except OSError as exc:
                self._last_persistence_error = str(exc)
        return event

    def persistence_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._persistence_dir is not None,
                "directory": str(self._persistence_dir or ""),
                "ok": not self._last_persistence_error,
                "error": self._last_persistence_error,
            }

    def query(self, event_type: str = "", category: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if isinstance(category, int):
            # Preserve the former query(event_type, limit) positional call.
            limit = category
            category = ""
        with self._lock:
            events = list(self._events)
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        if category:
            events = [event for event in events if event.category == category]
        return [asdict(event) for event in events[-max(1, min(limit, 1000)):]]


def runtime_log_category(name: str, level: str, message: str) -> str:
    normalized_level = level.upper()
    normalized_name = name.lower()
    normalized_message = message.lower()
    if normalized_level in {"ERROR", "CRITICAL"}:
        return "error"
    if "risk" in normalized_name or "风控" in normalized_message:
        return "monitor"
    if any(
        token in normalized_message
        for token in ("委托", "成交", "撤单", "下单", "发送信号", "order submitted", "trade filled")
    ):
        return "trade"
    if any(
        token in normalized_name or token in normalized_message
        for token in ("risk", "trading", "gateway", "vnpy", "connection", "连接", "重连", "断开")
    ):
        return "monitor"
    return "system"


class AuditLogHandler(logging.Handler):
    """Persist standard-library logs into the categorized audit store."""

    def __init__(self, event_log: AuditEventLog):
        super().__init__(level=logging.DEBUG)
        self.event_log = event_log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", ""),
            }
            category = runtime_log_category(entry["name"], entry["level"], entry["message"])
            entry["category"] = category
            self.event_log.record(
                "log",
                record.name,
                record.levelname,
                category=category,
                request_id=entry["request_id"],
                detail=entry,
            )
        except Exception:
            self.handleError(record)


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
                    {"method": m, "path": p, "status": s, "count": c}
                    for (m, p, s), c in self.http_requests.items()
                ],
                "http_latency_seconds_sum": [
                    {"method": m, "path": p, "sum": round(v, 6)}
                    for (m, p), v in self.http_latency_sum.items()
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
                % (item["method"], item["path"], item["status"], item["count"])
            )
        lines.extend([
            "# HELP quant_http_latency_seconds_sum HTTP request latency sum.",
            "# TYPE quant_http_latency_seconds_sum counter",
        ])
        for item in snap["http_latency_seconds_sum"]:
            lines.append(
                'quant_http_latency_seconds_sum{method="%s",path="%s"} %s'
                % (item["method"], item["path"], item["sum"])
            )
        lines.extend([
            "# HELP quant_ws_connections Active WebSocket connections.",
            "# TYPE quant_ws_connections gauge",
        ])
        for channel, count in snap["ws_connections"].items():
            lines.append(f'quant_ws_connections{{channel="{channel}"}} {count}')
        lines.extend([
            "# HELP quant_ws_broadcasts_total WebSocket broadcast attempts.",
            "# TYPE quant_ws_broadcasts_total counter",
        ])
        for channel, count in snap["ws_broadcasts"].items():
            lines.append(f'quant_ws_broadcasts_total{{channel="{channel}"}} {count}')
        lines.extend([
            "# HELP quant_ws_dropped_total WebSocket clients dropped during broadcast.",
            "# TYPE quant_ws_dropped_total counter",
        ])
        for channel, count in snap["ws_dropped"].items():
            lines.append(f'quant_ws_dropped_total{{channel="{channel}"}} {count}')
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


audit_log = AuditEventLog()
metrics = RuntimeMetrics()
