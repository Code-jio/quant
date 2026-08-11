"""
交易引擎模块
"""

import logging
import copy
import hashlib
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

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
from .risk_state_store import LiveRiskStateStore
from .bar_aggregator import BarAggregator
from .symbols import symbol_key, symbols_match
from .trial_run_execution import TrialRunExecutionState, TrialRunOutcome
from .execution_adapter import (
    GatewayExecutionAdapter,
    SimulationFillResult,
    SimulationLedgerError,
    TrialRunSimulationAdapter,
    TrialRunSimulationLedger,
)
from ..common.exceptions import ExceptionHandler


class TradingEngine:
    """实盘交易引擎"""

    def __init__(self, gateway: Optional[GatewayBase] = None, monotonic_clock=None, clock=None):
        self.gateway = gateway or create_gateway("vnpy")
        self._monotonic = monotonic_clock or clock or time.monotonic
        self.strategy = None
        self.trial_run_execution: Optional[TrialRunExecutionState] = None
        self._gateway_execution_adapter = GatewayExecutionAdapter(self)
        self.strategy_execution_adapter = self._gateway_execution_adapter
        self.simulation_adapter: Optional[TrialRunSimulationAdapter] = None
        self._simulation_source_order_id = ""
        self._simulation_requested = False
        self._trial_bound_strategy = None
        self._trial_submission_lock = threading.RLock()
        self._strategy_callback_lock = threading.RLock()
        self._signal_submission_lock = threading.RLock()
        self._trial_submission_in_flight = False
        self._signal_submission_in_flight = False
        self._trial_run_stopping = False
        self._trial_shutdown_cancel_requested_order_ids: set[str] = set()
        self._untracked_trial_order_ids: set[str] = set()
        self._deferred_trial_callbacks: list[tuple[str, Any]] = []
        self._processed_signal_count = 0
        self.status = TradingStatus.STOPPED

        self.order_manager = OrderManager(self.gateway, monotonic_clock=self._monotonic)
        self.order_manager.submit_signal_callback = self.send_signal
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
        self.gateway.on_tick_callback = self._on_tick
        self.gateway.on_error_callback = self._on_gateway_error
        self.gateway.on_account_callback = self._on_gateway_account
        if hasattr(self.gateway, "on_trading_day_callback"):
            self.gateway.on_trading_day_callback = self._on_gateway_trading_day

        self.order_manager.on_order_callback = self._on_order
        self.order_manager.on_trade_callback = self._on_trade
        self.order_manager.on_pre_order_status_change = self._on_pre_order_status_change
        self.order_manager.on_timer_callback = self.on_timer

        self.risk_manager = RiskManager(monotonic_clock=self._monotonic)
        self._live_risk_state_store: Optional[LiveRiskStateStore] = None
        self._live_risk_scope = ""
        self._live_risk_trading_day = ""
        self.bar_aggregator: Optional[BarAggregator] = None  # set in start()
        self._bar_interval: int = 1
        self._emit_first_tick_bar = False
        self._first_tick_bar_symbols: set[str] = set()
        self._first_tick_bar_emitted_symbols: set[str] = set()
        self._first_tick_bar_skip_reason = ""
        self._last_strategy_tick: Optional[MarketData] = None
        self._last_strategy_tick_sequence = 0
        self._trial_checkpoint_store = None
        self._trial_max_hold_seconds = 75.0
        self._trial_hold_deadline_monotonic: Optional[float] = None

        self.last_reject_reason = ""
        self._error_count = 0
        self._max_errors = 10
        self.exception_handler = ExceptionHandler()

    def set_strategy(self, strategy):
        """设置策略"""
        with self._strategy_callback_lock:
            self.strategy = strategy
            self._processed_signal_count = len(getattr(strategy, "signals", []))
        if hasattr(strategy, "set_position_source"):
            source = (
                self.simulation_adapter.positions
                if self.simulation_adapter is not None
                else self.gateway.positions
            )
            strategy.set_position_source(source)

    def begin_simulation_request(self, source_order_id: str) -> bool:
        """Freeze real-order chasing before the broker cancel is requested."""
        source_order_id = str(source_order_id or "")
        with self._strategy_callback_lock:
            with self._trial_submission_lock:
                execution = self.trial_run_execution
                strategy = self.strategy
                if execution is None or strategy is None:
                    raise RuntimeError("trial_execution_unbound")
                can_prepare = getattr(strategy, "can_prepare_simulated_entry", None)
                if not callable(can_prepare) or not can_prepare(source_order_id):
                    raise RuntimeError("simulation_entry_not_current")
                execution.request_simulation(source_order_id)
                already_requested = bool(
                    strategy.request_simulation_cancel(source_order_id)
                )
                self._simulation_source_order_id = source_order_id
                self._simulation_requested = True
                self._persist_trial_execution()
                return already_requested

    def rollback_simulation_request(self, source_order_id: str) -> None:
        """Undo the transition marker when no broker cancel was sent."""
        source_order_id = str(source_order_id or "")
        with self._strategy_callback_lock:
            with self._trial_submission_lock:
                execution = self.trial_run_execution
                strategy = self.strategy
                if execution is not None:
                    execution.cancel_simulation_request(source_order_id)
                rollback = getattr(strategy, "rollback_simulation_cancel", None)
                if callable(rollback):
                    rollback(source_order_id)
                self._simulation_source_order_id = ""
                self._simulation_requested = False
                self._persist_trial_execution()

    def activate_simulation(
        self,
        ledger: TrialRunSimulationLedger,
        *,
        source_order_id: str = "",
    ) -> TrialRunSimulationAdapter:
        """Switch execution and strategy position reads after reconciliation."""
        synthetic_id = ledger.current_order_id
        synthetic_order = ledger.get_order(synthetic_id) if synthetic_id else None
        if synthetic_order is None or not source_order_id:
            raise RuntimeError("simulation_entry_missing")
        adapter = TrialRunSimulationAdapter(
            ledger,
            on_fill=self._on_simulation_fill,
            on_cancel=self._on_simulation_cancel,
        )
        with self._strategy_callback_lock:
            with self._trial_submission_lock:
                execution = self.trial_run_execution
                strategy = self.strategy
                if execution is None or strategy is None:
                    raise RuntimeError("trial_execution_unbound")
                if self._trial_submission_in_flight or self._trial_run_stopping:
                    raise RuntimeError("trial_submission_in_flight")
                if execution.real_trade_ids:
                    raise RuntimeError("real_fill_prevents_simulation")
                can_prepare = getattr(strategy, "can_prepare_simulated_entry", None)
                if not callable(can_prepare) or not can_prepare(source_order_id):
                    raise RuntimeError("simulation_entry_not_current")
                execution.record_simulated_submission(
                    synthetic_order.order_id,
                    "entry",
                    float(synthetic_order.price),
                    synthetic_order.direction.value,
                    synthetic_order.offset.value,
                    source_order_id=source_order_id,
                    symbol=synthetic_order.symbol,
                    volume=synthetic_order.volume,
                )
                prepare_entry = getattr(strategy, "prepare_simulated_entry", None)
                prepared = bool(
                    callable(prepare_entry)
                    and prepare_entry(
                    source_order_id,
                    synthetic_order_id=synthetic_id,
                    synthetic_price=float(getattr(synthetic_order, "price", 0.0) or 0.0),
                    synthetic_direction=getattr(
                        getattr(synthetic_order, "direction", None),
                        "value",
                        getattr(synthetic_order, "direction", "long"),
                    ),
                    synthetic_volume=int(getattr(synthetic_order, "volume", 1) or 0),
                )
                )
                if not prepared:
                    execution.mark_failed(
                        "simulation_evidence_conflict",
                        "strategy_rejected_simulation_entry",
                    )
                    raise RuntimeError("simulation_entry_not_current")
                self.simulation_adapter = adapter
                self.strategy_execution_adapter = adapter
                self._simulation_source_order_id = str(source_order_id)
                self._simulation_requested = True
                set_source = getattr(strategy, "set_position_source", None)
                if callable(set_source):
                    set_source(ledger.positions)
                self._persist_trial_execution()
        return adapter

    def disable_simulation(self) -> None:
        """Restore broker execution after a simulation-track failure."""
        with self._strategy_callback_lock:
            with self._trial_submission_lock:
                self.simulation_adapter = None
                self.strategy_execution_adapter = self._gateway_execution_adapter
                strategy = self.strategy
                self._persist_trial_execution()
            if strategy is not None:
                set_source = getattr(strategy, "set_position_source", None)
                if callable(set_source):
                    set_source(self.gateway.positions)

    def clear_simulation_context(self) -> None:
        """Remove an inactive run's adapter and migration identifiers."""
        self.disable_simulation()
        with self._trial_submission_lock:
            self._simulation_source_order_id = ""
            self._simulation_requested = False
            self._persist_trial_execution()

    def _on_simulation_fill(self, result: SimulationFillResult) -> None:
        try:
            with self._strategy_callback_lock:
                with self._trial_submission_lock:
                    execution = self.trial_run_execution
                    strategy = self.strategy
                    if execution is None or self.simulation_adapter is None:
                        raise RuntimeError("simulation_not_ready")
                    if result.order.offset.value == "open":
                        execution.record_simulated_entry(
                            result.order.order_id,
                            result.trade.trade_id,
                            result.trade.price,
                            symbol=result.order.symbol,
                            volume=result.order.volume,
                            direction=result.order.direction.value,
                            offset=result.order.offset.value,
                        )
                    else:
                        execution.record_simulated_close(
                            result.order.order_id,
                            result.trade.trade_id,
                            result.trade.price,
                            symbol=result.order.symbol,
                            volume=result.order.volume,
                            direction=result.order.direction.value,
                            offset=result.order.offset.value,
                        )
                if strategy is None:
                    raise RuntimeError("trial_strategy_missing")
                strategy.update_position(result.trade.symbol, result.trade)
                strategy.on_trade(result.trade)
                self._persist_trial_execution()
        except Exception as exc:
            self._mark_trial_execution_failed(
                "simulation_evidence_conflict",
                type(exc).__name__,
                execution=self.trial_run_execution,
            )
            self.disable_simulation()
            raise SimulationLedgerError("simulation_evidence_conflict") from exc

    def _on_simulation_cancel(self, order: 'Order') -> None:
        try:
            with self._strategy_callback_lock:
                with self._trial_submission_lock:
                    execution = self.trial_run_execution
                    strategy = self.strategy
                    if execution is None or self.simulation_adapter is None:
                        raise RuntimeError("simulation_not_ready")
                    execution.record_simulated_order_update(
                        order.order_id,
                        "cancelled",
                        symbol=order.symbol,
                        volume=order.volume,
                        direction=order.direction.value,
                    )
                if strategy is not None:
                    cancel_confirmed = getattr(strategy, "on_chase_cancel_confirmed", None)
                    if callable(cancel_confirmed):
                        cancel_confirmed(
                            order.order_id,
                            quote_sequence=self._last_strategy_tick_sequence,
                            market=self._last_strategy_tick,
                        )
                    strategy.on_order(order)
                self._persist_trial_execution()
        except Exception as exc:
            self._mark_trial_execution_failed(
                "simulation_evidence_conflict",
                type(exc).__name__,
                execution=self.trial_run_execution,
            )
            self.disable_simulation()
            raise SimulationLedgerError("simulation_evidence_conflict") from exc

    def clear_strategy(self, expected_strategy: Any = None) -> bool:
        with self._strategy_callback_lock:
            if expected_strategy is not None and self.strategy is not expected_strategy:
                return False
            self.strategy = None
            return True

    def bind_trial_run_execution(self, execution: TrialRunExecutionState) -> None:
        with self._trial_submission_lock:
            if self.trial_run_execution is not None and self.trial_run_execution is not execution:
                raise RuntimeError("trial_execution_already_bound")
            if self._trial_submission_in_flight or self._untracked_trial_order_ids:
                raise RuntimeError("trial_submission_in_flight")
            self.trial_run_execution = execution
            self._trial_bound_strategy = self.strategy
            self._trial_run_stopping = False
            self._trial_shutdown_cancel_requested_order_ids.clear()
            self._trial_hold_deadline_monotonic = execution.hold_deadline_monotonic
            self._persist_trial_execution()

    def set_trial_run_checkpoint_store(self, store) -> None:
        self._trial_checkpoint_store = store
        self._persist_trial_execution()

    def _persist_trial_execution(self) -> None:
        store = self._trial_checkpoint_store
        execution = self.trial_run_execution
        if store is None or execution is None:
            return
        try:
            store.save(execution)
        except Exception as exc:
            logger.error("Unable to persist trial-run checkpoint: %s", exc)

    def unbind_trial_run_execution(self) -> bool:
        with self._trial_submission_lock:
            execution = self.trial_run_execution
            if (
                self._trial_submission_in_flight
                or self._untracked_trial_order_ids
                or (execution is not None and bool(execution.current_order_id))
            ):
                return False
            self.trial_run_execution = None
            return True

    def begin_trial_run_shutdown(self, expected_strategy: Any = None) -> bool:
        """Freeze strategy submissions while broker state is reconciled."""
        with self._trial_submission_lock:
            self._trial_run_stopping = True
        with self._strategy_callback_lock:
            if expected_strategy is not None and self.strategy is not expected_strategy:
                return False
            with self._trial_submission_lock:
                if self._trial_submission_in_flight:
                    return False
            revoke = getattr(self.strategy, "revoke_authorization", None) if self.strategy else None
            if callable(revoke):
                revoke()
            return True

    def request_trial_run_shutdown_cancel(self, order_id: str, *, already_requested: bool = False) -> bool:
        order_id = str(order_id or "")
        if not order_id:
            return False
        with self._trial_submission_lock:
            if order_id in self._trial_shutdown_cancel_requested_order_ids:
                return True
            self._trial_shutdown_cancel_requested_order_ids.add(order_id)
        if already_requested:
            return True
        if self._cancel_strategy_order(order_id):
            return True
        with self._trial_submission_lock:
            self._trial_shutdown_cancel_requested_order_ids.discard(order_id)
        return False

    def has_untracked_trial_orders(self) -> bool:
        with self._trial_submission_lock:
            return bool(self._untracked_trial_order_ids)

    def resolve_untracked_trial_orders_after_reconciliation(self) -> None:
        with self._trial_submission_lock:
            self._untracked_trial_order_ids.clear()

    def finish_trial_run_shutdown(self) -> None:
        with self._trial_submission_lock:
            self._trial_run_stopping = False
            self._trial_shutdown_cancel_requested_order_ids.clear()
            if self.trial_run_execution is None:
                self._trial_bound_strategy = None

    def configure_risk(self, config: Optional[Dict[str, Any]] = None):
        """Configure pre-order risk controls."""
        config = config or {}
        self.risk_manager.configure(config)
        if getattr(self.gateway, "requires_persistent_risk_state", False):
            if self._live_risk_state_store is None or config.get("broker_id"):
                self._configure_live_risk_state(config)
        elif config.get("initial_capital"):
            self.risk_manager.set_day_open_balance(float(config.get("initial_capital") or 0.0))

    def _configure_live_risk_state(self, config: Dict[str, Any]) -> None:
        account_id = str(getattr(self.gateway.account, "account_id", "") or "").strip()
        broker_id = str(config.get("broker_id") or "").strip()
        trading_day = str(getattr(self.gateway, "trading_day", "") or "").strip()
        if not account_id or not broker_id or not trading_day:
            missing = ", ".join(
                label
                for label, value in (
                    ("account_id", account_id),
                    ("broker_id", broker_id),
                    ("trading_day", trading_day),
                )
                if not value
            )
            self.risk_manager.fail_closed_for_persistence(
                f"Live risk state identity is incomplete: {missing}"
            )
            return

        state_path = config.get("live_risk_state_path")
        path = (
            Path(str(state_path))
            if state_path
            else Path(__file__).resolve().parents[3]
            / "data"
            / "historical"
            / "live_risk_state.json"
        )
        scope_material = f"{broker_id}\0{account_id}".encode("utf-8")
        scope = f"live:{hashlib.sha256(scope_material).hexdigest()}"
        store = LiveRiskStateStore(path)
        initial_balance = float(getattr(self.gateway.account, "balance", 0.0) or 0.0)
        try:
            self.risk_manager.bind_persistent_state(
                store,
                scope=scope,
                trading_day=trading_day,
                day_open_balance=initial_balance,
            )
        except RuntimeError as exc:
            logger.error("实盘风控状态绑定失败: %s", exc)
            self.risk_manager.fail_closed_for_persistence(
                f"Live risk state persistence failed: {exc}"
            )
            return
        self._live_risk_state_store = store
        self._live_risk_scope = scope
        self._live_risk_trading_day = trading_day

    def _on_gateway_trading_day(self, trading_day: str) -> None:
        normalized_day = str(trading_day or "").strip()
        if (
            not normalized_day
            or self._live_risk_state_store is None
            or not self._live_risk_scope
            or normalized_day == self._live_risk_trading_day
        ):
            return
        try:
            self.risk_manager.bind_persistent_state(
                self._live_risk_state_store,
                scope=self._live_risk_scope,
                trading_day=normalized_day,
                day_open_balance=0.0,
            )
        except RuntimeError as exc:
            logger.error("实盘交易日风控状态切换失败: %s", exc)
            self.risk_manager.fail_closed_for_persistence(
                f"Live risk state trading-day switch failed: {exc}"
            )
            return
        self._live_risk_trading_day = normalized_day

    def _on_gateway_account(self, account: AccountInfo) -> None:
        if (
            self._live_risk_state_store is not None
            and self._live_risk_trading_day
            and self.risk_manager.day_open_balance <= 0
            and float(getattr(account, "balance", 0.0) or 0.0) > 0
        ):
            self.risk_manager.set_day_open_balance(float(account.balance))

    def start(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """启动交易引擎"""
        try:
            if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
                logger.warning("交易引擎已在运行中")
                return False

            config = config or {}
            self._bar_interval = max(1, int(config.get("bar_interval_minutes", 1)))
            self._emit_first_tick_bar = bool(config.get("emit_first_tick_bar", False))
            self._first_tick_bar_symbols.clear()
            self._first_tick_bar_emitted_symbols.clear()
            self._first_tick_bar_skip_reason = ""
            self._last_strategy_tick = None
            self._last_strategy_tick_sequence = 0
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

            self.configure_risk(config)

            if self.strategy:
                try:
                    with self._strategy_callback_lock:
                        self.strategy.on_init()
                        set_clock = getattr(self.strategy, "set_monotonic_clock", None)
                        if callable(set_clock):
                            set_clock(self._monotonic)
                        set_market_capability = getattr(self.strategy, "set_market_order_supported", None)
                        if callable(set_market_capability):
                            set_market_capability(
                                bool(
                                    self.gateway.supports_market_order(
                                        getattr(self.strategy, "symbol", "")
                                    )
                                )
                            )
                        self.strategy.on_start()
                except Exception as e:
                    logger.error(f"策略初始化失败: {e}")
                    self.status = TradingStatus.ERROR
                    return False

            if self.order_manager.start() is False:
                with self._trial_submission_lock:
                    if self.trial_run_execution is not None:
                        self._trial_run_stopping = True
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

    def stop(self) -> bool:
        """停止交易引擎"""
        try:
            with self._trial_submission_lock:
                preserve_trial = self.trial_run_execution is not None or bool(self._untracked_trial_order_ids)
            if preserve_trial:
                strategy = self.strategy
                self.begin_trial_run_shutdown(strategy)
                self.order_manager.stop()
                with self._trial_submission_lock:
                    current_order_id = str(
                        getattr(self.trial_run_execution, "current_order_id", "") or ""
                    )
                if current_order_id:
                    already_requested = str(
                        getattr(strategy, "_chase_pending_cancel_order_id", "") or ""
                    ) == current_order_id
                    self.request_trial_run_shutdown_cancel(
                        current_order_id,
                        already_requested=already_requested,
                    )
                self.status = TradingStatus.ERROR
                logger.error("试运行执行域尚未完成券商对账，保留网关连接和订单证据")
                return False

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
            self.risk_manager.close_persistent_state()
            self.status = TradingStatus.STOPPED
            logger.info("交易引擎已停止")
            return True
        except Exception as e:
            logger.error(f"停止交易引擎失败: {e}")
            self.status = TradingStatus.ERROR
            return False

    def send_signal(self, signal: 'Signal', *, allow_stale_close: bool = False) -> str:
        """Send a manual or external signal through the real broker path."""
        if allow_stale_close:
            return self._send_gateway_signal(signal, allow_stale_close=True)
        return self._send_gateway_signal(signal)

    def _send_gateway_signal(self, signal: 'Signal', *, allow_stale_close: bool = False) -> str:
        """Send a real signal through the existing risk and gateway path."""
        with self._signal_submission_lock:
            self.last_reject_reason = ""
            if self._signal_submission_in_flight:
                self.last_reject_reason = "Another order submission is in progress"
                return ""
            self._signal_submission_in_flight = True
            try:
                if self.status not in (TradingStatus.TRADING, TradingStatus.CONNECTED):
                    # 也检查网关状态，允许网关已连接但引擎未正式 start 的场景（手动交易）
                    if self.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                        logger.warning("交易引擎未连接")
                        self.last_reject_reason = "Trading engine is not connected"
                        return ""

                risk_result = self.risk_manager.check_signal(
                    signal,
                    positions=self.gateway.positions,
                    active_orders=self.gateway.orders.values(),
                    account=getattr(self.gateway, "account", None),
                    market_data=self._market_data_for_symbol(signal.symbol),
                    allow_stale_close=allow_stale_close,
                )
                if not risk_result.allowed:
                    self.last_reject_reason = risk_result.reason
                    logger.warning(f"风控拒单: {risk_result.reason}")
                    return ""

                order_id = self.order_manager.submit_order(signal)
                if order_id:
                    self.risk_manager.record_order(signal)
                    logger.info(f"发送信号: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
                else:
                    gateway_reason = str(
                        getattr(self.gateway, "last_reject_reason", "") or ""
                    )
                    if gateway_reason:
                        self.last_reject_reason = gateway_reason
                return order_id
            except Exception as e:
                self._error_count += 1
                logger.error(f"发送信号失败: {e}")
                if self._error_count >= self._max_errors:
                    logger.error(f"错误次数过多 ({self._error_count}), 停止交易")
                    self.stop()
                return ""
            finally:
                self._signal_submission_in_flight = False

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a manual or external order through the real broker path."""
        check = self.risk_manager.check_cancel_request(order_id)
        if not check.allowed:
            self.last_reject_reason = check.reason
            logger.warning("风控拒绝撤单: %s", check.reason)
            return False
        accepted = self._cancel_gateway_order(order_id)
        self.risk_manager.record_cancel(order_id, accepted=accepted)
        return accepted

    def _cancel_strategy_order(self, order_id: str) -> bool:
        """Cancel only the bound strategy order through its selected adapter."""
        check = self.risk_manager.check_cancel_request(order_id)
        if not check.allowed:
            self.last_reject_reason = check.reason
            logger.warning("风控拒绝策略撤单: %s", check.reason)
            return False
        accepted = False
        try:
            accepted = bool(self.strategy_execution_adapter.cancel(order_id))
        except Exception as e:
            logger.error(f"撤销策略订单失败: {e}")
        self.risk_manager.record_cancel(order_id, accepted=accepted)
        return accepted

    def _cancel_gateway_order(self, order_id: str) -> bool:
        """Request a real broker cancellation; callback confirmation is separate."""
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

    def on_timer(self, now_monotonic: Optional[float] = None):
        """Run timer-driven strategy work without requiring another tick."""
        now = self._monotonic() if now_monotonic is None else float(now_monotonic)
        execution = self.trial_run_execution
        if (
            execution is not None
            and execution.final_outcome is TrialRunOutcome.RUNNING
            and (execution.real_close_trade_id or execution.simulated_close_trade_id)
        ):
            self.reconcile_trial_run_completion()
        self._handle_trial_hold_deadline(now)
        with self._strategy_callback_lock:
            tick = self._last_strategy_tick
            quote_sequence = self._last_strategy_tick_sequence
        if tick is not None:
            self._maybe_chase_strategy_order(
                tick,
                now_monotonic=now,
                quote_sequence=quote_sequence,
            )

    def _handle_trial_hold_deadline(self, now_monotonic: float) -> None:
        execution = self.trial_run_execution
        if execution is None or execution.final_outcome is not TrialRunOutcome.RUNNING:
            return
        if not execution.real_entry_trade_id or execution.real_close_trade_id:
            return
        deadline = self._trial_hold_deadline_monotonic
        if deadline is None:
            deadline = execution.hold_deadline_monotonic
        if deadline is None or now_monotonic < float(deadline):
            return
        strategy = self.strategy
        if strategy is None:
            return
        if execution.current_order_id:
            return
        tick = self._last_strategy_tick
        stale_reason = self._stale_market_data_reason(tick) if tick is not None else "missing_market_data"
        if stale_reason or float(getattr(tick, "last_price", 0.0) or 0.0) <= 0:
            self._mark_trial_execution_failed(
                "flatten_required_market_data",
                stale_reason or "invalid_market_price",
                execution=execution,
            )
            self._persist_trial_execution()
            return
        request_close = getattr(strategy, "request_close_from_tick", None)
        if callable(request_close) and request_close(tick):
            self._dispatch_strategy_signals()
            self._persist_trial_execution()

    def reconcile_trial_run_completion(self) -> bool:
        """Finalize a completed close only from a fresh broker snapshot."""
        execution = self.trial_run_execution
        if execution is None or not (
            execution.real_close_trade_id or execution.simulated_close_trade_id
        ):
            return False
        gateway = self.gateway
        try:
            reconciliation = gateway.refresh_reconciliation(timeout_seconds=8.0)
        except Exception as exc:
            logger.error("Trial-run completion reconciliation failed: %s", exc)
            positions = getattr(gateway, "positions", {})
            cached_position_volume = sum(
                abs(int(getattr(position, "volume", 0) or 0))
                for position in positions.values()
                if self._symbols_match(getattr(position, "symbol", ""), execution.symbol)
            ) if isinstance(positions, dict) else execution.broker_position_volume
            orders = getattr(gateway, "orders", {})
            cached_active_order_ids = [
                str(getattr(order, "order_id", "") or "")
                for order in orders.values()
                if self._symbols_match(getattr(order, "symbol", ""), execution.symbol)
                and callable(getattr(order, "is_active", None))
                and order.is_active()
            ] if isinstance(orders, dict) else list(execution.broker_active_order_ids)
            execution.evaluate(cached_position_volume, cached_active_order_ids, False)
            self._persist_trial_execution()
            return False
        reconcile_ok = bool(
            isinstance(reconciliation, dict)
            and reconciliation.get("ok") is True
            and reconciliation.get("fresh") is True
        )
        positions = getattr(gateway, "positions", {})
        broker_position_volume = sum(
            abs(int(getattr(position, "volume", 0) or 0))
            for position in positions.values()
            if self._symbols_match(getattr(position, "symbol", ""), execution.symbol)
        ) if isinstance(positions, dict) else 0
        orders = getattr(gateway, "orders", {})
        active_order_ids = [
            str(getattr(order, "order_id", "") or "")
            for order in orders.values()
            if self._symbols_match(getattr(order, "symbol", ""), execution.symbol)
            and callable(getattr(order, "is_active", None))
            and order.is_active()
        ] if isinstance(orders, dict) else []
        execution.evaluate(
            broker_position_volume=broker_position_volume,
            broker_active_order_ids=active_order_ids,
            reconcile_ok=reconcile_ok,
        )
        if reconcile_ok and (broker_position_volume or active_order_ids):
            self._mark_trial_execution_failed(
                "broker_state_not_flat",
                (
                    f"position_volume={broker_position_volume}:"
                    f"active_order_ids={','.join(active_order_ids)}"
                ),
                execution=execution,
            )
        self._persist_trial_execution()
        return execution.final_outcome in {
            TrialRunOutcome.PASSED_REAL,
            TrialRunOutcome.PASSED_SIMULATED,
        }

    def _on_tick(self, tick: MarketData):
        """行情推送内部处理，同时更新预埋单市场数据"""
        stale_reason = self._stale_market_data_reason(tick)
        if stale_reason:
            self.last_reject_reason = stale_reason
            self._first_tick_bar_skip_reason = "stale_market_data"
            with self._strategy_callback_lock:
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
        with self._strategy_callback_lock:
            strategy = self.strategy
            if strategy:
                strategy_symbol = str(getattr(strategy, "symbol", "") or "")
                if not strategy_symbol or self._symbols_match(tick.symbol, strategy_symbol):
                    self._last_strategy_tick = tick
                    self._last_strategy_tick_sequence += 1
                available = getattr(self.gateway.account, "available", 0.0)
                if available > 0:
                    strategy.current_capital = available
                on_tick = getattr(strategy, "on_tick", None)
                if callable(on_tick):
                    try:
                        on_tick(tick)
                        self._dispatch_strategy_signals()
                    except Exception as e:
                        logger.error(f"处理 Tick 策略回调失败: {e}")
                quote_sequence = self._last_strategy_tick_sequence
            else:
                quote_sequence = 0

        self._maybe_chase_strategy_order(
            tick,
            now_monotonic=self._monotonic(),
            quote_sequence=quote_sequence,
        )

        # Bar aggregation — only calls on_bar when a bar completes
        if self.bar_aggregator:
            try:
                finished = self.bar_aggregator.push(tick)
                if finished is None:
                    with self._strategy_callback_lock:
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

        strategy = self.strategy
        if strategy is None:
            return bar
        if bar is None:
            bar = self._tick_to_bar(tick)
        bar_frame = pd.DataFrame([bar.to_dict()], index=[tick.timestamp])

        existing = strategy.data.get(tick.symbol)
        if existing is None or existing.empty:
            updated = bar_frame
        else:
            updated = pd.concat([existing, bar_frame])
            updated = updated[~updated.index.duplicated(keep="last")].sort_index()

        # Keep only the tail to bound memory usage (1000 bars ≈ 1 trading day of 1m)
        if len(updated) > 1000:
            updated = updated.iloc[-1000:]

        strategy.data[tick.symbol] = updated
        return bar

    def _on_bar_completed(self, symbol: str, bar):
        """Called by BarAggregator when a bar completes."""
        with self._strategy_callback_lock:
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

        strategy = self.strategy
        if strategy is None:
            return
        bar_frame = pd.DataFrame([bar.to_dict()], index=[bar["datetime"]])
        existing = strategy.data.get(symbol)
        if existing is None or existing.empty:
            updated = bar_frame
        else:
            updated = pd.concat([existing, bar_frame])
            updated = updated[~updated.index.duplicated(keep="last")].sort_index()
        if len(updated) > 1000:
            updated = updated.iloc[-1000:]
        strategy.data[symbol] = updated

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
        return symbol_key(symbol)

    @classmethod
    def _symbols_match(cls, left: str, right: str) -> bool:
        return symbols_match(left, right)

    def _dispatch_strategy_signals(self):
        """Send newly generated strategy signals to the broker gateway."""
        strategy = self.strategy
        if strategy is None:
            return
        signals = getattr(strategy, "signals", [])
        if self._processed_signal_count > len(signals):
            self._processed_signal_count = len(signals)

        new_signals = signals[self._processed_signal_count:]
        for signal in new_signals:
            if not self._submit_strategy_signal(signal):
                break
        self._processed_signal_count = len(signals)

    def _begin_trial_submission(self) -> bool:
        with self._trial_submission_lock:
            if self._trial_run_stopping:
                raise RuntimeError("trial_run_stopping")
            if self.trial_run_execution is None:
                if self.strategy is not None and self.strategy is self._trial_bound_strategy:
                    raise RuntimeError("trial_execution_unbound")
                return False
            if self.trial_run_execution.final_outcome is not TrialRunOutcome.RUNNING:
                raise RuntimeError("trial_execution_terminal")
            if self._trial_submission_in_flight:
                self._mark_trial_execution_failed(
                    "order_chain_registration_failed",
                    "concurrent_trial_submission",
                )
                raise RuntimeError("concurrent_trial_submission")
            self._trial_submission_in_flight = True
            return True

    def _finish_trial_submission(self) -> None:
        with self._trial_submission_lock:
            self._trial_submission_in_flight = False
            callbacks = list(self._deferred_trial_callbacks)
            self._deferred_trial_callbacks.clear()
        for callback_type, payload in callbacks:
            if callback_type == "order":
                self._on_order(payload)
            else:
                self._on_trade(payload)
        with self._trial_submission_lock:
            stopping = self._trial_run_stopping
            current_order_id = str(
                getattr(self.trial_run_execution, "current_order_id", "") or ""
            )
        if stopping and current_order_id:
            self.request_trial_run_shutdown_cancel(current_order_id)

    def _defer_trial_callback(self, callback_type: str, payload: Any) -> bool:
        with self._trial_submission_lock:
            if not self._trial_submission_in_flight:
                return False
            try:
                payload = copy.copy(payload)
            except Exception:
                pass
            self._deferred_trial_callbacks.append((callback_type, payload))
            return True

    def _submit_strategy_signal(self, signal: 'Signal') -> str:
        try:
            trial_submission = self._begin_trial_submission()
        except RuntimeError as exc:
            reject_callback = getattr(self.strategy, "mark_signal_rejected", None)
            if callable(reject_callback):
                reject_callback(str(exc))
            return ""
        try:
            adapter = self.strategy_execution_adapter
            try:
                order_id = adapter.submit(signal)
            except Exception as exc:
                self.last_reject_reason = str(exc) or type(exc).__name__
                order_id = ""
            if not order_id:
                if trial_submission:
                    role = "close" if str(getattr(signal, "comment", "") or "") == "sell_close" else "entry"
                    track = "simulated" if getattr(adapter, "is_simulation", False) else "real"
                    self._mark_trial_execution_failed(
                        f"{track}_{role}_submission_failed",
                        str(self.last_reject_reason or "execution_adapter_rejected"),
                    )
                reject_callback = getattr(self.strategy, "mark_signal_rejected", None)
                if callable(reject_callback):
                    reject_callback(getattr(self, "last_reject_reason", ""))
                logger.warning(
                    "Strategy signal rejected: %s %s %s",
                    signal.symbol,
                    signal.direction,
                    signal.volume,
                )
                return ""
            metadata = self._notify_strategy_signal_submitted(signal, order_id)
            if getattr(adapter, "is_simulation", False):
                execution = self.trial_run_execution
                synthetic_order = adapter.get_order(order_id)
                if execution is None or synthetic_order is None:
                    self._mark_trial_execution_failed(
                        "simulation_evidence_conflict",
                        "synthetic_order_missing_after_submission",
                        execution=execution,
                    )
                    return ""
                try:
                    execution.record_simulated_submission(
                        order_id,
                        str(metadata.get("role") or ""),
                        float(getattr(synthetic_order, "price", 0.0) or 0.0),
                        synthetic_order.direction.value,
                        synthetic_order.offset.value,
                        source_order_id=self._simulation_source_order_id,
                        attempt=int(metadata.get("attempt", 0) or 0),
                        parent_order_id=str(metadata.get("parent_order_id") or ""),
                        symbol=synthetic_order.symbol,
                        volume=synthetic_order.volume,
                    )
                    self._persist_trial_execution()
                except Exception as exc:
                    try:
                        adapter.cancel(order_id)
                    except Exception:
                        pass
                    self._mark_trial_execution_failed(
                        "simulation_evidence_conflict",
                        type(exc).__name__,
                        execution=execution,
                    )
                    return ""
            elif not self._record_trial_submission(signal, order_id, metadata):
                return ""
            return order_id
        finally:
            if trial_submission:
                self._finish_trial_submission()

    def _notify_strategy_signal_submitted(self, signal: 'Signal', order_id: str):
        notify = getattr(self.strategy, "on_signal_submitted", None) if self.strategy else None
        if callable(notify):
            try:
                return notify(signal, order_id)
            except Exception as e:
                logger.error("Strategy order-submit notification failed: %s", e)
        return {}

    def _mark_trial_execution_failed(
        self,
        code: str,
        basis: str,
        *,
        execution: Optional[TrialRunExecutionState] = None,
    ) -> None:
        if execution is None:
            with self._trial_submission_lock:
                execution = self.trial_run_execution
        if execution is None:
            return
        try:
            execution.mark_failed(code, basis)
            self._persist_trial_execution()
        except Exception as exc:
            logger.error("Unable to mark trial run failed (%s): %s", code, exc)

    def _fail_untracked_trial_order(self, order_id: str, reason: str) -> bool:
        with self._trial_submission_lock:
            self._untracked_trial_order_ids.add(order_id)
        cancelled = self.cancel_order(order_id)
        basis = "broker_cancel_requested" if cancelled else "broker_cancel_request_failed"
        self._mark_trial_execution_failed(
            "order_chain_registration_failed",
            f"{basis}:{reason}",
        )
        reject_callback = getattr(self.strategy, "mark_signal_rejected", None)
        if callable(reject_callback):
            reject_callback("order_chain_registration_failed")
        return False

    def _record_trial_submission(self, signal, order_id: str, metadata: Any) -> bool:
        execution = self.trial_run_execution
        if execution is None:
            return True
        if not isinstance(metadata, dict) or not metadata:
            logger.error("Trial-run order %s has no ownership metadata", order_id)
            return self._fail_untracked_trial_order(order_id, "missing_order_metadata")
        try:
            execution.record_real_submission(
                order_id=order_id,
                role=str(metadata.get("role") or ""),
                price=float(metadata.get("price", getattr(signal, "price", 0.0)) or 0.0),
                direction=metadata.get("direction", getattr(signal, "direction", "")),
                offset=metadata.get("offset", getattr(signal, "offset", "")),
                attempt=int(metadata.get("attempt", 0)),
                parent_order_id=str(metadata.get("parent_order_id") or ""),
                status="submitting",
                symbol=str(metadata.get("symbol") or getattr(signal, "symbol", "")),
                volume=int(metadata.get("volume", getattr(signal, "volume", 1))),
            )
            self._persist_trial_execution()
            return True
        except Exception as exc:
            logger.error("Trial-run submission evidence rejected for %s: %s", order_id, exc)
            return self._fail_untracked_trial_order(order_id, type(exc).__name__)

    def _maybe_chase_strategy_order(
        self,
        tick: MarketData,
        now_monotonic: Optional[float] = None,
        quote_sequence: Optional[int] = None,
    ):
        with self._strategy_callback_lock:
            with self._trial_submission_lock:
                if self._trial_run_stopping:
                    return
            strategy = self.strategy
            action_fn = getattr(strategy, "next_chase_action", None) if strategy else None
            if not callable(action_fn):
                return
            role_fn = getattr(strategy, "pending_chase_role", None)
            role = role_fn() if callable(role_fn) else "entry"
            required = 1 if role in {"exit", "close"} else 2
            rate_snapshot = self.risk_manager.order_rate_snapshot(
                now_monotonic,
                required_capacity=required,
            )
            capacity_notice = getattr(strategy, "on_rate_capacity", None)
            if rate_snapshot["remaining"] < required:
                if callable(capacity_notice):
                    capacity_notice(False, rate_snapshot)
                return
            if callable(capacity_notice):
                capacity_notice(True, rate_snapshot)
            if self._stale_market_data_reason(tick):
                waiting_quote = getattr(strategy, "on_fresh_quote_required", None)
                if callable(waiting_quote):
                    waiting_quote()
                return
            try:
                action = action_fn(
                    tick,
                    now_monotonic=now_monotonic,
                    quote_sequence=quote_sequence,
                ) or {}
            except Exception as e:
                logger.error("Strategy chase decision failed: %s", e)
                return

        action_type = action.get("action")
        if action_type == "cancel":
            order_id = str(action.get("order_id") or "")
            if not order_id:
                return
            if not self._cancel_strategy_order(order_id):
                with self._strategy_callback_lock:
                    failed = getattr(strategy, "on_chase_cancel_failed", None)
                    if callable(failed):
                        failed(order_id)
                logger.warning("Strategy chase cancel failed: %s", order_id)
            return

        if action_type == "submit":
            signal = action.get("signal")
            if not signal:
                return
            with self._strategy_callback_lock:
                if self.strategy is not strategy:
                    return
                order_id = self._submit_strategy_signal(signal)
                if order_id:
                    self._processed_signal_count = max(
                        self._processed_signal_count,
                        len(getattr(strategy, "signals", [])),
                    )

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
        if self._defer_trial_callback("order", order):
            return
        order_id = str(getattr(order, "order_id", "") or "")
        status_value = str(
            getattr(getattr(order, "status", None), "value", getattr(order, "status", "")) or ""
        ).strip().lower()
        with self._trial_submission_lock:
            execution = self.trial_run_execution
            strategy = self.strategy
            untracked_order = order_id in self._untracked_trial_order_ids
        if execution is not None and execution.owns_order(order_id):
            try:
                execution.record_real_order_update(
                    order_id,
                    status=getattr(getattr(order, "status", ""), "value", getattr(order, "status", "")),
                    symbol=getattr(order, "symbol", None),
                    volume=int(getattr(order, "volume", 1) or 0),
                    direction=getattr(order, "direction", None),
                    traded_volume=int(getattr(order, "traded_volume", 0) or 0),
                )
            except Exception as exc:
                logger.error("Trial-run order evidence rejected for %s: %s", order_id, exc)
                current_order_id = execution.current_order_id
                if current_order_id:
                    self._cancel_strategy_order(current_order_id)
                self._mark_trial_execution_failed(
                    "order_evidence_conflict",
                    f"order_id={order_id}:{type(exc).__name__}",
                    execution=execution,
                )
                reject_callback = getattr(strategy, "mark_signal_rejected", None)
                if callable(reject_callback):
                    reject_callback("order_evidence_conflict")
                return
            self._persist_trial_execution()
            if status_value == "rejected":
                owned = next(
                    (item for item in execution.order_chain if item.order_id == order_id),
                    None,
                )
                if owned is not None:
                    failure_code = "real_close_rejected" if str(owned.role).lower() in {"exit", "close"} else "real_entry_rejected"
                    self._mark_trial_execution_failed(
                        failure_code,
                        f"order_id={order_id}",
                        execution=execution,
                    )
        if order_id:
            self.gateway.orders[order_id] = order
        self.order_manager.update_order(order)
        if status_value in {"cancelled", "filled", "rejected", "failed"}:
            with self._trial_submission_lock:
                self._trial_shutdown_cancel_requested_order_ids.discard(order_id)
                if untracked_order:
                    self._untracked_trial_order_ids.discard(order_id)
        if strategy:
            with self._strategy_callback_lock:
                try:
                    if status_value == "cancelled":
                        cancel_confirmed = getattr(strategy, "on_chase_cancel_confirmed", None)
                        if callable(cancel_confirmed):
                            cancel_confirmed(
                                order_id,
                                quote_sequence=self._last_strategy_tick_sequence,
                                market=self._last_strategy_tick,
                            )
                    strategy.on_order(order)
                except Exception as e:
                    logger.error(f"策略订单回调失败: {e}")

    def _on_trade(self, trade: 'Trade'):
        """成交回调"""
        if self._defer_trial_callback("trade", trade):
            return
        order_id = str(getattr(trade, "order_id", "") or "")
        with self._trial_submission_lock:
            execution = self.trial_run_execution
            strategy = self.strategy
            untracked_order = order_id in self._untracked_trial_order_ids
        if execution is not None:
            if self.simulation_adapter is not None and execution.owns_order(order_id):
                try:
                    execution.record_late_real_fill_conflict(
                        order_id=order_id,
                        trade_id=str(getattr(trade, "trade_id", "") or ""),
                        symbol=getattr(trade, "symbol", None),
                        volume=int(getattr(trade, "volume", 1) or 0),
                        direction=getattr(trade, "direction", None),
                    )
                except Exception as exc:
                    logger.error("Late real fill evidence could not be recorded: %s", exc)
                    self._mark_trial_execution_failed(
                        "late_real_fill_conflict",
                        f"order_id={order_id}:{type(exc).__name__}",
                        execution=execution,
                    )
                self.disable_simulation()
                return
            if not execution.owns_order(order_id) and not untracked_order:
                return
            if untracked_order:
                self._mark_trial_execution_failed(
                    "untracked_order_fill",
                    f"order_id={order_id}",
                    execution=execution,
                )
            else:
                current_order_id = execution.current_order_id
                try:
                    execution.record_real_trade(
                        order_id=order_id,
                        trade_id=str(getattr(trade, "trade_id", "") or ""),
                        symbol=getattr(trade, "symbol", None),
                        volume=int(getattr(trade, "volume", 1) or 0),
                        direction=getattr(trade, "direction", None),
                    )
                    broker_order = getattr(self.gateway, "orders", {}).get(order_id)
                    if broker_order is not None:
                        try:
                            broker_order.status = type(broker_order.status)("filled")
                        except (TypeError, ValueError):
                            broker_order.status = "filled"
                    if execution.real_entry_trade_id == str(getattr(trade, "trade_id", "") or ""):
                        deadline = self._monotonic() + float(self._trial_max_hold_seconds)
                        wall_deadline = (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=float(self._trial_max_hold_seconds))
                        ).isoformat()
                        execution.set_holding_deadline(deadline, wall_deadline)
                        self._trial_hold_deadline_monotonic = deadline
                    self._persist_trial_execution()
                except Exception as exc:
                    logger.error("Trial-run trade evidence rejected for %s: %s", order_id, exc)
                    current_order_id = execution.current_order_id
                    if current_order_id:
                        self._cancel_strategy_order(current_order_id)
                    self._mark_trial_execution_failed(
                        "trade_evidence_conflict",
                        f"order_id={order_id}:{type(exc).__name__}",
                        execution=execution,
                    )
                    reject_callback = getattr(strategy, "mark_signal_rejected", None)
                    if callable(reject_callback):
                        reject_callback("trade_evidence_conflict")
                    return
                if current_order_id and current_order_id != order_id:
                    if not self._cancel_strategy_order(current_order_id):
                        self._mark_trial_execution_failed(
                            "late_fill_cancel_failed",
                            f"filled_order={order_id}:active_order={current_order_id}",
                            execution=execution,
                        )
        if strategy:
            with self._strategy_callback_lock:
                try:
                    strategy.update_position(trade.symbol, trade)
                    strategy.on_trade(trade)
                except Exception as e:
                    logger.error(f"策略成交回调失败: {e}")
                    self._mark_trial_execution_failed(
                        "strategy_trade_callback_failed",
                        type(e).__name__,
                        execution=execution,
                    )

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
