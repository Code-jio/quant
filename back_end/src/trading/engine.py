"""
交易引擎模块
"""

import logging
import traceback
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..strategy import Signal, Order, Trade, Position
    from .types import AccountInfo, MarketData, TradingStatus
    from .gateway import GatewayBase
    from .errors import TradingError
    from .order_manager import OrderManager, PreOrder

logger = logging.getLogger(__name__)

from .types import TradingStatus, AccountInfo, MarketData
from .gateway import GatewayBase, create_gateway
from .errors import TradingError
from .order_manager import OrderManager, PreOrder
from .risk import RiskManager
from .bar_aggregator import BarAggregator
from ..common.exceptions import ExceptionHandler


class TradingEngine:
    """实盘交易引擎"""

    def __init__(self, gateway: GatewayBase = None):
        self.gateway = gateway or create_gateway("vnpy")
        self.strategy = None
        self._processed_signal_count = 0
        self.status = TradingStatus.STOPPED

        self.order_manager = OrderManager(self.gateway)
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
        self.gateway.on_tick_callback = self._on_tick
        self.gateway.on_error_callback = self._on_gateway_error

        self.order_manager.on_order_callback = self._on_order
        self.order_manager.on_trade_callback = self._on_trade
        self.order_manager.on_pre_order_status_change = self._on_pre_order_status_change

        self.risk_manager = RiskManager()
        self.bar_aggregator: BarAggregator = None  # set in start()
        self._bar_interval: int = 1
        self._emit_first_tick_bar = False
        self._first_tick_bar_symbols: set[str] = set()
        self._first_tick_bar_emitted_symbols: set[str] = set()
        self._first_tick_bar_skip_reason = ""

        self.last_reject_reason = ""
        self._error_count = 0
        self._max_errors = 10
        self.exception_handler = ExceptionHandler()

    def set_strategy(self, strategy):
        """设置策略"""
        self.strategy = strategy
        self._processed_signal_count = len(getattr(strategy, "signals", []))
        if hasattr(strategy, "set_position_source"):
            strategy.set_position_source(self.gateway.positions)

    def configure_risk(self, config: Dict[str, Any] = None):
        """Configure pre-order risk controls."""
        config = config or {}
        self.risk_manager.configure(config)
        if config.get("initial_capital"):
            self.risk_manager.set_day_open_balance(float(config.get("initial_capital") or 0.0))

    def start(self, config: Dict[str, Any] = None) -> bool:
        """启动交易引擎"""
        try:
            if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
                logger.warning("交易引擎已在运行中")
                return False

            config = config or {}
            self.configure_risk(config)
            self._bar_interval = max(1, int(config.get("bar_interval_minutes", 1)))
            self._emit_first_tick_bar = bool(config.get("emit_first_tick_bar", False))
            self._first_tick_bar_symbols.clear()
            self._first_tick_bar_emitted_symbols.clear()
            self._first_tick_bar_skip_reason = ""
            self.bar_aggregator = BarAggregator(
                interval_minutes=self._bar_interval,
                on_bar=self._on_bar_completed,
            )
            self._max_errors = max(1, int(config.get("max_errors", self._max_errors)))

            self.status = TradingStatus.CONNECTING

            if self.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                if not self.gateway.connect(config):
                    self.status = TradingStatus.ERROR
                    return False

            self.order_manager.start()

            if self.strategy:
                try:
                    self.strategy.on_init()
                    self.strategy.on_start()
                except Exception as e:
                    logger.error(f"策略初始化失败: {e}")
                    self.status = TradingStatus.ERROR
                    return False

            self.status = TradingStatus.TRADING
            logger.info("交易引擎已启动")
            return True

        except (ValueError, ImportError, ConnectionError, RuntimeError):
            self.status = TradingStatus.ERROR
            raise
        except Exception as e:
            logger.error(f"启动交易引擎失败: {e}\n{traceback.format_exc()}")
            self.status = TradingStatus.ERROR
            raise RuntimeError(f"启动交易引擎失败: {e}") from e

    def stop(self):
        """停止交易引擎"""
        try:
            if self.strategy:
                try:
                    self.strategy.on_stop()
                except Exception as e:
                    logger.error(f"策略停止失败: {e}")

            self.order_manager.stop()

            if self.bar_aggregator and self.strategy:
                for sym in list(self.bar_aggregator._current.keys()):
                    self.bar_aggregator.flush(sym)

            self.gateway.disconnect()
            self.status = TradingStatus.STOPPED
            logger.info("交易引擎已停止")
        except Exception as e:
            logger.error(f"停止交易引擎失败: {e}")
            self.status = TradingStatus.STOPPED

    def send_signal(self, signal: 'Signal') -> str:
        """发送交易信号"""
        self.last_reject_reason = ""
        if self.status not in (TradingStatus.TRADING, TradingStatus.CONNECTED):
            # 也检查网关状态，允许网关已连接但引擎未正式 start 的场景（手动交易）
            if self.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                logger.warning("交易引擎未连接")
                self.last_reject_reason = "Trading engine is not connected"
                return ""

        try:
            risk_result = self.risk_manager.check_signal(
                signal,
                positions=self.gateway.positions,
                active_orders=self.gateway.orders.values(),
                account=getattr(self.gateway, "account", None),
                market_data=self._market_data_for_symbol(signal.symbol),
            )
            if not risk_result.allowed:
                self.last_reject_reason = risk_result.reason
                logger.warning(f"风控拒单: {risk_result.reason}")
                return ""

            order_id = self.order_manager.submit_order(signal)
            if order_id:
                self.risk_manager.record_order(signal)
                logger.info(f"发送信号: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
            return order_id
        except Exception as e:
            self._error_count += 1
            logger.error(f"发送信号失败: {e}")
            if self._error_count >= self._max_errors:
                logger.error(f"错误次数过多 ({self._error_count}), 停止交易")
                self.stop()
            return ""

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        try:
            return self.order_manager.cancel_order(order_id)
        except Exception as e:
            logger.error(f"撤销订单失败: {e}")
            return False

    def place_pre_order(self, pre_order: PreOrder) -> str:
        """放置预埋单"""
        try:
            return self.order_manager.place_pre_order(pre_order)
        except Exception as e:
            logger.error(f"放置预埋单失败: {e}")
            return ""

    def cancel_pre_order(self, pre_order_id: str) -> bool:
        """撤销预埋单"""
        try:
            return self.order_manager.cancel_pre_order(pre_order_id)
        except Exception as e:
            logger.error(f"撤销预埋单失败: {e}")
            return False

    def update_market_data(self, symbol: str, data: dict):
        """更新市场数据，用于预埋单触发"""
        try:
            self.order_manager.update_market_data(symbol, data)
        except Exception as e:
            logger.error(f"更新市场数据失败: {e}")

    def on_tick(self, tick: MarketData):
        """行情推送（外部主动调用，兼容旧接口）"""
        self._on_tick(tick)

    def _on_tick(self, tick: MarketData):
        """行情推送内部处理，同时更新预埋单市场数据"""
        stale_reason = self._stale_market_data_reason(tick)
        if stale_reason:
            self.last_reject_reason = stale_reason
            self._first_tick_bar_skip_reason = "stale_market_data"
            mark_stale = getattr(self.strategy, "mark_market_data_stale", None) if self.strategy else None
            if callable(mark_stale):
                try:
                    mark_stale(tick.symbol, stale_reason)
                except Exception as e:
                    logger.error(f"标记过期行情失败: {e}")
            logger.warning("忽略过期行情 tick: %s", stale_reason)
            return

        self.order_manager.update_market_data(tick.symbol, {
            "last_price": tick.last_price,
            "bid_price_1": tick.bid_price_1,
            "ask_price_1": tick.ask_price_1,
            "timestamp": tick.timestamp,
        })
        # Update strategy capital from live account (per-tick)
        if self.strategy:
            available = getattr(self.gateway.account, "available", 0.0)
            if available > 0:
                self.strategy.current_capital = available
            on_tick = getattr(self.strategy, "on_tick", None)
            if callable(on_tick):
                try:
                    on_tick(tick)
                    self._dispatch_strategy_signals()
                    self._maybe_chase_strategy_order(tick)
                except Exception as e:
                    logger.error(f"处理 Tick 策略回调失败: {e}")

        # Bar aggregation — only calls on_bar when a bar completes
        if self.bar_aggregator:
            try:
                finished = self.bar_aggregator.push(tick)
                if finished is None:
                    self._maybe_emit_first_tick_bar(tick)
            except Exception as e:
                logger.error(f"Bar 聚合失败: {e}")

    def _stale_market_data_reason(self, tick: MarketData) -> str:
        max_age = getattr(self.risk_manager.config, "max_market_data_age_seconds", 0.0)
        if max_age <= 0:
            return ""
        age = self.risk_manager._market_data_age(getattr(tick, "timestamp", None))
        if age is None:
            return f"Market data timestamp is unavailable for {tick.symbol}"
        if age > max_age:
            return f"Market data is stale for {tick.symbol}: {round(age, 2)}s"
        return ""

    def _tick_to_bar(self, tick: MarketData):
        """Convert a live tick into the bar passed to strategy.on_bar."""
        import pandas as pd

        return pd.Series({
            "symbol": tick.symbol,
            "datetime": tick.timestamp,
            "open": tick.last_price,
            "high": tick.last_price,
            "low": tick.last_price,
            "close": tick.last_price,
            "volume": tick.volume,
            "bid": tick.bid_price_1,
            "ask": tick.ask_price_1,
        })

    def _append_live_bar(self, tick: MarketData, bar=None):
        """Append the processed tick to a bounded rolling window of live data."""
        import pandas as pd

        if bar is None:
            bar = self._tick_to_bar(tick)
        bar_frame = pd.DataFrame([bar.to_dict()], index=[tick.timestamp])

        existing = self.strategy.data.get(tick.symbol)
        if existing is None or existing.empty:
            updated = bar_frame
        else:
            updated = pd.concat([existing, bar_frame])
            updated = updated[~updated.index.duplicated(keep="last")].sort_index()

        # Keep only the tail to bound memory usage (1000 bars ≈ 1 trading day of 1m)
        if len(updated) > 1000:
            updated = updated.iloc[-1000:]

        self.strategy.data[tick.symbol] = updated
        return bar

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

    def _maybe_emit_first_tick_bar(self, tick: MarketData):
        """Let verification flows advance on the first valid tick instead of waiting for a full minute."""
        if not self._emit_first_tick_bar or not self.strategy:
            return
        if float(getattr(tick, "last_price", 0.0) or 0.0) <= 0:
            self._first_tick_bar_skip_reason = "invalid_tick_price"
            return

        tick_key = self._normalize_symbol_key(tick.symbol)
        if tick_key in self._first_tick_bar_symbols:
            return

        strategy_symbol = str(getattr(self.strategy, "symbol", "") or "")
        if strategy_symbol and not self._symbols_match(tick.symbol, strategy_symbol):
            self._first_tick_bar_skip_reason = "symbol_mismatch"
            return

        snapshot = {}
        snapshot_fn = getattr(self.strategy, "snapshot", None)
        if callable(snapshot_fn):
            try:
                snapshot = snapshot_fn()
            except Exception:
                snapshot = {}
        if int(snapshot.get("bar_count", 0) or 0) > 0:
            self._first_tick_bar_symbols.add(tick_key)
            self._first_tick_bar_skip_reason = ""
            return

        bar = self._tick_to_bar(tick)
        try:
            self.strategy.current_date = bar["datetime"]
            self._append_live_bar_via_bar(tick.symbol, bar)
            self.strategy.on_bar(bar)
            self._dispatch_strategy_signals()
            self._first_tick_bar_symbols.add(tick_key)
            self._first_tick_bar_emitted_symbols.add(tick_key)
            self._first_tick_bar_skip_reason = ""
            logger.info("试运行首个 tick 已生成验证 Bar: %s %.2f", tick.symbol, tick.last_price)
        except Exception as e:
            self._first_tick_bar_skip_reason = "first_tick_bar_not_emitted"
            logger.error(f"处理首个 tick 验证 Bar 失败: {e}")

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        raw = str(symbol or "").strip().lower()
        if "." not in raw:
            return raw
        parts = [part for part in raw.split(".") if part]
        for part in parts:
            if any(ch.isdigit() for ch in part):
                return part
        return parts[0] if parts else raw

    @classmethod
    def _symbols_match(cls, left: str, right: str) -> bool:
        return cls._normalize_symbol_key(left) == cls._normalize_symbol_key(right)

    def _dispatch_strategy_signals(self):
        """Send newly generated strategy signals to the broker gateway."""
        signals = getattr(self.strategy, "signals", [])
        if self._processed_signal_count > len(signals):
            self._processed_signal_count = len(signals)

        new_signals = signals[self._processed_signal_count:]
        for signal in new_signals:
            order_id = self.send_signal(signal)
            if not order_id:
                reject_callback = getattr(self.strategy, "mark_signal_rejected", None)
                if callable(reject_callback):
                    reject_callback(getattr(self, "last_reject_reason", ""))
                logger.warning(
                    "Strategy signal rejected: %s %s %s",
                    signal.symbol, signal.direction, signal.volume,
                )
            else:
                self._notify_strategy_signal_submitted(signal, order_id)
        self._processed_signal_count = len(signals)

    def _notify_strategy_signal_submitted(self, signal: 'Signal', order_id: str):
        notify = getattr(self.strategy, "on_signal_submitted", None) if self.strategy else None
        if callable(notify):
            try:
                notify(signal, order_id)
            except Exception as e:
                logger.error("Strategy order-submit notification failed: %s", e)

    def _maybe_chase_strategy_order(self, tick: MarketData):
        action_fn = getattr(self.strategy, "next_chase_action", None) if self.strategy else None
        if not callable(action_fn):
            return
        try:
            action = action_fn(tick) or {}
        except Exception as e:
            logger.error("Strategy chase decision failed: %s", e)
            return

        action_type = action.get("action")
        if action_type == "cancel":
            order_id = str(action.get("order_id") or "")
            if not order_id:
                return
            if not self.cancel_order(order_id):
                failed = getattr(self.strategy, "on_chase_cancel_failed", None)
                if callable(failed):
                    failed(order_id)
                logger.warning("Strategy chase cancel failed: %s", order_id)
            return

        if action_type == "submit":
            signal = action.get("signal")
            if not signal:
                return
            order_id = self.send_signal(signal)
            if order_id:
                self._notify_strategy_signal_submitted(signal, order_id)
                self._processed_signal_count = max(self._processed_signal_count, len(getattr(self.strategy, "signals", [])))
            else:
                reject_callback = getattr(self.strategy, "mark_signal_rejected", None)
                if callable(reject_callback):
                    reject_callback(getattr(self, "last_reject_reason", ""))

    def _market_data_for_symbol(self, symbol: str) -> Dict[str, Any]:
        data = self._lookup_symbol_mapping(self.order_manager.market_data, symbol)
        if data:
            return data

        latest_ticks = getattr(self.gateway, "latest_ticks", {})
        tick = self._lookup_symbol_mapping(latest_ticks, symbol) if isinstance(latest_ticks, dict) else None
        if tick:
            return {
                "last_price": getattr(tick, "last_price", 0.0),
                "bid_price_1": getattr(tick, "bid_price_1", 0.0),
                "ask_price_1": getattr(tick, "ask_price_1", 0.0),
                "timestamp": getattr(tick, "timestamp", None),
            }

        snapshots = getattr(self.gateway, "latest_tick_snapshots", {})
        snapshot = self._lookup_symbol_mapping(snapshots, symbol) if isinstance(snapshots, dict) else None
        if snapshot:
            return snapshot

        return {}

    @classmethod
    def _lookup_symbol_mapping(cls, mapping: Dict[str, Any], symbol: str) -> Any:
        if not isinstance(mapping, dict):
            return None
        if symbol in mapping:
            return mapping[symbol]
        for key, value in mapping.items():
            if cls._symbols_match(str(key), symbol):
                return value
        return None

    def get_account(self) -> AccountInfo:
        """获取账户信息"""
        try:
            return self.gateway.query_account()
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            return AccountInfo(error_msg=str(e))

    def get_positions(self) -> Dict[str, 'Position']:
        """获取持仓"""
        return self.gateway.positions

    def get_orders(self) -> Dict[str, 'Order']:
        """获取订单"""
        return self.order_manager.active_orders

    def get_pre_orders(self) -> Dict[str, PreOrder]:
        """获取预埋单"""
        return self.order_manager.pre_orders

    def _on_order(self, order: 'Order'):
        """订单回调"""
        self.order_manager.update_order(order)
        if self.strategy:
            try:
                self.strategy.on_order(order)
            except Exception as e:
                logger.error(f"策略订单回调失败: {e}")

    def _on_trade(self, trade: 'Trade'):
        """成交回调"""
        if self.strategy:
            try:
                self.strategy.update_position(trade.symbol, trade)
                self.strategy.on_trade(trade)
            except Exception as e:
                logger.error(f"策略成交回调失败: {e}")

    def _on_pre_order_status_change(self, pre_order: PreOrder):
        """预埋单状态变更回调"""
        if self.strategy:
            try:
                logger.info(f"预埋单状态变更: {pre_order.pre_order_id} -> {pre_order.status.value}")
            except Exception as e:
                logger.error(f"预埋单状态变更回调失败: {e}")

    def _on_gateway_error(self, error: Exception, context: str = ""):
        """网关错误回调"""
        logger.error(f"网关错误 ({context}): {error}")
        self._error_count += 1
        if self._error_count >= self._max_errors:
            logger.error(f"错误次数过多，停止交易引擎")
            self.stop()
