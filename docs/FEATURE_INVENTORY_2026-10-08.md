# 当前功能清单与测试结果（2026-10-08）

后续增加[同账户多浏览器登录](MULTI_DEVICE_LOGIN_2026-10-08.md)及单端注销接口，HTTP 接口增至 38 个；最新隔离测试为后端 230 项、前端 29 项、浏览器 18 项。以下功能盘点保留当时的基线数量。

后续界面已完成 [2K 单屏交易台改造](TRADING_LAYOUT_2026-10-08.md)：首页资金/策略改为侧滑面板，行情与委托联动，宽屏常驻盘口。最新浏览器回归为 16 项；本文以下 11 项记录仍保留为功能盘点时的验收基线。

本轮按实际页面、正式 API、命令入口和内部交易库梳理，共 **6 个页面、37 个 HTTP 接口、6 个 WebSocket 通道**。功能矩阵覆盖下面 50 组能力，不把兼容入口、空路由模块或未使用的脚手架组件重复计数。

**当前服务与代码版本不同：** 用户选择保留当前实盘登录会话。本轮未重启后端、未切换真实账户、未启动真实策略、未提交任何实盘报单或撤单。此前公开搜索隔离、账户/持仓刷新调度两项后端修复仍待下次正常重启。此次前端修复已通过隔离浏览器验证，刷新页面可加载新代码，不要求退出账户。

表中“通过”指本轮列明范围内的隔离测试通过，不代表实盘、所有参数组合或生产容量已验收。“此前实测”引用同日已有记录，不冒充本轮重新登录或重新查询。截图全部来自具名 fixture / FakeGateway。

## 本轮结果

| 检查 | 实际结果 | 范围 |
|---|---|---|
| 后端 Python 3.13 + 原生依赖 | 214 passed | 完整 pytest；原生发送由捕获函数替代 |
| 后端 Python 3.12 研究环境 | 195 passed，19 skipped | 跳过的 19 项需要原生包 |
| 前端单测 | 26 passed | 网络、行情、指标、状态持久化、告警、快捷键等 |
| 浏览器 E2E | 11 passed | 使用临时 FakeGateway，端口 18000 / 55174 |
| 静态与构建 | Ruff、mypy 39 文件、ESLint、vue-tsc、Vite 均通过 | mypy 不代表全仓类型覆盖 |
| 依赖与配置检查 | pip check、研究环境检查、已跟踪配置扫描通过 | 不代表全面漏洞扫描或历史凭据已轮换 |
| CLI 回测 | 临时历史库实际运行成功 | main.py --mode backtest，来源 cli_fixture |
| 构建体积预算 | ECharts 734098 / 760000 B；Element Plus 882069 / 910000 B | 均在仓库预算内；仍有 >500kB 提示 |
| 离线回放 | 6000 根输入、保留 2000 根 | 后半程耗时未随历史长度持续增长；非实盘吞吐测试 |

后端 `src` 语句覆盖率约 **75.8%**，分支覆盖率约 **60.1%**；coverage 合并口径约 **72.3%**。不宣称百分之百功能覆盖。API 与图表有错误路径、真实柜台事件和复杂交互尚未覆盖，旧示例模块也计入分母。原始计数见 evidence 中的 coverage-summary.json。

## 功能矩阵

测试缩写：B 为 `back_end/tests/`，U 为 `front_end/tests/unit/`，E 为 `front_end/tests/e2e/`。对应完整执行日志及用例清单见文末证据目录。

| 编号 | 模块 | 当前功能 | 本轮对应测试 | 结果 | 使用边界 / 尚未验证 |
|---|---|---|---|---|---|
| F01 | 登录 | 预置 TD/MD 地址、经纪商 ID、AppID、服务端认证码；默认实盘 API；备用线路与手动覆盖 | B/test_login_defaults.py；E/recheck.spec.js | 通过 | 认证码不回传浏览器；未修改真实配置 |
| F02 | 登录 | 账号密码登录，无需合约；登录状态与错误反馈；默认不启动策略 | B/test_api_auth.py；B/test_login_defaults.py；E/recheck.spec.js | 通过；此前真实登录成功 | 本轮未重新登录；当前服务只读健康复核正常 |
| F03 | 身份 | Cookie 会话、过期、注销、账户切换、旧 Cookie/旧 WebSocket 失效 | B/test_review_auth.py；B/test_security_and_risk.py；E/remediation.spec.js | 通过 | 单账户执行边界；没有多用户并行账户管理 |
| F04 | 页面 | 登录、监控台、回测、系统监控、K 线、盯盘；路由权限与标题 | E/smoke.spec.js；E/remediation.spec.js；E/feature-inventory.spec.js | 通过 | 页面可打开不等于匿名可读取账户数据 |
| F05 | CTP | 原生网关加载、生产/测试 API 选择、登录/结算/合约/账户/持仓就绪门禁 | B/test_native_ctp_compat.py；B/test_review_gateway.py | 通过；此前前置与登录成功 | 原生兼容测试不连接柜台；其他主机与券商未验收 |
| F06 | CTP | TD/MD 状态分离、断线失效、空持仓快照清理 | B/test_review_gateway.py；B/test_ctp_margin.py | 本地通过 | 真实断线、跨夜重连尚未演练 |
| F07 | 合约 | 代码/名称搜索、交易所筛选；区分柜台元数据与历史记录 | B/test_review_data.py；B/test_ctp_margin.py；E/recheck.spec.js | 通过 | 历史与侧栏预置品种不是当前可交易清单；不猜测未知合约 |
| F08 | 行情 | REST 批量 Tick 查询、订阅、缓存快照、来源和缺失说明 | B/test_market_ticks.py；B/test_native_ctp_execution.py | 本地通过；此前黄金快照成功 | 持续新 Tick 与真实页面连续推送待交易时段验收 |
| F09 | 行情 | WebSocket 多合约订阅/取消、ping/pong、重连与消费者释放 | B/test_feature_inventory.py；U/review-ws.spec.js | 通过 | 实际吞吐、网络断线负载未测；每连接有订阅限制 |
| F10 | 行情 | 行情摘要、买卖盘口、价差、成交量与持仓量展示 | B/test_native_ctp_execution.py；E/remediation.spec.js；组件核对 | 部分验证 | 买一卖一字段已测；完整多档数据取决于柜台来源，未做实盘逐档核对 |
| F11 | K线 | 1m/5m/15m/30m/1h/4h/1d/1w 历史数据、before/since、向前分页 | B/test_review_data.py；B/test_feature_inventory.py；U/review-data.spec.js | 通过 | 需导入对应周期；缺失数据不会由日线伪造 |
| F12 | K线 | 自定义周期输入 | B/test_feature_inventory.py；E/feature-inventory.spec.js | 限制已验证 | 任意周期未实现；例如 7d 返回 400 并显示错误，仅支持 F11 的 8 种周期 |
| F13 | 指标 | MA、EMA、MACD、Wilder RSI、KDJ、BOLL、成交量均线 | B/test_feature_inventory.py；B/test_recheck_regressions.py；U/recheck-indicators.spec.js | 通过 | 固定样本与部分边界验证；未宣称与任意外部终端口径完全一致 |
| F14 | 指标 | Worker 计算、主线程回退、响应版本隔离、分页指标去重 | U/review-data.spec.js；E/remediation.spec.js | 通过 | 不把 fallback 成功算作真实 Worker；浏览器用例另验证 Worker 创建 |
| F15 | 图表 | 均线增删/显示/颜色/线型、副图指标切换与配置保存 | U/feature-inventory.spec.js；E/feature-inventory.spec.js | 修复后通过 | 已修复直接修改界面状态未持久化；刷新页面加载修复 |
| F16 | 图表 | 趋势线、水平线、垂直线、清除画线；十字光标与缩放 | E/feature-inventory.spec.js；源码及截图核对 | 部分验证 | 本轮实际绘制水平线并检查清空后 graphic 对象为 0；趋势线、垂直线及复杂缩放未逐项专项验收 |
| F17 | 图表 | 全屏、PNG 保存、快捷键与输入框避让 | E/feature-inventory.spec.js；U/feature-inventory.spec.js | 通过 | PNG 验证下载完成；快捷键生命周期及输入避让通过，未覆盖所有组合键 |
| F18 | 盯盘 | 自选增删/去重/20 条上限、最近访问、常看排序、最后周期记忆 | U/feature-inventory.spec.js；E/remediation.spec.js | 通过 | 本地浏览器保存，无跨设备同步 |
| F19 | 盯盘 | 图表配置覆盖、缓存按合约/周期隔离、过期与清理 | U/feature-inventory.spec.js；U/review-data.spec.js | 通过 | 前端缓存 15 分钟；未把这项等同于实时行情可长期缓存 |
| F20 | 告警 | 涨跌幅、跳价、成交量阈值提示，冷却、已读、清空 | U/feature-inventory.spec.js：100ms/600ms 事件序列 | 修复后通过 | 高频事件不再令检测永久推迟；这是页面提示，无短信/邮件/自动交易；成交量规则仍基于收到的量字段 |
| F21 | 交易 | 手动限价报单、方向/开平/手数/价格校验、柜台请求字段映射 | B/test_manual_trading.py；B/test_native_ctp_execution.py | 本地通过 | 未提交实盘委托；当前原生路径仅支持真实限价单，不能据 FakeGateway 市价用例宣称实盘市价可用 |
| F22 | 交易 | 单笔撤单、批量撤单、发送失败反馈、等待柜台终态 | B/test_manual_trading.py；B/test_native_ctp_execution.py | 本地通过 | 未发送实盘撤单；请求发送成功不等于已撤销 |
| F23 | 交易 | 快捷平仓、多空方向、可平数量、冻结量、平今/平昨校验 | B/test_manual_trading.py；B/test_review_trading.py；B/test_review_execution.py | 本地通过 | 实盘今昨仓与券商规则一致性仍待人工核对 |
| F24 | 交易 | 委托/成交/持仓列表，统一快照与修订号，WebSocket 合并/去重/补快照 | B/test_feature_inventory.py；B/test_trading_engine_auto_strategy.py；U/review-ws.spec.js | 通过 | 前端快照失败保留并标记旧数据；重试有限次 |
| F25 | 内部交易库 | 批量提交、撤改单，原委托终态后发送剩余量 | B/test_review_trading.py；OrderManager 源码核对 | 部分验证 | 内部 Python 能力，无完整对应网页/API；未实盘验收 |
| F26 | 内部交易库 | 触发入场、限价入场、止损、止盈、移动止损预埋单 | B/test_review_trading.py；OrderManager 源码核对 | 部分验证 | 已测风控不可绕过与平仓方向；未覆盖所有触发组合，无完整 UI，重启不恢复本地预埋单 |
| F27 | 保证金 | 当前账户多空比例、按手金额、相对交易所加收值查询与页面展示 | B/test_ctp_margin.py；B/test_native_ctp_compat.py；E/recheck.spec.js | 通过；此前 au2612 柜台查询 ready | 本轮不重取私有结果；单腿期货范围，不覆盖期权/组合抵扣 |
| F28 | 保证金 | 请求限速、超时、缓存到期刷新、账户/交易日失效及旧回包隔离 | B/test_ctp_margin.py | 本地通过 | 账户刷新调度和公开搜索隔离最后两项后端修复仍待重启加载 |
| F29 | 风控 | 数量/金额/持仓上限、挂单预留、行情过期、价格偏离、重复信号、日内损失、未知数据拒绝 | B/test_security_and_risk.py；B/test_review_trading.py | 通过 | 保守预留可能重复计算冻结资金；未做组合净额保证金优化 |
| F30 | 风控 | 动态风险配置、参数校验、急停/恢复、急停状态持久化 | B/test_api_auth.py；B/test_recheck_regressions.py；B/test_review_trading.py | 通过 | 本轮仅操作 fixture；未触发用户账户急停或恢复 |
| F31 | 账本 | 执行意图、委托/成交去重、账户隔离、日内基线、未决订单阻塞 | B/test_review_execution.py；B/test_review_trading.py；B/test_recheck_regressions.py | 通过 | 真实崩溃恢复与未决报单对账仍待验收；不自动重发未知委托 |
| F32 | 策略 | 双均线、RSI 均值回归、突破三种内置策略及参数校验 | B/test_recheck_regressions.py；B/test_review_backtest.py | 通过 | 固定序列验证信号与窗口，不代表策略盈利能力 |
| F33 | 策略 | 策略列表/详情/信号、启停、参数修改及受控重启 | B/test_feature_inventory.py；B/test_review_execution.py | 通过 | 针对已注册策略；默认网页登录不创建策略，显式 API 可在登录时初始化策略 |
| F34 | 策略 | 权重、保证金与合约乘数参与手数计算；持仓成本与费用处理 | B/test_feature_inventory.py；B/test_strategy_position_accounting.py；B/test_review_trading.py | 通过 | 单账户资源分配，无多账户资产组合系统 |
| F35 | 策略数据 | 事件时间聚合 bar、重复 Tick 处理、限定合约、停止后不发新信号 | B/test_trading_engine_auto_strategy.py；B/test_review_execution.py | 通过 | 小时桶不是完整交易所分节日历；真实跨夜待验收 |
| F36 | 回测 | 离线历史回测、显式临时模拟样本、来源标识、API 与 CLI 共用服务 | B/test_review_backtest.py；E/remediation.spec.js；CLI 实测 | 通过 | 不要求 CTP 登录；生产公开研究须显式启用；模拟样本不用于收益结论 |
| F37 | 回测撮合 | 下一 bar 执行、限价可达、滑点与费用、共享成交量、部分成交、反手顺序 | B/test_backtest_accounting.py；B/test_review_backtest.py | 通过 | 固定费率/保证金模型，无盘口队列、逐日结算、强平完整模型 |
| F38 | 分析 | 权益/收益/回撤、胜率、盈亏比、VaR/CVaR、夏普等指标、完整回合统计、JSON/文本报告 | B/test_analysis_metrics.py；B/test_feature_inventory.py；B/test_review_backtest.py | 通过 | 不可计算值在 JSON 中为 null；文本输出尚可能包含 nan；非完整正式绩效服务 |
| F39 | 回测页面 | 参数表单、运行状态、来源/错误提示、权益/分布/热力图及风险表 | E/remediation.spec.js；截图核对 | 通过主要流程 | 主要验证运行与权益图，未穷举所有图形控件及参数组合 |
| F40 | 数据 | CSV 导入、SQLite 持久化、结构升级、时间规范化、去重、行级来源 | B/test_data_governance.py；B/test_review_data.py；B/test_feature_inventory.py | 通过 | 不含外部历史行情供应商自动下载；CSV 必须明确来源 |
| F41 | 数据治理 | 范围/条数/缺口/元数据检查、模拟数据开关、非有限/非法 OHLC 拒绝 | B/test_data_governance.py；B/test_review_data.py；B/test_feature_inventory.py | 通过 | 未导入交易日历时缺口为启发式；质量接口需登录 |
| F42 | 数据缓存 | 通用 TTL/LRU 缓存、统计及 K 线清缓存接口 | B/test_data_governance.py；B/test_feature_inventory.py | 部分验证 | 当前 get_kline 直接读取数据库，不宣称服务端 K 线缓存命中已启用 |
| F43 | 仪表盘 | 账户权益/可用资金/持仓/盈亏与风险快照，分钟权益记录 | B/test_feature_inventory.py；B/test_recheck_regressions.py；E/feature-inventory.spec.js | 通过 | 本轮使用 fixture；未知费用/盈亏不可当作实际零值，跨日正式绩效未完成 |
| F44 | 运维页面 | CPU、内存、网络速率、TD/MD 状态、回报距今与系统 WebSocket | B/test_feature_inventory.py；B/test_review_gateway.py；E/feature-inventory.spec.js | 通过 | 回报距今不是 RTT；网络速率为主机计数，不是柜台专用带宽 |
| F45 | 日志审计 | 日志级别/关键词/暂停/清空；SQLite 审计含操作者与请求 ID，敏感字段脱敏 | B/test_feature_inventory.py；B/test_review_runtime.py；E/feature-inventory.spec.js | 通过 | 日志缓冲最多 500 条；审计库尚无自动归档 |
| F46 | 可观测性 | 健康检查、Prometheus、有界标签、请求 ID、API 文档及 WS 调试页 | B/test_api_auth.py；B/test_review_runtime.py；B/test_feature_inventory.py；只读健康复核 | 通过基础范围 | 调试页只验证 HTML 返回，未把旧示例页面算作完整客户端验收 |
| F47 | 运行边界 | 单执行者锁、单 worker、loopback、配置/环境优先级、研究/原生独立运行环境 | B/test_review_runtime.py；B/test_login_defaults.py；环境及依赖检查 | 通过本机范围 | 无分布式账户执行锁；未新起 CLI 实盘实例；其他主机安装未验收 |
| F48 | 维护恢复 | 数据导入命令、SQLite 在线备份、完整性检查、恢复到新库 | B/test_feature_inventory.py；B/test_review_runtime.py | 通过 | 只用临时测试库演练；不代表真实 CTP 未决委托灾难恢复完成 |
| F49 | 性能 | 历史/信号/完成订单/成交容量限制，离线回放与前端包体预算 | scripts/benchmark_offline.py；scripts/check_bundle.mjs；相关单测 | 通过限定基准 | 6000 根单合约回放，不代表实盘多合约吞吐或长时间稳定性 |
| F50 | 工程 | Windows CI 定义、依赖锁、静态/类型/单元/浏览器质量门、配置密钥基础检查 | 本轮全部质量门；pip check；scripts/check_secrets.py | 本地通过 | GitHub Actions 未远程触发；未新增全面安全审计；历史授权码仍需所有者核实 |

## 本轮新增测试及修复

- 新增后端 16 项功能验收：策略管理、账户快照、日志查询、7 类技术指标、分页、数据质量、六通道匿名拒绝、四通道首帧、订阅心跳、维护命令及报告格式。
- 新增前端 7 项单测和 3 项浏览器用例：自选/历史/缓存/指标配置、快捷键、100ms 与 600ms 行情告警、系统页、图表交互和不支持周期的错误反馈。
- 修复高频 Tick 令告警检测永久推迟：改为独立记录上次检测时间，账户切换和取消订阅时清理该记录。
- 修复副图选择、均线显示/样式直接编辑未保存：选择器调用持久化操作，均线深层变更同步保存。
- 修复画线清除后图形残留：使用 ECharts graphic 替换语义；浏览器断言实际图形对象数量归零，并复核截图，不再只看按钮状态。

没有修改后端生产逻辑。新增测试运行于独立进程、临时目录和离线网关；真实服务 8000/5174 及其登录会话保留。

## 使用边界与后续事项

1. 连续新 Tick、真实报单/撤单/成交、断线恢复、跨交易日和不同券商仍未完成实盘验收。
2. 公开搜索隔离与账户刷新调度的最后两项既有后端修复仍未装入当前进程；按用户选择保留会话，不要求本轮重启。
3. 任意自定义周期、完整多档盘口核对、所有画线/缩放组合、多账户、期权/组合保证金、自动历史数据下载、跨日正式绩效及自动归档，不列为已完整验收功能。
4. 告警当前为前端阈值提示；成交量计算使用收到的量字段，尚未单独设计逐笔增量的量能模型；声音配置无实际播放链路。
5. Starlette/httpx 弃用及前端大包提示仍存在。浏览器退出 fixture 会话时的 ECONNRESET 与关闭断言一起记录，不作为真实柜台故障。

## 证据与复现

[功能 CSV](FEATURE_INVENTORY_2026-10-08.csv) 与上表一一对应。[接口 CSV](evidence/feature-inventory-20261008/routes.csv) 列出全部 43 个正式注册入口；该表的“处理函数被执行”只说明动态覆盖，不等于每个分支已验收。

[证据目录](evidence/feature-inventory-20261008/) 包含两套后端日志、前端质量与浏览器日志、覆盖率摘要、分组用例数、CLI 回测摘要、离线基准、脱敏健康检查、fixture 截图及 SHA256SUMS。未保存真实账户标识、Cookie、密码、认证码、资金或实际账户保证金率。

复现前先设置 `QUANT_ENV=test`、`PYTHONUTF8=1`，使用项目测试夹具配置。后端可运行 `python -m coverage run --branch --source=src -m pytest`，再运行 `python -m coverage report`；前端运行 `npm run quality`。**有实盘后端运行时不能直接用默认 E2E 端口 8000**：本轮使用上一轮留存的隔离配置，后端 18000、Vite 55174；配置副本见 [live-execution-20261008](evidence/live-execution-20261008/)。
