# Trial Run Console Implementation Plan

> **Execution note:** Default to local execution with `executing-plans`. Only switch to `subagent-driven-development` when the user explicitly asks for delegation/parallel work. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a front-end “试运行操作台” that safely runs the approved `VerifyStrategy` CTP simulation flow from login through authorization, order submission, fill tracking, position monitoring, and close-out.

**Architecture:** Add a small backend trial-run module beside the existing FastAPI app, reusing the current auth, risk, order, position, log, WebSocket, and trading engine infrastructure. Keep the trial-run state machine and local config validation in a focused `trial_run.py` module, add an authorization gate to `VerifyStrategy`, and build a dedicated Vue page at `/trial-run` that consumes the new API plus existing operational endpoints.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, pytest, Vue 3, Vite, Element Plus, Pinia-compatible session state, Playwright.

---

## File Map

| Action | File | Responsibility |
| --- | --- | --- |
| Create | `back_end/src/api/trial_run.py` | Trial-run config loading, validation, state machine, and API route registration |
| Modify | `back_end/src/api/__init__.py` | Register trial-run routes and block manual opening orders in trial-run mode |
| Modify | `back_end/src/api/models.py` | Trial-run request/response schemas |
| Modify | `back_end/src/api/security.py` | Allow unauthenticated read-only trial-run config/status endpoints |
| Modify | `back_end/src/strategy/strategies/verify.py` | Add pre-trade authorization gate and state snapshot |
| Modify | `back_end/config/config.example.json` | Add safe trial-run example config with empty CTP credentials |
| Modify | `.gitignore` | Ignore `back_end/config/config.local.json` |
| Create | `back_end/tests/test_trial_run_api.py` | Backend trial-run endpoint coverage |
| Modify | `back_end/tests/test_verify_strategy.py` | Authorization-gate behavior coverage |
| Modify | `back_end/tests/test_security_and_risk.py` | Manual-open blocking and config safety coverage |
| Modify | `front_end/src/api/index.js` | Trial-run API client functions |
| Modify | `front_end/src/router/index.js` | Add `/trial-run` route |
| Create | `front_end/src/views/TrialRunView.vue` | Trial-run console UI |
| Create | `front_end/tests/unit/trialRunApi.spec.js` | API client URL/body unit tests |
| Modify | `front_end/tests/e2e/smoke.spec.js` | Page render smoke coverage |

## Task 1: Backend Schemas and Safe Local Config

**Files:**
- Modify: `back_end/src/api/models.py`
- Modify: `back_end/config/config.example.json`
- Modify: `.gitignore`
- Test: `back_end/tests/test_security_and_risk.py`

- [ ] **Step 1: Add trial-run schemas**

Add these models to `back_end/src/api/models.py` after `RiskConfigRequest`:

```python
class TrialRunConfigResponse(BaseModel):
    enabled: bool
    ready: bool
    config_source: str
    account_id: str = ""
    masked_account_id: str = ""
    gateway: str = "vnpy"
    environment: str = "测试"
    allowed_symbol: str = ""
    manual_open_enabled: bool = False
    trading: Dict[str, Any] = Field(default_factory=dict)
    strategy: Dict[str, Any] = Field(default_factory=dict)
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)


class TrialRunStatusResponse(BaseModel):
    state: str
    connected: bool
    gateway_status: str = "stopped"
    strategy_id: str = ""
    strategy_name: str = "verify"
    symbol: str = ""
    authorized: bool = False
    ready_to_arm: bool = False
    completed: bool = False
    bar_count: int = 0
    warmup_bars: int = 0
    hold_bars: int = 0
    bars_since_entry: int = 0
    position_volume: int = 0
    last_reject_reason: str = ""
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)


class TrialRunActionResponse(BaseModel):
    success: bool
    state: str
    message: str
    status: TrialRunStatusResponse
```

- [ ] **Step 2: Extend the example config**

Add this top-level block to `back_end/config/config.example.json`, keeping all credential values empty and using a clearly fake sample symbol:

```json
"trial_run": {
  "enabled": true,
  "account_id": "",
  "gateway": "vnpy",
  "vnpy_environment": "测试",
  "allowed_symbol": "rb2601",
  "manual_open_enabled": false
}
```

Also update the existing example `strategy` and `risk` values so a copied local config can be made safe by editing one symbol:

```json
"strategy": {
  "name": "verify",
  "symbol": "rb2601",
  "warmup_bars": 20,
  "hold_bars": 10,
  "volume": 1,
  "contract_multiplier": 10,
  "max_errors": 10
}
```

```json
"risk": {
  "enabled": true,
  "max_order_volume": 1,
  "max_position_volume": 1,
  "max_active_orders": 2,
  "max_orders_per_minute": 5,
  "max_daily_loss_ratio": 0.01,
  "max_order_value": 1000000,
  "max_position_value": 1000000,
  "max_price_deviation": 0.01,
  "max_market_data_age_seconds": 10,
  "duplicate_signal_window_seconds": 5,
  "default_contract_multiplier": 10,
  "contract_multipliers": {},
  "allow_market_orders": false,
  "allowed_symbols": ["rb2601"],
  "blocked_symbols": []
}
```

- [ ] **Step 3: Ignore real local config**

Add this line to `.gitignore` near the credential config section:

```gitignore
back_end/config/config.local.json
```

- [ ] **Step 4: Add config safety tests**

Append tests to `back_end/tests/test_security_and_risk.py`:

```python
def test_example_config_contains_trial_run_without_password():
    import json
    from pathlib import Path

    config = json.loads(Path("config/config.example.json").read_text(encoding="utf-8"))

    assert config["trial_run"]["enabled"] is True
    assert config["trial_run"]["manual_open_enabled"] is False
    assert config["trading"].get("password", "") == ""
    assert config["strategy"]["name"] == "verify"
    assert config["strategy"]["volume"] == 1
    assert config["risk"]["allowed_symbols"] == [config["trial_run"]["allowed_symbol"]]
```

- [ ] **Step 5: Verify the focused backend safety tests**

Run from `back_end`:

```bash
python -m pytest tests/test_security_and_risk.py -q
```

Expected: all tests in the file pass.

## Task 2: VerifyStrategy Authorization Gate

**Files:**
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/tests/test_verify_strategy.py`

- [ ] **Step 1: Replace the immediate-buy tests with gated behavior**

Update `back_end/tests/test_verify_strategy.py` so the warmup completion test asserts no signal until authorization:

```python
def test_warmup_completion_waits_for_authorization():
    s = VerifyStrategy("verify", {"symbol": "rb2601", "warmup_bars": 3, "hold_bars": 5, "volume": 1})
    s.on_init()

    for _ in range(3):
        s.on_bar(_bar(3128, symbol="rb2601"))

    assert len(s.signals) == 0
    assert s.ready_to_arm is True
    assert s.trial_state == "ready_to_arm"
    assert s.snapshot()["bar_count"] == 3
```

Add an explicit authorization test:

```python
def test_authorized_strategy_buys_on_next_bar_after_warmup():
    s = VerifyStrategy("verify", {"symbol": "rb2601", "warmup_bars": 2, "hold_bars": 5, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128, symbol="rb2601"))
    s.on_bar(_bar(3130, symbol="rb2601"))

    assert s.authorize_trading() is True
    s.on_bar(_bar(3132, symbol="rb2601"))

    assert len(s.signals) == 1
    assert s.signals[0].symbol == "rb2601"
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].offset.value == "open"
    assert s.trial_state == "armed"
```

Add a premature authorization test:

```python
def test_authorize_before_warmup_is_rejected():
    s = VerifyStrategy("verify", {"symbol": "rb2601", "warmup_bars": 2, "hold_bars": 5, "volume": 1})
    s.on_init()

    assert s.authorize_trading() is False
    assert s.trade_authorized is False
    assert s.trial_state == "warming"
```

- [ ] **Step 2: Run tests to verify current behavior fails**

Run from `back_end`:

```bash
python -m pytest tests/test_verify_strategy.py -q
```

Expected before implementation: at least the gated warmup tests fail because `VerifyStrategy` currently buys immediately after warmup and has no authorization methods.

- [ ] **Step 3: Add state and authorization methods**

In `back_end/src/strategy/strategies/verify.py`, initialize these fields in `on_init()`:

```python
self.trade_authorized = False
self.ready_to_arm = False
self.completed = False
self.trial_state = "warming"
self._entry_order_sent = False
self._close_order_sent = False
```

Add methods to `VerifyStrategy`:

```python
def authorize_trading(self) -> bool:
    if not self.ready_to_arm or self.completed or self._bought:
        return False
    self.trade_authorized = True
    self.trial_state = "armed"
    logger.info("VerifyStrategy 已授权交易，等待下一根 Bar 发出验证开仓信号")
    return True


def revoke_authorization(self) -> None:
    self.trade_authorized = False
    if self.ready_to_arm and not self._bought and not self.completed:
        self.trial_state = "ready_to_arm"


def snapshot(self) -> dict:
    return {
        "state": self.trial_state,
        "symbol": self.symbol,
        "authorized": self.trade_authorized,
        "ready_to_arm": self.ready_to_arm,
        "completed": self.completed,
        "bar_count": self._bar_count,
        "warmup_bars": self.warmup_bars,
        "hold_bars": self.hold_bars,
        "bars_since_entry": self._bars_since_entry,
        "bought": self._bought,
        "closed": self._closed,
        "volume": self._volume,
    }
```

- [ ] **Step 4: Gate the buy signal in `on_bar()`**

Change the warmup-complete block so it returns until authorized:

```python
if self._bar_count < self.warmup_bars:
    self.trial_state = "warming"
    if self._bar_count % 5 == 0 or self._bar_count == 1:
        logger.info(
            "预热中 Bar#%d/%d | %s O=%.0f H=%.0f L=%.0f C=%.0f V=%d",
            self._bar_count, self.warmup_bars,
            symbol, bar["open"], bar["high"], bar["low"], close, vol_so_far,
        )
    return

if not self._bought and not self.trade_authorized:
    self.ready_to_arm = True
    self.trial_state = "ready_to_arm"
    logger.info("预热完成，等待前端授权交易")
    return

if not self._bought and self.trade_authorized and not self._entry_order_sent:
    logger.info("已授权，发送验证买单")
    signal = self.buy(self.symbol, close, self._volume)
    if signal:
        self._entry_price = close
        self._entry_order_sent = True
        self._bought = True
        self._bars_since_entry = 0
        self.trial_state = "armed"
```

Keep close logic but guard against duplicate close signals using `_close_order_sent`. After the close signal is generated, set:

```python
self._close_order_sent = True
self._closed = True
self.completed = True
self.trial_state = "completed"
```

- [ ] **Step 5: Run VerifyStrategy tests**

Run from `back_end`:

```bash
python -m pytest tests/test_verify_strategy.py -q
```

Expected: all VerifyStrategy tests pass.

## Task 3: Trial-Run API Module

**Files:**
- Create: `back_end/src/api/trial_run.py`
- Modify: `back_end/src/api/__init__.py`
- Modify: `back_end/src/api/security.py`
- Create: `back_end/tests/test_trial_run_api.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `back_end/tests/test_trial_run_api.py` with tests for config, prepare, arm, and status:

```python
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from tests.helpers import RecordingGateway


def _write_trial_config(path: Path, symbol: str = "rb2601") -> None:
    path.write_text(json.dumps({
        "trial_run": {
            "enabled": True,
            "account_id": "TEST001",
            "gateway": "vnpy",
            "vnpy_environment": "测试",
            "allowed_symbol": symbol,
            "manual_open_enabled": False,
        },
        "strategy": {
            "name": "verify",
            "symbol": symbol,
            "warmup_bars": 2,
            "hold_bars": 2,
            "volume": 1,
            "contract_multiplier": 10,
        },
        "trading": {
            "gateway": "vnpy",
            "broker_id": "2071",
            "td_server": "tcp://test-td",
            "md_server": "tcp://test-md",
            "app_id": "",
            "auth_code": "",
            "vnpy_environment": "测试",
            "bar_interval_minutes": 1,
            "initial_capital": 100000,
            "max_errors": 10,
        },
        "risk": {
            "enabled": True,
            "allowed_symbols": [symbol],
            "max_order_volume": 1,
            "max_position_volume": 1,
            "max_active_orders": 2,
            "max_orders_per_minute": 5,
            "max_daily_loss_ratio": 0.01,
            "max_market_data_age_seconds": 10,
            "duplicate_signal_window_seconds": 5,
            "allow_market_orders": False,
        },
    }), encoding="utf-8")
```

Add a read-only config test:

```python
def test_trial_run_config_masks_account_and_omits_password(tmp_path, monkeypatch):
    config_path = tmp_path / "config.local.json"
    _write_trial_config(config_path)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/trial-run/config")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["account_id"] == "TEST001"
    assert body["masked_account_id"] == "TE****01"
    assert "password" not in json.dumps(body).lower()
```

Add a prepare/arm test using a connected in-memory engine:

```python
def test_prepare_and_arm_trial_strategy(tmp_path, monkeypatch):
    import src.trading

    config_path = tmp_path / "config.local.json"
    _write_trial_config(config_path)
    monkeypatch.setenv("QUANT_TRIAL_CONFIG", str(config_path))
    trading_state.clear_main()

    gateway = RecordingGateway()
    monkeypatch.setattr(src.trading, "create_gateway", lambda gateway_type="vnpy": gateway)

    app = create_app()
    with TestClient(app) as client:
        login_response = client.post("/auth/login", json={
            "username": "TEST001",
            "password": "pw",
            "broker_id": "2071",
            "gateway_type": "vnpy",
        })
        assert login_response.status_code == 200

        prepare = client.post("/trial-run/prepare")
        assert prepare.status_code == 200
        assert prepare.json()["status"]["state"] in {"warming", "ready_to_arm"}

        arm_before_warmup = client.post("/trial-run/arm")
        assert arm_before_warmup.status_code == 409
```

- [ ] **Step 2: Create `trial_run.py` with config validation**

Create `back_end/src/api/trial_run.py` with:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request

from ..strategy import create_strategy
from ..trading import TradingEngine, TradingStatus
from .models import TrialRunActionResponse, TrialRunConfigResponse, TrialRunStatusResponse

TRIAL_STRATEGY_ID = "verify_trial"
TRIAL_CONFIG_ENV = "QUANT_TRIAL_CONFIG"
DEFAULT_TRIAL_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.local.json"
EXAMPLE_TRIAL_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.example.json"
```

Implement these helpers:

```python
def _config_path() -> Path:
    raw = os.getenv(TRIAL_CONFIG_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_TRIAL_CONFIG


def _load_raw_config() -> tuple[dict, str]:
    path = _config_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), str(path)
    return json.loads(EXAMPLE_TRIAL_CONFIG.read_text(encoding="utf-8")), str(EXAMPLE_TRIAL_CONFIG)


def _mask_account_id(account_id: str) -> str:
    value = str(account_id or "")
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}****{value[-2:]}"
```

Implement `_validate_trial_config(raw: dict) -> tuple[dict, list[str]]` so it:

- Reads `trial_run`, `strategy`, `trading`, and `risk` dictionaries.
- Requires `trial_run.enabled` to be true.
- Requires exactly one `risk.allowed_symbols`.
- Requires `trial_run.allowed_symbol == strategy.symbol == risk.allowed_symbols[0]`.
- Requires `strategy.name == "verify"`.
- Requires `strategy.volume == 1`.
- Requires `risk.max_order_volume == 1`.
- Requires `risk.max_position_volume == 1`.
- Requires `risk.max_orders_per_minute <= 5`.
- Requires `risk.max_active_orders <= 2`.
- Requires `risk.max_market_data_age_seconds > 0`.
- Forces `trial_run.manual_open_enabled` to a boolean defaulting to false.

- [ ] **Step 3: Add status snapshot logic**

In `trial_run.py`, add:

```python
def _trial_status(trading_state, raw_config: Optional[dict] = None, errors: Optional[list[str]] = None) -> TrialRunStatusResponse:
    raw_config = raw_config or {}
    errors = errors or []
    engine = trading_state.primary_engine()
    connected = bool(engine and engine.gateway.status in (TradingStatus.CONNECTED, TradingStatus.TRADING))
    gateway_status = "stopped"
    risk = {}
    last_reject_reason = ""
    if engine:
        gateway_status = engine.gateway.status.value if hasattr(engine.gateway.status, "value") else str(engine.gateway.status)
        risk = engine.risk_manager.status()
        last_reject_reason = getattr(engine, "last_reject_reason", "")

    entry = trading_state.get(TRIAL_STRATEGY_ID)
    strategy = entry.strategy if entry else None
    snapshot = strategy.snapshot() if strategy and hasattr(strategy, "snapshot") else {}
    state = snapshot.get("state") or ("connected" if connected else "disconnected")

    if risk.get("emergency_stop"):
        state = "emergency_stopped"

    return TrialRunStatusResponse(
        state=state,
        connected=connected,
        gateway_status=gateway_status,
        strategy_id=TRIAL_STRATEGY_ID if entry else "",
        strategy_name="verify",
        symbol=snapshot.get("symbol") or raw_config.get("strategy", {}).get("symbol", ""),
        authorized=bool(snapshot.get("authorized", False)),
        ready_to_arm=bool(snapshot.get("ready_to_arm", False)),
        completed=bool(snapshot.get("completed", False)),
        bar_count=int(snapshot.get("bar_count", 0)),
        warmup_bars=int(snapshot.get("warmup_bars", raw_config.get("strategy", {}).get("warmup_bars", 0) or 0)),
        hold_bars=int(snapshot.get("hold_bars", raw_config.get("strategy", {}).get("hold_bars", 0) or 0)),
        bars_since_entry=int(snapshot.get("bars_since_entry", 0)),
        position_volume=int(getattr(strategy.get_position(snapshot.get("symbol")), "volume", 0)) if strategy and snapshot.get("symbol") else 0,
        last_reject_reason=last_reject_reason,
        risk=risk,
        validation_errors=errors,
    )
```

- [ ] **Step 4: Register trial-run routes**

Add `register_trial_run_routes(app, trading_state, subscribe_market_ticks, record_audit)`:

```python
def register_trial_run_routes(
    app: FastAPI,
    *,
    trading_state,
    subscribe_market_ticks: Callable[[TradingEngine, list[str]], None],
    record_audit: Callable,
) -> None:
    @app.get("/trial-run/config", response_model=TrialRunConfigResponse, tags=["试运行"])
    def trial_run_config():
        raw, source = _load_raw_config()
        normalized, errors = _validate_trial_config(raw)
        trial = normalized.get("trial_run", {})
        return TrialRunConfigResponse(
            enabled=bool(trial.get("enabled", False)),
            ready=not errors,
            config_source=source,
            account_id=trial.get("account_id", ""),
            masked_account_id=_mask_account_id(trial.get("account_id", "")),
            gateway=trial.get("gateway", "vnpy"),
            environment=trial.get("vnpy_environment", "测试"),
            allowed_symbol=trial.get("allowed_symbol", ""),
            manual_open_enabled=bool(trial.get("manual_open_enabled", False)),
            trading={k: v for k, v in normalized.get("trading", {}).items() if k != "password"},
            strategy=normalized.get("strategy", {}),
            risk=normalized.get("risk", {}),
            validation_errors=errors,
        )
```

Add route behavior:

- `GET /trial-run/status`: load and validate config, then return `_trial_status(trading_state, raw_config=normalized, errors=errors)`.
- `POST /trial-run/prepare`: require no validation errors, require a connected main engine, create `VerifyStrategy`, call `engine.set_strategy(strategy)`, call `engine.start({**trading, "risk": risk})`, register as `verify_trial`, subscribe the single symbol, record audit action `trial_run.prepare`.
- `POST /trial-run/arm`: require `verify_trial` exists and `strategy.authorize_trading()` returns true, otherwise return `409`; record audit action `trial_run.arm`.
- `POST /trial-run/stop`: stop the trial engine if registered, revoke authorization if available, unregister `verify_trial`, record audit action `trial_run.stop`.
- `POST /trial-run/reset`: only clear the trial entry and status; do not disconnect CTP; record audit action `trial_run.reset`.

- [ ] **Step 5: Mount routes from the existing app**

In `back_end/src/api/__init__.py`, import and call the route registrar inside `create_app()` after the core helpers are defined and before `return app`:

```python
from .trial_run import register_trial_run_routes

register_trial_run_routes(
    app,
    trading_state=trading_state,
    subscribe_market_ticks=_subscribe_market_ticks,
    record_audit=_record_audit,
)
```

- [ ] **Step 6: Expose read-only trial-run paths without login**

In `back_end/src/api/security.py`, add:

```python
"/trial-run/config",
"/trial-run/status",
```

to `OPEN_PATHS`. Leave `prepare`, `arm`, `stop`, and `reset` protected by the existing session middleware.

- [ ] **Step 7: Run focused API tests**

Run from `back_end`:

```bash
python -m pytest tests/test_trial_run_api.py tests/test_api_auth.py -q
```

Expected: trial-run endpoints pass and auth tests still pass.

## Task 4: Manual Open Blocking in Trial-Run Mode

**Files:**
- Modify: `back_end/src/api/__init__.py`
- Modify: `back_end/tests/test_security_and_risk.py`
- Modify: `back_end/tests/test_manual_trading.py`

- [ ] **Step 1: Add failing API test for manual open rejection**

Add a test to `back_end/tests/test_manual_trading.py` using the existing TestClient and fake engine pattern:

```python
def test_trial_run_mode_rejects_manual_open(monkeypatch):
    gateway = install_gateway(monkeypatch)
    monkeypatch.setattr("src.api.trial_run.trial_run_manual_open_enabled", lambda: False)

    app = create_app()
    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        response = client.post(
            "/orders",
            json={
                "symbol": "rb2505",
                "direction": "long",
                "offset": "open",
                "price": 0,
                "volume": 1,
                "order_type": "market",
            },
        )

    assert response.status_code == 400
    assert "试运行模式禁止手动开仓" in response.json()["detail"]
    assert gateway.sent_signals == []
```

- [ ] **Step 2: Add a focused helper in `trial_run.py`**

In `back_end/src/api/trial_run.py`, add:

```python
def trial_run_manual_open_enabled() -> bool:
    raw, _source = _load_raw_config()
    normalized, _errors = _validate_trial_config(raw)
    return bool(normalized.get("trial_run", {}).get("manual_open_enabled", False))
```

If config loading fails, return `False` so the safe default is to block manual opens.

- [ ] **Step 3: Block manual opening orders**

In `place_manual_order()` in `back_end/src/api/__init__.py`, after the `_normalize_choice` call that assigns `offset_name, offset` and before creating the `Signal`, add:

```python
from .trial_run import trial_run_manual_open_enabled

if offset_name == "open" and not trial_run_manual_open_enabled():
    _record_audit(
        "order",
        "manual_order",
        "rejected",
        resource=symbol,
        request=request,
        detail={"reason": "试运行模式禁止手动开仓", "volume": volume, "direction": direction_name},
    )
    raise HTTPException(status_code=400, detail="试运行模式禁止手动开仓")
```

Ensure `close`, `close_today`, and `close_yesterday` still flow through normal risk checks.

- [ ] **Step 4: Run manual trading and risk tests**

Run from `back_end`:

```bash
python -m pytest tests/test_manual_trading.py tests/test_security_and_risk.py -q
```

Expected: manual open is rejected in trial-run mode, existing close/cancel behavior still passes.

## Task 5: Frontend API Client and Route

**Files:**
- Modify: `front_end/src/api/index.js`
- Modify: `front_end/src/router/index.js`
- Create: `front_end/tests/unit/trialRunApi.spec.js`

- [ ] **Step 1: Add API client unit tests**

Create `front_end/tests/unit/trialRunApi.spec.js`:

```js
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchTrialRunConfig,
  fetchTrialRunStatus,
  prepareTrialRun,
  armTrialRun,
  stopTrialRun,
  resetTrialRun,
} from '@/api/index.js'

describe('trial run api client', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    }))
  })

  it('uses trial-run endpoints', async () => {
    await fetchTrialRunConfig()
    await fetchTrialRunStatus()
    await prepareTrialRun()
    await armTrialRun()
    await stopTrialRun()
    await resetTrialRun()

    const paths = global.fetch.mock.calls.map(([url]) => String(url))
    expect(paths.some((url) => url.includes('/trial-run/config'))).toBe(true)
    expect(paths.some((url) => url.includes('/trial-run/status'))).toBe(true)
    expect(paths.some((url) => url.includes('/trial-run/prepare'))).toBe(true)
    expect(paths.some((url) => url.includes('/trial-run/arm'))).toBe(true)
    expect(paths.some((url) => url.includes('/trial-run/stop'))).toBe(true)
    expect(paths.some((url) => url.includes('/trial-run/reset'))).toBe(true)
  })
})
```

- [ ] **Step 2: Add trial-run API functions**

In `front_end/src/api/index.js`, add:

```js
export const fetchTrialRunConfig = () => request('/trial-run/config')
export const fetchTrialRunStatus = () => request('/trial-run/status')
export const prepareTrialRun = () => request('/trial-run/prepare', { method: 'POST' })
export const armTrialRun = () => request('/trial-run/arm', { method: 'POST' })
export const stopTrialRun = () => request('/trial-run/stop', { method: 'POST' })
export const resetTrialRun = () => request('/trial-run/reset', { method: 'POST' })
```

- [ ] **Step 3: Add the route**

In `front_end/src/router/index.js`, add before the catch-all route:

```js
{
  path: '/trial-run',
  name: 'TrialRun',
  component: () => import('@/views/TrialRunView.vue'),
  meta: { requiresAuth: false, title: '试运行操作台' },
},
```

- [ ] **Step 4: Run frontend unit tests**

Run from `front_end`:

```bash
npm run test -- trialRunApi
```

Expected: the new API client test passes.

## Task 6: TrialRunView UI

**Files:**
- Create: `front_end/src/views/TrialRunView.vue`
- Modify: `front_end/tests/e2e/smoke.spec.js`

- [ ] **Step 1: Add smoke test expectation**

Update `front_end/tests/e2e/smoke.spec.js`:

```js
await page.goto('/trial-run')
await expect(page.locator('.trial-run-page')).toBeVisible()
await expect(page.getByRole('heading', { name: '试运行操作台' })).toBeVisible()
```

- [ ] **Step 2: Create the page skeleton**

Create `front_end/src/views/TrialRunView.vue` with a first-pass structure:

```vue
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  armTrialRun,
  cancelAllOrders,
  closePosition,
  emergencyStop,
  fetchAuthStatus,
  fetchOrders,
  fetchPositions,
  fetchRiskStatus,
  fetchSystemLogs,
  fetchTrades,
  fetchTrialRunConfig,
  fetchTrialRunStatus,
  login,
  prepareTrialRun,
  resetTrialRun,
  resumeTrading,
  stopTrialRun,
} from '@/api/index.js'

const loading = ref(false)
const actionLoading = ref('')
const config = ref(null)
const status = ref({ state: 'disconnected' })
const auth = ref({ gateway_connected: false, gateway_status: 'stopped' })
const risk = ref({ risk: null })
const orders = ref([])
const trades = ref([])
const positions = ref([])
const logs = ref([])
const password = ref('')

const loginForm = reactive({
  username: '',
  broker_id: '',
  td_server: '',
  md_server: '',
  app_id: '',
  auth_code: '',
  environment: '测试',
})
</script>
```

Use template sections with these class hooks for tests and maintainability:

- `.trial-run-page`
- `.trial-status-strip`
- `.trial-flow-panel`
- `.trial-risk-panel`
- `.trial-monitor-panel`
- `.trial-bottom-tabs`

- [ ] **Step 3: Load read-only config and status**

Add `loadConfig()`:

```js
async function loadConfig() {
  const data = await fetchTrialRunConfig()
  config.value = data
  loginForm.username = data.account_id || ''
  loginForm.broker_id = data.trading?.broker_id || ''
  loginForm.td_server = data.trading?.td_server || ''
  loginForm.md_server = data.trading?.md_server || ''
  loginForm.app_id = data.trading?.app_id || ''
  loginForm.auth_code = data.trading?.auth_code || ''
  loginForm.environment = data.environment || data.trading?.vnpy_environment || '测试'
}
```

Add `refreshSnapshots()`:

```js
async function refreshSnapshots() {
  const settled = await Promise.allSettled([
    fetchTrialRunStatus(),
    fetchAuthStatus(),
    fetchRiskStatus(),
    fetchOrders(),
    fetchTrades(),
    fetchPositions(),
    fetchSystemLogs({ limit: 120 }),
  ])
  if (settled[0].status === 'fulfilled') status.value = settled[0].value
  if (settled[1].status === 'fulfilled') auth.value = settled[1].value
  if (settled[2].status === 'fulfilled') risk.value = settled[2].value
  if (settled[3].status === 'fulfilled') orders.value = settled[3].value
  if (settled[4].status === 'fulfilled') trades.value = settled[4].value
  if (settled[5].status === 'fulfilled') positions.value = settled[5].value
  if (settled[6].status === 'fulfilled') logs.value = settled[6].value.logs || []
}
```

Use a 2-second polling timer for first implementation; WebSocket refinement can reuse existing composables later.

- [ ] **Step 4: Implement actions**

Add handlers:

```js
async function handleLogin() {
  if (!password.value) {
    ElMessage.warning('请输入 CTP 密码')
    return
  }
  loading.value = true
  try {
    await login({
      username: loginForm.username,
      password: password.value,
      broker_id: loginForm.broker_id,
      td_server: loginForm.td_server,
      md_server: loginForm.md_server,
      app_id: loginForm.app_id,
      auth_code: loginForm.auth_code,
      environment: loginForm.environment,
      auto_start_strategy: false,
      risk: config.value?.risk || {},
    })
    password.value = ''
    ElMessage.success('连接成功')
    await refreshSnapshots()
  } catch (err) {
    ElMessage.error(err.message || '连接失败')
  } finally {
    loading.value = false
  }
}
```

Add `runAction(name, fn, successMessage)` wrapper and handlers for prepare, arm, stop, reset, emergency stop, resume, cancel all, and close position. For `armTrialRun()`, show a confirmation dialog:

```js
await ElMessageBox.confirm(
  `授权后 VerifyStrategy 将仅允许对 ${config.value.allowed_symbol} 发出 1 手验证开仓信号。确认继续？`,
  '授权交易确认',
  { confirmButtonText: '确认授权', cancelButtonText: '取消', type: 'warning' },
)
```

- [ ] **Step 5: Build the operational layout**

Use Element Plus controls with existing CSS variables:

- Top strip: `masked_account_id` or `account_id`, environment, `allowed_symbol`, `auth.gateway_status`, `status.state`, emergency state.
- Flow panel: numbered steps for connect, prepare, warmup, arm, holding/closing, completed.
- Risk panel: list `max_order_volume`, `max_position_volume`, `max_orders_per_minute`, `allowed_symbols`, `allow_market_orders`, `last_reject_reason`.
- Monitor panel: bar progress `bar_count / warmup_bars`, position volume, latest orders/trades counts.
- Bottom tabs: compact tables for orders, trades, positions, logs.

Do not add hand-open controls. Only include buttons for:

- `连接`
- `准备策略`
- `授权交易`
- `停止策略`
- `重新准备`
- `急停`
- `解除急停`
- `一键撤单`
- `快捷平仓`

- [ ] **Step 6: Run frontend checks**

Run from `front_end`:

```bash
npm run lint
npm run test -- trialRunApi
npm run build
```

Expected: lint, unit test, and production build pass.

## Task 7: End-to-End Verification and Regression

**Files:**
- No feature files; this task verifies the completed implementation.

- [ ] **Step 1: Run backend focused tests**

Run from `back_end`:

```bash
python -m pytest tests/test_trial_run_api.py tests/test_verify_strategy.py tests/test_manual_trading.py tests/test_security_and_risk.py -q
```

Expected: all focused backend tests pass.

- [ ] **Step 2: Run backend full suite**

Run from `back_end`:

```bash
python -m pytest
python -m ruff check src tests
python -m mypy
```

Expected: pytest, ruff, and mypy pass. If `mypy` surfaces unrelated existing issues, capture exact filenames and messages before deciding whether to fix or document them.

- [ ] **Step 3: Run frontend full suite**

Run from `front_end`:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
```

Expected: all frontend checks pass.

- [ ] **Step 4: Browser verification**

Start the app stack using the repository’s existing development commands:

```bash
cd back_end
uvicorn src.api:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd front_end
npm run dev
```

Open `http://localhost:5173/trial-run` in the Codex in-app browser and verify:

- The page renders without overlap at desktop width.
- The page renders without horizontal overflow at mobile width.
- Read-only config appears and does not show a password field value.
- The only trading buttons are prepare, arm, stop, reset, emergency stop, resume, cancel, and close.
- There is no manual open button or open-order form.

- [ ] **Step 5: Manual CTP simulation checklist**

During an active simulation trading window, use `back_end/config/config.local.json` with a valid account and a confirmed currently tradable contract. From `/trial-run`, verify:

- Login succeeds with password entered in the page and not saved.
- Prepare strategy enters `warming`.
- Before `ready_to_arm`, the arm button is disabled or rejected.
- In `ready_to_arm`, clicking arm enables the next strategy signal.
- The strategy submits only 1 lot for the configured symbol.
- Orders, trades, positions, and logs are visible.
- Emergency stop blocks further strategy signals.
- Cancel all remains available during emergency stop.
- Quick close never exceeds the current position.
- Completed state appears after the close fill.

## Self-Review Checklist

- Spec coverage: this plan covers config, backend API, VerifyStrategy authorization, manual-open blocking, frontend page, existing endpoint reuse, tests, and real CTP verification.
- Type consistency: response models in Task 1 are used by `trial_run.py` in Task 3; frontend client functions in Task 5 are used by `TrialRunView.vue` in Task 6.
- Safety defaults: missing or invalid local config never enables manual opening orders; real local config is ignored by Git; password is never included in config responses.
- Remaining implementation risk: `POST /trial-run/prepare` starts the existing `TradingEngine` on an already connected gateway, which matches current `TradingEngine.start()` behavior. If that interaction exposes order-manager lifecycle failures, fix them inside `TradingEngine` with focused tests rather than special-casing the frontend.
