# 登录预留配置修复（2026-10-08）

> 后续已完成 Python 3.13 原生环境安装和实盘 MD/TD 前置回调验证，见 [原生接入验证](NATIVE_CTP_VERIFICATION_2026-10-08.md)。本文末尾“缺少依赖”描述保留为当时状态。

已恢复 Web 登录对预留连接参数的使用，默认选择实盘 API，移除登录页合约、保证金率和自动启动策略输入。配置完整时只需输入投资者账号、密码。

## 原因与实现

- Web 原本只读取 `QUANT_CTP_*` 环境变量，没有读取被忽略的本地配置；前端又用空字符串覆盖后端默认值。现按非空请求覆盖值、环境变量、本地配置的顺序解析，兼容旧客户端空字段。
- 默认配置文件固定为 `back_end/config/config_production.json`，与进程工作目录无关；`QUANT_CTP_CONFIG` 可指定其他文件。每次读取新的登录配置，不把认证码作为 Pydantic 默认值写入 OpenAPI schema。
- 从既有历史版本恢复本机原先的连接配置：交易前置 6 条、行情前置 6 条、经纪商 ID、AppID、认证码。前端历史默认值与配置文件历史值一致。只写入 Git 忽略的本地文件，未恢复历史账户密码、自动启动行为或未经核实的保证金率。
- `/auth/servers` 仅返回连接参数白名单及 `auth_code_configured`。认证码在后端使用；用户可在密码型输入框中临时覆盖，留空沿用已配置值。公开响应、OpenAPI 和本次跟踪文件均检查未包含本地认证码。
- 登录页显示当前 API 环境，配置加载期间禁止提交，加载错误明确展示；仍支持自定义地址和测试环境。手机窄屏改为单列，避免地址和配置提示被截断。
- 登录不再收集合约或保证金率，也不自动启动策略。交易侧仍校验已核实的合约数据和保证金率；本地 `trading.contract_margin_rates` 或显式 API 参数可提供比例映射，缺失时继续禁止开仓。
- 测试进程使用专门的空配置/浏览器 fixture，不读取本机真实配置。删除已移除的保证金表单工具及其 9 项单测，由后端配置校验和登录浏览器回归覆盖当前路径。

## 验证

| 检查 | 结果 |
|---|---|
| 后端完整 pytest | 156 passed |
| Ruff / mypy | 通过 / 34 个源文件通过，新增 settings 模块检查 |
| 前端 lint / vue-tsc / Vitest / build | 全部通过，19 项单测 |
| Edge / Playwright | 7 passed：预留配置无合约登录、读取失败、自定义覆盖、回测、行情 Worker、注销、公开页面 |
| 本地真实 HTTP | 8000 与 5174 代理均返回已恢复配置；各 6 条线路、实盘环境、认证码不公开 |
| UI | 桌面与 390px 手机截图检查；应用内浏览器已显示本机预留线路、经纪商 ID 和实盘 API |

证据：[后端测试](evidence/login-fix-20261008/backend-tests.txt)、[静态检查](evidence/login-fix-20261008/backend-static.txt)、[前端质量检查](evidence/login-fix-20261008/frontend-quality.txt)、[浏览器回归](evidence/login-fix-20261008/browser-e2e.txt)、[本地配置核验](evidence/login-fix-20261008/local-defaults-checks.json)、[桌面截图](evidence/login-fix-20261008/login-defaults-desktop.png)、[手机截图](evidence/login-fix-20261008/login-defaults-mobile.png)。截图使用虚构 fixture 参数；文本日志统一 LF 换行并去除行末空白，证据文件附 SHA256 清单。

## 运行状态与未验证项

登录页：[http://127.0.0.1:5174/login](http://127.0.0.1:5174/login)。后端仍以 loopback、单执行者启动，关闭自动模拟数据。当前后端进程记录在本机 `D:/mine/quant-login-fix-20261008/local-api.pid`，停止前需重新核对进程身份。

本轮业务测试使用 FakeGateway，未向真实柜台发送交易指令。本机当前 Python 环境缺少 `vnpy` 和 `vnpy_ctp`，尚不能将配置读取和页面验证视为真实 MD/TD 登录成功；原生安装、认证有效性及柜台连通性仍待实测。现有 Starlette/httpx 弃用警告、前端大分块提示保留，不影响上述检查通过。
