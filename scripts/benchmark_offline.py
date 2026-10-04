"""Bounded history replay benchmark; no gateway connection or orders."""
import json
import statistics
import sys
import time
import tracemalloc
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'back_end'))
from src.strategy import create_strategy
from src.trading.engine import TradingEngine
from src.trading.types import MarketData

engine = TradingEngine()
engine.set_strategy(create_strategy('ma_cross', {'symbol': 'BENCH'}))
start = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
timings = deque(maxlen=1000)
medians = []
memory = []
tracemalloc.start()
for i in range(6000):
    tick = MarketData('BENCH', 100, 99, 101, 1, 1, i, i*100, start + timedelta(minutes=i))
    before = time.perf_counter()
    engine._append_live_bar(tick)
    timings.append((time.perf_counter() - before) * 1000)
    if i in (2999, 5999):
        medians.append(statistics.median(timings))
        import gc
        gc.collect()
        memory.append(tracemalloc.get_traced_memory()[0])
print(json.dumps({'bars_replayed': 6000, 'retained_bars': len(engine.strategy.data['BENCH']),
    'median_ms_2000_3000': medians[0],
    'median_ms_5000_6000': medians[1],
    'traced_memory_bytes_at_3000_6000': memory,
    'scope': 'single-symbol local pandas history path with tracemalloc, not a native feed throughput benchmark'}, indent=2))
