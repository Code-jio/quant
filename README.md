# 期货量化交易系统 — 项目文档

## 一、系统架构总览

┌──────────────────────────────────────────────────────────┐
│                    期货量化交易系统                        │
├──────────┬──────────┬───────────┬───────────┬────────────┤
│ 数据层    │ 策略层   │ 回测层     │ 执行层     │ 监控层     │
│          │          │           │           │            │
│ Tick/Bar │ 因子计算  │ 历史回测   │ CTP交易网关│ 风控模块   │
│ 合约管理  │ 信号生成  │ 绩效归因   │ 订单路由   │ 持仓监控   │
│ 主力换月  │ 仓位计算  │ 滑点模拟   │ 模拟/实盘  │ 盈亏告警   │
└──────────┴──────────┴───────────┴───────────┴────────────┘

## 二、项目结构

quant-trading-system/
├── config/                  # 配置文件
│   ├── settings.yaml        # 全局配置（数据库连接、API密钥等）
│   └── contracts.yaml       # 合约信息表（乘数、保证金率、手续费）
├── data/
│   ├── fetcher.py           # 数据采集（Tick / K线）
│   ├── cleaner.py           # 数据清洗（夜盘时间归属、异常值处理）
│   ├── storage.py           # 数据存储（时序数据库读写）
│   └── contract_manager.py  # 主力合约换月 & 价格复权
├── strategy/
│   ├── base.py              # 策略基类（统一接口）
│   ├── factors.py           # 因子计算
│   ├── cta_strategy.py      # CTA趋势策略（均线、通道突破等）
│   ├── spread_strategy.py   # 套利策略（跨期、跨品种）
│   ├── ml_strategy.py       # 机器学习策略
│   └── signals.py           # 信号生成 & 仓位计算
├── backtest/
│   ├── engine.py            # 回测引擎
│   ├── analyzer.py          # 绩效分析（夏普、Calmar、最大回撤）
│   └── visualizer.py        # 回测可视化（K线 + 信号标注 + 净值曲线）
├── execution/
│   ├── gateway.py           # CTP 交易网关封装
│   ├── order_manager.py     # 订单管理（下单、撤单、状态追踪）
│   ├── position_manager.py  # 持仓管理
│   └── risk.py              # 风控模块
├── monitor/
│   ├── logger.py            # 日志记录
│   ├── alert.py             # 告警推送（钉钉/企业微信）
│   └── dashboard.py         # Web 仪表盘（FastAPI）
├── notebooks/               # Jupyter 策略研究笔记
├── tests/                   # 单元测试
├── main.py                  # 系统入口
└── requirements.txt         # 依赖清单

```
---

## 三、技术选型与依赖库

### 3.1 数据层

| 用途 | 推荐方案 | 说明 |
|------|---------|------|
| 实时行情 | CTP 接口（`vnpy_ctp`） | 期货公司提供的标准接口，Tick 级数据 |
| 历史数据 | `tqsdk` / `akshare` / vnpy 自带录制 | tqsdk 历史数据质量好，免费版有限制 |
| 数据存储 | `InfluxDB` / `PostgreSQL + TimescaleDB` | Tick 数据量大，时序数据库是刚需 |
| 内存缓存 | `Redis` | 实时行情分发、最新持仓缓存 |
| 数据处理 | `pandas` / `polars` / `numpy` | 日常计算 |

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
