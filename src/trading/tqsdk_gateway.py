"""
TqSdk 期货交易网关
使用天勤量化 (TqSdk) 实现，支持 CTP 接口，无需编译
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import threading

try:
    from tqsdk import TqApi, TqAuth, TqKq, TqSim
except ImportError:
    TqApi = None
    TqAuth = None
    TqKq = None
    TqSim = None

from . import GatewayBase, TradingStatus, AccountInfo, MarketData
from ..strategy import Signal, Order, Trade, Direction, Position, OrderStatus

logger = logging.getLogger(__name__)


class TqSdkGateway(GatewayBase):
    """TqSdk 期货交易网关"""

    def __init__(self):
        super().__init__("TQSDK")
        self.api: Optional[TqApi] = None
        self.auth: Optional[TqAuth] = None
        self.account_type: str = "sim"  # sim: 模拟, kq: 快期实盘

        # TqSdk 账号信息
        self.username: str = ""
        self.password: str = ""
        self.app_id: str = ""
        self.auth_code: str = ""

        # 本地订单映射: TqSdk 订单号 -> Order
        self.order_map: Dict[str, Order] = {}

        # 行情订阅
        self.quote_subscribed: set = set()

    def connect(self, config: Dict[str, Any]) -> bool:
        """连接 TqSdk"""
        self.status = TradingStatus.CONNECTING
        logger.info("正在连接 TqSdk...")

        # 从配置中读取参数
        self.account_type = config.get('account_type', 'sim')  # sim/kq
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.app_id = config.get('app_id', '')
        self.auth_code = config.get('auth_code', '')

        try:
            # 创建 TqApi 实例
            if self.account_type == 'kq':
                # 快期实盘模式
                logger.info(f"连接模式: 快期实盘 - {self.username}")
                self.api = TqApi(TqKq(self.username, self.password), auth=TqAuth(self.app_id, self.auth_code))
            else:
                # 模拟模式 (SimNow)
                logger.info("连接模式: 模拟交易 (SimNow)")
                self.api = TqApi(TqSim())

            # 等待连接成功
            self.api.wait_update()

            self.status = TradingStatus.CONNECTED
            logger.info("TqSdk 连接成功")
            return True

        except Exception as e:
            logger.error(f"TqSdk 连接失败: {e}")
            self.status = TradingStatus.ERROR
            return False

    def disconnect(self):
        """断开 TqSdk 连接"""
        logger.info("正在断开 TqSdk...")

        try:
            if self.api:
                self.api.close()

            self.status = TradingStatus.STOPPED
            logger.info("TqSdk 连接已断开")

        except Exception as e:
            logger.error(f"断开 TqSdk 连接时出错: {e}")

    def send_order(self, signal: Signal) -> str:
        """发送订单到 TqSdk"""
        if self.status != TradingStatus.CONNECTED or not self.api:
            logger.warning("TqSdk 未连接，无法发送订单")
            return ""

        try:
            # 订阅合约
            symbol = signal.symbol
            if symbol not in self.quote_subscribed:
                self.api.get_quote(symbol)
                self.quote_subscribed.add(symbol)

            # 获取合约信息
            quote = self.api.get_quote(symbol)

            # 转换方向
            if signal.direction == Direction.LONG:
                direction = "BUY"
                offset = "OPEN" if signal.volume > 0 else "CLOSETODAY"
            else:
                direction = "SELL"
                offset = "OPEN" if signal.volume > 0 else "CLOSETODAY"

            # 发送订单
            order = self.api.insert_order(
                symbol=symbol,
                direction=direction,
                offset=offset,
                limit_price=signal.price,
                volume=signal.volume
            )

            # 创建本地订单记录
            local_order_id = f"TQ_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            local_order = Order(
                order_id=local_order_id,
                symbol=signal.symbol,
                direction=signal.direction,
                order_type=signal.order_type,
                price=signal.price,
                volume=signal.volume,
                status=OrderStatus.SUBMITTED
            )

            # 存储 TqSdk 订单号映射
            self.order_map[order.order_id] = local_order
            self.orders[local_order_id] = local_order

            logger.info(f"订单已发送: {signal.symbol} {signal.direction.value} {signal.volume}@{signal.price}, TqSdk订单号: {order.order_id}")

            return local_order_id

        except Exception as e:
            logger.error(f"发送订单失败: {e}")
            return ""

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False

        try:
            local_order = self.orders[order_id]

            if not local_order.is_active():
                logger.warning(f"订单非活动状态，无法撤销: {order_id}")
                return False

            # 查找对应的 TqSdk 订单号并撤销
            tq_order_id = None
            for tid, lo in self.order_map.items():
                if lo.order_id == order_id:
                    tq_order_id = tid
                    break

            if tq_order_id and self.api:
                order = self.api.get_order(tq_order_id)
                if order:
                    self.api.cancel_order(order)
                    local_order.status = OrderStatus.CANCELLED
                    self.on_order(local_order)
                    logger.info(f"订单已撤销: {order_id}")
                    return True

            return False

        except Exception as e:
            logger.error(f"撤销订单失败: {e}")
            return False

    def query_account(self) -> AccountInfo:
        """查询账户信息"""
        try:
            if not self.api:
                return AccountInfo()

            # 获取账户信息
            account = self.api.get_account()

            account_info = AccountInfo(
                account_id=self.account_type,
                balance=account.balance,
                available=account.available,
                margin=account.margin,
                commission=account.commission,
                position_pnl=account.position_profit,
                total_pnl=account.close_profit + account.position_profit
            )

            self.account = account_info
            return account_info

        except Exception as e:
            logger.error(f"查询账户失败: {e}")
            return AccountInfo()

    def query_positions(self) -> List[Position]:
        """查询持仓"""
        try:
            if not self.api:
                return []

            positions = []
            account = self.api.get_account()

            # 遍历持仓
            for symbol, pos in account.positions.items():
                if pos.volume_long > 0:
                    positions.append(Position(
                        symbol=symbol,
                        direction=Direction.LONG,
                        volume=pos.volume_long,
                        price=pos.open_price_long,
                        pnl=pos.position_profit_long
                    ))

                if pos.volume_short > 0:
                    positions.append(Position(
                        symbol=symbol,
                        direction=Direction.SHORT,
                        volume=pos.volume_short,
                        price=pos.open_price_short,
                        pnl=pos.position_profit_short
                    ))

            self.positions = {p.symbol: p for p in positions}
            return positions

        except Exception as e:
            logger.error(f"查询持仓失败: {e}")
            return []

    def query_orders(self) -> List[Order]:
        """查询订单"""
        try:
            if not self.api:
                return []

            # 更新订单状态
            for tq_order_id, local_order in self.order_map.items():
                try:
                    order = self.api.get_order(tq_order_id)
                    if order:
                        # 转换订单状态
                        if order.status == "ALIVE":
                            local_order.status = OrderStatus.SUBMITTED
                        elif order.status == "FINISHED":
                            local_order.status = OrderStatus.FILLED
                            local_order.traded_volume = order.trade_volume
                        elif order.status == "CANCELLED":
                            local_order.status = OrderStatus.CANCELLED
                except:
                    pass

            return list(self.orders.values())

        except Exception as e:
            logger.error(f"查询订单失败: {e}")
            return []

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情"""
        try:
            if not self.api:
                return

            for symbol in symbols:
                if symbol not in self.quote_subscribed:
                    self.api.get_quote(symbol)
                    self.quote_subscribed.add(symbol)
                    logger.info(f"已订阅行情: {symbol}")

        except Exception as e:
            logger.error(f"订阅行情失败: {e}")

    def wait_update(self):
        """等待行情更新"""
        try:
            if self.api:
                self.api.wait_update()
        except Exception as e:
            logger.error(f"等待更新失败: {e}")

    def get_quote(self, symbol: str) -> Optional[MarketData]:
        """获取行情数据"""
        try:
            if not self.api:
                return None

            # 订阅合约
            if symbol not in self.quote_subscribed:
                self.api.get_quote(symbol)
                self.quote_subscribed.add(symbol)

            # 获取行情
            quote = self.api.get_quote(symbol)

            tick = MarketData(
                symbol=symbol,
                last_price=quote.last_price,
                bid_price_1=quote.bid_price1,
                ask_price_1=quote.ask_price1,
                bid_volume_1=int(quote.bid_volume1),
                ask_volume_1=int(quote.ask_volume1),
                volume=int(quote.volume),
                turnover=quote.turnover,
                timestamp=datetime.now()
            )

            self.on_tick(tick)
            return tick

        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            return None


# TqSdk Gateway 工厂函数
def create_tqsdk_gateway() -> TqSdkGateway:
    """创建 TqSdk 网关实例"""
    return TqSdkGateway()
