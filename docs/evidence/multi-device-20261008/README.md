# 同账户多端登录验证证据

2026-10-08，Asia/Shanghai。功能说明见 [多浏览器登录](../../MULTI_DEVICE_LOGIN_2026-10-08.md)。本目录截图和请求均使用具名夹具，不包含实际账户或登录凭证。

| 文件 | 内容 |
|---|---|
| backend-tests.txt / test-summary.json | 原生 Python 3.13 完整回归 230 项通过，退出码 0；双重 quiet 参数使 pytest 日志用点号表示逐项结果，summary 对应计数 |
| backend-red.txt | 实现前新增用例的失败证据：第二端被拒绝、单端注销等能力缺失；不是最终结果 |
| backend-ruff.txt / backend-mypy.txt | Ruff 通过；mypy 纳入的 40 个文件通过 |
| frontend-quality.txt | ESLint、vue-tsc、29 项 Vitest、Vite 构建通过 |
| browser-e2e.txt | 18 项完整浏览器回归通过，包含双独立会话及旧后端单端退出失败兼容测试 |
| bundle-budget.txt | ECharts / Element Plus 均在既有预算内 |
| multi-device-session.png | 具名 E2E_ONLY 样本页面中的账户会话菜单 |
| runtime-summary.json | 实盘服务只读健康检查；保持原进程和已有会话，没有执行交易 |

后端在 `back_end` 运行 `.venv-live/Scripts/python.exe -m pytest -q --basetemp D:/mine/quant-multidevice-20261008/pytest-full`，环境 `QUANT_ENV=test`、`PYTHONUTF8=1`，清除外部 `PYTHONPATH`。Ruff/mypy 使用同一解释器。临时目录不写入运行中的实盘账本。

前端使用 Node 24 运行 `npm run quality` 和 `node ../scripts/check_bundle.mjs`。浏览器 `PLAYWRIGHT_PORT=55174`，执行 `node node_modules/@playwright/test/cli.js test --config D:/mine/quant-native-verify-20261008/execution-e2e/playwright.config.mjs`。该配置使用独立 18000/55174 端口，沿用[隔离配置](../live-execution-20261008/e2e-playwright.config.mjs)、[Vite 代理](../live-execution-20261008/e2e-vite.config.mjs)、[后端包装](../live-execution-20261008/e2e-backend-wrapper.py)。绝对路径是本机执行路径，移机需调整。

多端浏览器验收使用同一 Edge 内核中两个独立 BrowserContext，分别填写登录表单；没有复制 Cookie。测试只读账户状态及执行隔离夹具的登录/注销/断开接口，不提交真实交易。前端仍有大包告警，后端有 Starlette/httpx 弃用提示；日志保留这些信息。

`SHA256SUMS` 覆盖本目录除自身外的文件；文本仅规范换行与行尾空白，统一 LF。最终校验暂存区哈希及本地敏感配置值未进入提交。
