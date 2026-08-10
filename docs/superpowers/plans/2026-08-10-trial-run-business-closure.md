# Trial-Run Business Closure Implementation Plan

> **Execution note:** The user selected Subagent-Driven execution. Assign one bounded task at a time to an implementation subagent, then have the main thread review the diff and run the task gate before continuing. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在固定合约、1 手规模下，完成可审计的试运行双轨闭环：真实 CTP 委托链路能够报单、撤单和最多追价 5 次；没有对手盘时，在确认真实委托已撤且真实持仓为 0 后，使用隔离的模拟账本验证开仓成交、持仓、平仓和归零，并导出 DOCX 报告。

**Architecture:** 保留真实 CTP 报单路径作为 `real_track`，不承诺测试环境一定真实成交。将模拟成交改为 `simulation_track`：它只能在真实委托撤单确认和对账通过后启用，使用独立持仓/订单/成交账本，并通过策略专用执行适配器复用 `VerifyStrategy` 的成交后状态机；绝不修改真实 gateway 的订单和持仓。追价改为本地单调时钟驱动，撤单确认后才允许重报。

**Tech Stack:** Python 3.13、FastAPI、现有 `TradingEngine`/`VerifyStrategy`、Pydantic、pytest、Vue 3、Element Plus、Vitest、Playwright、python-docx。

---

## 0. Execution Prerequisite

当前 `codex/trial-run` 工作区已经包含未提交的模拟成交、诊断和前端按钮改动。执行本计划时遵守以下约束：

- 不重置、不覆盖当前用户改动；每个任务开始前读取 `git status --short` 和目标文件 diff。
- Task 1 首先让现有 `/trial-run/simulate-fill` 入口 fail closed，在隔离账本完成前固定返回 `409 simulation_migration_in_progress`。
- `data/historical/sessions.db` 不得加入任何实现提交。
- Task 4 通过 gateway 不变性测试前，分支不得合并或用于手工模拟成交。
- 每个子代理只修改任务列出的文件；主线程负责审查跨任务契约和执行测试门。

---

## 1. Achievable Business Objective

### 1.1 Accepted outcomes

| Outcome | Required evidence | Business conclusion |
|---|---|---|
| `passed_real` | 真实开仓成交、真实平仓成交、券商持仓归零、无活动委托、对账一致 | 真实成交闭环通过 |
| `passed_simulated` | 至少一笔真实委托被券商接受；真实委托已撤且真实持仓为 0；模拟开仓和平仓均完成；模拟持仓归零 | 真实报单链路和成交后业务处理通过；未证明真实成交 |
| `failed` | 拒单、撤单失败、追价耗尽后仍有活动委托、对账不一致、晚到真实成交与模拟账本冲突 | 试运行失败，必须人工处置 |
| `aborted` | 服务重启、用户急停或状态无法恢复；完成券商对账后终止本轮 | 本轮无通过结论，可安全开始新一轮 |

`passed_real` 和 `passed_simulated` 都是本项目可接受的通过结果，但页面与报告不得把后者描述为券商真实成交。

### 1.2 Fixed boundaries

- 固定使用配置中的唯一合约；当前默认是 `rb2610`，不实现自动主力合约选择。
- 每次只允许 1 手，流程固定为“开多 -> 持仓 -> 平多 -> 归零”。
- 首笔委托不计入追价次数；最多重报 5 次，总真实报单数上限为 6。
- 默认每 2 秒检查一次追价条件；撤单未确认时不得重报。
- 保留既有 `risk.max_orders_per_minute <= 5` 安全标准。任一滚动分钟最多占用 4 个开仓报单名额，始终为真实平仓保留至少 1 个名额；因此 5 次追价允许跨分钟完成，不能绕过限频。
- 无成交满 2 秒后即可请求切换模拟轨道，不要求先耗尽 5 次追价；请求模拟后，正在进行的追价撤单可被接管，但不得再自动重报。
- 第 5 次重报执行最终成交策略：gateway 明确支持市价单时才使用市价，否则使用受涨跌停和最小变动价位约束的最激进限价单。
- 模拟成交价格固定取当前模拟订单已存价格；该订单价格在创建时来自最后一笔有效行情，成交时不再临时改价。用户不能输入成交价、手续费或保证金，后两者在模拟账本中固定为 0。
- 没有第二个账号，不实现双账号撮合。
- 页面刷新后从后端恢复本轮状态；后端进程重启不自动续跑，而是标记 `aborted`，完成对账后才能开始新一轮。
- 试运行期间独占主交易引擎；检测到其他运行策略时，`prepare` 返回冲突，不静默替换策略。

### 1.3 Preflight gate

在真实报单前必须同时满足：

1. CTP gateway 已连接，账户查询成功。
2. 配置中的 `trial_run.allowed_symbol`、`strategy.symbol`、`risk.allowed_symbols[0]` 等价。
3. 目标合约已有有效 tick，`last_price > 0`，行情接收时间不超过风险阈值。
4. 目标合约真实持仓为 0，且不存在该合约活动委托。
5. 风控限制为单笔 1 手、最大持仓 1 手、只允许唯一合约。
6. 没有其他策略占用主引擎。
7. 风控滚动窗口至少还有 2 个报单名额，分别供首次开仓和必要平仓使用。

任一条件失败都只返回诊断，不发送委托。

---

## 2. Target State Machine

```text
preflight
  -> waiting_market_data
  -> real_entry_pending
  -> real_entry_filled -> real_holding -> real_close_pending -> passed_real
  -> real_chasing
  -> real_cancel_pending
  -> simulation_ready
  -> simulated_entry_pending
  -> simulated_holding
  -> simulated_close_pending
  -> passed_simulated

Any state -> failed
Any state -> aborted
```

关键不变量：

- `simulation_ready` 之前，真实 gateway 中目标合约活动委托数必须为 0、真实持仓必须为 0。
- `simulation_track` 启用后，新策略信号只能进入模拟执行适配器，不能发送到 CTP。
- 模拟订单 ID 使用 `SIM-` 前缀；真实订单 ID 不被改写为 `filled`。
- 若进入模拟轨道后收到同一真实委托的晚到成交，立即进入 `failed`，错误码为 `late_real_fill_conflict`。

---

## 3. File Map

### Backend domain and execution

- Create `back_end/src/trading/trial_run_execution.py`
  - 双轨状态、订单链、通过结论和不变量检查。
- Create `back_end/src/trading/execution_adapter.py`
  - 策略信号执行协议和 gateway 默认适配器。
- Create `back_end/src/trading/trial_run_store.py`
  - 持久化 run ID、状态快照和重启中止结论。
- Modify `back_end/src/trading/simulated_fill.py`
  - 替换当前直接修改 gateway 的实现，改为隔离模拟账本和模拟执行适配器。
- Modify `back_end/src/trading/gateway.py`
  - 声明市价单能力查询，默认 fail closed。
- Modify `back_end/src/trading/vnpy_gateway.py`
  - 仅对已确认支持的合约/交易所返回市价单能力。
- Modify `back_end/src/trading/order_manager.py`
  - 在已有 100ms 监控线程中提供本地单调时钟 heartbeat。
- Modify `back_end/src/trading/risk.py`
  - 暴露滚动限频余量和下一可用时间，追价时保留平仓名额。
- Modify `back_end/src/trading/engine.py`
  - 接入 heartbeat、执行适配器、模拟成交策略回调和恢复 gateway 执行路径。
- Modify `back_end/src/strategy/strategies/verify.py`
  - 订单角色、追价状态、单调时间、模拟轨道状态和订单归属。

### Backend API and reporting

- Modify `back_end/src/api/models.py`
  - 增加双轨状态、订单链、模拟准备和报告结论模型。
- Modify `back_end/src/api/__init__.py`
  - 暴露实际登录时使用的脱敏主引擎环境，供试运行预检比对。
- Modify `back_end/src/api/trial_run.py`
  - 预检、唯一订单校验、模拟准备/成交路由、对账和状态响应。
- Modify `back_end/config/config.example.json`
  - 固定 2 秒无成交阈值、1 根持仓 Bar 和模拟功能示例开关。
- Modify `back_end/config/config_production.json`
  - 在当前明确为“仿真”的运行配置中启用隔离模拟验证，并应用相同短周期参数。
- Create `back_end/src/api/trial_run_report.py`
  - 生成 DOCX 测试报告。

### Frontend

- Modify `front_end/src/api/index.js`
  - 增加模拟准备接口，保留 Blob 报告下载。
- Modify `front_end/src/views/TrialRunView.vue`
  - 双轨结果、订单链、追价状态、模拟准备和两次模拟成交操作。
- Modify `front_end/tests/unit/trialRunApi.spec.js`
  - API helper 和错误响应覆盖。
- Modify `front_end/tests/e2e/smoke.spec.js`
  - 使用 Playwright 路由模拟真实后端状态，不允许 502 仍判绿。

### Tests

- Create `back_end/tests/test_trial_run_execution.py`
- Replace `back_end/tests/test_simulated_fill.py` with isolated-ledger tests.
- Modify `back_end/tests/test_trading_engine_auto_strategy.py`
- Modify `back_end/tests/test_security_and_risk.py`
- Modify `back_end/tests/test_verify_strategy.py`
- Modify `back_end/tests/test_trial_run_api.py`
- Create `back_end/tests/test_trial_run_closed_loop.py`
- Create `back_end/tests/test_trial_run_store.py`
- Create `back_end/tests/test_trial_run_report.py`
- Modify `.gitignore`
  - 忽略 `back_end/data/runtime/*.db*`，避免运行状态数据库进入版本控制。

---

## Task 1: Lock the Acceptance Contract and Preflight

**Files:**
- Create: `back_end/src/trading/trial_run_execution.py`
- Modify: `back_end/src/api/models.py`
- Modify: `back_end/src/api/__init__.py`
- Modify: `back_end/src/api/trial_run.py`
- Test: `back_end/tests/test_trial_run_execution.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [x] **Step 0: Disable the legacy simulated-fill mutation during migration**

Until Task 4 installs `TrialRunSimulationLedger`, `/trial-run/simulate-fill` must return:

```json
{
  "detail": {
    "failure_code": "simulation_migration_in_progress",
    "message": "隔离模拟账本尚未启用"
  }
}
```

with HTTP `409`. `TrialRunStatusResponse.simulate_fill_allowed` must be `false` during this migration state.

- [x] **Step 1: Write failing outcome-model tests**

Define tests for these exact values:

```python
def test_simulated_pass_requires_real_submission_and_clean_broker_state():
    state = TrialRunExecutionState(symbol="rb2610", volume=1)
    state.record_real_submission(order_id="R1", role="entry", price=3120)
    state.record_real_cancel(order_id="R1")
    state.record_simulated_entry(order_id="SIM-E1", trade_id="SIM-T1", price=3120)
    state.record_simulated_close(order_id="SIM-C1", trade_id="SIM-T2", price=3121)

    assert state.evaluate(
        broker_position_volume=0,
        broker_active_order_ids=[],
        reconcile_ok=True,
    ).outcome == "passed_simulated"
```

Also assert that missing real submission, non-zero broker position, an active broker order, or `reconcile_ok=False` cannot produce a passing outcome.

- [x] **Step 2: Run the new tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_execution.py -q
```

Expected: collection or import failure because `TrialRunExecutionState` does not exist.

- [x] **Step 3: Implement typed execution state**

Create these enums/dataclasses in `trial_run_execution.py`:

```python
class TrialRunOutcome(str, Enum):
    RUNNING = "running"
    PASSED_REAL = "passed_real"
    PASSED_SIMULATED = "passed_simulated"
    FAILED = "failed"
    ABORTED = "aborted"

class TrialRunTrack(str, Enum):
    REAL = "real"
    SIMULATED = "simulated"

@dataclass
class TrialOrderAttempt:
    order_id: str
    role: str
    track: TrialRunTrack
    attempt: int
    parent_order_id: str
    symbol: str
    direction: str
    offset: str
    price: float
    volume: int
    status: str
    created_at: datetime
    updated_at: datetime
```

`TrialRunExecutionState` must own the order chain, real/simulated trade IDs, real-submission proof, current order ID, simulated position volume, failure code, and final outcome. Every mutator must reject a symbol other than the configured symbol and a volume other than 1.

- [x] **Step 4: Add typed API response fields**

Add response models for:

- `outcome`
- `success_basis`
- `current_track`
- `current_order_id`
- `order_chain`
- `broker_position_volume`
- `simulated_position_volume`
- `broker_active_order_ids`
- `reconcile_ok`
- `simulation_state`
- `simulation_prepare_allowed`
- `failure_code`
- `rate_limit_remaining`
- `rate_limit_retry_after_seconds`
- `hold_deadline_at`

Keep existing fields for frontend compatibility during migration.

- [x] **Step 5: Add preflight failures**

Extend `/trial-run/prepare` to reject with `409` and a stable reason code when:

- another strategy is registered on the primary engine;
- the configured symbol has a non-zero broker position;
- the configured symbol has an active broker order;
- account data is unavailable;
- no fresh valid target tick exists after the existing 15-second diagnostic window.

Add `TradingState.main_config_snapshot()` that returns only gateway type, environment, and front addresses from the actual successful login configuration. Simulation authorization must use this runtime environment as the source of truth. A trial config that says “测试” cannot override a runtime login marked “实盘”.

Add tests for both mismatches:

- trial config test + runtime login live -> simulation denied;
- trial config live + runtime login test -> remain denied until the trial config is corrected, because both sources must agree on a non-production environment.

The first `prepare` call may enter `waiting_market_data`; it must not send an order until tick validity is proven.

- [x] **Step 6: Run Task 1 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_execution.py back_end\tests\test_trial_run_api.py -q
```

Expected: all selected tests pass.

- [x] **Step 7: Commit Task 1**

```powershell
git add back_end/src/trading/trial_run_execution.py back_end/src/api/models.py back_end/src/api/__init__.py back_end/src/api/trial_run.py back_end/tests/test_trial_run_execution.py back_end/tests/test_trial_run_api.py
git commit -m "feat(trial-run): define business acceptance contract"
```

---

## Task 2: Bind Every Order to the Trial-Run Order Chain

**Files:**
- Modify: `back_end/src/trading/trial_run_execution.py`
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/src/trading/engine.py`
- Modify: `back_end/src/api/trial_run.py`
- Test: `back_end/tests/test_verify_strategy.py`
- Test: `back_end/tests/test_trial_run_execution.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [x] **Step 1: Write coordinator ownership rejection tests**

Because Task 1 deliberately keeps the legacy simulation endpoint fail closed, put these checks on `TrialRunExecutionState.require_current_order()` first. Prove it rejects:

- an active order from the same symbol but a different strategy/order chain;
- an old canceled order superseded by a chase replacement;
- an order with a different symbol;
- an attempted state mutation whose volume or direction differs from the tracked order.

The domain error must carry `failure_code="order_not_owned_by_trial_run"` or `failure_code="order_not_current"`. Task 4 maps those errors to API `409` responses after the isolated endpoint is enabled.

- [x] **Step 2: Record order role and parent relationship**

Extend `VerifyStrategy.on_signal_submitted()` so the engine can register:

- `role="entry"` for `buy_open`;
- `role="exit"` for `sell_close`;
- `attempt=0` for the initial order;
- `attempt=1..5` for replacements;
- `parent_order_id` pointing to the canceled order.

Expose `current_order_id`, `entry_order_id`, `close_order_id`, and the active role in `snapshot()`.

- [x] **Step 3: Make the coordinator authoritative**

The trial-run API must obtain the current order exclusively from `TrialRunExecutionState`. Remove frontend/backend behavior that scans all gateway orders and chooses the first matching symbol.

- [x] **Step 4: Test callback ordering**

Cover these sequences:

```text
submit -> submitted -> cancelled -> replacement submitted
submit -> partfilled -> filled
submit -> rejected
cancel requested -> late filled
```

Callbacks for unrelated order IDs must not alter the trial-run state.

- [x] **Step 5: Run Task 2 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_execution.py back_end\tests\test_verify_strategy.py back_end\tests\test_trial_run_api.py back_end\tests\test_trading_engine_auto_strategy.py -q
```

- [x] **Step 6: Commit Task 2**

```powershell
git add back_end/src/trading/trial_run_execution.py back_end/src/strategy/strategies/verify.py back_end/src/trading/engine.py back_end/src/api/trial_run.py back_end/tests/test_trial_run_execution.py back_end/tests/test_verify_strategy.py back_end/tests/test_trial_run_api.py back_end/tests/test_trading_engine_auto_strategy.py
git commit -m "feat(trial-run): bind orders to verification chain"
```

---

## Task 3: Make Chasing Timer-Driven and Bounded

**Files:**
- Modify: `back_end/src/trading/order_manager.py`
- Modify: `back_end/src/trading/engine.py`
- Modify: `back_end/src/trading/risk.py`
- Modify: `back_end/src/trading/gateway.py`
- Modify: `back_end/src/trading/vnpy_gateway.py`
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/src/api/models.py`
- Modify: `back_end/src/api/trial_run.py`
- Test: `back_end/tests/test_trading_engine_auto_strategy.py`
- Test: `back_end/tests/test_verify_strategy.py`
- Test: `back_end/tests/test_security_and_risk.py`

- [x] **Step 1: Write a no-new-tick chase test**

Use an injected monotonic clock. Submit the initial order, advance the clock beyond 2 seconds without delivering another tick, run one heartbeat, and assert exactly one cancel request was sent.

```python
clock.advance(2.1)
engine.on_timer(clock.now())
assert gateway.cancelled_order_ids == [initial_order_id]
assert strategy.snapshot()["chase_state"] == "cancel_pending"
```

- [x] **Step 2: Add an OrderManager heartbeat**

Add `on_timer_callback: Optional[Callable[[float], None]]`. The existing `_monitor_pre_orders` loop calls it once per loop with `time.monotonic()`, outside the order-manager lock. Exceptions are logged and do not stop the monitor thread.

- [x] **Step 3: Use monotonic order age**

Change `VerifyStrategy` to record `submitted_monotonic` separately from display timestamps. `next_chase_action()` accepts `now_monotonic` and must not calculate timeout from the exchange tick timestamp.

- [x] **Step 4: Expose and reserve rolling rate capacity**

Inject the monotonic clock into `RiskManager` and add `order_rate_snapshot(now_monotonic=None)` returning `remaining` and `retry_after_seconds`. Keep the configured cap at 5 per rolling minute.

Before an automatic action:

- initial real entry requires `remaining >= 2`;
- an entry replacement requires `remaining >= 2` before canceling the current order;
- a real close or close replacement requires `remaining >= 1`.

When capacity is insufficient, leave the current broker order active, set `chase_state="waiting_rate_capacity"`, expose the retry time, and do not cancel. This guarantees that entry chasing cannot consume the final slot needed to reduce a real position. Add a risk test proving no rolling 60-second window contains more than five submissions.

At this task, extend `/trial-run/prepare` to use the same snapshot: when fewer than 2 submissions remain, return `409 failure_code="rate_capacity_not_ready"` with `retry_after_seconds` and do not send the initial order.

- [x] **Step 5: Enforce cancel-before-replace**

The state sequence must be:

```text
waiting_timeout -> waiting_rate_capacity -> cancel_pending -> cancelled -> waiting_fresh_quote -> resubmit_pending
```

No replacement signal may be generated until the canceled order callback is received. If cancellation fails, set `chase_state="cancel_failed"` and stop automatic re-submission.

- [x] **Step 6: Handle stale and missing quotes explicitly**

For normal chasing, require a fresh valid quote and sufficient rate capacity before requesting cancellation. Re-check both after the cancellation callback because they may change while the broker responds. If the quote becomes stale after cancellation, remain flat with `chase_state="waiting_fresh_quote"` and show a warning; do not price from a stale tick. A simulation-preparation request may still cancel an entry order with stale market data because it will reconcile to a flat real account and will not re-submit to CTP.

- [x] **Step 7: Implement attempt 5 fallback policy**

Add `GatewayBase.supports_market_order(symbol) -> bool`, defaulting to `False`. `VnpyGateway` returns `True` only when the concrete contract/exchange capability is known. On attempt 5:

- supported and `chase_fallback_to_market=true`: submit market;
- otherwise: submit final aggressive limit, rounded to `price_tick` and clamped to available limit prices when present.

Apply the same maximum of five replacements independently to the real entry chain and real close chain. Never send an unsupported market order merely because the configuration asks for one.

- [x] **Step 8: Add the complete 5-attempt and close-reserve tests**

Advance the injected clock across rolling-window boundaries and assert initial order plus five replacements, unique IDs, strict parent chain, no sixth replacement, and `chase_state="exhausted"` after the final order is canceled or rejected. In every rolling minute, assert at most four entry submissions and at least one remaining rate slot. Add a second case where the fourth entry submission fills and the real close is accepted immediately in the reserved fifth slot.

- [x] **Step 9: Run Task 3 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_security_and_risk.py back_end\tests\test_trading_engine_auto_strategy.py back_end\tests\test_verify_strategy.py back_end\tests\test_trial_run_api.py -q
```

- [x] **Step 10: Commit Task 3**

```powershell
git add back_end/src/trading/order_manager.py back_end/src/trading/engine.py back_end/src/trading/risk.py back_end/src/trading/gateway.py back_end/src/trading/vnpy_gateway.py back_end/src/strategy/strategies/verify.py back_end/src/api/models.py back_end/src/api/trial_run.py back_end/tests/test_security_and_risk.py back_end/tests/test_trading_engine_auto_strategy.py back_end/tests/test_verify_strategy.py back_end/tests/test_trial_run_api.py
git commit -m "feat(trial-run): drive bounded chase from timer"
```

---

## Task 4: Replace Gateway Mutation with an Isolated Simulation Ledger

**Files:**
- Create: `back_end/src/trading/execution_adapter.py`
- Modify: `back_end/src/trading/simulated_fill.py`
- Modify: `back_end/src/trading/engine.py`
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/src/api/models.py`
- Modify: `back_end/src/api/trial_run.py`
- Modify: `back_end/config/config.example.json`
- Modify: `back_end/config/config_production.json`
- Replace: `back_end/tests/test_simulated_fill.py`
- Test: `back_end/tests/test_trial_run_api.py`

- [x] **Step 1: Write the gateway-immutability test**

Snapshot `gateway.orders` and `gateway.positions`, run a simulated entry fill, then assert both snapshots are unchanged while the simulation ledger holds one synthetic trade and one long position.

```python
before_orders = copy.deepcopy(gateway.orders)
before_positions = copy.deepcopy(gateway.positions)
result = ledger.fill(current_simulated_order_id)
assert gateway.orders == before_orders
assert gateway.positions == before_positions
assert result.trade.trade_id.startswith("SIM-")
assert ledger.position_volume("rb2610") == 1
```

- [x] **Step 2: Define the signal-execution adapter contract**

Create:

```python
class SignalExecutionAdapter(Protocol):
    def submit(self, signal: Signal) -> str:
        raise NotImplementedError

    def cancel(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_order(self, order_id: str) -> Optional[Order]:
        raise NotImplementedError
```

The default engine behavior remains the gateway/order-manager path. A `TrialRunSimulationAdapter` stores synthetic orders, trades, and positions only in `TrialRunSimulationLedger`.

- [x] **Step 3: Add the simulation preparation API**

Add `POST /trial-run/simulation/prepare` with `{ "source_order_id": "R1" }`.

Behavior:

1. Require authenticated test/simulation environment.
2. Require the source ID to be the current trial order.
3. Require `simulation_prepare_allowed=true`: the real order has a broker-accepted callback, has no fill, and has remained unfilled for at least 2 seconds.
4. If active, set `simulation_requested=true`, request cancellation, and return `simulation_state="cancel_pending"`.
5. Require a broker callback showing `CANCELLED`.
6. Query current broker orders and positions.
7. Enter `simulation_state="ready"` only when no target-symbol active order exists and real position is 0.

The endpoint is idempotent. While the frontend is in `cancel_pending`, it calls the same endpoint once per second with the same source order ID. The first call sends at most one cancel request; later calls never repeat the cancel. If automatic chasing had already requested cancellation, setting `simulation_requested` transfers ownership of that cancellation to simulation preparation; the cancellation callback must not create a replacement. Once the broker callback is `CANCELLED`, the next call performs reconciliation and either returns `ready` or a stable failure code. `GET /trial-run/status` remains read-only.

On the successful transition to `ready`, create exactly one synthetic entry order in the ledger by copying symbol, direction, offset, price, and volume from the canceled source order. Give it a `SIM-E-` ID, mark it as the current order, and return that ID in `current_order_id`. Retrying the prepare call must return the same synthetic order ID.

Set the checked-in trial configuration explicitly:

- `trial_run.no_fill_timeout_seconds = 2` in both config files;
- `trial_run.simulate_fill_enabled = true` in the current `config_production.json` only because both its trial and trading environment values are “仿真”;
- retain the dual config/runtime non-production checks from Task 1, so changing either environment to production disables both preparation and fill without a restart-time loophole.

- [x] **Step 4: Switch execution only after reconciliation**

When simulation becomes ready:

- bind `VerifyStrategy` position source to `ledger.positions`;
- set the engine's strategy execution adapter to `TrialRunSimulationAdapter`;
- retain the real gateway for market data only;
- record the adapter switch in the audit log.

Add `VerifyStrategy.prepare_simulated_entry(source_order_id)` so a cancellation performed specifically for simulation preparation does not enter the generic rejected/canceled error branch. The method must accept only the current entry order, preserve the one-shot entry invariant, and set `trial_state="simulation_entry_ready"`.

Any simulation-track failure immediately disables the simulation adapter and restores the gateway position source, while preserving the run evidence. Reset, stop, and logout may clear the run only when broker reconciliation is flat; otherwise Task 5's flatten guard takes precedence.

- [x] **Step 5: Reimplement `/trial-run/simulate-fill`**

The request body is exactly `{ "order_id": "SIM-E-1" }`; configure the Pydantic request model with `extra="forbid"`. The operator cannot override volume, direction, fee, margin, or fill price, and extra fields return `422`. The endpoint accepts only the current synthetic order ID, maps Task 2 ownership errors to `409`, uses that order's stored limit price, updates the isolated ledger, then dispatches the synthetic trade through the same strategy callback order used by the engine. It must not call `gateway.on_order`, `gateway.on_trade`, or `gateway.on_position`.

- [x] **Step 6: Handle a late real fill conflict**

If a real CTP trade for the canceled source order arrives after the adapter switch, set:

```text
outcome=failed
failure_code=late_real_fill_conflict
```

Disable further simulated fills and require manual reconciliation.

- [x] **Step 7: Run Task 4 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_simulated_fill.py back_end\tests\test_trial_run_api.py back_end\tests\test_trading_engine_auto_strategy.py -q
```

Expected: all tests pass; at least one test explicitly proves real gateway dictionaries are unchanged.

- [x] **Step 8: Commit Task 4**

```powershell
git add back_end/src/trading/execution_adapter.py back_end/src/trading/simulated_fill.py back_end/src/trading/engine.py back_end/src/strategy/strategies/verify.py back_end/src/api/models.py back_end/src/api/trial_run.py back_end/config/config.example.json back_end/config/config_production.json back_end/tests/test_simulated_fill.py back_end/tests/test_trial_run_api.py back_end/tests/test_trading_engine_auto_strategy.py
git commit -m "feat(trial-run): isolate simulated execution ledger"
```

---

## Task 5: Complete Both Real and Simulated Close Loops

**Files:**
- Modify: `back_end/src/trading/trial_run_execution.py`
- Create: `back_end/src/trading/trial_run_store.py`
- Modify: `back_end/src/trading/simulated_fill.py`
- Modify: `back_end/src/trading/engine.py`
- Modify: `back_end/src/strategy/strategies/verify.py`
- Modify: `back_end/src/api/__init__.py`
- Modify: `back_end/src/api/trial_run.py`
- Modify: `back_end/config/config.example.json`
- Modify: `back_end/config/config_production.json`
- Modify: `.gitignore`
- Create: `back_end/tests/test_trial_run_closed_loop.py`
- Create: `back_end/tests/test_trial_run_store.py`

- [x] **Step 1: Write the simulated closed-loop integration test**

The test must perform this exact API/domain sequence:

```text
prepare -> valid real tick -> real entry submitted -> real entry canceled
-> simulation prepared -> simulated entry filled -> holding
-> required real bars arrive -> synthetic close order created
-> simulated close filled -> simulated position 0 -> passed_simulated
```

Assert real gateway position remains 0 throughout.

- [x] **Step 2: Route close signals into the active adapter**

After simulated entry fill, future real market bars continue to drive the same `VerifyStrategy`. When `hold_bars` is reached, the generated close signal goes to `TrialRunSimulationAdapter`, creating a `SIM-` close order. The frontend then manually fills that exact order.

- [x] **Step 3: Cover the real-fill branch**

Set `strategy.hold_bars=1` in both checked-in trial configurations. Add an integration test where a real entry trade arrives from the gateway, the first valid post-fill Bar creates a real close order, and the real close trade returns the gateway position to 0. The evaluator may return `passed_real` only after broker position is 0, no active order remains, and reconciliation succeeds. A real close order uses the independent five-replacement close chain defined in Task 3.

- [x] **Step 4: Add a hard real-position holding guard**

Add `trial_run.max_hold_seconds=75` to both checked-in configurations. On a real entry fill, store a monotonic holding deadline and expose its wall-clock equivalent as `hold_deadline_at`.

- The first valid post-fill Bar normally creates the close order.
- If no Bar arrives by 75 seconds but a fresh target tick is available, the timer creates the close order from that tick using the same price-tick and limit-price rules.
- If market data is stale or absent at the deadline, set `failure_code="flatten_required_market_data"`, keep the real position visible, and require operator action; never manufacture a price.
- `/trial-run/stop`, `/trial-run/reset`, and `/auth/logout` return `409 failure_code="flatten_required"` while broker position is non-zero. They must not discard the run, order chain, report evidence, or disconnect the CTP session needed to flatten.
- Global emergency stop may cancel active orders, but it does not prove flatness. After the operator resumes trading and uses the existing quick-close action, reconciliation may mark the run flat; its outcome remains `failed` with `success_basis="manual_flatten_after_trial_failure"`, not `passed_real`.

Test the normal Bar close, the 75-second fresh-tick fallback, stale-market blocking, reset rejection while holding, and manual flatten reconciliation.

- [x] **Step 5: Cover terminal failures**

Add tests for:

- real close rejected;
- simulated close order ID mismatch;
- real position non-zero at simulated completion;
- emergency stop during holding;
- backend restart marker causing `aborted` rather than auto-resume.

- [x] **Step 6: Persist active-run checkpoints and abort on restart**

Create `TrialRunCheckpointStore` with a configurable SQLite path:

```python
class TrialRunCheckpointStore:
    def save(self, state: TrialRunExecutionState) -> None:
        raise NotImplementedError

    def load_latest(self) -> Optional[TrialRunExecutionState]:
        raise NotImplementedError

    def mark_aborted(self, run_id: str, reason: str) -> None:
        raise NotImplementedError
```

Use `QUANT_TRIAL_RUN_STATE_DB` when set; otherwise use `back_end/data/runtime/trial_run_state.db`. Persist after every order, trade, track switch, failure, and terminal outcome. On application startup, load the latest run; if it is non-terminal, mark it `aborted` with `failure_code="backend_restarted"`. A new run remains blocked until broker reconciliation confirms no active target order and zero position.

Tests must inject a `tmp_path` database, construct a second store instance, and prove a non-terminal run becomes aborted while a terminal run remains unchanged.

- [x] **Step 7: Run Task 5 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_closed_loop.py back_end\tests\test_trial_run_store.py back_end\tests\test_trial_run_api.py back_end\tests\test_verify_strategy.py -q
```

- [x] **Step 8: Commit Task 5**

```powershell
git add back_end/src/trading/trial_run_execution.py back_end/src/trading/trial_run_store.py back_end/src/trading/simulated_fill.py back_end/src/trading/engine.py back_end/src/strategy/strategies/verify.py back_end/src/api/__init__.py back_end/src/api/trial_run.py back_end/config/config.example.json back_end/config/config_production.json back_end/tests/test_trial_run_closed_loop.py back_end/tests/test_trial_run_store.py back_end/tests/test_trial_run_api.py back_end/tests/test_verify_strategy.py .gitignore
git commit -m "feat(trial-run): complete real and simulated close loops"
```

---

## Task 6: Build the Operator-Facing Dual-Track UI

**Files:**
- Modify: `front_end/src/api/index.js`
- Modify: `front_end/src/views/TrialRunView.vue`
- Modify: `front_end/tests/unit/trialRunApi.spec.js`
- Modify: `front_end/tests/e2e/smoke.spec.js`

- [x] **Step 1: Add API helper tests**

Cover:

- `prepareTrialRunSimulation({ source_order_id })`;
- `simulateTrialRunFill({ order_id })`;
- DOCX Blob download;
- `409` reason-code display;
- no redirect loop when polling reports an interrupted run.

- [x] **Step 2: Replace first-active-order selection**

Delete the computed behavior that scans global `orders`. The simulation action uses only `trialStatus.current_order_id` and confirms that the current order belongs to the displayed order-chain row.

- [x] **Step 3: Add an order-chain table**

Columns:

- 次序（首单、追价 1..5）
- 委托号
- 来源（真实/模拟）
- 合约
- 方向（多/空）
- 开平（开仓/平仓）
- 委托价格
- 数量/已成
- 委托时间
- 状态
- 撤单或失败原因

The current order row must be visually identifiable without relying on color alone.

- [x] **Step 4: Implement safe simulation controls**

Button sequence:

1. `撤单并准备模拟验证`
2. `等待券商撤单确认` (disabled; once per second repeat the idempotent prepare request until `ready` or terminal failure)
3. `模拟开仓成交`
4. `等待模拟平仓委托`
5. `模拟平仓成交`

Before the 2-second no-fill threshold, show the remaining time and keep the first button disabled. Enable it only from backend `simulation_prepare_allowed`; do not infer eligibility from the browser clock. Each confirmation dialog shows order ID, symbol, 多/空, 开/平, price, volume, and states that this is not a broker fill.

- [x] **Step 5: Display the two success outcomes**

- `passed_real`: “真实成交闭环通过”
- `passed_simulated`: “真实报单链路通过；成交后处理由模拟成交验证”

Do not use a single generic “测试通过” label.

Show `导出测试报告` only for terminal outcomes. For `running`, keep it disabled with no download request. `failed` and `aborted` remain exportable so the operator can retain failure evidence.

Display `rate_limit_remaining`, the next allowed chase time, broker position, and holding deadline. When `failure_code` is `flatten_required` or `flatten_required_market_data`, show a persistent “真实持仓尚未归零” error band, disable every simulated-fill action, and keep the existing quick-close action prominent until reconciliation reports zero.

- [x] **Step 6: Make E2E fail on backend errors**

Use `page.route('/api/**', route => route.fulfill(fixtureFor(route.request())))` to return deterministic fixtures for disconnected, real-order pending, waiting-rate-capacity, chasing, simulation-ready, simulated-holding, flatten-required, and passed-simulated states. Capture `page.on('console')` and fail on unexpected API errors. The smoke test must no longer pass with `/trial-run/status` returning 502.

- [x] **Step 7: Run Task 6 gate**

```powershell
cd front_end
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
npm.cmd run e2e
```

Expected: zero errors; existing lint warnings may be logged separately but no new warning may come from `TrialRunView.vue` or `src/api/index.js`.

- [x] **Step 8: Commit Task 6**

```powershell
git add front_end/src/api/index.js front_end/src/views/TrialRunView.vue front_end/tests/unit/trialRunApi.spec.js front_end/tests/e2e/smoke.spec.js
git commit -m "feat(trial-run): show dual-track verification workflow"
```

---

## Task 7: Generate the DOCX Acceptance Report

**Files:**
- Create: `back_end/src/api/trial_run_report.py`
- Modify: `back_end/src/api/trial_run.py`
- Create: `back_end/tests/test_trial_run_report.py`
- Modify: `front_end/src/api/index.js`
- Modify: `front_end/src/views/TrialRunView.vue`

- [x] **Step 1: Write the failing report test**

After a `passed_simulated` test run, call `GET /trial-run/report.docx` and assert:

- HTTP 200;
- DOCX MIME type;
- filename contains `trial-run-report`;
- document paragraphs include “真实报单链路通过” and “模拟成交”；
- document does not state “真实成交通过”.

Add the complementary `passed_real` assertion.

- [x] **Step 2: Implement report generation**

`trial_run_report.py` must generate these sections:

1. 测试基本信息：run ID、时间、账号掩码、环境、固定合约。
2. 前置检查结果。
3. 行情证据：最近 tick、行情年龄、首 tick Bar。
4. 真实订单链：首单及最多 5 次追价。
5. 真实成交或撤单证据。
6. 模拟订单/成交/持仓证据（仅模拟轨道）。
7. 券商持仓和活动委托对账。
8. 风控、滚动限频余量、真实持仓截止时间、异常、手工平仓和急停记录。
9. 最终结论与 `success_basis`。

手续费和保证金在模拟轨道显示“未计入（0）”，不提供用户输入字段。

- [x] **Step 3: Reject reports without a terminal conclusion**

For `running`, return `409` with `detail="trial run has no terminal acceptance result"`. `failed` and `aborted` may export failure reports.

- [x] **Step 4: Run Task 7 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests\test_trial_run_report.py back_end\tests\test_trial_run_closed_loop.py -q
```

- [x] **Step 5: Commit Task 7**

```powershell
git add back_end/src/api/trial_run_report.py back_end/src/api/trial_run.py back_end/tests/test_trial_run_report.py front_end/src/api/index.js front_end/src/views/TrialRunView.vue
git commit -m "feat(trial-run): export dual-track DOCX report"
```

---

## Task 8: Full Regression and Manual CTP Acceptance

**Files:**
- Create: `docs/live-test-checklist.md`
- Create: `docs/live-test-report-template.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/plans/2026-08-10-trial-run-business-closure.md` (checkboxes only)

- [x] **Step 1: Run full backend quality gate**

```powershell
.\.venv\Scripts\python.exe -m pytest back_end\tests -q
.\.venv\Scripts\ruff.exe check back_end\src back_end\tests
```

First extend the mypy command in `.github/workflows/ci.yml` to include all newly created trial-run modules. Then run that exact command from `back_end`:

```powershell
cd back_end
..\.venv\Scripts\python.exe -m mypy src\api\security.py src\api\trial_run.py src\api\trial_run_report.py src\trading\risk.py src\trading\execution_adapter.py src\trading\trial_run_execution.py src\trading\trial_run_store.py src\trading\simulated_fill.py src\data\cache.py src\data\governance.py src\observability.py
```

- [x] **Step 2: Run full frontend quality gate**

```powershell
cd front_end
npm.cmd ci
npm.cmd run quality
npm.cmd run e2e
```

- [ ] **Step 3: Verify test-environment real submission**

During an active trading session:

1. Log in to the CTP test environment.
2. Confirm the fixed configured contract is currently tradable.
3. Confirm the rate panel shows at least 2 remaining submissions, then start trial-run.
4. Within 5 seconds of a valid tick, verify one real entry order has a broker order ID.
5. Verify direction is “多”, offset is “开仓”, volume is 1, and time is complete.
6. If it fills, verify the first post-fill Bar or 75-second watchdog creates the real close order and the broker position eventually returns to 0.
7. If it does not fill, observe timer-driven chase and confirm no replacement precedes cancellation confirmation.
8. Confirm no rolling minute contains more than 5 submissions, at least one close slot is reserved, and neither entry nor close has more than 5 replacements.

- [ ] **Step 4: Verify the no-counterparty simulated branch**

1. After the current real order has remained unfilled for 2 seconds, request simulation preparation without waiting for all chase attempts.
2. Confirm that current real order is canceled at the broker and no chase replacement appears after the request.
3. Confirm broker position is 0 and no target active order exists.
4. Click simulated entry fill; verify simulated position becomes 1 while broker position stays 0.
5. Wait for the configured hold bars and verify a synthetic close order appears.
6. Click simulated close fill; verify simulated position returns to 0.
7. Confirm outcome is `passed_simulated`.
8. Export DOCX and verify the conclusion explicitly says the fill was simulated.

- [ ] **Step 5: Verify failure and recovery paths**

Run at least these manual cases:

- no tick for 15 seconds;
- stale quote during chase;
- cancel failure;
- insufficient rolling rate capacity before prepare;
- risk rejection;
- page refresh during `cancel_pending`;
- emergency stop during simulated holding;
- no post-fill Bar for 75 seconds with a fresh tick, proving watchdog close submission;
- stale market data at the real holding deadline, proving stop/reset/logout remain blocked until manual flatten reconciliation;
- backend restart followed by `aborted` and reconciliation.

- [x] **Step 6: Inspect the final worktree**

```powershell
git status --short
git diff --check
```

Acceptance requires no generated database, generated report, newly introduced credential value, `dist`, `.pnpm-store`, or cache file to be staged.

- [x] **Step 7: Final review and commit**

Run a main-thread code review focused on order ownership, late-fill races, adapter cleanup, and report truthfulness. Fix all P0/P1 findings, rerun the affected gates, then commit documentation:

```powershell
git add .github/workflows/ci.yml docs/live-test-checklist.md docs/live-test-report-template.md docs/superpowers/plans/2026-08-10-trial-run-business-closure.md
git commit -m "chore(trial-run): add acceptance gates and procedure"
```

---

## 4. Completion Audit

The objective is complete only when every row has authoritative evidence:

| Requirement | Required evidence |
|---|---|
| Real CTP order submitted | Broker order ID and accepted/submitted callback in order chain |
| No-tick diagnosis | API/UI test plus manual 15-second case |
| Five bounded chase attempts | Monotonic-timer unit test and manual order chain; no sixth replacement |
| Five-per-minute safety and close reserve | Injected-clock risk test, rate-capacity UI, and manual rolling-window evidence |
| Cancel-before-replace | Callback-order integration test |
| No blind market order | Gateway capability test and fallback-policy test |
| Correct order selected for simulation | Wrong-order API rejection tests and UI fixture test |
| Two-second simulation eligibility | Backend monotonic-timer test and manual no-fill case |
| Runtime environment is authoritative | Config/runtime mismatch tests and manual environment display |
| Real gateway unchanged by simulation | Explicit before/after dictionary assertions |
| Simulated entry and close | Closed-loop integration test with simulated position 1 -> 0 |
| Broker remains flat in simulated branch | Reconciliation assertions before and after simulation |
| Real-fill branch | Real callback integration test with broker position returning to 0 |
| Real-position holding guard | Bar-close and 75-second watchdog tests; reset/logout rejection until flat |
| Page refresh | E2E fixture restoring current status from API |
| Backend restart | `aborted` test plus required reconciliation |
| DOCX truthfulness | Parsed DOCX tests for both `passed_real` and `passed_simulated` |
| Full regression | Backend, lint, mypy, frontend quality, and Playwright all exit 0 |
| Manual CTP evidence | Completed checklist with timestamps, order IDs, screenshots/log references, and exported report |

Passing unit tests alone does not complete this plan. The final manual CTP evidence must prove real broker submission and either the real or isolated simulated close loop.

---

## 5. Superseded Behavior

This plan supersedes the unsafe simulated-fill behavior described in `docs/superpowers/plans/2026-06-03-trial-run-fill-verification.md` Tasks 2 and 3. Specifically, implementation must remove behavior that:

- marks a real gateway order as locally filled without broker cancellation;
- writes simulated positions into `gateway.positions`;
- selects the first active global order by symbol;
- treats a simulated trade as proof of a real broker fill;
- drives chase timeout only when a new tick arrives.

The older plan remains useful as history, but this document is the source of truth for the remaining business closure work.
