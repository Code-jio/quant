# 全局审查整改实施计划

> **Execution note:** 按用户现有规则在当前会话本地执行，使用 executing-plans。当前分支为 fix/global-review-20261004。用户已授权实施，无需在计划完成后再次请求执行许可。

**Goal:** 对审查 Q01～Q55 建立修复、回归证据和残余外部验收记录，恢复可验证的研究与交易基础。

**Architecture:** 保留模块化单体和单账户执行模式。只保留一套 API 工厂；会话绑定账户代次，交易动作串行通过风险预留；研究与交易连接解耦。成交、数据来源、时间、单位采用明确契约，未知状态禁止新增交易风险。

**Tech Stack:** Python/FastAPI/SQLite/vn.py，Vue/Pinia/ECharts/Vite，pytest/Vitest/Playwright。

## 执行批次

- [x] **1. 入口与账户边界（Q01～Q07、Q45）**：修复 main.py 初始化；app.py 指向唯一工厂，移除重复路由实现；会话代次、原子登录切换、匿名日志隔离、WS 撤销、CORS；增加 tests/test_review_auth.py。验证 `pytest tests/test_review_auth.py tests/test_api_auth.py tests/test_security_and_risk.py`。
- [x] **2. 执行与风控（Q08～Q24、Q55）**：修改 strategy/types.py/base.py、trading/engine.py/risk.py/order_manager.py/vnpy_gateway.py；新增事件时间 bar 聚合、SQLite 执行账本、严格参数与合约元数据；验证重复成交、迟到撤单、风险预留、冻结仓、紧急停止、未知状态、停止/重启。以 tests/test_review_trading.py 与既有交易测试作为门禁。
- [x] **3. 回测与统计（Q25～Q31）**：修改 backtest/engine.py/config.py、analysis、api/backtest_service.py；用下一根可执行 bar 撮合，同步账户，保留订单/成交关联；统一回撤比例、净利润配对、有限 JSON、失败状态、取消/并发上限。新增 tests/test_review_backtest.py，使用手算样本验收。
- [x] **4. 历史与实时行情（Q32～Q37）**：修改 data/db.py/manager.py/governance.py、watch、API；显式模拟数据集、来源保留、结束日期半开区间、before 游标、真实合约、交易时段配置和事件时间聚合；增加 tests/test_review_data.py。未知交易日历不冒充权威日历。
- [x] **5. 前端数据一致性（Q38～Q46）**：修改 api/index.js、KlineChart、useKlineData/useIndicatorWorker/useWatchWs/useOrderBookWs、TradingPanel/LoginView；请求版本与取消、指标纯函数及回退、分页、快照补偿和过期状态；Vitest 覆盖异步乱序及 Worker，Playwright 验证主要交互。
- [x] **6. 工程与运行（Q47～Q54）**：更新依赖锁、启动/配置/文档、CI、持久审计、指标标签、保留窗口和按需依赖；增加导入/备份/恢复工具及参数校验；检查 npm audit、后端依赖审计，明确未验证柜台与原生运行时。
- [x] **7. 全量验收与交付**：完整 pytest、Ruff（包含 main.py）、Mypy、前端 lint/typecheck/unit/build/E2E；更新 55 项 CSV 状态和 docs/REMEDIATION_RESULTS_2026-10-04.md。每项只按已完成代码与实测证据标记，外部验收单独列出。

## 回归契约示例

```python
def test_anonymous_logout_cannot_disconnect(client, connected_gateway):
    response = client.post('/auth/logout')
    assert response.status_code == 401
    assert connected_gateway.connected

def test_drawdown_unit():
    assert metrics_for_equity([100, 90])['max_drawdown_pct'] == 10
```

上述为验收形状，实际测试使用仓库 FakeGateway 与测试 fixture，不连接外部账户。前一轮审查探针已复现根因，本次将其转为以正确结果为断言的正式回归测试，先记录 RED 再实现 GREEN。

## 实施边界

当前非交易时段，不执行真实下单。历史授权码有效性、指定柜台今昨仓/手续费/保证金、真实 MD/TD 重连、合约/交易日历来源需外部数据，代码必须在未确认时明确未知或拒绝新增风险，不能用猜测默认值通过验收。保留原审查报告作为基线，不覆盖其中历史事实。

## 执行结论

各批次的本地实施、验证与登记已完成；不等于 55 项全部关闭。41 项离线关闭、7 项待外部验收、7 项部分完成，详见整改结果。
