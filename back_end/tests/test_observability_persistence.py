from __future__ import annotations

import json
import logging
from pathlib import Path

from src.observability import AuditEventLog, AuditLogHandler


def _jsonl_records(persistence_dir: Path) -> list[tuple[Path, dict]]:
    records: list[tuple[Path, dict]] = []
    for path in persistence_dir.rglob("*.jsonl"):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append((path, json.loads(line)))
    return records


def test_audit_events_persist_each_category_and_are_filterable(tmpdir):
    persistence_dir = Path(tmpdir)
    log = AuditEventLog(persistence_dir=persistence_dir)
    cases = [
        ("order", "order_submitted", "trade"),
        ("risk", "risk_checked", "monitor"),
        ("status", "strategy_rejected", "error"),
        ("system", "service_started", "system"),
    ]

    for event_type, action, category in cases:
        log.record(event_type, action, "ok", category=category)

    persisted = _jsonl_records(persistence_dir)
    assert {record["category"] for _, record in persisted} == {
        "trade",
        "monitor",
        "error",
        "system",
    }
    for category in ("trade", "monitor", "error", "system"):
        assert [event["category"] for event in log.query(category=category)] == [category]

    # Category/date partitioning may use directories or filename segments; do
    # not couple the contract test to one concrete naming convention.
    assert all(
        any(category in str(path.relative_to(persistence_dir)) for path, record in persisted if record["category"] == category)
        for category in ("trade", "monitor", "error", "system")
    )


def test_new_log_instance_reads_utf8_jsonl_events_from_same_directory(tmpdir):
    persistence_dir = Path(tmpdir)
    first = AuditEventLog(persistence_dir=persistence_dir)
    first.record(
        "connection",
        "gateway_connected",
        "ok",
        category="monitor",
        detail={"message": "网关连接成功"},
    )

    second = AuditEventLog(persistence_dir=persistence_dir)
    restored = second.query(event_type="connection", category="monitor")

    assert len(restored) == 1
    assert restored[0]["detail"] == {"message": "网关连接成功"}
    persisted = _jsonl_records(persistence_dir)
    assert persisted[0][1]["detail"]["message"] == "网关连接成功"


def test_query_combines_event_type_category_and_limit_for_persisted_events(tmpdir):
    log = AuditEventLog(persistence_dir=Path(tmpdir))
    log.record("order", "first", "ok", category="trade")
    log.record("order", "second", "ok", category="trade")
    log.record("trade", "filled", "ok", category="trade")
    log.record("risk", "checked", "ok", category="monitor")

    filtered = log.query(event_type="order", category="trade", limit=1)

    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "order"
    assert filtered[0]["category"] == "trade"
    assert filtered[0]["action"] == "second"
    assert log.query(event_type="order", category="monitor") == []


def test_logging_handler_persists_trade_monitor_and_error_categories(tmpdir):
    store = AuditEventLog(persistence_dir=Path(tmpdir))
    handler = AuditLogHandler(store)
    gateway_logger = logging.getLogger("src.trading.vnpy_gateway.test")
    engine_logger = logging.getLogger("src.trading.engine.test")
    gateway_logger.setLevel(logging.INFO)
    engine_logger.setLevel(logging.INFO)
    gateway_logger.addHandler(handler)
    engine_logger.addHandler(handler)
    gateway_logger.propagate = False
    engine_logger.propagate = False
    try:
        gateway_logger.info("交易服务器连接断开")
        gateway_logger.error("柜台返回错误")
        engine_logger.info("发送信号: rb2610 long 1@3130")
    finally:
        gateway_logger.removeHandler(handler)
        engine_logger.removeHandler(handler)
        gateway_logger.propagate = True
        engine_logger.propagate = True

    restored = AuditEventLog(persistence_dir=Path(tmpdir))
    assert len(restored.query(event_type="log", category="trade")) == 1
    assert len(restored.query(event_type="log", category="monitor")) == 1
    assert len(restored.query(event_type="log", category="error")) == 1


def test_unavailable_persistence_directory_degrades_without_breaking_runtime(tmp_path):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    blocked_dir = blocked_parent / "audit"

    store = AuditEventLog()
    store.configure_persistence(blocked_dir)
    event = store.record("system", "service_running", "ok")

    assert event.action == "service_running"
    assert store.query(event_type="system")[0]["action"] == "service_running"
    status = store.persistence_status()
    assert status["enabled"] is True
    assert status["directory"] == str(blocked_dir)
    assert status["ok"] is False
    assert status["error"]
