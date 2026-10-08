# 第二轮复查修复结果（2026-10-08）

针对 [2026-10-05 复查](D:/mine/quant/docs/RECHECK_2026-10-05.md) 中 R01–R06，已完成代码修复、回归用例和本地 HTTP 联调。**六项具体缺陷均通过离线验证；真实柜台接入未完成验收。** 原 55 项清单中的交易所日历、原生 CTP、完整对账等剩余事项仍按原记录跟进。

## 修复内容

| 项目 | 当前行为 | 主要验证 |
|---|---|---|
| R01 账户权益串用 | 注销/切换清空权益、日内显示基准、连接日志与回报计时；快照、订单和成交广播携带账户代次，旧代次结果被丢弃 | 直接切换及注销重登后不包含 A 的收益；B 的回撤不再变为 90%；旧快照及排队广播被拒绝 |
| R02 Web 保证金配置缺失 | 登录表单按合约接收经核实的百分比并提交比例；前后端拒绝空合约、无效范围和布尔值；不猜测未配置比例 | 浏览器填写 12，实际请求收到 0.12；无效输入不发起登录；API 验证失败保留原账户 |
| R03 策略窗口错位 | MA/RSI 包含本次已完成 bar；突破策略取此前最后 N 根，不再漏最新一根；维持下一根 bar 最早撮合 | 金叉当根发信号；150 未突破 200 时无开多；3 种内置策略在分钟回放与回测中决策时间/方向/价格一致 |
| R04 快照失败漏推送 | 失败时按最后成功 revision 重放缓冲增量，保留 stale，并按 1/2/4 秒最多重试三次；销毁取消重试 | 刷新超时期间的 filled、trade、position 消息仍生效；后续成功刷新恢复新鲜状态；持续失败不会无限重试 |
| R05 RSI 边界和口径错误 | 后端数据层、行情 API、策略及前端 Worker 使用同一 Wilder 定义：首 N 个变化平均值初始化，预热未知、横盘 50、持续上涨 100、持续下跌 0 | 共用 [固定样本](D:/mine/quant/test_fixtures/rsi_cases.json)，覆盖单边、横盘、反转、预热及 period=1 |
| R06 风控无效输入返回 500 | Pydantic 校验在修改引擎前执行；返回 422；设置更新与报单共用执行锁 | 无效数量/比例/布尔/集合/映射/空值均返回 422，当前与持久配置保持不变 |

RSI 的算法定义和信号时点发生了实质修正，旧版本回测结果需要重新计算。指标预热依赖提供的历史窗口；本轮未实现跨交易所完整历史预热和交易时段日历。

## 验证证据

- 后端完整回归 **145 passed**，仅有现存 Starlette/httpx 弃用警告：[backend-full.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/backend-full.txt)。
- Ruff 通过，mypy **31 个源文件**通过（新 RSI 模块已纳入）：[backend-static.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/backend-static.txt)。
- 前端 lint、vue-tsc、**28 项单测**及生产构建通过：[frontend-quality.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/frontend-quality.txt)。保留已有大分块提示，未将其描述为构建失败。
- Playwright/Edge **5 passed**：登录保证金、离线回测、真实 Worker 图表、注销、公开页：[browser-e2e.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/browser-e2e.txt)。登录输入区已检查桌面和 390px 宽度，无横向溢出。
- 新回归先在修改前运行，后端和前端均捕获相应缺陷，修复后通过；保留 [backend-red.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/backend-red.txt) 和 [frontend-red.txt](D:/mine/quant/docs/evidence/recheck-fix-20261008/frontend-red.txt)。
- 运行中的标准 `server.py`、HTTP 文档、认证边界、行情未连接提示、5174 Vite 代理及 CORS 共 9 项断言通过：[local-api-checks.json](D:/mine/quant/docs/evidence/recheck-fix-20261008/local-api-checks.json)。401/503 在该文件中是未登录/未接柜台时的预期响应。

业务回归使用测试网关；实际服务检查是只读 HTTP。没有向真实柜台发送交易指令。

## 本地服务状态

本轮发现 8000 原本未启动，5173 返回另一个 React 应用的 HTML。原 5173 进程保留。本项目前后端已经分别启动：

- 前端：[http://127.0.0.1:5174/login](http://127.0.0.1:5174/login)
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

服务以 development、loopback、单执行者运行，显式关闭模拟数据自动生成。启动进程与日志记录位于本机 `D:/mine/quant-recheck-fix-20261008/`；PID 文件仅供本次运行定位，停止进程前应再次核对端口和进程身份，不能复用旧 PID 盲目终止。

当前 API 使用的 Python 环境缺少 `vnpy` 和 `vnpy_ctp`，账户未登录。因此本轮已验证 HTTP 服务及业务逻辑，尚未验证真实 MD/TD 连接、实时行情和柜台回报。用户说明当前可交易；当前联调限制来自运行依赖与未建立柜台会话，不能归因于休市。现有 `requirements-live.in` 仍只是待原生环境验收的候选组合。

新增测试入口为 [test_recheck_regressions.py](D:/mine/quant/back_end/tests/test_recheck_regressions.py)、[前端订单回归](D:/mine/quant/front_end/tests/unit/review-ws.spec.js)、[RSI 回归](D:/mine/quant/front_end/tests/unit/recheck-indicators.spec.js)、[保证金回归](D:/mine/quant/front_end/tests/unit/recheck-margin.spec.js)、[登录浏览器用例](D:/mine/quant/front_end/tests/e2e/recheck.spec.js)。全部证据文件带 SHA256 清单。
