# 试运行操作台设计

**日期**: 2026-05-18  
**状态**: 设计已在会话中认可，待按本文评审后进入实施计划  
**范围**: `back_end/` FastAPI + 交易引擎安全闸门，`front_end/` Vue 操作台页面  

## 目标

第一版目标是从前端完成一个轻量但安全的国内期货仿真交易闭环：

`前端登录 CTP -> 读取单合约白名单 -> 准备 VerifyStrategy -> 预热 1 分钟 Bar -> 用户授权交易 -> 策略开 1 手 -> 风控检查 -> 委托/成交/持仓更新 -> 策略自动平仓 -> 前端确认闭环完成`

这不是临时开发 Demo，而是长期保留的“试运行 / 上线验收操作台”。后续可用于 CTP 连通性验收、新策略小手数试运行、盘前检查、发布后回归、异常排查。

## 已确认决策

- 前端操作台是第一版主入口，不以 CLI 为主入口。
- 新增独立页面“试运行操作台”，现有 Dashboard、Watch、Kline、Backtest、System 页面暂时保留。
- 第一版只支持 `VerifyStrategy`，不做通用多策略平台。
- 只允许本地配置中的唯一白名单合约。
- 不保留手动开仓能力；只允许策略开仓、手动撤单、手动平仓、急停、停止策略。
- 策略采用两段式安全流程：先准备并预热，预热完成后必须由用户点击“授权交易”才允许下单。
- 本地配置保存账号、CTP 前置、环境、白名单合约、策略参数和风控阈值；密码每次在前端输入，不保存、不落日志。

## 非目标

- 不做策略市场、策略参数优化、多策略实例或资金分配。
- 不做真实生产级权限体系；第一版仍默认本机部署、本机访问。
- 不做复杂实盘策略，`VerifyStrategy` 的目的只是验证交易链路。
- 不在第一版改造所有现有页面，只新增试运行主流程并复用已有组件/API。
- 不在 Git 中提交真实账号密码或真实本地运行配置。

## 用户流程

1. 用户打开 `/trial-run`。
2. 页面调用后端读取本地试运行配置，展示账号、环境、CTP 前置、唯一允许合约、风控阈值、策略参数。
3. 用户输入密码并点击连接。前端复用现有 `POST /auth/login`，账号和服务器配置由本地配置预填。
4. 登录成功后页面拉取 `/trading/reconcile`、`/risk/status`、订单、成交、持仓、日志，确认当前账户状态。
5. 用户点击“准备策略”。后端创建并注册 `VerifyStrategy`，订阅白名单合约行情，进入 `warming`。
6. 策略只积累 Bar，不允许发单。预热完成后状态进入 `ready_to_arm`。
7. 用户点击“授权交易”。后端设置策略授权标记，进入 `armed`。
8. 下一个有效 Bar 到来后，`VerifyStrategy` 生成 1 手开仓信号，经风控通过后提交网关。
9. 前端实时展示订单、成交、持仓、策略状态和日志。
10. 持有配置的 `hold_bars` 后，策略自动发出平仓信号，成交后状态进入 `completed`。
11. 任意阶段用户都可以触发急停、撤单、快捷平仓或停止策略。

## 状态机

| 状态 | 含义 | 允许动作 |
| --- | --- | --- |
| `disconnected` | 未连接 CTP | 连接 |
| `connected` | CTP 已连接但未准备策略 | 准备策略、急停 |
| `prepared` | 策略实例已创建，等待行情/启动 | 启动预热、停止策略、急停 |
| `warming` | 正在积累 Bar | 停止策略、急停 |
| `ready_to_arm` | 预热完成，等待用户授权 | 授权交易、停止策略、急停 |
| `armed` | 已授权，下一次策略信号可发单 | 停止策略、急停 |
| `holding` | 开仓成交后持仓中 | 快捷平仓、停止策略、急停 |
| `closing` | 策略或人工正在平仓 | 撤单、急停 |
| `completed` | 开仓和平仓闭环已完成 | 重新准备、断开连接 |
| `emergency_stopped` | 急停生效 | 撤单、快捷平仓、解除急停 |
| `error` | 连接、策略、风控或网关异常 | 停止策略、急停、重新准备 |

急停优先级最高。急停后，策略信号和手动下单都必须被风控拒绝；撤单和平仓保留为风险处置能力。

## 后端设计

### 配置

新增试运行本地配置概念，建议使用被 Git 忽略的 `back_end/config/config.local.json`，同时提交一个无真实凭据的示例配置。现有 `config/config.example.json` 可以扩展，也可以新增 `config/config.trial.example.json`。

配置必须能表达：

```json
{
  "trial_run": {
    "enabled": true,
    "account_id": "0061839732",
    "gateway": "vnpy",
    "vnpy_environment": "仿真",
    "allowed_symbol": "rb2601",
    "manual_open_enabled": false
  },
  "strategy": {
    "name": "verify",
    "symbol": "rb2601",
    "warmup_bars": 20,
    "hold_bars": 10,
    "volume": 1,
    "contract_multiplier": 10
  },
  "trading": {
    "broker_id": "",
    "td_server": "",
    "md_server": "",
    "app_id": "",
    "auth_code": "",
    "bar_interval_minutes": 1,
    "initial_capital": 100000,
    "max_errors": 10
  },
  "risk": {
    "enabled": true,
    "allowed_symbols": ["rb2601"],
    "max_order_volume": 1,
    "max_position_volume": 1,
    "max_active_orders": 2,
    "max_orders_per_minute": 5,
    "max_daily_loss_ratio": 0.01,
    "max_market_data_age_seconds": 10,
    "duplicate_signal_window_seconds": 5,
    "allow_market_orders": false
  }
}
```

实施时不硬编码示例合约。运行前由操作员把 `allowed_symbol`、`strategy.symbol` 和 `risk.allowed_symbols[0]` 设置为当天确认可交易的同一个真实合约。后端启动或读取配置时必须校验这三个值一致，并校验白名单长度恰好为 1。

### 新增试运行服务

建议新增 `back_end/src/api/trial_run.py`，封装：

- 本地配置读取和校验。
- 试运行状态机。
- `VerifyStrategy` 创建、注册、授权和状态读取。
- 面向前端的脱敏配置响应。

现有 `back_end/src/api/__init__.py` 已经较大，新增逻辑应尽量放入独立模块，再在 `create_app()` 中挂载路由或调用注册函数。

### API 清单

新增 API：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/trial-run/config` | 返回本地试运行配置，含账号、环境、允许合约、策略参数、风控阈值；不返回密码 |
| `GET` | `/trial-run/status` | 返回试运行状态机、Bar 进度、授权状态、策略摘要、最近拒单原因 |
| `POST` | `/trial-run/prepare` | 创建/重置 `VerifyStrategy`，应用风控配置，订阅唯一合约行情，进入预热 |
| `POST` | `/trial-run/arm` | 仅在 `ready_to_arm` 时授权交易，策略才可生成开仓信号 |
| `POST` | `/trial-run/stop` | 停止试运行策略；不强制断开 CTP |
| `POST` | `/trial-run/reset` | 在 `completed`、`error` 或停止后清理试运行状态，允许重新准备 |

复用现有 API：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/auth/login` | CTP 登录，密码只随请求使用 |
| `POST` | `/auth/logout` | 断开连接 |
| `GET` | `/auth/status` | 连接状态 |
| `GET` | `/risk/status` | 风控状态 |
| `POST` | `/risk/emergency-stop` | 急停，可撤单、停策略 |
| `POST` | `/risk/resume` | 解除急停 |
| `GET` | `/trading/reconcile` | 账户、委托、持仓、策略对账 |
| `GET` | `/orders` | 委托列表 |
| `GET` | `/trades` | 成交列表 |
| `DELETE` | `/orders/{order_id}` | 撤单 |
| `POST` | `/orders/cancel-all` | 一键撤单 |
| `POST` | `/positions/{symbol}/close` | 快捷平仓 |
| `GET` | `/system/logs` | 日志历史 |
| `WS` | `/ws/orders`、`/ws/positions`、`/ws/logs`、`/ws/watch` | 实时推送 |

### VerifyStrategy 授权闸门

现有 `VerifyStrategy` 在预热完成后会立即发出开仓信号，需要改为显式授权：

- 新增属性：`trade_authorized`、`trial_state`、`ready_to_arm`、`completed`。
- 新增方法：`authorize_trading()`、`revoke_authorization()`、`snapshot()`。
- `on_bar()` 在预热完成但未授权时只更新状态为 `ready_to_arm`，不生成信号。
- 授权后下一根有效 Bar 才生成 1 手开仓信号。
- 成交回调后根据持仓状态进入 `holding`。
- 到达 `hold_bars` 后生成平仓信号，平仓完成后进入 `completed`。

### 手动开仓禁用

前端不展示手动开仓控件，但后端仍必须防绕过：

- 当 `trial_run.manual_open_enabled=false` 时，`POST /orders` 中 `offset=open` 必须直接拒绝。
- 拒绝需要写入审计日志，原因明确为“试运行模式禁止手动开仓”。
- 手动平仓、撤单、急停不受该限制。
- 策略开仓仍通过 `TradingEngine.send_signal()` 和 `RiskManager.check_signal()`，由白名单、手数、持仓、限频、行情新鲜度等风控约束。

### 风控标准

第一版试运行配置必须满足：

- `risk.allowed_symbols` 恰好 1 个，并且等于 `strategy.symbol`。
- `risk.max_order_volume = 1`。
- `risk.max_position_volume = 1`。
- `risk.max_orders_per_minute <= 5`。
- `risk.max_active_orders <= 2`。
- `risk.max_market_data_age_seconds > 0`。
- `strategy.volume = 1`。
- 急停开启后策略信号和手动开仓/平仓请求按现有风控拒绝；撤单保留。

## 前端设计

### 路由

新增路由：

- 文件：`front_end/src/router/index.js`
- 路径：`/trial-run`
- 页面：`front_end/src/views/TrialRunView.vue`
- 标题：`试运行操作台`
- 鉴权：页面本身可访问，未登录时只允许连接和查看本地配置；策略和交易动作必须在登录后启用。

### API 客户端

在 `front_end/src/api/index.js` 增加：

- `fetchTrialRunConfig()`
- `fetchTrialRunStatus()`
- `prepareTrialRun()`
- `armTrialRun()`
- `stopTrialRun()`
- `resetTrialRun()`

继续复用已有 `login()`、`logout()`、`fetchRiskStatus()`、`emergencyStop()`、`cancelAllOrders()`、`closePosition()`、`fetchOrders()`、`fetchTrades()`、`fetchPositions()`、`fetchSystemLogs()`。

### 页面结构

`TrialRunView.vue` 建议采用一个工作台页面：

- 顶部状态带：账号、环境、CTP 状态、唯一合约、急停状态。
- 左侧流程区：连接账户、准备策略、预热进度、授权交易、闭环完成。
- 中间监控区：合约最新 tick、Bar 计数、策略状态、当前持仓、浮动盈亏估算。
- 右侧风险区：风控参数、最近拒单原因、对账状态、急停按钮。
- 底部信息区：订单、成交、日志三个 Tab。

按钮规则：

- 未连接：只启用连接。
- 已连接未准备：启用准备策略、急停。
- `warming`：禁用授权交易，显示预热进度。
- `ready_to_arm`：启用授权交易。
- `armed`、`holding`、`closing`：启用急停、撤单、快捷平仓、停止策略。
- `completed`：启用重新准备、断开连接。
- `emergency_stopped`：突出显示急停状态，启用撤单、快捷平仓、解除急停。

## 数据流

1. 前端加载 `/trial-run/config` 和 `/trial-run/status`。
2. 用户输入密码后调用 `/auth/login`。
3. 登录成功后前端连接 WebSocket：`/ws/orders`、`/ws/positions`、`/ws/logs`、`/ws/watch`。
4. 用户点击准备策略，调用 `/trial-run/prepare`。
5. 后端加载配置，校验单合约和风控，创建 `VerifyStrategy`，绑定 `TradingEngine`，订阅行情。
6. tick 经 `BarAggregator` 转成完成 Bar 后调用 `VerifyStrategy.on_bar()`。
7. 策略预热完成但未授权时只更新状态，不生成信号。
8. 用户点击授权交易，调用 `/trial-run/arm`。
9. 策略生成开仓信号，经 `RiskManager` 通过后提交网关。
10. 网关订单和成交回调更新订单、持仓和策略状态，前端实时显示。
11. 策略自动平仓后进入 `completed`，前端显示闭环完成。

## 文件影响范围

后端预计改动：

- `back_end/src/api/trial_run.py`：新增试运行配置、状态机和路由注册。
- `back_end/src/api/__init__.py`：挂载试运行路由；手动开仓禁用检查可在这里或专用服务中接入。
- `back_end/src/api/models.py`：新增试运行请求/响应模型。
- `back_end/src/strategy/strategies/verify.py`：增加授权闸门和状态快照。
- `back_end/src/trading/engine.py`：必要时暴露订阅/状态辅助方法，避免前端试运行服务访问内部细节。
- `back_end/config/config.example.json` 或 `back_end/config/config.trial.example.json`：补充试运行配置示例。
- `.gitignore`：忽略真实本地配置，例如 `back_end/config/config.local.json`。

前端预计改动：

- `front_end/src/views/TrialRunView.vue`：新增页面。
- `front_end/src/router/index.js`：新增 `/trial-run` 路由。
- `front_end/src/api/index.js`：新增试运行 API 客户端方法。
- 可复用现有组件：`OrderBook.vue`、`LogViewer.vue`、`StrategyPanel.vue`、`TradingPanel.vue` 中的安全子能力；若组件职责过宽，第一版可以在新页面内做更窄的展示组件。

测试预计新增/更新：

- `back_end/tests/test_trial_run_api.py`
- `back_end/tests/test_verify_strategy.py`
- `back_end/tests/test_security_and_risk.py`
- `front_end/tests/unit/network.spec.js` 或新增 `trialRunApi.spec.js`
- `front_end/tests/e2e/smoke.spec.js` 增加 `/trial-run` 冒烟覆盖

## 验收标准

功能验收：

- 用户能从 `/trial-run` 看到本地配置中的账号、环境、唯一允许合约、风控阈值和策略参数。
- 用户输入密码后能在前端完成 CTP 登录。
- 登录后能从页面看到连接状态、行情状态、风控状态、订单、成交、持仓、日志。
- 点击准备策略后，策略进入预热；预热完成前不会发单。
- 预热完成后，未点击授权交易不会发单。
- 点击授权交易后，策略只对唯一白名单合约发出 1 手开仓信号。
- 策略持有指定 Bar 数后自动发出平仓信号。
- 开仓、成交、持仓、平仓、完成状态都能在前端看到。

安全验收：

- 非白名单合约请求被拒绝。
- 手动 `offset=open` 请求被拒绝。
- 超过 1 手请求被拒绝。
- 急停后策略信号被拒绝。
- 急停后仍可撤单。
- 快捷平仓只允许已有持仓且不超过当前持仓数量。
- 所有拒绝事件写入审计日志或系统日志，且密码不出现在日志、响应或前端存储中。

验证命令：

```bash
cd back_end
python -m pytest
python -m ruff check src tests
python -m mypy
```

```bash
cd front_end
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
```

真实 CTP 仿真验收需要在交易时段使用本地配置的有效账号和当天确认可交易合约执行。

## 风险与缓解

- 合约过期或不可交易：不再硬编码旧合约；由本地配置维护唯一合约，后端校验配置一致性。
- 前端隐藏手动开仓不等于安全：后端必须拒绝 `offset=open` 的手动请求。
- 授权前策略误发单：`VerifyStrategy` 本身必须实现授权闸门，不能只靠前端按钮状态。
- 急停后处置受阻：急停阻止新交易信号，但撤单必须保留；快捷平仓的策略需要在实施时用测试明确覆盖。
- 现有 API 文件过大：新增试运行逻辑应尽量模块化，避免继续膨胀 `back_end/src/api/__init__.py`。
- WebSocket 登录态：现有 WebSocket 鉴权需要继续复用 session cookie；本地开发若使用 query token，必须保持默认关闭。

## 后续演进

- 将试运行操作台扩展为盘前检查清单。
- 增加“仿真验收报告”导出：连接时间、合约、风控参数、订单、成交、平仓结果、日志摘要。
- 将成熟的订单、持仓、日志、风险组件反哺 Dashboard 和 System 页面。
- 在 `VerifyStrategy` 验收稳定后，再设计真实示例策略上线流程。
