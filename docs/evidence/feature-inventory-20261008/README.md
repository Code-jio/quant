# 功能盘点测试证据

日期：2026-10-08，Asia/Shanghai。对应 [50 项功能矩阵](../../FEATURE_INVENTORY_2026-10-08.md)。本目录日志只使用具名测试样本、FakeGateway 或匿名健康检查；截图不含真实账户。

## 最终结果

| 文件 | 结果与用途 |
|---|---|
| backend-native.txt | Python 3.13 原生依赖环境：214 passed |
| backend-research.txt | Python 3.12 研究环境：195 passed / 19 skipped；与原生套件重叠，不累加 |
| frontend-quality.txt | ESLint、vue-tsc、26 项 Vitest 和 Vite 构建通过 |
| browser-e2e.txt | 11 项 Playwright 浏览器测试通过；临时后端 18000 / 前端 55174 |
| tests-summary.json | 后端逐项用例名称、结果及接口计数 |
| routes.csv | 37 个 HTTP + 6 个 WebSocket；43 个处理函数体均有动态执行记录，不能据此推断全部分支通过 |
| coverage.txt / coverage-summary.json | 4733 / 6245 语句、1079 / 1794 分支；coverage 合并口径约 72.3% |
| ruff.txt / mypy.txt | Ruff 通过；mypy 的 39 个纳入文件通过 |
| cli-backtest.json | 独立 CLI 对临时具名历史样本运行成功，无柜台连接；source=cli_fixture 不是实际市场数据 |
| benchmark.txt | 6000 根单合约回放，保留 2000 根；不是实盘吞吐或生产压力测试 |
| bundle-budget.txt | ECharts、Element Plus 均在仓库规定预算内 |
| live-dependencies.txt / research-environment.txt | 依赖完整性与解释器版本检查 |
| secrets.txt | 已跟踪配置及私钥头基础扫描通过；不等于历史授权码已轮换 |
| runtime-summary.json | 正式服务及前端代理匿名健康检查均为 200，connected，1 个会话；后端仍为原进程 |
| alert-regression-before.txt | 修复前：100ms 高频告警用例失败。故意保留的反例证据，不是最终测试失败 |
| drawing-regression-before.txt | 修复前：清除后仍有 2 个 graphic 元素。修复后最终 E2E 断言为 0 |

四张 PNG 均由最终 E2E 生成：监控台、系统页、RSI 图表及离线回测。图表已检查清线后的实际图形对象和截图。PNG 下载另外由 Playwright 验证文件名、完成状态，截图与下载文件不混同。

## 执行范围与复现

- 后端原生解释器：`back_end/.venv-live/Scripts/python.exe`，清除外部 `PYTHONPATH`，设置 `QUANT_ENV=test`、`PYTHONUTF8=1`。在 `back_end` 运行 `python -m coverage run --branch --source=src -m pytest`，再运行 `python -m coverage report` 和 `python -m coverage json`。测试夹具把 CTP 配置替换为空测试文件，柜台发送由捕获函数或 FakeGateway 代替。
- 后端研究环境：本机 bundled Python 3.12.14，`PYTHONPATH=D:/mine/quant-audit-20261004/python-deps`；完整运行 pytest、Ruff、mypy。没有把原生依赖跳过当作通过。
- 前端：Node 24，运行 `npm run quality` 及 `node ../scripts/check_bundle.mjs`。当前依赖仍有大包提示，不隐去告警。
- 浏览器：复用 [隔离 Playwright 配置](../live-execution-20261008/e2e-playwright.config.mjs)、[Vite 配置](../live-execution-20261008/e2e-vite.config.mjs)、[临时后端包装](../live-execution-20261008/e2e-backend-wrapper.py)。本机执行副本在 `D:/mine/quant-native-verify-20261008/execution-e2e/`，`PLAYWRIGHT_PORT=55174`。执行 `node node_modules/@playwright/test/cli.js test --config D:/mine/quant-native-verify-20261008/execution-e2e/playwright.config.mjs`。配置内含本机绝对路径，移机时需相应修改，且不可占用已运行实盘后端的 8000 端口。
- 新增测试分别为 `back_end/tests/test_feature_inventory.py`、`front_end/tests/unit/feature-inventory.spec.js`、`front_end/tests/e2e/feature-inventory.spec.js`。最终全套运行包含这些用例及本轮三个前端修复。

保留用户实盘会话，未重启后端、未更换账户、未启停真实策略、未提交真实报单/撤单。当前线上进程还未加载此前最后两项后端修复。连续新 Tick、真实成交/撤单回报、跨夜及断线恢复不在本轮通过范围。

`SHA256SUMS` 覆盖本目录中除自身外的所有文件；文本统一 LF，并通过 `.gitattributes` 固定。提交前校验工作区与暂存区字节哈希。
