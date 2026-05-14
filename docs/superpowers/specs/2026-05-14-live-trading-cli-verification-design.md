# Live Trading CLI Verification Design

**Date**: 2026-05-14
**Status**: Draft
**Scope**: `back_end/` — live trading engine, bar aggregation, minimal strategy, config

## Goal

Achieve the first end-to-end live auto-trading loop on Chinese futures:
`CTP tick → bar aggregation → strategy signal → risk check → order submission → fill → position update`

Target: **rb2510 (螺纹钢)** on broker simulation environment, 1 lot, verify the full chain then exit.

## Architecture

### Problem

Current live trading engine (`TradingEngine._on_tick`) feeds every raw tick as a "bar" to `strategy.on_bar()`. The strategy expects proper OHLC bars with datetime indices for indicator calculation.

### Solution

Insert a `BarAggregator` between the gateway tick stream and the strategy:

```
CTP Tick → BarAggregator → N-minute completed Bar → strategy.on_bar()
              │
              └── every tick still updates pre-order market data (unchanged)
```

The `BarAggregator`:
- Maintains current incomplete bar O/H/L/C/V in memory per symbol
- When a bar interval completes (default 1min, configurable via `bar_interval_minutes`), emits the bar to the strategy
- Appends bar to `strategy.data[symbol]` before calling `on_bar()`
- Does not affect the existing pre-order tick-level path

### Component Diagram

```
main.py -m live -c config/config_production.json
├── TradingEngine
│   ├── VnpyGateway (CTP)      — unchanged, tick stream
│   ├── BarAggregator (NEW)    — tick → bar conversion
│   ├── RiskManager            — unchanged, pre-order checks
│   ├── OrderManager           — unchanged, order lifecycle
│   └── VerifyStrategy (NEW)   — minimal verification strategy
```

## VerifyStrategy

Minimal strategy to validate the full trading chain. Not designed for profit.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| symbol | rb2510 | Trading instrument |
| warmup_bars | 20 | Bars to accumulate before trading |
| hold_bars | 10 | Bars to hold position before closing |
| volume | 1 | Fixed lot size |
| contract_multiplier | 10 | SHFE rebar multiplier |

### State Machine

```
[Warmup] ── bars >= warmup_bars ──→ [Send Order]
                                        │
                                  1 lot market buy (rb2510)
                                  Log: "验证信号已发出"
                                        │
                                        ▼
                                   [Hold Position]
                                   bar count since entry
                                   on_trade callback logs fill
                                        │
                                  bars >= hold_bars
                                        │
                                        ▼
                              1 lot market sell (close)
                              Log: "验证流程完成"
                                        │
                                        ▼
                                   [Complete]
```

### Lifecycle

- `on_init()` — read params, validate symbol
- `on_start()` — log "策略启动，等待 Bar 预热..."
- `on_bar(bar)` — state machine as above
- `on_order(order)` — log order status changes
- `on_trade(trade)` — log fill info, update position awareness

## Configuration

File: `config/config_production.json`

```json
{
  "mode": "live",
  "strategy": {
    "name": "verify",
    "symbol": "rb2510",
    "warmup_bars": 20,
    "hold_bars": 10,
    "volume": 1,
    "contract_multiplier": 10
  },
  "trading": {
    "gateway": "vnpy",
    "broker_id": "2071",
    "td_server": "tcp://114.94.128.1:42205",
    "md_server": "tcp://114.94.128.1:42213",
    "app_id": "client_TraderMaster_v1.0.0",
    "auth_code": "20260324LHJYMHBG",
    "vnpy_environment": "仿真",
    "bar_interval_minutes": 1,
    "initial_capital": 100000,
    "max_errors": 10
  },
  "risk": {
    "max_position_per_symbol": 1,
    "max_order_volume": 5,
    "max_daily_loss": 5000,
    "max_order_rate": 5,
    "price_deviation_pct": 0.5
  }
}
```

Username and password are entered interactively at CLI startup via `getpass`, not stored in the config file.

## CLI Interaction Flow

```
$ python main.py -m live -c config/config_production.json

============================================================
  量化交易系统 - 实盘交易
============================================================
[INPUT] CTP 账号: <user types>
[INPUT] CTP 密码: <hidden input>
[INFO] 使用交易网关: vnpy
[INFO] vn.py CTP 环境: 仿真 | Broker: 2071
[INFO] 正在连接 CTP...
[INFO] CTP 连接成功
[INFO] 订阅行情: rb2510.SHFE
[INFO] 策略 verify 启动，等待 Bar 预热...

[实时日志输出 tick 计数、Bar 完成、订单状态、成交信息]

[INFO] 验证完成 - 全链路通过
[INFO] 按 Ctrl+C 停止
```

## Verification Checklist

- [ ] CTP connection succeeds, settlement confirmation received
- [ ] Market data subscribed, ticks arriving continuously
- [ ] Bar aggregation produces reasonable O/H/L/C/V values
- [ ] Strategy sends signal after warmup
- [ ] Risk check passes (within volume/price deviation limits)
- [ ] vn.py returns valid order ID
- [ ] Fill callback received (order status PARTIAL/FILLED)
- [ ] Position updated correctly (direction/lots/avg price)
- [ ] Close order flow completes
- [ ] Graceful shutdown: cancel orders → disconnect

## Changes Required

### New Files

| File | Description |
|------|-------------|
| `back_end/src/trading/bar_aggregator.py` | Tick → Bar aggregation |
| `back_end/src/strategy/strategies/verify.py` | Minimal verification strategy |

### Modified Files

| File | Change |
|------|--------|
| `back_end/src/trading/engine.py` | Integrate BarAggregator; keep pre-order tick path |
| `back_end/src/strategy/strategies/__init__.py` | Register VerifyStrategy |
| `back_end/main.py` | Add interactive credential input; wire BarAggregator |

## Out of Scope (Follow-up)

- Frontend live trading UI
- WebSocket reconnection
- Strategy parameter optimization
- Multi-symbol / multi-strategy
- Production risk hardening
- API module split
- K-line module tests
- Frontend component tests
