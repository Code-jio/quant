# Quant — 期货研究与交易工作台

Vue 3 + FastAPI + SQLite。支持历史数据导入、离线回测、图表和单账户 CTP 适配。研究/开发环境为 **Windows x64 / Python 3.12 / Node.js 24**，原生 CTP 使用独立 **Python 3.13** 环境。原生加载、前置连接和一次真实账户登录已验证；实时行情、断线恢复和交易回报仍需专项验收。

整改结果见 [整改报告](docs/REMEDIATION_RESULTS_2026-10-04.md) 和 [55 项清单](docs/IMPROVEMENT_BACKLOG_2026-10-04.csv)。原始审查保存在 [审查报告](docs/PROJECT_REVIEW_2026-10-04.md)。

第二轮复查的 6 项问题已完成代码修复及离线回归，见 [2026-10-08 修复结果](docs/RECHECK_FIXES_2026-10-08.md)、[复查清单](docs/RECHECK_BACKLOG_2026-10-05.csv)。后续已恢复登录预留参数并完成原生运行环境验证，当前原生环境回归为后端 159 项、前端 19 项、浏览器 7 项。

## 安装与启动

在项目根目录运行 PowerShell：

```powershell
py -3.12 -m venv back_end/.venv
back_end/.venv/Scripts/python -m pip install -r back_end/requirements-dev.lock
back_end/.venv/Scripts/python scripts/check_environment.py
cd front_end
npm ci
npm run dev
```

在另一个 PowerShell 窗口启动后端：

```powershell
cd back_end
.venv/Scripts/python server.py
```

前端为 `http://localhost:5173`，后端默认仅监听 `127.0.0.1:8000`。`start.bat` 和 `start-dev.bat` 使用同一个 `.venv`，均不热重载、不启动多个 worker。仅运行研究/API 时可安装 `requirements-research.lock`；开发与 CI 安装 `requirements-dev.lock`。锁定了直接和传递依赖版本；锁适用于上述 Windows/Python 环境，尚未做全新主机安装验收。

5173 被其他项目占用时，可在 `front_end` 使用 `npm run dev -- --host 127.0.0.1 --port 5174 --strictPort`；默认 CORS 已允许 5174。应确认 `/api/health` 返回本项目的 JSON，避免把其他项目的首页当作 API 响应。

离线访问 `/backtest` 无需 CTP 登录。在配置页显式开启“模拟数据”后才会生成带标记的临时样本；样本不会写进真实历史库。设置 `QUANT_ALLOW_SYNTHETIC_DATA=0` 可以全局禁止生成。生产环境默认关闭公开研究接口；如需独立研究服务，可在受控访问范围内显式设置 `QUANT_ALLOW_PUBLIC_RESEARCH=1`。

## 历史数据与回测

CSV 列为 `datetime,open,high,low,close,volume`，可带 `open_interest`。时间按 Asia/Shanghai 规范化，来源必须明确。每种周期独立导入；分钟数据缺失时不会用日线拼造。

```powershell
cd back_end
.venv/Scripts/python -m src.maintenance import-bars quotes.csv --symbol rb2610 --timeframe 1m --source your-data-provider
Copy-Item config/config.example.json config/config_production.json
# 编辑本地 config_production.json 的合约、日期、周期和回测参数后运行：
.venv/Scripts/python main.py --mode backtest --config config/config_production.json
```

`rb2610` 仅示例，不意味着当前挂牌或可交易。真实可交易合约来自柜台元数据。历史合约搜索结果不保证可交易。交易日历未导入时，缺口检测只是启发式诊断，不能当作交易所日历结论。

回测信号最早在下一根 bar 执行，限价需可达，同一根 bar 的成交量由多个委托共享。内置策略反手会先等已有委托完成，再平仓、开仓。成交统计按完整平仓回合汇总双方手续费；未平仓回合不计入已完成交易。无法计算的指标返回 `null` 并显示“—”。权益曾非正时，年化和收益率类风险指标不可计算。

策略历史只包含当前回调之前的 bar；内置 MA/RSI 将本次完成 bar 纳入指标，突破策略使用此前最后 N 根。服务端、Worker 与策略的 RSI 统一为 Wilder 平滑，以首 N 个变化的平均涨跌初始化；预热不足为未知，横盘为 50。时点和 RSI 修正会改变旧版本的信号与回测结果，需要重新计算旧结果。

模型使用固定保证金和费率；没有模拟柜台逐日结算、强制平仓、盘口排队、各品种费率规则。模拟数据只用于功能验证，不能用来评估策略收益。

## CTP 接入边界

2026-10-08 的后续复核已取得黄金 `au2612` 柜台收盘行情，持续 Tick 和实盘委托回报仍待交易时段人工验收。另已修复原生撤单发送失败码被忽略的问题，必须在后端重启、重新登录后才应用于现有运行实例；见[行情与委托链路复核](docs/LIVE_EXECUTION_VERIFICATION_2026-10-08.md)。

`requirements-live.lock` 固定了 Windows x64 / Python 3.13.15 下的 59 个运行依赖，包括 vnpy 4.4.0 / vnpy_ctp 6.7.11.4。这个 CTP 版本的 PyPI Windows 安装包对应 CPython 3.13；3.12 需要自行配置 C++ 编译环境，因此本项目使用独立环境安装已发布的二进制包。已通过原生导入、回调包装、实盘 MD/TD 前置连接、完整后端回归及浏览器回归；用户已在本地完成一次真实登录，后端确认登录就绪门禁通过，快照、持仓、风控与对账接口返回 200。[验证记录与剩余边界](docs/NATIVE_CTP_VERIFICATION_2026-10-08.md)。

```powershell
# 项目根目录；使用本机 Python 3.13，或由 uv 安装并建立独立环境
uv venv back_end/.venv-live --python 3.13 --seed
back_end/.venv-live/Scripts/python -m pip install --only-binary=:all: -r back_end/requirements-live.lock
back_end/.venv-live/Scripts/python -m pip check
back_end/.venv-live/Scripts/python scripts/check_native_ctp.py
# 可选：实际连接已配置的实盘 TD/MD 前置，但不发送账号认证、登录、订阅、下单请求
back_end/.venv-live/Scripts/python scripts/check_native_ctp.py --fronts
# 启动前先停止同一工作区旧的后端进程，保持单执行者
back_end/start-live.bat
```

`start-live.bat` 使用 `.venv-live`、清除外部 `PYTHONPATH` 并关闭自动模拟数据；普通 `start.bat` 仍使用研究环境 `.venv`。原生探测在有时限的独立子进程中运行，临时隔离日志和 flow 文件，不需要账号密码。

服务必须完成 MD/TD 登录、结算确认、合约、账户、持仓快照才可开仓。保证金比例需要提供已核实的 `contract_margin_rates`；缺失时拒绝开仓。当前仅支持真实限价委托，快捷平仓使用新鲜买卖价。挂单预留采用保守计算，账户冻结资金已经更新时可能重复预留而拒绝部分原本可行的委托，优先避免超额开仓。

网页登录自动读取 `back_end/config/config_production.json` 的 `trading` 连接配置（或 `QUANT_CTP_CONFIG` 指定的文件），与启动目录无关。配置优先级为：请求中的非空覆盖值 → `QUANT_CTP_*` 环境变量 → 本地配置。默认使用实盘（生产版 API）；如明确配置为测试，登录页会显示测试 API。前置地址、经纪商 ID 和 AppID 自动填入，认证码只在后端使用，页面显示“已在后端配置”，留空即可；高级配置仍可手动覆盖。未设置参数时不会编造券商地址或认证信息。

配置完整后，只需填写账号和密码即可登录，不需要填写合约、保证金率或策略；登录不会自动启动策略。交易/行情备用线路分别通过 `trading.td_servers` / `md_servers` 配置为 `[{"label":"线路名称","value":"tcp://host:port"}]`，也可使用 `QUANT_CTP_TD_PRESETS` / `MD_PRESETS` 覆盖。

开仓所需的已核实保证金比例由本地 `trading.contract_margin_rates` 提供（例如 `{"rb2610":0.12}`），直接 API 调用也可显式覆盖；这不是登录必填项。维护者需按账户和交易日核验，未配置合约继续禁止开仓。真实配置和认证码不提交 Git。恢复预留配置的说明见 [登录配置修复记录](docs/LOGIN_FIXES_2026-10-08.md)。

停止策略保留网关以接收回报；退出账户关闭引擎并撤销会话。急停、日内基线、参数、权重、成交和订单持久化。重启不会自动启动策略；未确定报单结果或缺失活动订单回放时拒绝新增报单，需结合柜台回报核对。`GET /trading/reconcile` 是有来源状态的核对快照，不能替代柜台全量对账。

## 检查与运行文档

```powershell
cd back_end
.venv/Scripts/python -m ruff check main.py server.py src tests
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest
cd ../front_end
npm run quality
# 将 Python 测试解释器加入 PATH，或指定 QUANT_TEST_PYTHON
$env:QUANT_TEST_PYTHON = (Resolve-Path ../back_end/.venv/Scripts/python.exe).Path
npm run e2e
```

Playwright 会在 8000 端口启动临时离线 fixture 后端，禁止真实报单；运行前须停止占用该端口的正式服务。本机使用 Edge，CI 安装 Chromium。CI 配置位于 `.github/workflows/ci.yml`；提交后由远程 GitHub Actions 执行，目前仅有本地运行证据。

部署、配置、备份恢复、交易时段验收见 [运行手册](docs/OPERATIONS.md)。
