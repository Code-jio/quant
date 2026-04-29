"""全局交易状态单例。"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..trading import TradingEngine, TradingStatus

logger = logging.getLogger(__name__)


class _StrategyEntry:
    def __init__(self, strategy_id, strategy, engine, config):
        self.strategy_id = strategy_id
        self.strategy    = strategy
        self.engine      = engine
        self.config      = config

    @property
    def status(self) -> str:
        es = self.engine.status
        if es == TradingStatus.TRADING:    return "running"
        if es == TradingStatus.CONNECTED:  return "running"
        if es == TradingStatus.STOPPED:    return "stopped"
        if es == TradingStatus.ERROR:      return "error"
        if es == TradingStatus.CONNECTING: return "connecting"
        return "stopped"


# 模块级事件循环引用，在 lifespan 中赋值
_event_loop: Optional[asyncio.AbstractEventLoop] = None


class TradingState:
    """
    全局单例：持有所有引擎/策略引用，供 API 路由及 WebSocket 推送读取。

    登录后调用 set_main_engine() 注册主引擎（CTP 网关）；
    策略注册后调用 register() 绑定到具体策略。
    """

    EQUITY_MAXLEN = 300

    def __init__(self):
        self._entries:             Dict[str, _StrategyEntry]  = {}
        self._main_engine:         Optional[TradingEngine]    = None
        self._main_config:         Dict[str, Any]             = {}
        self._connect_log:         List[str]                  = []
        self._equity_curve:        deque                      = deque(maxlen=self.EQUITY_MAXLEN)
        self._day_open_balance:    float                      = 0.0
        self._weights:             Dict[str, float]           = {}
        self._last_gw_callback_ts: float                      = 0.0

    # ── 主引擎（登录产生的 CTP 引擎）────────────────────────────────────────
    def set_main_engine(self, engine: TradingEngine, config: Dict[str, Any] = None):
        from .deps import _install_hook_on_engine  # noqa: PLC0415
        self._main_engine = engine
        self._main_config = config or {}
        if _event_loop and not _event_loop.is_closed():
            _install_hook_on_engine(engine)
        logger.info("[API] 主引擎已设置")

    def clear_main(self):
        if self._main_engine:
            try:
                self._main_engine.stop()
            except Exception:
                pass
        self._main_engine = None
        self._main_config = {}

    # ── 连接日志 ──────────────────────────────────────────────────────────────
    def add_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._connect_log.append(entry)
        logger.info(f"[ConnLog] {msg}")
        if len(self._connect_log) > 200:
            self._connect_log = self._connect_log[-100:]

    def clear_log(self):
        self._connect_log = []

    def get_log(self) -> List[str]:
        return list(self._connect_log)

    # ── 策略权重 ──────────────────────────────────────────────────────────────
    def set_weights(self, weights: Dict[str, float]):
        for sid, w in weights.items():
            self._weights[sid] = max(0.0, min(1.0, float(w)))

    def get_weight(self, strategy_id: str) -> float:
        if strategy_id in self._weights:
            return self._weights[strategy_id]
        n = max(len(self._entries), 1)
        return round(1.0 / n, 4)

    def all_weights(self) -> Dict[str, float]:
        ids = list(self._entries.keys())
        if not ids:
            return {}
        if self._weights:
            return {sid: self._weights.get(sid, round(1.0 / len(ids), 4)) for sid in ids}
        w = round(1.0 / len(ids), 4)
        return {sid: w for sid in ids}

    # ── 权益曲线历史 ──────────────────────────────────────────────────────────
    def push_equity(self, pnl: float, balance: float):
        ts = datetime.now().strftime("%H:%M:%S")
        self._equity_curve.append({"ts": ts, "p": round(pnl, 2), "b": round(balance, 2)})
        if self._day_open_balance == 0.0 and balance > 0:
            self._day_open_balance = balance

    def get_equity_data(self) -> list:
        return list(self._equity_curve)

    # ── 策略注册 ──────────────────────────────────────────────────────────────
    def register(self, strategy_id, strategy, engine, config=None):
        from .deps import _install_order_hook  # noqa: PLC0415
        entry = _StrategyEntry(strategy_id, strategy, engine, config or {})
        self._entries[strategy_id] = entry
        if _event_loop and not _event_loop.is_closed():
            _install_order_hook(entry)
        logger.info(f"[API] 策略已注册: {strategy_id}")

    def unregister(self, strategy_id: str):
        self._entries.pop(strategy_id, None)

    def get(self, strategy_id: str) -> Optional[_StrategyEntry]:
        return self._entries.get(strategy_id)

    def all_entries(self) -> List[_StrategyEntry]:
        return list(self._entries.values())

    def primary_engine(self) -> Optional[TradingEngine]:
        if self._main_engine:
            return self._main_engine
        for entry in self._entries.values():
            return entry.engine
        return None


trading_state = TradingState()
