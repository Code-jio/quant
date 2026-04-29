# 量化交易系统

中国期货量化交易系统，支持回测和实盘交易，前后端分离架构。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.13 · FastAPI · vn.py CTP · SQLite |
| 前端 | Vue 3 · Vite · Element Plus · ECharts · Pinia |
| 测试 | pytest · Vitest · Playwright · ruff · mypy |

## 快速开始

### 后端

```bash
cd back_end
pip install -r requirements.txt

# CLI 回测/实盘模式
python main.py

# 启动 API 服务器 (端口 8000)
start.bat
# 或
python -m uvicorn src.api:create_app --factory --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd front_end
npm install
npm run dev     # 开发服务器 (端口 5173，代理 API 到 :8000)
```

### 配置

复制 `back_end/config/config_example.jsonc` 为 `config_production.jsonc`，填入真实 CTP 账户信息。

## 项目结构

```
├── back_end/                     # Python 后端
│   ├── config/                   # 配置文件（.jsonc）
│   ├── data/historical/          # SQLite 历史行情数据库
│   ├── src/
│   │   ├── api/                  # FastAPI REST + WebSocket API
│   │   │   ├── app.py            # 应用工厂
│   │   │   ├── schemas.py        # Pydantic 模型
│   │   │   ├── state.py          # 全局交易状态
│   │   │   ├── deps.py           # 共享辅助函数
│   │   │   ├── ws.py             # WebSocket 端点
│   │   │   ├── security.py       # 会话管理
│   │   │   └── routers/          # 路由模块
│   │   ├── data/                 # 数据管理 (DataManager, DB, Cache, 指标)
│   │   ├── strategy/             # 策略框架 (基类, 注册, 内置策略)
│   │   ├── backtest/             # 回测引擎 (事件驱动)
│   │   ├── trading/              # 交易引擎 (CTP 网关, 风控)
│   │   ├── analysis/             # 分析模块 (风险, 绩效)
│   │   ├── watch/                # 行情监听 (K线, 合约搜索)
│   │   ├── common/               # 公共异常
│   │   └── observability.py      # 可观测性 (指标, 审计)
│   ├── tests/                    # 测试
│   └── main.py                   # CLI 入口
│
└── front_end/                    # Vue 3 前端
    ├── src/
    │   ├── components/           # UI 组件 (K线图, 交易面板, 订单簿)
    │   ├── composables/          # WebSocket composables
    │   ├── views/                # 页面视图
    │   ├── stores/               # Pinia 状态管理
    │   ├── config/               # 网络配置
    │   └── workers/              # Web Worker (指标计算)
    ├── tests/                    # Vitest + Playwright 测试
    └── package.json
```

## 功能

- **CTP 登录** — 连接期货公司交易/行情前置
- **实时行情** — WebSocket 推送 tick 数据，K 线图 + 技术指标
- **手动交易** — 开仓/平仓/撤单，支持市价/限价
- **策略管理** — 内置 MA 交叉、RSI、突破策略，支持热更新参数
- **回测引擎** — 事件驱动回测，返回资金曲线、交易标记、风险指标、月度热力图
- **风险控制** — 单笔/持仓/频率限制，日内亏损熔断
- **系统监控** — CPU/内存/网络实时监控，结构化日志
- **仪表盘** — PnL、夏普比率、最大回撤、权益曲线

## 内置策略

| 策略 | 说明 | 参数 |
|------|------|------|
| `ma_cross` | 双均线金叉/死叉 | fast_period, slow_period |
| `rsi` | RSI 均值回归 | rsi_period, oversold, overbought |
| `breakout` | N 日高低点突破 | lookback_period |
