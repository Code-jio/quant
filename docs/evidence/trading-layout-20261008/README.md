# 交易台布局验证证据

日期：2026-10-08，Asia/Shanghai。对应 [2K 单屏交易台改造](../../TRADING_LAYOUT_2026-10-08.md)。截图使用隔离测试账户和具名样本，行情、余额和委托均不是实盘数据。

| 文件 | 结果及范围 |
|---|---|
| frontend-quality.txt | ESLint、vue-tsc、26 项 Vitest、Vite 构建通过 |
| bundle-budget.txt | ECharts、Element Plus 均在既有预算内 |
| browser-e2e.txt | 全部 16 项通过；新增 5 项布局及交互验收 |
| backend-tests.txt | 原生 Python 3.13 环境完整 214 项通过 |
| backend-ruff.txt / backend-mypy.txt | Ruff 通过；mypy 纳入的 39 个文件通过 |
| backend-first.txt | 首次完整后端运行：WebSocket 测试上下文关闭时出现一次 CancelledError；原始失败证据保留 |
| backend-focused.txt | 随后复核该测试所在模块，16 项全部通过；再运行完整套件得到 backend-tests.txt |
| runtime-summary.json | 匿名只读健康检查：后端 connected，1 个会话；8000 仍为原进程 PID 33720 |
| trading-desk-2560.png / 1920 / 1366 | 最终桌面宽度截图；无整页溢出，行情、委托及订单簿保持首屏可见 |
| trading-desk-768.png / 390 | 最终平板及窄屏截图；主要控件可滚动到达，无整页横向溢出 |
| trading-orders-scroll.png | 60 条具名委托的内部滚动，右侧交易区保持可见 |
| trading-strategy-error.png | 策略接口失败提示，不自动填充模拟策略 |

2K 截图中的五档行情来自 Playwright `routeWebSocket` 具名测试消息，历史来源明确为 `e2e_fixture`。其他截图允许报价未知。测试网关不支持保证金查询的提示不代表实盘柜台查询能力发生变化。

本轮不把首次未复现的取消异常宣称为已修复。最终后端仍有 Starlette/httpx 弃用和旧 pytest 缓存目录权限提示；前端仍有大包提示，未隐去告警。测试只打开并取消下单、全部撤单及急停确认，拦截并断言未发送任何交易写请求。

## 执行环境与复现

- 后端：`back_end/.venv-live/Scripts/python.exe`；清除外部 `PYTHONPATH`，设置 `QUANT_ENV=test`、`PYTHONUTF8=1`；在 `back_end` 运行 `python -m pytest`、`python -m ruff check main.py server.py src tests`、`python -m mypy`。模块复核为 `tests/test_feature_inventory.py`。测试使用 FakeGateway/具名夹具，不向柜台报单。
- 前端：Node 24，运行 `npm run quality` 及 `node ../scripts/check_bundle.mjs`。
- 浏览器：`PLAYWRIGHT_PORT=55174`、`PYTHONUTF8=1`，清除外部 `PYTHONPATH`。执行 `node node_modules/@playwright/test/cli.js test --config D:/mine/quant-native-verify-20261008/execution-e2e/playwright.config.mjs`。使用 [隔离 Playwright 配置](../live-execution-20261008/e2e-playwright.config.mjs)、[Vite 代理配置](../live-execution-20261008/e2e-vite.config.mjs)、[后端包装](../live-execution-20261008/e2e-backend-wrapper.py) 的本机执行副本，后端端口 18000、前端 55174。配置包含本机绝对路径，移机需修改；不占用已有实盘服务的 8000 端口。

保留实盘登录会话，未重启后端，未执行真实下单、撤单或策略启停。本轮没有修改后端生产代码。截图及断言验证页面布局和交互，不构成真实 Tick、成交或撤单回报验收。

`SHA256SUMS` 覆盖本目录除自身外的所有文件。日志仅规范换行及行尾空白；文本固定 LF，提交前校验暂存区字节哈希。
