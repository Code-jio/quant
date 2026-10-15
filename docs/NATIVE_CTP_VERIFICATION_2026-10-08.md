# CTP 原生接入验证（2026-10-08）

原生运行环境已安装并接入当前后端，用户在本地登录页完成了一次真实账户登录。**已确认 MD/TD 登录、结算确认、合约查询和登录就绪门禁通过；本轮没有下单、撤单或启动策略。**

## 运行环境

| 项目 | 结果 |
|---|---|
| 系统 / 解释器 | Windows AMD64 / CPython 3.13.15 |
| 独立环境 | `back_end/.venv-live` |
| 原生包 | vnpy 4.4.0 / vnpy_ctp 6.7.11.4 |
| 完整运行依赖 | `requirements-live.lock` 固定 59 项，已逐项核对实际版本 |
| 依赖一致性 | `pip check` 通过 |
| 启动 | `back_end/start-live.bat`，清除外部 PYTHONPATH，关闭自动模拟数据 |
| 当前服务 | `http://127.0.0.1:8000`；前端 `http://127.0.0.1:5174/login` |

原 Python 3.12 环境无法直接使用该版本的 Windows wheel。尝试解析源码包时发现本机没有 MSVC/C++ 编译器，因此改用独立 3.13 环境安装官方二进制包。未替换原研究运行时，也没有更改全局默认 Python。版本与发布文件见 [vnpy PyPI](https://pypi.org/project/vnpy/4.4.0/) 和 [vnpy_ctp PyPI](https://pypi.org/project/vnpy_ctp/6.7.11.4/)。

`scripts/check_native_ctp.py` 默认仅检查加载与回调包装，添加 `--fronts` 才实际连接配置中的默认前置；使用原生 API 的 production_mode=True，并在 onFrontConnected 记录结果，不调用认证、账户登录、订阅或交易方法。探测由有时限的子进程承载，隔离临时 flow 和 vn.py 配置目录。

## 验证证据

- 预留 TD 6 条、MD 6 条线路全部 TCP 连通。TCP 结果不替代 CTP 协议或账户认证。
- 原生 DLL 导入、实际 CtpGateway 回调包装成功；实盘模式下默认 TD/MD 都收到 onFrontConnected。
- 实际安装包回归覆盖实盘/测试环境参数传递、空持仓查询结束以及 MD/TD 断线回调；测试没有连接外部柜台。
- Python 3.13 环境完整后端回归 **159 passed**；Ruff 通过、mypy **35 个源文件**通过。
- 原 Python 3.12 研究环境回归 **156 passed、3 skipped**；3 个跳过项是仅在原生包已安装时运行的兼容性检查。
- 前端 lint、vue-tsc、**19 项单测**和构建通过；以新 Python 运行 fixture 后端的浏览器回归 **7 passed**。
- 用户报告登录成功；运行中后端有 `POST /auth/login 200` 及持久审计 `login/success`，不是 FakeGateway。连接日志出现 TD/MD 登录成功、授权验证成功、结算确认成功、合约查询成功。
- API 登录返回成功前，项目网关要求 MD、TD、结算、合约、资金、持仓全部就绪，账户必要字段已知且持仓快照新鲜。因此成功响应也证明该次登录通过了就绪门禁；不据此保证之后每一时刻数据都新鲜。
- 登录后的 `/trading/snapshot`、`/positions`、`/risk/status`、`/strategies`、`/trading/reconcile` 均有 200 响应；未见 500/502/503 或异常栈。健康检查显示 `gateway_status=connected`、一个活跃会话。
- 另有未带当前有效会话的重复登录请求被 409 拒绝，现有账户连接保持；未绕过账户隔离规则。

证据位于 [evidence/native-ctp-20261008](evidence/native-ctp-20261008/)，只保存脱敏布尔状态、版本、测试输出及接口状态码；不保存账号、密码、认证码、资金数额或登录 Cookie。文本统一 LF 并去除行末空白，文件带 SHA256 清单。

## 尚未验证

浏览器连接工具无法读取用户当时使用的已登录页面，因此真实账户验收依据用户反馈、后端连接日志、持久审计和 HTTP 记录；不能将离线浏览器截图描述为真实账户页面截图。

尚未验证实时行情订阅后的持续 Tick 更新、跨日/断线恢复、手续费和资金持仓与券商客户端的逐项对账，以及报单/撤单/成交链路。真实登录成功不代表完整交易验收通过。缺少经核实保证金率的合约仍禁止开仓。

后端保留用户当前连接。启动器/实际服务 PID 分别记录在本机 `D:/mine/quant-native-verify-20261008/local-api-launcher.pid` 与 `local-api.pid`；停止前需核对进程身份，不能盲用历史 PID。此机 `.venv-live` 的基础解释器位于 `D:/mine/quant-native-verify-20261008/python/`，该目录含运行依赖，不能按纯日志目录清理。研究服务仍可独立使用 Python 3.12 的 `.venv` 和对应锁文件。

本次为 3.12 源码安装探测创建的 `.venv` 没有安装研究依赖，未用于当前服务。清理该目录的命令被自动审批以“blocked by policy”拒绝，未提供更具体原因，已保留原样；若将来使用研究启动脚本，应先按 README 安装 `requirements-dev.lock`。
