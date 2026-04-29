---
description:
alwaysApply: true
---

# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

This is a Chinese futures quantitative trading system with a **front-end / back-end separated architecture**:

- **Backend** (`back_end/`): Python 3.13, FastAPI REST API + WebSocket, vn.py CTP gateway for live futures trading
- **Frontend** (`front_end/`): Vue 3 + Vite + Element Plus + ECharts dashboard with real-time WebSocket monitoring

## Running the System

```bash
# Backend
cd back_end
pip install -r requirements.txt
python main.py                          # CLI backtest/live mode
start.bat                               # Launch FastAPI API server (port 8000)

# Frontend
cd front_end
npm install
npm run dev                             # Dev server (port 5173, proxies to :8000)
```

## Architecture

### Backend (`back_end/src/`)

```
src/
├── api/              # FastAPI REST + WebSocket API
│   ├── app.py        # create_app factory + middleware + lifespan
│   ├── schemas.py    # Pydantic request/response models
│   ├── state.py      # TradingState global singleton
│   ├── deps.py       # Shared helpers (audit, serialization, snapshots, hooks)
│   ├── ws.py         # ConnectionManager + WebSocket endpoints + broadcast loops
│   ├── security.py   # Session management (in-memory token store)
│   ├── _constants.py # Preset CTP server lists
│   └── routers/      # Route modules (auth, system, strategy, trading, dashboard, backtest, watch)
├── data/             # DataManager, DatabaseManager (SQLite), DataCache, indicators, governance
├── strategy/         # StrategyBase, registry, types (Signal, Order, Trade, Position, Direction)
│   └── strategies/   # Built-in: ma_cross, rsi, breakout
├── backtest/         # BacktestEngine (event-driven), BacktestConfig, BacktestResult
├── trading/          # TradingEngine, GatewayBase, VnpyGateway, OrderManager, RiskManager
├── analysis/         # RiskAnalyzer, PerformanceAnalyzer, Analyzer, report formatters
├── common/           # Shared exception hierarchy
├── watch/            # search_contracts(), K-line data + technical indicator endpoint
└── observability.py  # Metrics, audit logging, request IDs
```

### Frontend (`front_end/src/`)

```
src/
├── api/index.js        # REST + auth client
├── components/         # Vue components (KlineChart, TradingPanel, OrderBook, etc.)
├── composables/        # WebSocket composables (useWatchWs, useKlineData, etc.)
├── config/network.js   # Centralized API/WS base URLs
├── router/index.js     # Routes: /login, /, /backtest, /system, /kline, /watch
├── stores/             # Pinia stores (auth, chart, watch, indicator)
├── views/              # Page views (Dashboard, Backtest, Kline, Watch, System, Login)
└── workers/            # Web Worker for indicator calculations
```

### Configuration

- **Production config**: `back_end/config/config_production.jsonc` (gitignored, contains real credentials)
- **Example config**: `back_end/config/config_example.jsonc` (uses placeholder credentials)
- **Database**: `back_end/data/historical/quotes.db` (SQLite)

### Data Flow

1. **Backtest Mode**: `DataManager` loads bars → `BacktestEngine` iterates → `Strategy.on_bar()` → `BacktestEngine` executes → `Analyzer` generates report
2. **Live Mode**: `TradingEngine` connects via CTP gateway → receives ticks → `Strategy` processes → `Gateway` sends orders → callbacks update state → WebSocket broadcasts to frontend

### Adding New Strategies

Create a class inheriting from `StrategyBase` in `back_end/src/strategy/strategies/`:
```python
class MyStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get('symbol', 'IF9999')

    def on_bar(self, bar: pd.Series):
        df = self.get_data(self.symbol)
        # ... strategy logic
        self.buy() / self.sell() / self.short() / self.cover()
```

Register in `STRATEGY_REGISTRY` at `back_end/src/strategy/registry.py`.

### Adding New Gateways

Create a class inheriting from `GatewayBase` in `back_end/src/trading/` implementing:
`connect()`, `disconnect()`, `send_order()`, `cancel_order()`, `query_account()`, `query_positions()`

### API Endpoints (30 REST + 6 WebSocket)

- **Auth**: `/auth/login`, `/auth/logout`, `/auth/status`, `/auth/servers`
- **System**: `/health`, `/metrics`, `/audit/events`, `/system/status`, `/system/logs`
- **Strategy**: `/strategies`, `/strategies/{id}`, `/strategies/{id}/params`, `/strategies/weights`, `/strategy/{id}/action`
- **Trading**: `/orders`, `/trades`, `/positions`, `POST /orders`, `DELETE /orders/{id}`, `/orders/cancel-all`, `/positions/{symbol}/close`
- **Dashboard**: `/dashboard/metrics`
- **Backtest**: `/backtest/strategies`, `/backtest/run`
- **Watch**: `/watch/kline`, `/watch/tick`, `/watch/search`, `/watch/kline/cache`
- **WebSocket**: `/ws/system`, `/ws/orders`, `/ws/positions`, `/ws/dashboard`, `/ws/logs`, `/ws/watch`

### Key Patterns

- **Registry Pattern**: Strategies registered in `STRATEGY_REGISTRY`, created via `create_strategy()`
- **Gateway Registry**: Gateways registered in `GATEWAY_REGISTRY`, created via `create_gateway()`
- **Callback Architecture**: Trading gateway updates strategy through callbacks
- **Event-Driven**: Backtest engine processes bars sequentially
- **Dataclass Models**: Signal, Order, Trade, Position, BacktestConfig, BacktestResult
- **Enum Direction**: `Direction.LONG` and `Direction.SHORT`
- **APIRouter**: Each API route group uses FastAPI `APIRouter`, assembled in `app.py`

### Database Schema

SQLite at `back_end/data/historical/quotes.db`:
- `bars` table: symbol, timeframe, datetime, OHLCV, open_interest
- Indexed on (symbol, timeframe, datetime)
