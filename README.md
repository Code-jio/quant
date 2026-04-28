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
pip install akshare tqsdk pandas numpy polars influxdb-client redis
```

### 3.2 策略层

| 用途     | 推荐方案               | 说明                                     |
| -------- | ---------------------- | ---------------------------------------- |
| 技术指标 | `pandas-ta` / `ta-lib` | CCI、ATR、布林带等期货常用指标           |
| 机器学习 | `lightgbm` / `xgboost` | 截面因子预测、趋势分类                   |
| 深度学习 | `pytorch`              | LSTM / Transformer 时序预测              |
| 统计模型 | `statsmodels`          | 协整检验（跨品种套利）、GARCH 波动率建模 |

```bash
pip install pandas-ta scikit-learn lightgbm torch statsmodels
```

### 3.3 回测层

| 用途     | 推荐方案                   | 说明                                    |
| -------- | -------------------------- | --------------------------------------- |
| 回测引擎 | `vnpy` 自带 / `backtrader` | vnpy 对期货支持最好，保证金、手续费内置 |
| 绩效分析 | `quantstats` / `empyrical` | 夏普、Calmar、最大回撤等                |
| 可视化   | `plotly`                   | K线 + 信号标注 + 净值曲线               |

```bash
pip install vnpy vnpy_ctp backtrader quantstats plotly
```

### 3.4 执行层

| 用途     | 推荐方案           | 说明                              |
| -------- | ------------------ | --------------------------------- |
| 交易网关 | `vnpy_ctp`         | 国内期货标准接口                  |
| 模拟盘   | SimNow（上期技术） | 免费 CTP 模拟环境，与实盘接口一致 |
| 备选框架 | `tqsdk`            | 封装更简洁，适合快速验证          |
| 任务调度 | `APScheduler`      | 盘前初始化、收盘结算等定时任务    |

```bash
pip install vnpy vnpy_ctp apscheduler
```

### 3.5 监控层

| 用途     | 推荐方案                 | 说明                     |
| -------- | ------------------------ | ------------------------ |
| 日志     | `loguru`                 | 记录每笔订单、信号、异常 |
| 告警     | 钉钉 / 企业微信机器人    | 开仓、平仓、异常实时推送 |
| Web 面板 | `FastAPI` + `Vue`        | 查看持仓、净值、策略状态 |
| 系统监控 | `Grafana` + `Prometheus` | 系统级指标监控           |

```bash
pip install loguru fastapi uvicorn
```

---

## 四、期货特有注意事项

### 4.1 数据处理

- **主力合约换月** — 需做价格复权（比值复权或差值复权），否则回测结果失真
- **夜盘数据** — 夜盘时间段归属下一个交易日，时间戳处理需特别注意
- **涨跌停板** — 涨跌停时挂单无法成交，回测中需模拟此限制
- **合约信息维护** — 不同品种的合约乘数、保证金率、手续费差异大，需维护合约信息表

### 4.2 手续费模型

期货有两种收费方式，必须分别处理：

```python
# 1. 按手数固定收费：如玉米 1.2 元/手
# 2. 按成交额比例收费：如螺纹钢 万分之一
```

### 4.3 滑点模拟

```python
# 活跃品种（螺纹、原油）：1 跳滑点
# 不活跃品种（纤维板等）：2-3 跳甚至更多
```

### 4.4 保证金计算

```python
# 实际可用资金 = 总资金 - 占用保证金
# 不能像股票那样简单用"持仓市值"衡量
```

---

## 五、核心风控规则

```python
RISK_RULES = {
    "max_position_per_symbol": 10,      # 单品种最大持仓手数
    "max_margin_ratio": 0.6,            # 最大保证金占用比例（60%）
    "daily_loss_limit": -0.03,          # 单日亏损 3% 触发熔断
    "max_order_per_minute": 20,         # 每分钟最大下单次数（防止程序 bug 疯狂下单）
    "price_deviation_limit": 0.02,      # 委托价偏离最新价超过 2% 拒绝下单
}
```

---

## 六、期货常见策略类型

| 策略类型      | 说明                            | 难度       |
| ------------- | ------------------------------- | ---------- |
| CTA 趋势跟踪  | 双均线、海龟、ATR 通道突破      | ⭐ 入门首选 |
| 截面动量/反转 | 多品种轮动                      | ⭐⭐         |
| 跨期套利      | 同品种近远月价差回归            | ⭐⭐         |
| 跨品种套利    | 相关品种协整关系（如豆粕/菜粕） | ⭐⭐⭐        |
| 日内高频      | Tick 级别，对延迟要求高         | ⭐⭐⭐⭐       |

---

## 七、CTP 接入流程

1. 在期货公司开户（推荐选支持 CTP 的，如中信、永安、国投安信等）
2. 申请 CTP 程序化交易权限
3. 使用模拟环境调试交易逻辑
4. 模拟盘稳定运行后切换至实盘

---

## 八、开发路线

| 阶段      | 周期     | 目标                                                       |
| --------- | -------- | ---------------------------------------------------------- |
| 第 1 周   | 数据层   | akshare/tqsdk 拉取期货历史数据 → 存入数据库 → 处理主力换月 |
| 第 2 周   | 回测层   | 用 backtrader/vnpy 写一个双均线 CTA 策略 → 跑通回测        |
| 第 3 周   | 回测优化 | 加入手续费、滑点、保证金的真实模拟 → 绩效分析              |
| 第 4 周   | 执行层   | 注册 SimNow → vnpy_ctp 接入模拟盘 → 跑通下单流程           |
| 第 5 周   | 监控层   | 风控模块 + 钉钉告警                                        |
| 第 6 周起 | 策略迭代 | 引入 ML、多品种、套利等高级策略                            |

---

## 九、最小起步方案

快速跑通验证，只需：

```bash
pip install vnpy pandas pandas-ta plotly loguru
```

策略成熟后再迁移到 `vnpy` + CTP 做正式实盘系统。

---

## 十、完整依赖清单（requirements.txt）

```txt
# 数据层
akshare
tqsdk
pandas
numpy
polars
influxdb-client
redis
sqlalchemy
psycopg2-binary

# 策略层
pandas-ta
scikit-learn
lightgbm
torch
statsmodels

# 回测层
backtrader
quantstats
plotly

# 执行层
vnpy
vnpy_ctp
apscheduler

# 监控层
loguru
fastapi
uvicorn
requests
```

