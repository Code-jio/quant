# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quantitative trading system with a Python backend (FastAPI + vn.py/CTP) and a Vue 3 frontend (Vite + Element Plus + ECharts). Supports backtesting and live CTP futures trading.

## Project Structure

```
back_end/               # Python backend
  main.py               # CLI entry point (backtest/live modes)
  src/
    api/                # FastAPI app: REST + WebSocket + auth
    backtest/           # Backtest engine, config, result
    common/             # Exception handling, retry, circuit breaker
    data/               # SQLite storage, cache, indicators, governance
    strategy/           # Strategy base, registry, built-in strategies
    trading/            # Trading engine, gateway, risk, order management
    analysis/           # Risk & performance analysis, report generation
    watch/              # Contract search, K-line data + indicators
    observability.py    # Metrics, audit logging
    settings.py         # Env-based config, risk defaults
  tests/                # Pytest tests (10 files)
  config/
    config.example.json
front_end/              # Vue 3 frontend
  src/
    views/              # Dashboard, Login, Kline, Watch, Backtest, System
    components/         # KlineChart, StrategyPanel, OrderBook, etc.
    composables/        # WebSocket hooks, search, data fetching
    stores/             # Pinia stores (auth, watch, chart, history)
    router/             # Vue Router config
```

## Running the System

```bash
# Backend — CLI backtest
cd back_end && python main.py

# Backend — API server
cd back_end && uvicorn src.api:create_app --factory --host 0.0.0.0 --port 8000

# Frontend — dev server
cd front_end && npm run dev
```

## Architecture

### Core Components

1. **Data Module** (`back_end/src/data/`)
   - `DataManager`: Unified data access with cache
   - `DatabaseManager`: SQLite storage with persistent connection (WAL mode)
   - `DataCache`: In-memory TTL cache with LRU eviction
   - `indicators.py`: Authoritative RSI/MACD/KDJ/Bollinger — all modules import from here

2. **Strategy Module** (`back_end/src/strategy/`)
   - `StrategyBase`: Abstract base with lifecycle (`on_init`, `on_start`, `on_bar`, `on_stop`, `on_order`, `on_trade`)
   - `buy()`, `sell()`, `short()`, `cover()` — generate signals
   - `STRATEGY_REGISTRY` + `create_strategy()` factory
   - Built-in: `MACrossStrategy`, `RSIStrategy`, `BreakoutStrategy`
   - Precomputes indicators in `on_start()` (not per-bar)

3. **Backtest Module** (`back_end/src/backtest/`)
   - `BacktestEngine`: Signal queued for next-bar-open execution to eliminate look-ahead bias
   - `BacktestConfig`: commission, slippage, margin, contract_multiplier
   - `BacktestResult`: equity curve, daily returns, trade markers
   - Engine is single source of truth for positions; strategy delegates

4. **Trading Module** (`back_end/src/trading/`)
   - `GatewayBase` → `VnpyGateway`: vn.py CTP adapter
   - `TradingEngine`: live execution orchestration
   - `RiskManager`: pre-order checks (volume, rate, daily loss, price deviation)
   - `OrderManager`: orders + pre-orders (stop/limit/trailing)

5. **API Module** (`back_end/src/api/`)
   - FastAPI + WebSocket: system/dashboard/orders/positions/logs/watch
   - Session-based auth (in-memory, 24h TTL)
   - Manual trading, emergency stop, risk config endpoints

### Key Patterns

- **Signal queue for next-bar execution**: Backtest generates signals on bar N, executes at bar N+1 open
- **Contract multiplier in position sizing**: `volume = capital * ratio / (price * multiplier)`
- **Single indicator source**: All RSI/MACD/KDJ calc from `data/indicators.py`
- **WAL-mode SQLite**: Persistent connection, better read concurrency
- **Registry pattern**: `create_strategy(name, params)` factory
- **Callback architecture**: Trading gateway → strategy callbacks
- **Dataclass models**: `Signal`, `Order`, `Trade`, `Position`

### Indicator Reference

```python
from src.data.indicators import calc_rsi, calc_macd, calc_kdj, calc_bollinger, calc_ma, calc_ema
# All use EWM-based RSI (Wilder's smoothing), consistent with TradingView/vnpy
```

### Database Schema

SQLite at `back_end/data/historical/quotes.db`:
- `bars` table: symbol, timeframe, datetime, OHLCV, open_interest, data_source, adjustment, rollover_rule, ingested_at
- `bar_metadata` table: per-symbol/timeframe governance info
- `schema_migrations` table: version tracking
- Indexed on (symbol, timeframe, datetime)
