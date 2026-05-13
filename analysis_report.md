# 量化交易系统全局分析报告

**日期**: 2026-05-08（更新于 2026-05-13）
**范围**: `back_end/` (Python) + `front_end/` (Vue 3)

---

## 已解决问题（自上次报告以来）

| 原编号 | 问题 | 解决方案 |
|--------|------|----------|
| 1.1 | 回测前瞻偏差 | 引擎已实现 next-bar-open 执行 + signal.price 覆盖 |
| 1.2 | 仓位计算忽略合约乘数 | engine.set_strategy() 传递 contract_multiplier；全系统默认值统一为 10 |
| 4.1 | 持仓状态双轨制 | update_position() 简化为纯记账；get_position() 委托 engine/gateway |
| 5.1 | 策略每 bar 重算指标 O(n²) | on_start() 预计算全部指标，on_bar() 仅 O(1) iloc[] 读取 |
| 5.2 | 数据库连接不复用 | _get_conn() 持久连接 + WAL 模式 |
| 6.1 | RSI/MACD 多实现不一致 | 统一收敛到 data/indicators.py |
| 2.1 | 配置示例 market_data_age 为 0 | config.example.json 已改为 5 |
| 2.3 | 风控默认值隐式退化 | API lifespan 加入 warn_production_risk_defaults() |
| 2.2 | /system/logs 无需鉴权 | 移出 OPEN_PATHS，同时移出 /watch/tick |
| 4.2 | 信号可靠性 | pending_signals 处理加 per-signal try/except |
| 3.1 | OHLC 生成不合理 | max(h,o,c)+epsilon 和 min(l,o,c)-epsilon 保证 OHLC 约束 |
| 2.1 | CTP 网关无测试 | 新增 test_vnpy_gateway.py (45 tests) |
| — | RecordingGateway 重复 | 提取到 tests/helpers.py |

---

## 二、风控与安全（剩余）

### 2.4 前端路由守卫仅依赖 sessionStorage — 低

**位置**: `front_end/src/router/index.js`

客户端可任意修改 sessionStorage，路由守卫仅起 UX 作用。真正的鉴权依赖服务端 cookie/header 中间件，问题不大但前端代码暗示了错误的安全假设。

---

## 三、数据完整性（剩余）

### 3.2 数据库 datetime 存储为字符串 — 低

**位置**: `back_end/src/data/db.py:51`

datetime 存为 TEXT 而非 SQLite 原生时间类型。当前用 ISO format 存所以风险可控，但建议强制固定格式（YYYY-MM-DD HH:MM:SS）。

### 3.3 缓存 key 直接拼接用户输入 — 低

**位置**: `back_end/src/data/manager.py:34`

`cache_key = f"{symbol}_{timeframe}_{start_date}_{end_date}"` 若包含特殊字符可能产生 key 冲突。实际影响小。

---

## 四、架构与设计（剩余）

### 4.3 API 模块巨型单文件 — 中等

**位置**: `back_end/src/api/__init__.py` (~2600 行)

单个文件包含 FastAPI 工厂、路由定义、Pydantic 模型、连接管理器、广播循环、日志缓冲等。建议拆分为 auth/trading/watch/websocket/models/backtest 模块。

### 4.4 CLAUDE.md 与实际项目结构不符 — 低

根目录 CLAUDE.md 描述的是旧版结构。

---

## 五、性能优化点（新发现）

### 5.3 `_append_live_bar` 使用 `pd.concat` — 内存泄漏（P2）

**位置**: `back_end/src/trading/engine.py:230-245`

每次 tick 执行 `pd.concat([existing, bar_frame])`，DataFrame 无限增长。运行数天后可能 OOM。建议用 deque 限制长度。

### 5.4 WebSocket 广播循环空转 — 低

**位置**: `back_end/src/api/__init__.py`

system/dashboard/positions 广播任务每秒执行，虽有无客户端时提前退出但不防止 `_build_dashboard_metrics()` 中的 pandas 计算开销。

---

## 六、代码质量与可维护性（剩余）

### 6.2 错误处理策略不一致

| 位置 | 失败行为 |
|------|----------|
| `DataManager.get_bars()` | 返回空 DataFrame |
| `DataManager.save_bars()` | 返回 False |
| `DatabaseManager.load_bars()` | 抛出 DatabaseError |
| `TradingEngine.send_signal()` | 返回空字符串 |
| `BacktestEngine.run()` | 抛出 BacktestError |

调用方需要记住每种失败模式，容易遗漏处理。

### 6.3 `__init__.py` 暴露过度

`src/api/__init__.py` 在模块顶层创建 `app = create_app()` 实例，导致 `import src.api` 即启动 FastAPI 应用对象，不便于单独测试。

---

## 七、测试覆盖缺口（部分已补）

- ✅ **vnpy_gateway 测试**: test_vnpy_gateway.py (45 tests) — 已补
- ❌ **K 线模块测试**: watch/kline.py 无自动化覆盖
- ❌ **前端组件/单元测试**: vitest 已配置但无 .test.js 文件
- ❌ **API 集成测试**: 无 httpx-based 端到端测试
- ❌ **回测端到端回归测试**: 无黄金标准测试

---

## 八、上线前检查清单（更新）

- [x] ~~P0 合约乘数~~ — 已统一为 10
- [x] ~~P0 前瞻偏差~~ — next-bar-open 正确
- [x] ~~P1 持仓双轨制~~ — 单一真实源
- [x] ~~P1 指标预计算~~ — on_start() 模式
- [x] ~~P1 数据库连接~~ — _get_conn() 复用
- [x] ~~P2 风控告警~~ — lifespan 启动日志
- [x] ~~P2 指标统一~~ — indicators.py
- [x] ~~P2 日志鉴权~~ — 移出 OPEN_PATHS
- [x] ~~P2 信号可靠性~~ — per-signal try/except
- [x] ~~P2 CTP 网关测试~~ — 45 tests
- [x] P2 Session 持久化（SQLite write-through，重启不掉线）
- [x] P2 优雅关闭（lifespan: 撤单→停策略→断网关）
- [x] P2 `_append_live_bar` 内存泄漏（tail 1000 条上限）
- [x] P2 数据库路径（env QUANT_DB_PATH + 绝对路径回退）
- [x] P2 Rate limiting（slowapi: /auth/login 5/min/IP, 交易端点 20/min/session）
- [x] P2 WebSocket 30s 心跳超时断开
- [ ] P2 前端 WebSocket 断线重连
- [ ] P3 API 模块拆分
- [ ] P3 Dockerfile + CI/CD
- [ ] P3 前端组件测试
- [ ] P3 K 线模块测试

---

**上次分析中已验证无问题的项**: 2.1 市场数据时效（config.example.json 已改为 5）、1.3 RSI 交叉计算（已统一）、5.2 连接复用（已实现）、5.1 指标预计算（on_start 模式）、4.1 持仓双轨制（已修复）
