# Live Trading Compliance Gaps Implementation Plan

> **Execution note:** Repository policy requires `sol-flash-routing`; the main thread owns contracts, security decisions, review, and verification, while bounded test/implementation work is delegated with non-overlapping write scopes.

**Goal:** Close the five categories currently assessed as unmet against the GTJA process record: connection recovery, order/cancel counts, duplicate-operation monitoring, threshold alerts, and durable categorized logs.

**Architecture:** Extend the existing vn.py adapter with an explicit TD/MD health snapshot and state-transition tracking, expose that snapshot through the existing system status feed, and reuse vn.py/CTP's native reconnect behavior instead of starting a second connection. Extend `RiskManager` with thread-safe compliance counters, duplicate-cancel protection, threshold alerts, and a queryable snapshot. Replace memory-only audit evidence with a bounded JSONL-backed store split into trade/system/monitor/error categories while preserving current API response compatibility.

**Tech Stack:** Python 3.12, FastAPI, vn.py CTP adapter, pytest, JSONL persistence, Vue 3 existing monitoring UI.

---

### Task 1: Connection health and recovery evidence

**Files:**
- Modify: `back_end/src/trading/vnpy_gateway.py`
- Modify: `back_end/src/api/__init__.py`
- Test: `back_end/tests/test_vnpy_gateway.py`
- Test: `back_end/tests/test_api_auth.py`

- [x] Add failing tests proving a TD or MD disconnect moves the gateway out of connected state and later login messages restore it.
- [x] Add `VnpyGateway.connection_snapshot() -> dict[str, Any]` with `td_connected`, `md_connected`, `fully_connected`, `reconnecting`, `reconnect_count`, `last_disconnect_reason`, and `changed_at`.
- [x] Synchronize the snapshot with both CTP API `login_status` flags when the native gateway is available.
- [x] Update `_build_system_snapshot()` to report TD and MD independently.
- [x] Verify with `python -m pytest tests/test_vnpy_gateway.py tests/test_api_auth.py -q`.

### Task 2: Counts, duplicate operations, and threshold alerts

**Files:**
- Modify: `back_end/src/trading/risk.py`
- Modify: `back_end/src/trading/engine.py`
- Modify: `back_end/src/api/__init__.py`
- Test: `back_end/tests/test_security_and_risk.py`
- Test: `back_end/tests/test_manual_trading.py`

- [x] Add failing tests for submitted-order count, accepted-cancel count, duplicate open/close count, duplicate-cancel rejection, and one-shot threshold alerts.
- [x] Add configurable thresholds: `order_count_alert_threshold`, `cancel_count_alert_threshold`, `duplicate_open_alert_threshold`, `duplicate_close_alert_threshold`, `duplicate_cancel_alert_threshold`, and `duplicate_cancel_window_seconds`.
- [x] Expose `compliance` counters and active alerts through `RiskManager.status()` and `/risk/status`.
- [x] Route single and all-order cancellation through `TradingEngine.cancel_order()` so monitoring cannot be bypassed.
- [x] Verify with `python -m pytest tests/test_security_and_risk.py tests/test_manual_trading.py -q`.

### Task 3: Durable categorized evidence logs

**Files:**
- Modify: `back_end/src/observability.py`
- Modify: `back_end/src/api/__init__.py`
- Test: `back_end/tests/test_observability_persistence.py`
- Test: `back_end/tests/test_api_auth.py`

- [x] Add failing tests showing records survive a new store instance and can be filtered by `trade`, `system`, `monitor`, and `error`.
- [x] Append each audit event as UTF-8 JSONL under a configurable log directory with daily files and a bounded query.
- [x] Classify order/trade events as trade, risk/connection events as monitor, failed/error events as error, and remaining events as system.
- [x] Extend `/audit/events` with an optional `category` filter without breaking `event_type` filtering.
- [x] Verify with `python -m pytest tests/test_observability_persistence.py tests/test_api_auth.py -q`.

### Task 4: Integrated verification

**Files:**
- Review every modified file above.

- [x] Run backend full pytest, Ruff, mypy target, and compileall.
- [x] Run frontend tests, typecheck, lint, and build because the system-status contract is consumed by Vue.
- [x] Exercise mocked API flows for independent TD/MD state, risk counters/alerts, and persisted log filtering.
- [x] Confirm no credentials, session tokens, debug output, temporary files, TODOs, or undeclared dependencies were introduced.
- [x] Record the boundary that real CTP disconnect/reconnect and broker-side thresholds still require the GTJA test environment.
