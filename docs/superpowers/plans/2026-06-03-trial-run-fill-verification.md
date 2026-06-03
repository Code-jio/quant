# Trial Run Fill Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a broker-test-friendly trial-run verification flow that can prove real order submission, diagnose no-counterparty non-fills, manually inject a test-environment simulated fill, verify position changes, and export a DOCX test report.

**Architecture:** Keep the real CTP/vn.py order path unchanged. Add a test-environment-only simulated fill path that mutates local gateway order/trade/position state and reuses existing callbacks so strategy, API, WebSocket, and UI observers see the same lifecycle as a real fill. Add report generation from trial-run status, reconciliation snapshots, orders, trades, positions, risk status, and audit events.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, vn.py gateway adapter, Vue 3, Element Plus, Vitest, Vite, `python-docx` for DOCX export.

---

## Scope Judgment

This is a complex cross-system change. It touches backend API models, trial-run routes, trading state mutation, strategy snapshots, config defaults, frontend UI actions, API client helpers, test coverage, and a generated Word report. The work should be executed in small checkpoints because a mistake in simulated fills can contaminate real-trading safety assumptions.

## Fixed Product Boundaries

- Fixed trading symbol only. Do not add automatic contract switching.
- Use `rb2610` as the configured trial-run symbol unless the user explicitly changes it later.
- Real order submission remains mandatory for the trial-run flow.
- No real tick means no real order.
- No counterparty in the broker test environment is not a system failure once the order reaches a valid submitted state.
- Manual simulated fill is allowed from the frontend only in test environment.
- Simulated fill must not require fee or margin input.
- No-fill diagnostic threshold is 10 seconds.
- Chase/requote attempts are 5.
- Market fallback is optional and must be guarded by risk config and gateway rejection handling.
- There is no second account, so do not build dual-account matching automation.
- Report export format is DOCX.

## File Structure

### Backend

- Modify `back_end/requirements.txt`
  - Add `python-docx>=1.1.2`.
- Modify `back_end/config/config.example.json`
  - Test-friendly trial-run defaults: `no_fill_timeout_seconds=10`, `simulate_fill_enabled=true`, `chase_max_attempts=5`.
- Modify `back_end/config/config_production.json`
  - Production-safe defaults: `simulate_fill_enabled=false`, `chase_max_attempts=5`, no auto contract switching.
- Modify `back_end/src/api/models.py`
  - Add request/response models for simulated fill and report metadata.
  - Extend `TrialRunStatusResponse` with execution diagnostics.
- Create `back_end/src/trading/simulated_fill.py`
  - Encapsulate local order/trade/position mutation for test-environment simulated fills.
- Create `back_end/src/api/trial_run_report.py`
  - Build DOCX reports from snapshots and audit events.
- Modify `back_end/src/api/trial_run.py`
  - Add no-fill diagnostics, simulated fill route, report route, and safety checks.
- Modify `back_end/src/strategy/strategies/verify.py`
  - Ensure snapshots expose enough fill/chase state for report and UI.
- Test `back_end/tests/test_trial_run_api.py`
  - Simulated fill route, safety gating, no-fill timeout, report export.
- Test `back_end/tests/test_verify_strategy.py`
  - Chase attempts default and fallback behavior.

### Frontend

- Modify `front_end/src/api/index.js`
  - Add `simulateTrialRunFill()` and `downloadTrialRunReport()`.
- Modify `front_end/src/views/TrialRunView.vue`
  - Add no-fill execution diagnostics.
  - Add manual "模拟成交回报" action.
  - Add "导出测试报告" action.
- Create `front_end/tests/unit/trialRunApi.spec.js`
  - Verify API client endpoints and download behavior.

## Task 1: Configure Boundaries and API Contracts

**Files:**
- Modify: `back_end/requirements.txt`
- Modify: `back_end/config/config.example.json`
- Modify: `back_end/config/config_production.json`
- Modify: `back_end/src/api/models.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [ ] **Step 1: Write failing backend model/config tests**

Add tests to `back_end/tests/test_trial_run_api.py`:

```python
def test_trial_run_config_exposes_fill_verification_boundaries(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "fill-boundaries"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["no_fill_timeout_seconds"] = 10
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["strategy"]["chase_max_attempts"] = 5
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["trial_run"]["no_fill_timeout_seconds"] == 10
    assert body["config"]["trial_run"]["simulate_fill_enabled"] is True
    assert body["strategy"]["chase_max_attempts"] == 5
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_config_exposes_fill_verification_boundaries -q
```

Expected: FAIL because the config response does not yet expose these fields.

- [ ] **Step 3: Add dependency**

Modify `back_end/requirements.txt`:

```text
python-docx>=1.1.2
```

- [ ] **Step 4: Add config defaults**

In `back_end/config/config.example.json`, set:

```json
"trial_run": {
  "enabled": true,
  "allowed_symbol": "rb2610",
  "auto_arm": true,
  "bar_timeout_seconds": 90,
  "no_fill_timeout_seconds": 10,
  "simulate_fill_enabled": true
}
```

In `back_end/config/config_production.json`, set:

```json
"trial_run": {
  "enabled": true,
  "allowed_symbol": "rb2610",
  "auto_arm": true,
  "bar_timeout_seconds": 90,
  "no_fill_timeout_seconds": 10,
  "simulate_fill_enabled": false
}
```

In both config files, set strategy chase attempts:

```json
"chase_max_attempts": 5
```

- [ ] **Step 5: Add Pydantic models and status fields**

Modify `back_end/src/api/models.py`:

```python
class TrialRunSimulateFillRequest(BaseModel):
    order_id: str = ""
    price: Optional[float] = None
    volume: Optional[int] = None
```

Extend `TrialRunStatusResponse`:

```python
    no_fill_timeout_seconds: float = 10.0
    unfilled_wait_seconds: float = 0.0
    execution_issue: str = ""
    execution_warning: str = ""
    simulate_fill_enabled: bool = False
    simulate_fill_allowed: bool = False
    last_fill_source: str = ""
```

After `TrialRunStatusResponse` is defined, add:

```python
class TrialRunSimulateFillResponse(BaseModel):
    success: bool
    message: str
    order: Dict[str, Any] = Field(default_factory=dict)
    trade: Dict[str, Any] = Field(default_factory=dict)
    status: TrialRunStatusResponse
```

- [ ] **Step 6: Expose safe config fields**

Modify `_safe_config_response()` in `back_end/src/api/trial_run.py` so `safe_trial_run` includes:

```python
"no_fill_timeout_seconds": max(1.0, _float_value(trial_run.get("no_fill_timeout_seconds"), 10.0)),
"simulate_fill_enabled": bool(trial_run.get("simulate_fill_enabled", False)),
```

- [ ] **Step 7: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py -q
```

Expected: PASS.

## Task 2: Add Test-Environment Simulated Fill Engine

**Files:**
- Create: `back_end/src/trading/simulated_fill.py`
- Modify: `back_end/tests/test_trial_run_api.py`

- [ ] **Step 1: Write failing simulated fill test**

Add to `back_end/tests/test_trial_run_api.py`:

```python
def test_trial_run_simulate_fill_marks_order_filled_and_updates_position(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulate-fill"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["trial_run"]["vnpy_environment"] = "测试"
    payload["strategy"]["chase_max_attempts"] = 5
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130))
        assert gateway.sent_signals
        order_id = next(iter(entry.engine.gateway.orders.keys()))

        response = client.post("/trial-run/simulate-fill", json={"order_id": order_id})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["order"]["status"] == "filled"
    assert body["trade"]["order_id"] == order_id
    assert body["status"]["position_volume"] == 1
    assert body["status"]["last_fill_source"] == "simulated"
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_simulate_fill_marks_order_filled_and_updates_position -q
```

Expected: FAIL because `/trial-run/simulate-fill` and helper do not exist.

- [ ] **Step 3: Create simulated fill helper**

Create `back_end/src/trading/simulated_fill.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..strategy import Direction, OffsetFlag, OrderStatus, Position, Trade


@dataclass
class SimulatedFillResult:
    order: Any
    trade: Trade
    position: Position


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _position_key(symbol: str, direction: Direction) -> str:
    return f"{symbol}_{direction.value}"


def _remaining_volume(order: Any) -> int:
    return max(0, int(getattr(order, "volume", 0) or 0) - int(getattr(order, "traded_volume", 0) or 0))


def apply_simulated_fill(engine: Any, order_id: str, *, price: Optional[float] = None, volume: Optional[int] = None) -> SimulatedFillResult:
    clean_order_id = str(order_id or "").strip()
    if not clean_order_id:
        raise ValueError("order_id is required")

    order = getattr(engine.gateway, "orders", {}).get(clean_order_id)
    if order is None:
        raise ValueError(f"order {clean_order_id} not found")

    remaining = _remaining_volume(order)
    if remaining <= 0:
        raise ValueError(f"order {clean_order_id} has no remaining volume")

    fill_volume = int(volume or remaining)
    if fill_volume <= 0 or fill_volume > remaining:
        raise ValueError(f"fill volume must be between 1 and {remaining}")

    fill_price = float(price if price is not None else getattr(order, "price", 0.0) or 0.0)
    if fill_price <= 0:
        raise ValueError("fill price must be positive")

    order.traded_volume = int(getattr(order, "traded_volume", 0) or 0) + fill_volume
    order.status = OrderStatus.FILLED if order.traded_volume >= int(order.volume) else OrderStatus.PARTFILLED
    if hasattr(order, "update_time"):
        order.update_time = datetime.now()

    trade = Trade(
        trade_id=f"SIM-{clean_order_id}-{int(datetime.now().timestamp())}",
        order_id=clean_order_id,
        symbol=order.symbol,
        direction=order.direction,
        price=fill_price,
        volume=fill_volume,
        trade_time=datetime.now(),
    )

    direction = order.direction if isinstance(order.direction, Direction) else Direction(_value(order.direction))
    offset = getattr(order, "offset", OffsetFlag.OPEN)
    offset_value = _value(offset)
    positions = engine.gateway.positions

    if offset_value == OffsetFlag.OPEN.value:
        key = _position_key(order.symbol, direction)
        pos = positions.get(key) or Position(symbol=order.symbol, direction=direction, volume=0, price=fill_price, cost=fill_price)
        pos.volume = int(getattr(pos, "volume", 0) or 0) + fill_volume
        pos.price = fill_price
        pos.cost = fill_price
        positions[key] = pos
    else:
        close_direction = Direction.SHORT if direction == Direction.LONG else Direction.LONG
        key = _position_key(order.symbol, close_direction)
        pos = positions.get(key) or Position(symbol=order.symbol, direction=close_direction, volume=0, price=fill_price, cost=fill_price)
        pos.volume = max(0, int(getattr(pos, "volume", 0) or 0) - fill_volume)
        positions[key] = pos

    engine.gateway.on_order(order)
    engine.gateway.on_trade(trade)
    return SimulatedFillResult(order=order, trade=trade, position=pos)
```

- [ ] **Step 4: Run helper-focused test after route work is still expected to fail**

Run the same test:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_simulate_fill_marks_order_filled_and_updates_position -q
```

Expected: still FAIL because the route is not wired yet.

## Task 3: Wire Simulated Fill Route with Safety Gates

**Files:**
- Modify: `back_end/src/api/trial_run.py`
- Modify: `back_end/src/api/models.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [ ] **Step 1: Write failing safety tests**

Add to `back_end/tests/test_trial_run_api.py`:

```python
def test_trial_run_simulate_fill_rejects_when_disabled(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulate-disabled"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = False
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        response = client.post("/trial-run/simulate-fill", json={"order_id": "O1"})

    assert response.status_code == 403


def test_trial_run_simulate_fill_rejects_production_environment(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "simulate-production"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["trial_run"]["vnpy_environment"] = "实盘"
    payload["trading"]["environment"] = "production"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        response = client.post("/trial-run/simulate-fill", json={"order_id": "O1"})

    assert response.status_code == 403
```

- [ ] **Step 2: Run failing safety tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py -k simulate_fill -q
```

Expected: FAIL because route is not implemented.

- [ ] **Step 3: Add safety helpers**

In `back_end/src/api/trial_run.py`, import:

```python
from .models import TrialRunActionResponse, TrialRunConfigResponse, TrialRunSimulateFillRequest, TrialRunSimulateFillResponse, TrialRunStatusResponse
from ..trading.simulated_fill import apply_simulated_fill
```

Add helpers:

```python
def _environment_text(config: Dict[str, Any]) -> str:
    trial_run = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
    trading = config.get("trading") if isinstance(config.get("trading"), dict) else {}
    return str(
        trial_run.get("vnpy_environment")
        or trading.get("vnpy_environment")
        or trading.get("environment")
        or ""
    ).strip().lower()


def _is_production_environment(config: Dict[str, Any]) -> bool:
    text = _environment_text(config)
    return any(marker in text for marker in ("prod", "production", "real", "live", "实盘", "生产"))


def _simulate_fill_enabled(config: Dict[str, Any]) -> bool:
    trial_run = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
    return bool(trial_run.get("simulate_fill_enabled", False)) and not _is_production_environment(config)
```

- [ ] **Step 4: Add route**

Inside `register_trial_run_routes()` in `back_end/src/api/trial_run.py`, add:

```python
    @app.post(
        "/trial-run/simulate-fill",
        response_model=TrialRunSimulateFillResponse,
        summary="测试环境手动模拟成交回报",
        tags=["试运行"],
    )
    def simulate_trial_run_fill(body: TrialRunSimulateFillRequest, request: Request):
        try:
            _path, config, _allowed_symbol, errors = _read_trial_config(allow_example=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"试运行配置读取失败: {exc}") from exc
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
        if not _simulate_fill_enabled(config):
            raise HTTPException(status_code=403, detail="模拟成交仅允许在测试环境启用")

        entry = trading_state.get(TRIAL_STRATEGY_ID)
        if entry is None:
            raise HTTPException(status_code=409, detail="试运行策略尚未准备")

        try:
            result = apply_simulated_fill(entry.engine, body.order_id, price=body.price, volume=body.volume)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        strategy = getattr(entry, "strategy", None)
        if strategy is not None:
            setattr(strategy, "_last_fill_source", "simulated")
        record_audit(
            "trial_run",
            "simulate_fill",
            "success",
            request=request,
            resource=body.order_id,
            detail={"order_id": body.order_id, "price": body.price, "volume": body.volume},
        )
        return TrialRunSimulateFillResponse(
            success=True,
            message="模拟成交回报已注入",
            order=_order_to_safe_dict(result.order),
            trade=_trade_to_safe_dict(result.trade),
            status=_status_response(trading_state),
        )
```

Add local serialization helpers in `trial_run.py`:

```python
def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _order_to_safe_dict(order: Any) -> Dict[str, Any]:
    return {
        "order_id": getattr(order, "order_id", ""),
        "symbol": getattr(order, "symbol", ""),
        "direction": _enum_value(getattr(order, "direction", "")),
        "offset": _enum_value(getattr(order, "offset", "open")),
        "price": float(getattr(order, "price", 0.0) or 0.0),
        "volume": int(getattr(order, "volume", 0) or 0),
        "traded_volume": int(getattr(order, "traded_volume", 0) or 0),
        "status": _enum_value(getattr(order, "status", "")),
    }


def _trade_to_safe_dict(trade: Any) -> Dict[str, Any]:
    return {
        "trade_id": getattr(trade, "trade_id", ""),
        "order_id": getattr(trade, "order_id", ""),
        "symbol": getattr(trade, "symbol", ""),
        "direction": _enum_value(getattr(trade, "direction", "")),
        "price": float(getattr(trade, "price", 0.0) or 0.0),
        "volume": int(getattr(trade, "volume", 0) or 0),
        "trade_time": _timestamp_to_text(getattr(trade, "trade_time", "")),
    }
```

- [ ] **Step 5: Add status integration**

In `_status_response()`, set:

```python
simulate_fill_enabled = False
simulate_fill_allowed = False
try:
    simulate_fill_enabled = bool(config.get("trial_run", {}).get("simulate_fill_enabled", False))
    simulate_fill_allowed = simulate_fill_enabled and not _is_production_environment(config)
except Exception:
    simulate_fill_enabled = False
    simulate_fill_allowed = False
last_fill_source = str(snapshot.get("last_fill_source") or getattr(strategy, "_last_fill_source", "") or "")
```

Pass fields into `TrialRunStatusResponse`.

- [ ] **Step 6: Run simulated fill tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py -k simulate_fill -q
```

Expected: PASS.

## Task 4: Add No-Fill Diagnostics and 5-Attempt Chase Defaults

**Files:**
- Modify: `back_end/src/api/models.py`
- Modify: `back_end/src/api/trial_run.py`
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/config/config.example.json`
- Modify: `back_end/config/config_production.json`
- Test: `back_end/tests/test_trial_run_api.py`
- Test: `back_end/tests/test_verify_strategy.py`

- [ ] **Step 1: Write failing no-fill diagnostic test**

Add to `back_end/tests/test_trial_run_api.py`:

```python
def test_trial_run_status_reports_waiting_counterparty_after_no_fill_timeout(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "no-fill-timeout"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["no_fill_timeout_seconds"] = 10
    payload["strategy"]["chase_max_attempts"] = 5
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130))
        order = next(iter(entry.engine.gateway.orders.values()))
        created_at = getattr(order, "create_time")
        monkeypatch.setattr(trial_run_module.time, "time", lambda: created_at.timestamp() + 11)

        response = client.get("/trial-run/status")

    assert response.status_code == 200
    body = response.json()
    assert body["execution_issue"] == "waiting_counterparty"
    assert body["unfilled_wait_seconds"] >= 10
    assert "对手盘" in body["execution_warning"]
    assert body["chase_max_attempts"] == 5
```

- [ ] **Step 2: Write failing chase default test**

Add to `back_end/tests/test_verify_strategy.py`:

```python
def test_verify_strategy_defaults_to_five_chase_attempts():
    s = VerifyStrategy("verify", {"warmup_bars": 1, "hold_bars": 3, "volume": 1, "auto_arm": True})
    snap = s.snapshot()
    assert snap["chase_max_attempts"] == 5
```

- [ ] **Step 3: Run failing tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_status_reports_waiting_counterparty_after_no_fill_timeout back_end\tests\test_verify_strategy.py::test_verify_strategy_defaults_to_five_chase_attempts -q
```

Expected: FAIL.

- [ ] **Step 4: Change default chase attempts**

Modify `back_end/src/strategy/strategies/verify.py`:

```python
self._chase_max_attempts = self._parse_non_negative_int(self.params.get("chase_max_attempts", 5), default=5)
```

- [ ] **Step 5: Add execution diagnostic fields**

In `back_end/src/api/models.py`, extend `TrialRunStatusResponse`:

```python
    execution_issue: str = ""
    execution_warning: str = ""
    unfilled_wait_seconds: float = 0.0
    no_fill_timeout_seconds: float = 10.0
```

- [ ] **Step 6: Calculate unfilled active-order age**

In `back_end/src/api/trial_run.py`, add:

```python
def _active_order_wait_seconds(engine: Any, symbol: str) -> float:
    if engine is None:
        return 0.0
    now = datetime.now()
    active_statuses = {"submitting", "submitted", "partfilled"}
    waits: List[float] = []
    for order in getattr(engine.gateway, "orders", {}).values():
        status = _enum_value(getattr(order, "status", ""))
        if status not in active_statuses:
            continue
        if symbol and not _symbols_match(getattr(order, "symbol", ""), symbol):
            continue
        created = getattr(order, "create_time", None)
        if isinstance(created, datetime):
            waits.append(max(0.0, (now - created).total_seconds()))
    return max(waits) if waits else 0.0
```

In `_status_response()`, compute:

```python
no_fill_timeout_seconds = max(1.0, _float_value(trial_config.get("no_fill_timeout_seconds"), 10.0))
unfilled_wait_seconds = round(_active_order_wait_seconds(engine, symbol), 1)
execution_issue = ""
execution_warning = ""
if unfilled_wait_seconds >= no_fill_timeout_seconds and not bool(snapshot.get("completed", False)):
    execution_issue = "waiting_counterparty"
    execution_warning = (
        f"订单已报入但 {int(unfilled_wait_seconds)} 秒未成交；"
        "测试环境可能没有对手盘，请等待券商撮合条件或使用模拟成交回报验证成交后处理。"
    )
```

Pass these values into `TrialRunStatusResponse`.

- [ ] **Step 7: Run diagnostics tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py back_end\tests\test_verify_strategy.py -q
```

Expected: PASS.

## Task 5: Add Frontend Simulated Fill and Report Actions

**Files:**
- Modify: `front_end/src/api/index.js`
- Modify: `front_end/src/views/TrialRunView.vue`
- Create: `front_end/tests/unit/trialRunApi.spec.js`

- [ ] **Step 1: Add failing API client tests**

Create `front_end/tests/unit/trialRunApi.spec.js`:

```javascript
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { simulateTrialRunFill, downloadTrialRunReport } from '../../src/api/index.js'

describe('trial-run api helpers', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  it('posts simulated fill requests', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ success: true }),
    })

    const result = await simulateTrialRunFill({ order_id: 'O1' })

    expect(result.success).toBe(true)
    expect(fetch).toHaveBeenCalledWith(
      '/api/trial-run/simulate-fill',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ order_id: 'O1' }),
      }),
    )
  })

  it('downloads trial-run docx report as a blob', async () => {
    const blob = new Blob(['docx'])
    fetch.mockResolvedValueOnce({
      ok: true,
      headers: new Headers({ 'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }),
      blob: async () => blob,
    })

    const result = await downloadTrialRunReport()

    expect(result).toBe(blob)
    expect(fetch).toHaveBeenCalledWith('/api/trial-run/report.docx', expect.objectContaining({ method: 'GET' }))
  })
})
```

- [ ] **Step 2: Run failing frontend tests**

Run:

```powershell
npm.cmd run test
```

from `D:\quant\front_end`.

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Add API helpers**

Modify `front_end/src/api/index.js`:

```javascript
export function simulateTrialRunFill(body) {
  return request('/trial-run/simulate-fill', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function downloadTrialRunReport() {
  const response = await fetch('/api/trial-run/report.docx', { method: 'GET' })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `HTTP ${response.status}`)
  }
  return response.blob()
}
```

- [ ] **Step 4: Add UI computed values**

Modify `front_end/src/views/TrialRunView.vue` imports to include the new helpers:

```javascript
import { simulateTrialRunFill, downloadTrialRunReport } from '@/api'
```

Add computed values:

```javascript
const executionIssue = computed(() => String(trialStatus.value.execution_issue || ''))
const executionWarning = computed(() => String(trialStatus.value.execution_warning || ''))
const simulateFillAllowed = computed(() => Boolean(trialStatus.value.simulate_fill_allowed))
const firstActiveOrder = computed(() => orders.value.find(order => ACTIVE_ORDER_STATUSES.has(normalizeCode(order.status))) || null)
const canSimulateFill = computed(() => hasSession.value && simulateFillAllowed.value && Boolean(firstActiveOrder.value) && !actionLoading.simulateFill)
```

Extend `actionLoading`:

```javascript
simulateFill: false,
report: false,
```

- [ ] **Step 5: Add UI actions**

Add methods:

```javascript
async function simulateSelectedFill() {
  const order = firstActiveOrder.value
  if (!order) {
    ElMessage.warning('没有可模拟成交的活跃订单')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认注入模拟成交回报：${order.symbol} ${directionLabel(order.direction)} ${order.volume}手？`,
      '模拟成交回报',
      { type: 'warning' },
    )
    actionLoading.simulateFill = true
    await simulateTrialRunFill({ order_id: order.order_id })
    ElMessage.success('模拟成交回报已注入')
    await refreshAll()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error?.message || '模拟成交失败')
  } finally {
    actionLoading.simulateFill = false
  }
}

async function exportTrialRunReport() {
  try {
    actionLoading.report = true
    const blob = await downloadTrialRunReport()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `trial-run-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.docx`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(error?.message || '导出测试报告失败')
  } finally {
    actionLoading.report = false
  }
}
```

- [ ] **Step 6: Add buttons and warning text**

In the trial-run action toolbar, add buttons:

```vue
<el-button
  type="warning"
  plain
  :disabled="!canSimulateFill"
  :loading="actionLoading.simulateFill"
  @click="simulateSelectedFill"
>
  模拟成交回报
</el-button>
<el-button
  plain
  :loading="actionLoading.report"
  @click="exportTrialRunReport"
>
  导出测试报告
</el-button>
```

Near diagnostics, show:

```vue
<el-alert
  v-if="executionWarning"
  type="warning"
  :closable="false"
  :title="executionWarning"
/>
```

- [ ] **Step 7: Run frontend tests and build**

Run from `D:\quant\front_end`:

```powershell
npm.cmd run test
npm.cmd run build
```

Expected: both PASS. Existing chunk-size warnings are acceptable.

## Task 6: Add DOCX Trial-Run Report Export

**Files:**
- Create: `back_end/src/api/trial_run_report.py`
- Modify: `back_end/src/api/trial_run.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [ ] **Step 1: Write failing report test**

Add to `back_end/tests/test_trial_run_api.py`:

```python
def test_trial_run_report_docx_downloads_word_file(monkeypatch, tmp_path):
    config_path = _trial_config(_config_path(tmp_path, "report"))
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["trial_run"]["simulate_fill_enabled"] = True
    payload["trial_run"]["vnpy_environment"] = "测试"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        assert client.post("/trial-run/prepare").status_code == 200
        entry = trading_state.get("verify_trial")
        entry.engine.on_tick(_tick(3130))
        order_id = next(iter(entry.engine.gateway.orders.keys()))
        assert client.post("/trial-run/simulate-fill", json={"order_id": order_id}).status_code == 200
        response = client.get("/trial-run/report.docx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content[:2] == b"PK"
    assert "trial-run-report" in response.headers["content-disposition"]
```

- [ ] **Step 2: Run failing report test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_report_docx_downloads_word_file -q
```

Expected: FAIL because report route does not exist.

- [ ] **Step 3: Create report builder**

Create `back_end/src/api/trial_run_report.py`:

```python
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterable, List

from docx import Document


def _text(value: Any) -> str:
    if value is None or value == "":
        return "--"
    return str(value)


def _add_key_values(document: Document, rows: Iterable[tuple[str, Any]]) -> None:
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    header = table.rows[0].cells
    header[0].text = "项目"
    header[1].text = "值"
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].text = key
        cells[1].text = _text(value)


def _add_list_table(document: Document, title: str, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    document.add_heading(title, level=2)
    table = document.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    for index, column in enumerate(columns):
        table.rows[0].cells[index].text = column
    for row in rows:
        cells = table.add_row().cells
        for index, column in enumerate(columns):
            cells[index].text = _text(row.get(column))


def build_trial_run_report_docx(
    *,
    status: Dict[str, Any],
    reconcile: Dict[str, Any],
    orders: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> bytes:
    document = Document()
    document.add_heading("程序化交易试运行测试报告", level=1)
    document.add_paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    document.add_heading("测试结论", level=2)
    _add_key_values(document, [
        ("试运行状态", status.get("state")),
        ("合约", status.get("symbol") or status.get("allowed_symbol")),
        ("报单链路", "通过" if orders else "未验证"),
        ("成交来源", status.get("last_fill_source") or "真实成交或未成交"),
        ("执行问题", status.get("execution_issue") or "无"),
        ("执行提示", status.get("execution_warning") or "无"),
    ])

    document.add_heading("环境与风控", level=2)
    _add_key_values(document, [
        ("环境", config.get("environment") or config.get("trading", {}).get("environment")),
        ("模拟成交启用", config.get("trial_run", {}).get("simulate_fill_enabled")),
        ("无成交阈值", config.get("trial_run", {}).get("no_fill_timeout_seconds")),
        ("追价次数", config.get("strategy", {}).get("chase_max_attempts")),
        ("急停状态", reconcile.get("risk", {}).get("emergency_stop")),
    ])

    _add_list_table(document, "订单记录", orders, ["order_id", "symbol", "direction", "offset", "price", "volume", "traded_volume", "status", "create_time", "update_time"])
    _add_list_table(document, "成交记录", trades, ["trade_id", "order_id", "symbol", "direction", "price", "volume", "trade_time"])
    _add_list_table(document, "持仓记录", reconcile.get("positions", {}).get("items", []), ["symbol", "direction", "volume", "price", "cost", "pnl"])
    _add_list_table(document, "审计记录", audit_events[:50], ["timestamp", "event_type", "action", "status", "resource"])

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
```

- [ ] **Step 4: Add report route**

In `back_end/src/api/trial_run.py`, import:

```python
from fastapi.responses import Response
from ..observability import audit_log
from .trial_run_report import build_trial_run_report_docx
```

Add route:

```python
    @app.get(
        "/trial-run/report.docx",
        summary="导出试运行测试报告",
        tags=["试运行"],
    )
    def trial_run_report_docx():
        status = _status_response(trading_state).model_dump()
        engine = trading_state.primary_engine()
        reconcile = {
            "risk": engine.risk_manager.status() if engine is not None else {},
            "positions": {"items": _build_report_positions(engine)},
        }
        orders = _build_report_orders(engine)
        trades = _build_report_trades(trading_state)
        audit_events = audit_log.query(event_type="", limit=200)
        _path, config, _allowed_symbol, _errors = _read_trial_config(allow_example=True)
        payload = build_trial_run_report_docx(
            status=status,
            reconcile=reconcile,
            orders=orders,
            trades=trades,
            audit_events=audit_events,
            config=config,
        )
        filename = f"trial-run-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.docx"
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
```

Add helper functions in `trial_run.py` if the existing `_collect_all_orders()` functions in `api/__init__.py` are not directly available in this module:

```python
def _build_report_orders(engine: Any) -> List[Dict[str, Any]]:
    if engine is None:
        return []
    return [_order_to_safe_dict(order) for order in getattr(engine.gateway, "orders", {}).values()]


def _build_report_trades(trading_state: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for entry in trading_state.all_entries():
        for trade in getattr(entry.strategy, "trades", []):
            rows.append(_trade_to_safe_dict(trade))
    return rows


def _build_report_positions(engine: Any) -> List[Dict[str, Any]]:
    if engine is None:
        return []
    rows: List[Dict[str, Any]] = []
    for pos in getattr(engine.gateway, "positions", {}).values():
        rows.append({
            "symbol": getattr(pos, "symbol", ""),
            "direction": _enum_value(getattr(pos, "direction", "")),
            "volume": int(getattr(pos, "volume", 0) or 0),
            "price": float(getattr(pos, "price", 0.0) or 0.0),
            "cost": float(getattr(pos, "cost", 0.0) or 0.0),
            "pnl": float(getattr(pos, "pnl", 0.0) or 0.0),
        })
    return rows
```

- [ ] **Step 5: Run report test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py::test_trial_run_report_docx_downloads_word_file -q
```

Expected: PASS.

## Task 7: Full Regression and Acceptance Evidence

**Files:**
- Verify only; no new files expected unless tests reveal a gap.

- [ ] **Step 1: Run backend trial-run and trading tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_api.py back_end\tests\test_verify_strategy.py back_end\tests\test_manual_trading.py back_end\tests\test_vnpy_gateway.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend test/build**

Run from `D:\quant\front_end`:

```powershell
npm.cmd run test
npm.cmd run build
```

Expected: PASS. Existing Vite large chunk warnings are acceptable.

- [ ] **Step 3: Clean test artifacts**

If pytest creates `D:\quant\data\historical\sessions.db`, remove it after verifying it is untracked:

```powershell
git status --short
Get-ChildItem -Path data -Recurse
Remove-Item -LiteralPath data -Recurse -Force
```

Expected: `data/` removed, no untracked SQLite test artifact remains.

- [ ] **Step 4: Manual acceptance script**

Run this in the browser with the backend and frontend started:

1. Log in to the CTP test environment.
2. Open `/trial-run`.
3. Confirm fixed symbol is `rb2610`.
4. Start trial-run.
5. Confirm latest tick is visible.
6. Confirm one order is sent.
7. Wait 10 seconds if it does not fill.
8. Confirm the page says the test environment may lack a counterparty.
9. Confirm chase attempts can progress up to 5.
10. Click "模拟成交回报".
11. Confirm order status becomes filled.
12. Confirm trade record appears.
13. Confirm position volume becomes 1.
14. Click "导出测试报告".
15. Open the DOCX and confirm it contains order, trade, position, risk, audit, and final conclusion.

- [ ] **Step 5: Final status check**

Run:

```powershell
git status --short
```

Expected: only intentional source/config/test/doc changes are listed. Unrelated `.claude/settings.json` and the existing DOCX fixture remain untouched unless the user explicitly asks to manage them.

## Self-Review

### Spec Coverage

- Frontend manual simulated fill entry: Task 5.
- Simulated fill fields without fee/margin: Task 2 and Task 3.
- Short no-fill wait: Task 4 sets 10 seconds.
- Chase attempts 5: Task 1 and Task 4.
- Market fallback guarded by config/risk: Task 4 preserves existing fallback behavior and raises visibility through status/report.
- No second account: explicitly excluded in Fixed Product Boundaries.
- DOCX report: Task 6.
- Success standard split into real order submission and fill closure: Task 4, Task 6, Task 7.
- Real order path unchanged: Architecture and Task 2 helper are separate from gateway send-order path.
- Production safety: Task 3 safety tests and route gating.

### Placeholder Scan

The plan contains no unresolved placeholder text or unspecified validation steps. Each implementation task has concrete files, tests, code snippets, and commands.

### Type Consistency

- `TrialRunSimulateFillRequest` is used by `/trial-run/simulate-fill`.
- `TrialRunSimulateFillResponse.status` uses existing `TrialRunStatusResponse`.
- `execution_issue`, `execution_warning`, `unfilled_wait_seconds`, `simulate_fill_allowed`, and `last_fill_source` are added in models, status response, frontend computed state, and report generation.
- Simulated fill uses existing `OrderStatus`, `Direction`, `OffsetFlag`, `Trade`, and `Position` types.
