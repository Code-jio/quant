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
from .gateway import GatewayBase, SimulatedGateway
from .errors import TradingError
from .order_manager import OrderManager, PreOrder
from ..common.exceptions import ExceptionHandler


class TradingEngine:
    """实盘交易引擎"""

    def __init__(self, gateway: GatewayBase = None):
        self.gateway = gateway or SimulatedGateway()
        self.strategy = None
        self.status = TradingStatus.STOPPED

        self.order_manager = OrderManager(self.gateway)
        self.gateway.on_order_callback = self._on_order
        self.gateway.on_trade_callback = self._on_trade
        self.gateway.on_error_callback = self._on_gateway_error

        self.order_manager.on_order_callback = self._on_order
        self.order_manager.on_trade_callback = self._on_trade
        self.order_manager.on_pre_order_status_change = self._on_pre_order_status_change

        self._error_count = 0
        self._max_errors = 10
        self.exception_handler = ExceptionHandler()

    def set_strategy(self, strategy):
        """设置策略"""
        self.strategy = strategy

    def start(self, config: Dict[str, Any] = None) -> bool:
        """启动交易引擎"""
        try:
            if self.status in [TradingStatus.CONNECTING, TradingStatus.TRADING]:
                logger.warning("交易引擎已在运行中")
                return False

            config = config or {}

            self.status = TradingStatus.CONNECTING

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

            self.gateway.disconnect()
            self.status = TradingStatus.STOPPED
            logger.info("交易引擎已停止")
        except Exception as e:
            logger.error(f"停止交易引擎失败: {e}")
            self.status = TradingStatus.STOPPED

    def send_signal(self, signal: 'Signal') -> str:
        """发送交易信号"""
        from ..strategy import Signal as SignalClass
        if self.status not in (TradingStatus.TRADING, TradingStatus.CONNECTED):
            # 也检查网关状态，允许网关已连接但引擎未正式 start 的场景（手动交易）
            if self.gateway.status not in (TradingStatus.CONNECTED, TradingStatus.TRADING):
                logger.warning("交易引擎未在运行")
                return ""

        try:
            order_id = self.order_manager.submit_order(signal)
            if order_id:
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
        """行情推送"""
        if not self.strategy:
            return
        try:
            import pandas as pd
            bar = pd.Series({
                'symbol': tick.symbol,
                'datetime': tick.timestamp,
                'open': tick.last_price,
                'high': tick.last_price,
                'low': tick.last_price,
                'close': tick.last_price,
                'volume': tick.volume,
                'bid': tick.bid_price_1,
                'ask': tick.ask_price_1,
            })
            self.strategy.current_date = tick.timestamp
            self.strategy.on_bar(bar)
        except Exception as e:
            logger.error(f"处理行情数据失败: {e}")

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
