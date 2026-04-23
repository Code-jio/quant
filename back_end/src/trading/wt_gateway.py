"""
WonderTrader (wtpy) 交易网关适配层

架构说明：
  wtpy 的 WtEngine 本身是事件驱动的，它通过 BaseCtaStrategy / BaseSelStrategy
  等回调驱动策略。本适配层不使用 wtpy 的策略基类，而是把 wtpy 的底层
  TraderAdapter（交易通道）直接包装成与现有 GatewayBase 兼容的接口，
  使上层 TradingEngine / API 层无需改动。

依赖：
  pip install wtpy          # 安装后 WtCore.dll 等核心库随包附带
  wtpy >= 0.9.x

连接流程：
  1. 读取 config 中的 td_cfg / md_cfg（wtpy 标准 json 配置路径）
  2. 初始化 WtEngine，注册交易通道
  3. 登录完成后触发 on_account / on_position 回调
  4. 后续通过 send_order / cancel_order 操作
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .gateway import GatewayBase
from .types import AccountInfo, MarketData, TradingStatus
from .errors import GatewayError
from ..strategy.types import (
    Direction,
    OffsetFlag,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    Trade,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# wtpy 延迟导入（安装后才可用）
# ──────────────────────────────────────────────────────────────────────────────


def _import_wtpy():
    try:
        from wtpy import WtEngine, EngineType
        from wtpy.TraderDefs import BaseTraderSpi

        return WtEngine, EngineType, BaseTraderSpi
    except ImportError as e:
        raise ImportError("wtpy 未安装，请先执行: pip install wtpy\n" f"原始错误: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# 内部 SPI 回调实现
# ──────────────────────────────────────────────────────────────────────────────


class _WtTraderSpi:
    """
    实现 wtpy TraderSpi 回调，将 wtpy 事件转换为 GatewayBase 回调。
    wtpy 的 TraderSpi 是鸭子类型接口，直接定义同名方法即可。
    """

    def __init__(self, gateway: "WtGateway"):
        self._gw = gateway

    # ── 连接 ──────────────────────────────────────────────────────────────────

    def on_login(self, trader_id: str, code: int, msg: str):
        if code == 0:
            logger.info(f"[WT] 登录成功: {trader_id}")
            self._gw.status = TradingStatus.CONNECTED
            self._gw._login_event.set()
        else:
            logger.error(f"[WT] 登录失败 [{code}]: {msg}")
            self._gw.status = TradingStatus.ERROR
            self._gw._login_event.set()

    def on_logout(self, trader_id: str):
        logger.info(f"[WT] 已登出: {trader_id}")
        self._gw.status = TradingStatus.STOPPED

    # ── 订单回报 ───────────────────────────────────────────────────────────────

    def on_order(
        self,
        localid: int,
        code: str,
        isBuy: bool,
        totalQty: float,
        leftQty: float,
        price: float,
        isCanceled: bool,
        userTag: str,
        orderTime: int,
        oidx: int = -1,
    ):
        order_id = str(localid)
        if isCanceled:
            status = OrderStatus.CANCELLED
        elif leftQty == 0:
            status = OrderStatus.FILLED
        elif leftQty < totalQty:
            status = OrderStatus.PARTFILLED
        else:
            status = OrderStatus.SUBMITTED

        direction = Direction.LONG if isBuy else Direction.SHORT
        traded = int(totalQty - leftQty)

        order = self._gw.orders.get(order_id)
        if order is None:
            order = Order(
                order_id=order_id,
                symbol=code,
                direction=direction,
                order_type=OrderType.LIMIT,
                price=price,
                volume=int(totalQty),
                traded_volume=traded,
                status=status,
            )
        else:
            order.status = status
            order.traded_volume = traded
            order.update_time = datetime.now()

        self._gw.orders[order_id] = order
        self._gw.on_order(order)

    # ── 成交回报 ───────────────────────────────────────────────────────────────

    def on_trade(
        self,
        localid: int,
        code: str,
        isBuy: bool,
        qty: float,
        price: float,
        userTag: str,
        tradeTime: int,
        oidx: int = -1,
    ):
        direction = Direction.LONG if isBuy else Direction.SHORT
        trade = Trade(
            trade_id=f"WT_{localid}_{tradeTime}",
            order_id=str(localid),
            symbol=code,
            direction=direction,
            price=price,
            volume=int(qty),
            trade_time=datetime.now(),
        )
        self._gw.on_trade(trade)

    # ── 持仓回报 ───────────────────────────────────────────────────────────────

    def on_position(
        self,
        code: str,
        isLong: bool,
        volume: float,
        holdPrice: float,
        rawHoldPrice: float,
        profit: float,
        frozen: float,
        oidx: int = -1,
    ):
        direction = Direction.LONG if isLong else Direction.SHORT
        pos = Position(
            symbol=code,
            direction=direction,
            volume=int(volume),
            frozen=int(frozen),
            price=holdPrice,
            cost=holdPrice * volume,
            pnl=profit,
        )
        key = f"{code}_{'long' if isLong else 'short'}"
        self._gw.positions[key] = pos
        self._gw.on_position(pos)

    # ── 账户回报 ───────────────────────────────────────────────────────────────

    def on_account(
        self,
        currency: str,
        prebalance: float,
        balance: float,
        dynbalance: float,
        avaliable: float,
        closeprofit: float,
        dynprofit: float,
        margin: float,
        fee: float,
        deposit: float,
        withdraw: float,
        oidx: int = -1,
    ):
        account = AccountInfo(
            account_id=self._gw._account_id,
            balance=balance,
            available=avaliable,
            margin=margin,
            commission=fee,
            position_pnl=dynprofit,
            total_pnl=closeprofit + dynprofit,
        )
        self._gw.account = account
        self._gw.on_account(account)

    # ── 错误回报 ───────────────────────────────────────────────────────────────

    def on_error(self, code: int, msg: str, oidx: int = -1):
        logger.error(f"[WT] 错误 [{code}]: {msg}")
        self._gw.on_error(GatewayError(msg), f"code={code}")

    # ── 撤单回报 ───────────────────────────────────────────────────────────────

    def on_cancel(
        self,
        localid: int,
        code: str,
        isBuy: bool,
        leftQty: float,
        price: float,
        userTag: str,
        orderTime: int,
        oidx: int = -1,
    ):
        order_id = str(localid)
        order = self._gw.orders.get(order_id)
        if order:
            order.status = OrderStatus.CANCELLED
            order.update_time = datetime.now()
            self._gw.on_order(order)


# ──────────────────────────────────────────────────────────────────────────────
# 主网关类
# ──────────────────────────────────────────────────────────────────────────────


class WtGateway(GatewayBase):
    """
    WonderTrader (wtpy) 交易网关

    config 字段：
      trader_id   str   交易通道 ID，默认 "CTP"
      account_id  str   账户 ID（用于日志/账户信息）
      td_cfg      str   wtpy 交易通道配置文件路径，如 "config/tdcfg.yaml"
      md_cfg      str   wtpy 行情通道配置文件路径（可选）
      env_cfg     str   wtpy 环境配置文件路径，如 "config/env.yaml"
      hot_cfg     str   主力合约映射文件路径（可选）
    """

    def __init__(self):
        super().__init__("WT")
        self._engine = None  # WtEngine 实例
        self._trader = None  # TraderAdapter
        self._spi: Optional[_WtTraderSpi] = None
        self._account_id: str = ""
        self._trader_id: str = "CTP"
        self._login_event = threading.Event()
        self._local_id: int = 0
        self._lock = threading.Lock()
        self.latest_ticks: Dict[str, MarketData] = {}

    # ── 连接 ──────────────────────────────────────────────────────────────────

    def connect(self, config: Dict[str, Any]) -> bool:
        WtEngine, EngineType, BaseTraderSpi = _import_wtpy()

        self._account_id = config.get("account_id", config.get("username", ""))
        self._trader_id = config.get("trader_id", "CTP")
        td_cfg = config.get("td_cfg", "config/tdcfg.yaml")
        env_cfg = config.get("env_cfg", "config/env.yaml")
        hot_cfg = config.get("hot_cfg", "")

        self.status = TradingStatus.CONNECTING
        self._login_event.clear()

        try:
            self._engine = WtEngine(EngineType.ET_CTA)
            self._engine.init(env_cfg, logCfg="config/logcfg.yaml")

            # 注册交易通道
            self._engine.add_cta_trader(self._trader_id, td_cfg)

            # 获取 TraderAdapter 并注册 SPI
            self._spi = _WtTraderSpi(self)
            self._trader = self._engine.get_trader(self._trader_id)
            if self._trader is None:
                raise GatewayError(f"无法获取交易通道: {self._trader_id}")

            self._trader.register_spi(self._spi)

            # 在后台线程启动引擎（非阻塞）
            threading.Thread(
                target=self._engine.run, kwargs={"bAsync": True}, daemon=True
            ).start()

            logger.info(f"[WT] 正在连接，等待登录回调（最长 30s）…")
            if not self._login_event.wait(timeout=30):
                logger.error("[WT] 登录超时（30s）")
                self.status = TradingStatus.ERROR
                return False

            return self.status == TradingStatus.CONNECTED

        except Exception as e:
            logger.error(f"[WT] 连接失败: {e}")
            self.status = TradingStatus.ERROR
            raise GatewayError(f"连接失败: {e}") from e

    def disconnect(self):
        try:
            if self._engine:
                self._engine.release()
                self._engine = None
        except Exception as e:
            logger.error(f"[WT] 断开连接异常: {e}")
        self.status = TradingStatus.STOPPED
        logger.info("[WT] 已断开连接")

    # ── 下单 ──────────────────────────────────────────────────────────────────

    def send_order(self, signal: Signal) -> str:
        if self.status != TradingStatus.CONNECTED:
            logger.warning("[WT] 网关未就绪，无法下单")
            return ""
        if self._trader is None:
            return ""

        with self._lock:
            self._local_id += 1
            local_id = self._local_id

        is_buy = signal.direction == Direction.LONG
        offset = getattr(signal, "offset", OffsetFlag.OPEN)
        is_open = offset == OffsetFlag.OPEN

        # wtpy buy/sell 接口：buy(code, qty, price, userTag)
        # 平仓用 sell（多头平仓）或 buy（空头平仓），通过 offset 区分
        try:
            if is_open:
                if is_buy:
                    ids = self._trader.buy(
                        signal.symbol, signal.volume, signal.price, str(local_id)
                    )
                else:
                    ids = self._trader.sell(
                        signal.symbol, signal.volume, signal.price, str(local_id)
                    )
            else:
                # 平仓：多头平仓 → sell_close；空头平仓 → buy_close
                if is_buy:
                    ids = self._trader.buy_close(
                        signal.symbol, signal.volume, signal.price, str(local_id)
                    )
                else:
                    ids = self._trader.sell_close(
                        signal.symbol, signal.volume, signal.price, str(local_id)
                    )
        except Exception as e:
            logger.error(f"[WT] 下单异常: {e}")
            raise GatewayError(f"下单失败: {e}") from e

        # wtpy 返回 localid 列表
        order_id = str(ids[0]) if ids else str(local_id)

        order = Order(
            order_id=order_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            status=OrderStatus.SUBMITTING,
            offset=offset,
        )
        self.orders[order_id] = order
        logger.info(
            f"[WT] 已发送委托: {signal.symbol} "
            f"{'买' if is_buy else '卖'} "
            f"{'开' if is_open else '平'} "
            f"{signal.volume}@{signal.price} id={order_id}"
        )
        return order_id

    # ── 撤单 ──────────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if self._trader is None:
            return False
        order = self.orders.get(order_id)
        if order is None or not order.is_active():
            return False
        try:
            self._trader.cancel(int(order_id))
            logger.info(f"[WT] 已发送撤单: {order_id}")
            return True
        except Exception as e:
            logger.error(f"[WT] 撤单异常: {e}")
            return False

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def query_account(self) -> AccountInfo:
        if self._trader and self.status == TradingStatus.CONNECTED:
            try:
                self._trader.query_account()
            except Exception:
                pass
        return self.account

    def query_positions(self) -> List[Position]:
        if self._trader and self.status == TradingStatus.CONNECTED:
            try:
                self._trader.query_positions()
            except Exception:
                pass
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        return list(self.orders.values())

    # ── 行情订阅 ───────────────────────────────────────────────────────────────

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情（需要 wtpy 行情通道已就绪）"""
        if self._engine is None:
            logger.warning("[WT] 引擎未初始化，无法订阅行情")
            return
        for sym in symbols:
            try:
                self._engine.subscribe_market_data(sym)
                logger.info(f"[WT] 已订阅行情: {sym}")
            except Exception as e:
                logger.warning(f"[WT] 订阅行情失败 {sym}: {e}")


def create_wt_gateway() -> WtGateway:
    return WtGateway()
