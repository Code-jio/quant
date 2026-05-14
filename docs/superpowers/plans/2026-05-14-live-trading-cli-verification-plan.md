# Live Trading CLI Verification — Implementation Plan

> **Execution note:** Default to local execution with `executing-plans`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First end-to-end live auto-trading loop on CTP simulation: tick → bar → strategy signal → risk check → order → fill → position update on rb2510.

**Architecture:** Insert BarAggregator between gateway tick stream and strategy.on_bar(). VerifyStrategy runs a minimal buy-hold-sell cycle. Credentials entered interactively via getpass, rest from config file.

**Tech Stack:** Python 3.13, vn.py + vnpy_ctp, pandas, stdlib getpass

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `back_end/src/trading/bar_aggregator.py` | Tick → OHLC bar aggregation per symbol |
| Create | `back_end/src/strategy/strategies/verify.py` | Minimal buy-hold-sell verification strategy |
| Modify | `back_end/src/strategy/strategies/__init__.py` | Export VerifyStrategy |
| Modify | `back_end/src/strategy/registry.py` | Register verify strategy |
| Modify | `back_end/src/strategy/__init__.py` | Export VerifyStrategy from package |
| Modify | `back_end/src/trading/engine.py` | Integrate BarAggregator into _on_tick |
| Modify | `back_end/main.py` | Interactive credential input via getpass |
| Create | `back_end/config/config_production.json` | CTP simulation config with broker credentials |

---

### Task 1: BarAggregator — Tick to OHLC Bar Conversion

**Files:**
- Create: `back_end/src/trading/bar_aggregator.py`

- [ ] **Step 1: Write BarAggregator class**

```python
"""
Bar aggregator — converts tick stream into OHLC bars per symbol.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BarAggregator:
    """Aggregate ticks into N-minute OHLC bars per symbol.

    Keeps an in-progress bar for each symbol.  When a bar interval
    completes, emits the finished bar via the on_bar callback and
    starts a new bar for the next interval.
    """

    def __init__(
        self,
        interval_minutes: int = 1,
        on_bar: Callable[[str, pd.Series], None] = None,
    ) -> None:
        self.interval_minutes = interval_minutes
        self.on_bar = on_bar
        self._current: Dict[str, Dict[str, Any]] = {}

    def push(self, tick: Any) -> Optional[pd.Series]:
        """Push a tick into the aggregator.

        If the tick completes a bar, returns the finished bar.
        Otherwise returns None.
        """
        symbol = tick.symbol
        ts = tick.timestamp

        bar_start = self._bar_start(ts)

        if symbol not in self._current:
            self._current[symbol] = self._new_bar(symbol, bar_start)

        current = self._current[symbol]

        # New interval started — emit previous bar and start fresh
        if bar_start > current["bar_start"]:
            finished = self._finish_bar(current)
            self._current[symbol] = self._new_bar(symbol, bar_start, tick)
            if self.on_bar:
                self.on_bar(symbol, finished)
            return finished

        # Update O/H/L/C/V for current bar
        price = float(tick.last_price)
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        current["volume"] += int(getattr(tick, "volume", 0) or 0)

        return None

    def _bar_start(self, ts: datetime) -> int:
        """Floor timestamp to bar interval boundary (Unix seconds)."""
        epoch = ts.timestamp() if hasattr(ts, "timestamp") else ts
        if isinstance(ts, datetime):
            epoch = ts.timestamp()
        else:
            epoch = float(ts)
        interval_s = self.interval_minutes * 60
        return int(epoch // interval_s) * interval_s

    def _new_bar(
        self,
        symbol: str,
        bar_start: int,
        tick: Any = None,
    ) -> Dict[str, Any]:
        price = float(tick.last_price) if tick else 0.0
        vol = int(getattr(tick, "volume", 0) or 0) if tick else 0
        return {
            "symbol": symbol,
            "bar_start": bar_start,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": vol,
        }

    def _finish_bar(self, state: Dict[str, Any]) -> pd.Series:
        dt = datetime.fromtimestamp(state["bar_start"] + self.interval_minutes * 60)
        return pd.Series({
            "symbol": state["symbol"],
            "datetime": dt,
            "open": state["open"],
            "high": state["high"],
            "low": state["low"],
            "close": state["close"],
            "volume": state["volume"],
        })

    def flush(self, symbol: str) -> Optional[pd.Series]:
        """Emit current incomplete bar (used on shutdown)."""
        if symbol in self._current:
            bar = self._finish_bar(self._current.pop(symbol))
            if self.on_bar:
                self.on_bar(symbol, bar)
            return bar
        return None
```

- [ ] **Step 2: Write test for BarAggregator**

```python
# tests/test_bar_aggregator.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

from src.trading.bar_aggregator import BarAggregator


def _tick(symbol, ts, price, volume=10):
    """Build a minimal tick-like object for testing."""
    return MagicMock(
        symbol=symbol,
        timestamp=ts,
        last_price=price,
        volume=volume,
    )


def test_single_bar_aggregation():
    """Ticks within the same interval update the same bar."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    assert agg.push(_tick("rb2510", t0, 3128)) is None
    assert agg.push(_tick("rb2510", t0.replace(second=15), 3132)) is None
    assert agg.push(_tick("rb2510", t0.replace(second=45), 3126)) is None

    current = agg._current["rb2510"]
    assert current["open"] == 3128
    assert current["high"] == 3132
    assert current["low"] == 3126
    assert current["close"] == 3126
    assert current["volume"] == 30


def test_bar_completion_emits():
    """Tick past the interval boundary emits a completed bar."""
    emitted = []
    agg = BarAggregator(interval_minutes=1, on_bar=lambda s, b: emitted.append((s, b)))
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    result = agg.push(_tick("rb2510", t0.replace(minute=31), 3135))

    assert len(emitted) == 1
    sym, bar = emitted[0]
    assert sym == "rb2510"
    assert bar["open"] == 3128
    assert bar["close"] == 3125  # close was set by last tick of previous bar
    # New bar started
    assert agg._current["rb2510"]["open"] == 3135


def test_bar_boundary_exact():
    """Tick exactly at bar boundary starts new bar."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    # 9:31:00 is a new bar boundary
    agg.push(_tick("rb2510", t0.replace(minute=31, second=0), 3130))

    assert agg._current["rb2510"]["open"] == 3130
    assert agg._current["rb2510"]["bar_start"] == int(t0.replace(minute=31).timestamp())


def test_multi_symbol():
    """Two symbols aggregate independently."""
    agg = BarAggregator(interval_minutes=1)
    t0 = datetime(2026, 5, 14, 9, 30, 0, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.push(_tick("MA509", t0, 2450))

    assert len(agg._current) == 2
    assert agg._current["rb2510"]["open"] == 3128
    assert agg._current["MA509"]["open"] == 2450


def test_flush_emits_incomplete_bar():
    """flush() emits the current bar regardless of interval completion."""
    emitted = []
    agg = BarAggregator(interval_minutes=1, on_bar=lambda s, b: emitted.append((s, b)))
    t0 = datetime(2026, 5, 14, 9, 30, 15, tzinfo=timezone.utc)

    agg.push(_tick("rb2510", t0, 3128))
    agg.flush("rb2510")

    assert len(emitted) == 1
    assert agg._current.get("rb2510") is None
```

- [ ] **Step 3: Run BarAggregator tests**

Run: `pytest tests/test_bar_aggregator.py -v`
Expected: 5 tests PASS

- [ ] **Step 4: Commit**

```bash
git add back_end/src/trading/bar_aggregator.py tests/test_bar_aggregator.py
git commit -m "feat: add BarAggregator for tick-to-bar conversion"
```

---

### Task 2: VerifyStrategy — Minimal Buy-Hold-Sell

**Files:**
- Create: `back_end/src/strategy/strategies/verify.py`

- [ ] **Step 1: Write VerifyStrategy**

```python
"""
VerifyStrategy — minimal verification strategy for live trading chain.
"""

import logging

import pandas as pd
from ..base import StrategyBase

logger = logging.getLogger(__name__)


class VerifyStrategy(StrategyBase):
    """Minimal strategy to validate the full live trading chain.

    State machine:
      Warmup (bars < warmup_bars) → Buy 1 lot → Hold (hold_bars) → Sell → Done
    """

    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2510")
        self.warmup_bars = int(self.params.get("warmup_bars", 20))
        self.hold_bars = int(self.params.get("hold_bars", 10))
        self._volume = int(self.params.get("volume", 1))
        self._multiplier = int(self.params.get("contract_multiplier", 10))

        self._bar_count = 0
        self._bars_since_entry = 0
        self._bought = False
        self._closed = False
        self._entry_price = 0.0

        self._initialized = True
        logger.info(
            "VerifyStrategy 初始化: symbol=%s warmup=%d hold=%d volume=%d",
            self.symbol, self.warmup_bars, self.hold_bars, self._volume,
        )

    def on_start(self):
        logger.info("策略启动，等待 Bar 预热... (需 %d 根 bar)", self.warmup_bars)

    def on_bar(self, bar: pd.Series):
        self._bar_count += 1
        symbol = bar.get("symbol", self.symbol)
        close = float(bar["close"])
        vol_so_far = int(bar.get("volume", 0))

        pos = self.get_position(symbol)

        if self._bought and not self._closed:
            self._bars_since_entry += 1
            pnl_est = (close - self._entry_price) * self._volume * self._multiplier
            logger.info(
                "持仓中... Bar#%d | 价格: %.0f | 持仓: %d手@%.0f | 浮盈: %.0f",
                self._bar_count, close,
                getattr(pos, "volume", 0), self._entry_price, pnl_est,
            )

            if self._bars_since_entry >= self.hold_bars:
                signal = self.sell(self.symbol, close, self._volume)
                if signal:
                    logger.info("信号发出: 平仓 %s %d手@%.0f", self.symbol, self._volume, close)
                    self._closed = True
                else:
                    logger.error("平仓信号生成失败")
            return

        if self._closed:
            return

        # Warmup phase — just log progress
        if self._bar_count < self.warmup_bars:
            if self._bar_count % 5 == 0 or self._bar_count == 1:
                logger.info(
                    "预热中... Bar#%d/%d | %s O=%.0f H=%.0f L=%.0f C=%.0f V=%d",
                    self._bar_count, self.warmup_bars,
                    symbol, bar["open"], bar["high"], bar["low"], close, vol_so_far,
                )
            return

        # Warmup complete — send buy order
        if not self._bought:
            logger.info("预热完成，发送验证买单")
            signal = self.buy(self.symbol, close, self._volume)
            if signal:
                self._entry_price = close
                self._bought = True
                self._bars_since_entry = 0
                logger.info("信号发出: 开仓 %s %d手@%.0f", self.symbol, self._volume, close)
            else:
                logger.error("开仓信号生成失败")

    def on_order(self, order):
        logger.info(
            "订单更新: %s | %s | %s | %d/%d | %s",
            getattr(order, "order_id", ""),
            getattr(order, "symbol", ""),
            getattr(order, "direction", ""),
            getattr(order, "traded_volume", 0),
            getattr(order, "volume", 0),
            getattr(order, "status", ""),
        )

    def on_trade(self, trade):
        logger.info(
            "成交回报: %s | %s | %s | %d手@%.2f | 手续费: %.2f",
            getattr(trade, "trade_id", ""),
            getattr(trade, "symbol", ""),
            getattr(trade, "direction", ""),
            getattr(trade, "volume", 0),
            getattr(trade, "price", 0.0),
            getattr(trade, "commission", 0.0),
        )
        self.update_position(trade.symbol, trade)
```

- [ ] **Step 2: Write test for VerifyStrategy**

```python
# tests/test_verify_strategy.py
import pandas as pd
from src.strategy.strategies.verify import VerifyStrategy


def _bar(close, symbol="rb2510"):
    return pd.Series({
        "symbol": symbol, "datetime": pd.Timestamp.now(),
        "open": close - 2, "high": close + 2, "low": close - 3, "close": close, "volume": 100,
    })


def test_warmup_phase_no_signal():
    """Strategy should not emit signals during warmup."""
    s = VerifyStrategy("verify", {"warmup_bars": 20, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(15):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0
    assert s._bar_count == 15


def test_buy_signal_after_warmup():
    """Strategy emits a buy signal after warmup bars."""
    s = VerifyStrategy("verify", {"warmup_bars": 5, "hold_bars": 10, "volume": 1})
    s.on_init()
    for _ in range(4):
        s.on_bar(_bar(3128))
    assert len(s.signals) == 0
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s.signals[0].direction.value == "long"
    assert s.signals[0].volume == 1


def test_sell_signal_after_hold():
    """Strategy emits close signal after holding for hold_bars."""
    s = VerifyStrategy("verify", {"warmup_bars": 3, "hold_bars": 5, "volume": 1})
    s.on_init()
    for _ in range(3):
        s.on_bar(_bar(3128))
    # Buy at bar 3
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    assert s._bought is True
    # Hold for 5 bars
    for _ in range(5):
        s.on_bar(_bar(3135))
    # Sell at bar 5 of hold
    assert len(s.signals) == 2
    assert s.signals[1].direction.value == "short"
    assert s.signals[1].offset.value == "close"
    assert s._closed is True


def test_no_duplicate_buy():
    """Strategy only buys once."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 10, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))
    assert len(s.signals) == 1
    s.on_bar(_bar(3135))
    s.on_bar(_bar(3132))
    assert len(s.signals) == 1  # No more buy signals


def test_no_signals_after_close():
    """Strategy emits nothing after close."""
    s = VerifyStrategy("verify", {"warmup_bars": 2, "hold_bars": 2, "volume": 1})
    s.on_init()
    s.on_bar(_bar(3128))
    s.on_bar(_bar(3130))  # buy
    s.on_bar(_bar(3132))
    s.on_bar(_bar(3135))  # sell
    count_after_sell = len(s.signals)
    s.on_bar(_bar(3140))
    s.on_bar(_bar(3138))
    assert len(s.signals) == count_after_sell  # No new signals
```

- [ ] **Step 3: Run VerifyStrategy tests**

Run: `pytest tests/test_verify_strategy.py -v`
Expected: 5 tests PASS

- [ ] **Step 4: Commit**

```bash
git add back_end/src/strategy/strategies/verify.py tests/test_verify_strategy.py
git commit -m "feat: add VerifyStrategy for live trading chain validation"
```

---

### Task 3: Register VerifyStrategy in Registry and Exports

**Files:**
- Modify: `back_end/src/strategy/strategies/__init__.py`
- Modify: `back_end/src/strategy/registry.py`
- Modify: `back_end/src/strategy/__init__.py`

- [ ] **Step 1: Add VerifyStrategy to strategies __init__.py**

Edit `back_end/src/strategy/strategies/__init__.py`:

```python
"""
策略实现子模块
"""

from .ma_cross import MACrossStrategy
from .rsi import RSIStrategy
from .breakout import BreakoutStrategy
from .verify import VerifyStrategy

__all__ = ["MACrossStrategy", "RSIStrategy", "BreakoutStrategy", "VerifyStrategy"]
```

- [ ] **Step 2: Register in registry.py**

Edit `back_end/src/strategy/registry.py` — add import and registration:

In the import block, change:
```python
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy
```
to:
```python
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy, VerifyStrategy
```

In STRATEGY_REGISTRY, add:
```python
    'verify': VerifyStrategy,
```

- [ ] **Step 3: Add to strategy package __init__.py**

Edit `back_end/src/strategy/__init__.py` — add to imports and __all__:

Import line change:
```python
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy
```
to:
```python
from .strategies import MACrossStrategy, RSIStrategy, BreakoutStrategy, VerifyStrategy
```

Add `"VerifyStrategy"` to `__all__` list.

- [ ] **Step 4: Verify import chain**

Run: `python -c "from src.strategy import create_strategy; s = create_strategy('verify', {'symbol': 'rb2510'}); print(type(s).__name__)"`
Expected: `VerifyStrategy`

- [ ] **Step 5: Commit**

```bash
git add back_end/src/strategy/strategies/__init__.py back_end/src/strategy/registry.py back_end/src/strategy/__init__.py
git commit -m "feat: register VerifyStrategy in strategy registry"
```

---

### Task 4: Integrate BarAggregator into TradingEngine

**Files:**
- Modify: `back_end/src/trading/engine.py`

The key change: replace the per-tick `_tick_to_bar` + `on_bar` call with `BarAggregator.push()` that only calls `on_bar` when a bar is complete. The pre-order market data path stays per-tick.

- [ ] **Step 1: Add BarAggregator to engine init**

In `TradingEngine.__init__`, add after the `self.risk_manager = RiskManager()` line:

```python
        from .bar_aggregator import BarAggregator

        self.bar_aggregator: BarAggregator = None  # set in start()
        self._bar_interval: int = 1
```

- [ ] **Step 2: Initialize BarAggregator in start()**

In `TradingEngine.start()`, after `self.configure_risk(config)`, add:

```python
            self._bar_interval = max(1, int(config.get("bar_interval_minutes", 1)))
            self.bar_aggregator = BarAggregator(
                interval_minutes=self._bar_interval,
                on_bar=self._on_bar_completed,
            )
```

- [ ] **Step 3: Replace _on_tick bar-delivery logic**

Replace the strategy call in `_on_tick()` (lines 203-213):

Remove this block:
```python
        if not self.strategy:
            return
        try:
            bar = self._tick_to_bar(tick)
            self.strategy.current_date = tick.timestamp
            available = getattr(self.gateway.account, "available", 0.0)
            if available > 0:
                self.strategy.current_capital = available
            self.strategy.on_bar(bar)
            self._dispatch_strategy_signals()
            self._append_live_bar(tick, bar)
        except Exception as e:
            logger.error(f"处理行情数据失败: {e}")
```

Replace with:

```python
        # Pre-order market data update (per-tick, unchanged)
        if self.strategy:
            available = getattr(self.gateway.account, "available", 0.0)
            if available > 0:
                self.strategy.current_capital = available

        # Bar aggregation — only calls on_bar when a bar completes
        if self.bar_aggregator:
            try:
                self.bar_aggregator.push(tick)
            except Exception as e:
                logger.error(f"Bar 聚合失败: {e}")
```

- [ ] **Step 4: Add _on_bar_completed callback**

Add new method to `TradingEngine`:

```python
    def _on_bar_completed(self, symbol: str, bar):
        """Called by BarAggregator when a bar completes."""
        if not self.strategy:
            return
        try:
            self.strategy.current_date = bar["datetime"]
            self._append_live_bar_via_bar(symbol, bar)
            self.strategy.on_bar(bar)
            self._dispatch_strategy_signals()
        except Exception as e:
            logger.error(f"处理 Bar 回调失败: {e}")

    def _append_live_bar_via_bar(self, symbol: str, bar):
        """Append a completed bar to strategy data."""
        import pandas as pd

        bar_frame = pd.DataFrame([bar.to_dict()], index=[bar["datetime"]])
        existing = self.strategy.data.get(symbol)
        if existing is None or existing.empty:
            updated = bar_frame
        else:
            updated = pd.concat([existing, bar_frame])
            updated = updated[~updated.index.duplicated(keep="last")].sort_index()
        if len(updated) > 1000:
            updated = updated.iloc[-1000:]
        self.strategy.data[symbol] = updated
```

- [ ] **Step 5: Update stop() to flush aggregator**

In `TradingEngine.stop()`, before gateway disconnect, add:

```python
            if self.bar_aggregator and self.strategy:
                for sym in list(self.bar_aggregator._current.keys()):
                    self.bar_aggregator.flush(sym)
```

- [ ] **Step 6: Ensure pre-order tick path is untouched**

Verify `_on_tick` still calls `self.order_manager.update_market_data(...)` — the block at the top of `_on_tick` (lines 197-202) is unchanged.

- [ ] **Step 7: Run existing trading tests to verify no regression**

Run: `pytest tests/test_trading_engine_auto_strategy.py tests/test_market_ticks.py -v`
Expected: All existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add back_end/src/trading/engine.py
git commit -m "feat: integrate BarAggregator into TradingEngine for live bar delivery"
```

---

### Task 5: Interactive Credential Input in main.py

**Files:**
- Modify: `back_end/main.py`

- [ ] **Step 1: Add getpass import and credential prompt**

At top of `main.py`, add:
```python
from getpass import getpass
```

- [ ] **Step 2: Add credential collection in run_live_trading()**

In `run_live_trading()`, before gateway creation, add:

```python
    trading_config = dict(config.get('trading', {}))
    if 'risk' in config:
        trading_config['risk'] = config['risk']

    # --- Interactive credential input ---
    if not trading_config.get("username"):
        trading_config["username"] = input("CTP 账号: ").strip()
    if not trading_config.get("password"):
        trading_config["password"] = getpass("CTP 密码: ")
    # --- End credential input ---

    gateway_type = trading_config.get('gateway', 'vnpy')
```

- [ ] **Step 3: Add bar_interval_minutes passthrough**

Ensure `bar_interval_minutes` is passed through to `trading_engine.start()`. The config dict already flows through `trading_config`, and `engine.start()` reads `bar_interval_minutes` from it — no additional change needed. Verify by reading the flow: `run_live_trading` → `trading_engine.start(trading_config)` → `config.get("bar_interval_minutes", 1)`.

- [ ] **Step 4: Verify CLI help and dry-run import**

Run: `python -c "from main import run_live_trading; print('import OK')"`
Expected: `import OK` (no connection attempt)

- [ ] **Step 5: Commit**

```bash
git add back_end/main.py
git commit -m "feat: add interactive CTP credential input via getpass"
```

---

### Task 6: Create Production Config File

**Files:**
- Create: `back_end/config/config_production.json`

- [ ] **Step 1: Write config file**

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

- [ ] **Step 2: Add to .gitignore (credentials handled by interactive input)**

Verify `config/config_production.json` is NOT in `.gitignore` — the file contains no secrets (username/password are interactive-only). The broker_id, app_id, auth_code are public broker-issued identifiers.

- [ ] **Step 3: Commit**

```bash
git add back_end/config/config_production.json
git commit -m "feat: add production config for CTP simulation (rb2510 verify)"
```

---

### Task 7: End-to-End Dry-Run Verification

**Files:**
- None (manual test)

- [ ] **Step 1: Verify config loads correctly**

Run: `python -c "import json; c=json.load(open('back_end/config/config_production.json')); print(c['mode'], c['strategy']['symbol'], c['trading']['broker_id'])`
Expected: `live rb2510 2071`

- [ ] **Step 2: Verify strategy factory works end-to-end**

Run: `python -c "from src.strategy import create_strategy; s=create_strategy('verify', {'symbol':'rb2510','warmup_bars':20,'hold_bars':10,'volume':1}); s.on_init(); print('init OK, bars:', s._bar_count)"`
Expected: `init OK, bars: 0`

- [ ] **Step 3: Verify BarAggregator + engine integration (unit integration)**

Run: `pytest tests/test_bar_aggregator.py tests/test_verify_strategy.py tests/test_trading_engine_auto_strategy.py -v`
Expected: All tests PASS

- [ ] **Step 4: Full test suite regression**

Run: `pytest back_end/tests/ -v --tb=short`
Expected: All existing tests PASS (no regressions)

- [ ] **Step 5: Verify vnpy import available**

Run: `python -c "from vnpy.event import EventEngine; from vnpy.trader.engine import MainEngine; print('vnpy OK')"`
Expected: `vnpy OK` (or a clear "not installed" message — skip live test if vnpy not in env)

- [ ] **Step 6: Document live test run command**

The actual CTP connection test is manual (requires trading hours + network access):

```bash
cd back_end && python main.py -m live -c config/config_production.json
```

Expected interactive flow:
1. Prompt for username → user enters `0061839732`
2. Prompt for password → user enters password (hidden)
3. CTP connection attempt
4. If connected: Bar aggregation begins, strategy warms up, sends order
5. If connection fails: vn.py error diagnostics printed

---

## Verification Checklist (from Spec)

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

## Risks

1. **CTP connection failure** — vn.py may reject connection due to network (firewall blocking ports 42205/42213) or broker-side IP whitelist. Mitigation: connection error diagnostics already in VnpyGateway._remember_connect_error().

2. **Trading hours** — CTP simulation may only accept connections during trading hours (weekdays 9:00-15:15, 21:00-02:30). Test during session times.

3. **Bar interval too short** — 1-minute bars during low-activity periods may produce bars with zero volume. VerifyStrategy handles this gracefully (uses close price, not volume, for signals).

4. **Existing strategy tests break** — The _on_tick change removes immediate on_bar calls per tick. Any test that relies on this behavior will need updating. The test regression run in Task 7 Step 4 will catch this.
