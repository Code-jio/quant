# 2026-10-15 验证证据

本目录仅包含隔离测试输出和测试账户截图，不含真实账户配置。源码基于 `3ab977f` 的工作区改动；未提交或推送。

| 检查 | 结果 | 证据 |
|---|---|---|
| Ruff | 通过 | [ruff.log](ruff.log) |
| mypy | 41 个文件通过 | [mypy.log](mypy.log) |
| 后端 pytest | 247 项通过 | [pytest.log](pytest.log) |
| 前端 quality | ESLint、vue-tsc、29 项单测及生产构建通过 | [frontend-quality.log](frontend-quality.log) |
| 浏览器 Playwright | 22 项通过 | [e2e.log](e2e.log) |
| 包体预算 | ECharts 734098 / 760000；Element Plus 882069 / 910000 字节 | [bundle.log](bundle.log) |

执行环境：仓库 `.venv-live` Python 3.13，固定依赖；Node 24；Playwright Edge。pytest 设置 `QUANT_ENV=test`、清除 `PYTHONPATH`，使用外部独立 `--basetemp`，避免机器旧临时目录权限问题。后端完整命令为 `python -m pytest -o addopts='' -q -p no:cacheprovider --basetemp D:/mine/quant-readiness-20261015/pytest-final-2 --junitxml D:/mine/quant-readiness-20261015/backend-final.xml`。

浏览器通过 `D:/mine/quant-native-verify-20261008/execution-e2e/playwright.config.mjs` 启动仓库 FakeGateway 测试服务，后端 18000、前端 55174，`reuseExistingServer=false`。此配置将仓库 `tests/e2e_backend.py` 的监听端口改为 18000，并把 Vite 代理指向同一端口。运行所有仓库 E2E 用例；测试服务在退出后自动关闭。测试绝不连接柜台；新下单及平仓确认测试的提交请求在浏览器直接拦截，不到达网关。

保留的非失败提示：Starlette TestClient/httpx 的弃用警告；Vite 第三方包超过默认 500 KB 提示，但项目约定包体预算通过；终端颜色环境提示。

截图已人工查看，检查区正常与阻断状态均没有横向溢出，下单按钮保持可见：

- [2K 检查通过及估算](preflight-ready-2560.png)
- [2K 阻断及未知值](preflight-blocked-2560.png)
- [1920 交易台](trading-desk-1920.png)
- [1366 交易台](trading-desk-1366.png)

截图及接口中的行情、账户和数值是测试样本，不代表真实交易状态。连续 Tick、真实报单/撤单回报、跨日与断线恢复未在本轮验证。未重启或注销用户实盘会话。
