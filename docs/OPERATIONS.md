# 单实例运行与恢复

## 配置与启动

- API 唯一入口 `src.api:create_app`；`server.py` 强制单 worker、无 reload，默认 loopback。
- `QUANT_ENV=production` 开启生产会话 Cookie 默认 Secure，须通过 HTTPS 反向代理；不要仅改成公网监听。反向代理仅允许受控访问，支持 WebSocket，配置可信域名。
- `QUANT_CORS_ORIGINS` 使用逗号分隔的精确前端 Origin；禁止通配符配合 Cookie。前后端同域代理优先。
- `QUANT_BIND_HOST`、`QUANT_PORT` 控制绑定；`QUANT_INSTANCE_LOCK` 必须指向所有同账户执行进程共享的本机锁文件。不同工作目录或机器没有分布式执行锁，需要部署层保证唯一执行者。
- `QUANT_LEDGER_PATH` 为执行账本，默认 `data/runtime/execution.db`；审计默认 `data/runtime/audit.db`。历史库 `data/historical/quotes.db`。
- Web/API 读取 `back_end/config/config_production.json`（或 `QUANT_CTP_CONFIG`）的 `trading` 连接配置；`QUANT_CTP_*` 环境变量优先，非空登录字段可再次覆盖。默认实盘 API；认证码不返回浏览器，仅返回是否已配置。只需账号、密码，不在登录页填写合约或启动策略。文件损坏/显式路径不存在时返回脱敏的 503 错误，不能静默换用另一套配置。
- 开仓要求已验证合约乘数、最小手数、最小变动价位和保证金。单腿期货默认通过 CTP 查询当前账户投机保证金；包含多空比例和按手金额，相对值会加上交易所基准。`GET /trading/margin-rate?symbol=au2612` 需当前登录会话，状态为 pending、ready 或 error。查询按网关队列限速，发送后 10 秒超时，错误状态 30 秒后可重试；不影响登录，也不发送委托。
- 柜台保证金缓存 30 分钟，到期清除旧值并排队刷新；账户切换、交易断线、柜台交易日变化后重新核验。本地 `trading.contract_margin_rates` 或显式登录 API 参数仅作为维护者按账户/交易日确认的备用配置；查询成功后以柜台值为准，失效时不会恢复旧配置。无有效保证金或合约元数据时拒绝开仓。公开合约搜索不返回账户保证金字段，带 Cookie 也不例外。

同一电脑的不同浏览器可用同一账户密码及连接配置登录，共享一个 CTP 执行器。“退出当前端”只注销该浏览器，最后一端退出后后台策略也继续运行；“断开全部终端”才关闭共享连接、停止引擎并撤销全部会话，不会自动撤销柜台已有委托。会话 24 小时过期；外部改密后需断开全部并重新登录刷新内存校验。参见[多端登录与启用步骤](MULTI_DEVICE_LOGIN_2026-10-08.md)。

## 备份和恢复演练

在 `back_end` 目录，使用 `.venv/Scripts/python`：

```powershell
.venv/Scripts/python -m src.maintenance backup data/runtime/execution.db backups/execution-20261005.db
.venv/Scripts/python -m src.maintenance backup data/runtime/audit.db backups/audit-20261005.db
# 停止执行服务，恢复到一个不存在的新路径，不覆盖原数据库
.venv/Scripts/python -m src.maintenance restore backups/execution-20261005.db data/runtime/restored-execution.db
$env:QUANT_LEDGER_PATH = 'data/runtime/restored-execution.db'
.venv/Scripts/python server.py
```

备份使用 SQLite backup API，涵盖 WAL 已提交内容并检查完整性。恢复工具获取本机执行锁，目标必须为新文件；旧库保留可回滚。测试已覆盖账本基线的备份恢复，但没有演练真实 CTP 委托的灾难恢复。`backups/` 也必须放在受控目录；备份包含账户及成交记录。

重启后会话失效，必须重新登录。历史成交按账户/交易日/交易所/成交 ID 去重；历史委托加载但未收到活动委托回放时保持阻塞。报单 intent 结果不确定时禁止自动重发。当前没有安全的自动清除未知 intent 功能；应先从柜台取得对应委托/成交凭证，再由维护者做受审计的数据修复，不可直接删除账本绕过门禁。

## 非交易时段与真实环境验收

Windows 原生运行时使用 `back_end/.venv-live`（Python 3.13）和 `requirements-live.lock`。`start-live.bat` 可启动原生后端；`scripts/check_native_ctp.py --fronts` 仅核验 DLL 加载、回调适配及默认实盘前置连接，不发送账户认证或交易请求。2026-10-08 已验证本机原生加载、MD/TD 前置连接以及用户在本地完成的一次真实登录，当前连接与只读接口正常；后续仍需按下列清单验证持续行情、断线恢复和真实柜台数据一致性。

休市时无法登录、无新行情、查询超时应作为环境状态记录。不能把最后回报距今当作网络 RTT，也不能凭日志文本判断就绪。真实账户验收应在合适时段依次验证：

1. 连接、结算确认、合约/资金/持仓完整快照，核对可用资金、已用保证金、费用、今昨仓。
2. MD 与 TD 独立断线、重连、空持仓查询和账户切换后旧会话失效。
3. 在专用测试账户核对限价、撤单后迟到成交、部分成交、撤改单、平今/平昨、重复成交回放。
4. 比较两个交易日的日内损失基线、重启后活动委托、未确认报单阻塞和急停恢复。
5. 核验品种交易时段、夜盘归属、节假日、跨夜小时线。当前日线使用 TradingDay，但小时聚合采用时钟桶，并非完整交易所分节日历。

本次已执行真实前置连接并由用户完成账户登录；2026-10-08 用户重启后的服务已取得 `au2612` 的账户保证金结果，见[验证记录](CTP_MARGIN_QUERY_2026-10-08.md)。尚未执行下单、撤单或完整跨日/断线演练。手续费和成交实现盈亏若原始回报未提供，会显示未知，不能拿 0 代替柜台事实。

## 保留策略与监测

内存历史 bar 2000、策略信号 1000、近期成交 500、已完成订单 2000；活动订单不因展示窗口而丢弃。SQLite 执行和审计表目前保留全部历史，需由运维按容量做备份、离线归档；尚未实现自动分区和归档。

`/health` 表示服务存活；`/risk/status`、`/trading/reconcile` 提供执行就绪信息。监控仅持久化分钟权益，尚未提供跨日正式绩效；样本不足的实时夏普显示未知。性能报告中的回放为本机单合约路径，不能推断真实高频柜台容量。

账户切换会清空内存权益窗口并丢弃旧账户代次的快照和排队广播。订单页面快照失败时会继续应用已收到的增量，保留过期标记，并按 1/2/4 秒最多重试 3 次；仍失败时需检查连接并手动刷新。无效风控参数返回 422，当前配置和持久配置保持不变。

## 凭据

真实 JSON 配置、环境文件、CTP 流和数据库不得提交。`scripts/check_secrets.py` 只检查已跟踪配置与私钥标记，是基础门禁，不替代全面密钥扫描。历史三处授权码归属和有效性仍需所有者核实；若是有效非公开凭据，先轮换，再评估 Git 历史清理。
