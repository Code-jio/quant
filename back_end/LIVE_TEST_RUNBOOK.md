# 券商测试账号实盘试运行清单

本清单用于 SimNow/券商测试账号或小资金实盘的人工盯盘试运行。

## 启动前

1. 设置 CTP 环境变量或复制 `config/config.example.json` 为本地配置，生产敏感信息不要提交到 Git。
2. 登录前确认风控：
   - `max_order_volume`: 单笔最大手数
   - `max_position_volume`: 单合约最大持仓
   - `max_order_value`: 单笔名义金额上限
   - `max_position_value`: 单合约名义持仓上限
   - `max_price_deviation`: 限价相对最新价最大偏离
   - `duplicate_signal_window_seconds`: 重复信号冷却窗口
   - `allowed_symbols`: 建议测试时只放行 1-3 个合约
3. 登录后打开仪表盘，确认风险状态不是急停，账户、持仓、活跃委托数量与券商端一致。

## 盘中

- 遇到异常信号、连续拒单、网络抖动或人工不确定时，先点“急停”。
- 急停会阻止后续手动和策略下单，并按请求撤销活跃委托。
- 解除急停前，先调用 `/trading/reconcile` 或在前端面板确认账户/委托/持仓快照。

## API

- `GET /risk/status`: 查看当前运行时风控。
- `PUT /risk/config`: 更新运行时风控。
- `POST /risk/emergency-stop`: 开启急停，可选撤单/停策略。
- `POST /risk/resume`: 解除急停。
- `GET /trading/reconcile`: 账户、委托、持仓、策略状态对账快照。

## 第一阶段建议

- 只允许一个合约，`max_order_volume` 设为 1。
- 先禁用自动启动策略，只做手动下单/撤单/平仓验证。
- 再开启一个策略，连续观察至少 1 个完整交易日。
