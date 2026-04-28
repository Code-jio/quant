---
description: 
alwaysApply: true
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a quantitative trading system built in Python that supports both backtesting and live trading modes. The system is modular and follows a clean architecture with separated concerns for data management, strategy execution, backtesting, trading, and analysis.

## Running the System

```bash
# Install dependencies
pip install -r requirements.txt

# Run backtest (default mode)
python main.py

# Run live trading simulation
python main.py
# Note: Set "mode": "live" in config/config.json or change in main.py
```

## Architecture

### Core Components

1. **Data Module** ([`src/data/`](src/data/__init__.py))
   - `DataManager`: Unified interface for data access with caching
   - `DatabaseManager`: SQLite-based storage for historical data
   - `DataSource`: Abstract base for implementing real data feeds
   - Technical indicator calculation support

2. **Strategy Module** ([`src/strategy/`](src/strategy/__init__.py))
   - `StrategyBase`: Abstract base class for all strategies
   - Defines lifecycle: `on_init()`, `on_bar()`, `on_start()`, `on_stop()`, `on_order()`, `on_trade()`
   - Core trading methods: `buy()`, `sell()`, `short()`, `cover()`
   - Built-in strategies: `MACrossStrategy`, `RSIStrategy`, `BreakoutStrategy`
   - `STRATEGY_REGISTRY`: Registry pattern for strategy factory
   - Key data structures: `Signal`, `Order`, `Trade`, `Position`

3. **Backtest Module** ([`src/backtest/`](src/backtest/__init__.py))
   - `BacktestEngine`: Event-driven backtesting engine
   - Processes bars sequentially, generates orders, tracks positions
   - Handles margin requirements (12% default), commissions, and slippage
   - Calculates performance metrics: Sharpe ratio, max drawdown, win rate

4. **Trading Module** ([`src/trading/`](src/trading/__init__.py))
   - `GatewayBase`: Abstract gateway for broker connections
   - `VnpyGateway`: vn.py CTP gateway adapter for test/live broker connections
   - `TradingEngine`: Orchestrates live trading execution
   - Callback-based architecture: `on_order`, `on_trade`, `on_position`, `on_account`, `on_tick`

5. **Analysis Module** ([`src/analysis/`](src/analysis/__init__.py))
   - `RiskAnalyzer`: VaR, CVaR, drawdown, Sharpe/Sortino/Calmar ratios
   - `PerformanceAnalyzer`: Win rate, profit/loss ratio, consecutive trades
   - `Analyzer`: Combined analysis with report generation

### Configuration

Configuration is loaded from `config/config.json`:
- `mode`: "backtest" or "live"
- `backtest`: Date range, capital, commission/slippage/margin rates
- `strategy`: Name, symbol, strategy-specific parameters
- `trading`: Gateway type, initial capital, risk parameters

### Data Flow

1. **Backtest Mode**: `DataManager` loads bars → `BacktestEngine` iterates bars → `Strategy.on_bar()` generates signals → `BacktestEngine` executes orders → `Analyzer` generates report
2. **Live Mode**: `TradingEngine` connects → `Gateway` receives ticks → `Strategy` processes data → `Gateway` sends orders → Callbacks update strategy

### Adding New Strategies

Create a class inheriting from `StrategyBase`:
```python
class MyStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get('symbol', 'IF9999')
        # ... other params

    def on_bar(self, bar: pd.Series):
        df = self.get_data(self.symbol)
        # ... strategy logic
        # Use self.buy(), self.sell(), self.short(), self.cover() to generate signals
```

Register in `STRATEGY_REGISTRY` at bottom of [`src/strategy/__init__.py`](src/strategy/__init__.py#L404)

### Adding New Gateways

Create a class inheriting from `GatewayBase` implementing:
- `connect()`, `disconnect()`
- `send_order()`, `cancel_order()`
- `query_account()`, `query_positions()`, `query_orders()`
- Use callback methods: `on_order()`, `on_trade()`, `on_position()`, `on_account()`, `on_tick()`

### Key Patterns

- **Registry Pattern**: Strategies are registered in `STRATEGY_REGISTRY` and created via `create_strategy()` factory function
- **Callback Architecture**: Trading gateway updates strategy through callbacks
- **Event-Driven**: Backtest engine processes bars sequentially with signal generation and execution
- **Dataclass Models**: `Signal`, `Order`, `Trade`, `Position`, `BacktestConfig`, `BacktestResult` use dataclasses for clean models
- **Enum Direction**: Use `Direction.LONG` and `Direction.SHORT` consistently throughout

### Database Schema

SQLite at `data/historical/quotes.db`:
- `bars` table: symbol, timeframe, datetime, OHLCV, open_interest
- Indexed on (symbol, timeframe, datetime)
