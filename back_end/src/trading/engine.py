"""
交易引擎模块
"""

import logging
import os
from datetime import datetime
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
from .bars import BarAggregator
from .ledger import ExecutionLedger
from ..common.exceptions import ExceptionHandler


class TradingEngine:
    """实盘交易引擎"""

    def __init__(self, gateway: GatewayBase = None):
        self.gateway = gateway or create_gateway("vnpy")
        self.strategy = None
        self._processed_signal_count = 0
        self.status = TradingStatus.STOPPED

        self.order_manager = OrderManager(self.gateway)
        self.order_manager.dispatch_signal = self.send_signal
        self.trades = {}
        self._seen_trades = set()
        self.ledger = None
        self._bars = {}
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
        self.gateway.on_tick_callback = self._on_tick
        self.gateway.on_error_callback = self._on_gateway_error

        self.order_manager.on_order_callback = self._on_order
        self.order_manager.on_trade_callback = self._on_trade
        self.order_manager.on_pre_order_status_change = self._on_pre_order_status_change

        self.risk_manager = RiskManager()
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
        if hasattr(self.gateway,'ready'):
            self.risk_manager.config.require_account=True
        account = self.gateway.account
        if self.ledger is None and account.account_id:
            default_path = ':memory:' if os.getenv('QUANT_ENV') == 'test' else 'data/runtime/execution.db'
            self.ledger = ExecutionLedger(os.getenv('QUANT_LEDGER_PATH', default_path),
                f"{config.get('broker_id', '')}:{account.account_id}")
            for trade in self.trades.values():
                self.ledger.record_trade(trade)
            if self.ledger.unresolved():
                self.risk_manager.set_emergency_stop(True, 'Unresolved execution intent; reconcile before resuming')
        if config.get("initial_capital"):
            balance = float(config.get("initial_capital") or 0.0)
            day = account.trading_day or (datetime.now().strftime('%Y%m%d') if os.getenv('QUANT_ENV') == 'test' else '')
            baseline = self.ledger.day_baseline(day, balance) if self.ledger else balance
            self.risk_manager.set_day_open_balance(baseline)

    def start(self, config: Dict[str, Any] = None) -> bool:
        """启动交易引擎"""
        try:
            if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
                logger.warning("交易引擎已在运行中")
                return False

            config = config or {}
            self.configure_risk(config)
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

            self.status = TradingStatus.STOPPED
            logger.info("交易引擎已停止")
        except Exception as e:
            logger.error(f"停止交易引擎失败: {e}")
            self.status = TradingStatus.STOPPED

    def close(self):
        self.stop()
        self.gateway.disconnect()
        if self.ledger:
            self.ledger.close()
            self.ledger = None

    def send_signal(self, signal):
        with self.order_manager.lock:
            return self._send_signal_locked(signal)

    def _send_signal_locked(self, signal: 'Signal') -> str:
        """发送交易信号"""
        self.last_reject_reason = ""
        if self.status not in (TradingStatus.TRADING, TradingStatus.CONNECTED):
            # 也检查网关状态，允许网关已连接但引擎未正式 start 的场景（手动交易）
            if self.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                logger.warning("交易引擎未连接")
                self.last_reject_reason = "Trading engine is not connected"
                return ""

        try:
            account = self.gateway.account
            if self.ledger and account.trading_day:
                self.risk_manager.set_day_open_balance(self.ledger.day_baseline(account.trading_day,account.balance))
            specs=getattr(self.gateway,'contract_specs',None)
            if specs is not None:
                spec=specs.get(signal.symbol,{})
                if not spec or not spec.get('size'):
                    self.last_reject_reason='Contract metadata is unavailable'
                    return ''
                self.risk_manager.config.contract_multipliers[signal.symbol]=spec['size']
                if signal.offset.value=='open':
                    margin=spec.get('margin_rate')
                    if not margin or not account.fields_known:
                        self.last_reject_reason='Broker margin rate/account fields must be verified before opening'
                        return ''
                    pending=sum(max(0,o.volume-o.traded_volume)*o.price*specs.get(o.symbol,{}).get('size',0)*
                        (specs.get(o.symbol,{}).get('margin_rate') or 1) for o in self.gateway.orders.values()
                        if o.is_active() and o.offset.value=='open' and o.status.value=='submitting')
                    if signal.price*signal.volume*spec['size']*margin+pending>account.available:
                        self.last_reject_reason='Insufficient available margin after reservations'
                        return ''
            if self.ledger and self.ledger.unresolved():
                self.last_reject_reason='Unresolved execution intent requires reconciliation'
                return ''
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

            if hasattr(self.gateway, 'ready') and not self.gateway.ready:
                self.last_reject_reason = 'Gateway snapshot is not ready'
                return ''
            identity = self.ledger.begin_intent(signal) if self.ledger else None
            order_id = self.order_manager._submit_validated(signal)
            if self.ledger:
                self.ledger.complete_intent(identity, order_id)
                if order_id:
                    self.ledger.record_order(self.gateway.orders[order_id])
            if order_id:
                self.risk_manager.record_order(signal)
                logger.info(f"发送信号: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}")
            return order_id
        except Exception as e:
            self.risk_manager.set_emergency_stop(True, 'Submission outcome unknown; reconcile before retry')
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
        self.order_manager.update_market_data(tick.symbol, {
            "last_price": tick.last_price,
            "bid_price_1": tick.bid_price_1,
            "ask_price_1": tick.ask_price_1,
            "timestamp": tick.timestamp,
        })
        if not self.strategy or self.status != TradingStatus.TRADING:
            return
        symbols = self.strategy.params.get("symbols") or [getattr(self.strategy, "symbol", None)]
        if tick.symbol not in symbols:
            return
        try:
            interval = self.strategy.params.get('timeframe', '1d')
            if interval == 'tick':  # Explicit custom tick strategies only; built-ins use bars.
                bar = self._tick_to_bar(tick)
            else:
                aggregate = self._bars.setdefault((tick.symbol, interval), BarAggregator(interval))
                completed = aggregate.update(tick)
                if completed is None:
                    return
                import pandas as pd
                bar = pd.Series(completed)
            self.strategy.current_date = bar['datetime']
            self.strategy.contract_specs = getattr(self.gateway, 'contract_specs', {})
            self.strategy.current_capital = max(0, self.gateway.account.available)
            self.strategy.on_bar(bar)
            self._dispatch_strategy_signals()
            self._append_live_bar(tick, bar)
        except Exception as e:
            logger.error(f"处理行情数据失败: {e}")

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
        """Append the processed tick to rolling live data after strategy.on_bar."""
        import pandas as pd

        if bar is None:
            bar = self._tick_to_bar(tick)
        bar_frame = pd.DataFrame([bar.to_dict()], index=[bar["datetime"]])

        existing = self.strategy.data.get(tick.symbol)
        if existing is None or existing.empty:
            updated = bar_frame
        else:
            updated = pd.concat([existing, bar_frame])
            updated = updated[~updated.index.duplicated(keep="last")].sort_index().tail(2000)

        self.strategy.data[tick.symbol] = updated
        return bar

    def _dispatch_strategy_signals(self):
        """Send newly generated strategy signals to the broker gateway."""
        signals = getattr(self.strategy, "signals", [])
        if self._processed_signal_count > len(signals):
            self._processed_signal_count = len(signals)

        new_signals = signals[self._processed_signal_count:]
        for signal in new_signals:
            order_id = self.send_signal(signal)
            if not order_id:
                logger.warning(
                    "Strategy signal rejected: %s %s %s",
                    signal.symbol, signal.direction, signal.volume,
                )
        if len(signals) > 1000:
            del signals[:-1000]
        self._processed_signal_count = len(signals)

    def _market_data_for_symbol(self, symbol: str) -> Dict[str, Any]:
        data = self.order_manager.market_data.get(symbol)
        if data:
            return data

        latest_ticks = getattr(self.gateway, "latest_ticks", {})
        tick = latest_ticks.get(symbol) if isinstance(latest_ticks, dict) else None
        if tick:
            return {
                "last_price": getattr(tick, "last_price", 0.0),
                "bid_price_1": getattr(tick, "bid_price_1", 0.0),
                "ask_price_1": getattr(tick, "ask_price_1", 0.0),
                "timestamp": getattr(tick, "timestamp", None),
            }

        snapshots = getattr(self.gateway, "latest_tick_snapshots", {})
        snapshot = snapshots.get(symbol) if isinstance(snapshots, dict) else None
        if snapshot:
            return snapshot

        return {}

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
        if self.ledger:
            self.ledger.record_order(order)
        if self.strategy:
            try:
                self.strategy.on_order(order)
            except Exception as e:
                logger.error(f"策略订单回调失败: {e}")

    def _on_trade(self, trade: 'Trade'):
        key = (trade.account_id or self.gateway.account.account_id, trade.trading_day,
               trade.exchange, trade.trade_id)
        with self.order_manager.lock:
            if not trade.trade_id:
                return
            if self.ledger:
                if not self.ledger.record_trade(trade):
                    return
            elif key in self._seen_trades:
                return
            self._seen_trades.add(key)
            self.trades[key] = trade
            if self.strategy:
                self.strategy.trades.append(trade)
                self.strategy.update_position(trade.symbol, trade)
                self.strategy.on_trade(trade)
                del self.strategy.trades[:-500]
            while len(self.trades) > 500:
                self.trades.pop(next(iter(self.trades)))
            if self.ledger:
                self._seen_trades.clear()

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
