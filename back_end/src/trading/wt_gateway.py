"""
WonderTrader (wtpy) 交易网关适配层

实现方式：
  用 BaseHftStrategy 包装一个内部"代理策略"，通过 HftContext 的
  stra_buy / stra_sell / stra_cancel 完成下单/撤单，
  并将 on_order / on_trade / on_position / on_channel_ready 等回调
  转发给 GatewayBase 的标准回调链，使上层 TradingEngine / API 无需改动。

连接流程：
  1. WtEngine.init(cfgfile)  — 加载品种/合约/时段/节假日等基础数据
  2. WtEngine.addTrader(id, params)  — 注册 CTP 交易通道
  3. WtEngine.add_hft_strategy(proxy, trader=id)  — 注册代理策略
  4. WtEngine.run(bAsync=True)  — 后台启动
  5. on_channel_ready 回调触发 → 标记 CONNECTED，解除 connect() 阻塞

依赖：
  pip install wtpy --no-deps
  pip install chardet pyyaml
"""

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from .gateway import GatewayBase
from .types import AccountInfo, MarketData, TradingStatus
from .errors import GatewayError
from ..strategy.types import (
    Direction, OffsetFlag, Order, OrderStatus, OrderType, Position, Signal, Trade,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 内部代理 HFT 策略（持有 WtGateway 引用，转发所有回调）
# ──────────────────────────────────────────────────────────────────────────────

class _GatewayHftProxy:
    """
    伪装成 BaseHftStrategy 的代理对象。
    wtpy 通过鸭子类型调用 on_* 方法，无需真正继承。
    """

    def __init__(self, gateway: "WtGateway", strategy_name: str = "__wt_gw_proxy__"):
        self._name = strategy_name
        self._gw = gateway
        self._ctx = None   # HftContext，在 on_init 后赋值

    def name(self) -> str:
        return self._name

    # ── 生命周期 ───────────────────────────────────────────────────────────────

    def on_init(self, context):
        self._ctx = context
        logger.info("[WT] HFT 代理策略初始化完成")

    def on_session_begin(self, context, curTDate: int):
        logger.info(f"[WT] 交易日开始: {curTDate}")

    def on_session_end(self, context, curTDate: int):
        logger.info(f"[WT] 交易日结束: {curTDate}")

    # ── 通道就绪（登录成功的信号）─────────────────────────────────────────────

    def on_channel_ready(self, context):
        logger.info("[WT] 交易通道就绪")
        self._gw.status = TradingStatus.CONNECTED
        self._gw._login_event.set()

    def on_channel_lost(self, context):
        logger.warning("[WT] 交易通道断开")
        self._gw.status = TradingStatus.ERROR

    # ── 委托回报 ───────────────────────────────────────────────────────────────

    def on_entrust(self, context, localid: int, stdCode: str,
                   bSucc: bool, msg: str, userTag: str):
        if not bSucc:
            logger.error(f"[WT] 委托失败 localid={localid} code={stdCode} msg={msg}")
            order = self._gw.orders.get(str(localid))
            if order:
                order.status = OrderStatus.REJECTED
                order.error_msg = msg
                order.update_time = datetime.now()
                self._gw.on_order(order)

    def on_order(self, context, localid: int, stdCode: str, isBuy: bool,
                 totalQty: float, leftQty: float, price: float,
                 isCanceled: bool, userTag: str):
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
                symbol=stdCode,
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

    def on_trade(self, context, localid: int, stdCode: str, isBuy: bool,
                 qty: float, price: float, userTag: str):
        direction = Direction.LONG if isBuy else Direction.SHORT
        trade = Trade(
            trade_id=f"WT_{localid}_{int(datetime.now().timestamp()*1000)}",
            order_id=str(localid),
            symbol=stdCode,
            direction=direction,
            price=price,
            volume=int(qty),
            trade_time=datetime.now(),
        )
        self._gw.on_trade(trade)

    # ── 持仓回报 ───────────────────────────────────────────────────────────────

    def on_position(self, context, stdCode: str, isLong: bool,
                    prevol: float, preavail: float, newvol: float, newavail: float):
        direction = Direction.LONG if isLong else Direction.SHORT
        pos = Position(
            symbol=stdCode,
            direction=direction,
            volume=int(newvol),
            frozen=int(newvol - newavail),
            price=0.0,
            cost=0.0,
            pnl=0.0,
        )
        key = f"{stdCode}_{'long' if isLong else 'short'}"
        self._gw.positions[key] = pos
        self._gw.on_position(pos)

    # ── tick 回报（转发给 TradingEngine）──────────────────────────────────────

    def on_tick(self, context, stdCode: str, newTick: dict):
        try:
            tick = MarketData(
                symbol=stdCode,
                last_price=newTick.get("price", 0.0),
                bid_price_1=newTick.get("bid_price_1", 0.0),
                ask_price_1=newTick.get("ask_price_1", 0.0),
                bid_volume_1=int(newTick.get("bid_qty_1", 0)),
                ask_volume_1=int(newTick.get("ask_qty_1", 0)),
                volume=int(newTick.get("volume", 0)),
                turnover=newTick.get("turnover", 0.0),
                timestamp=datetime.now(),
            )
            self._gw.latest_ticks[stdCode] = tick
            self._gw.on_tick(tick)
        except Exception as e:
            logger.debug(f"[WT] tick 处理异常 {stdCode}: {e}")

    # ── 其他必须实现的空方法 ───────────────────────────────────────────────────

    def on_bar(self, context, stdCode: str, period: str, newBar: dict):
        pass

    def on_order_queue(self, context, stdCode: str, newOrdQue: dict):
        pass

    def on_order_detail(self, context, stdCode: str, newOrdDtl: dict):
        pass

    def on_transaction(self, context, stdCode: str, newTrans: dict):
        pass

    def on_backtest_end(self, context):
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 主网关类
# ──────────────────────────────────────────────────────────────────────────────

class WtGateway(GatewayBase):
    """
    WonderTrader (wtpy) 交易网关

    config 字段：
      trader_id   str   交易通道 ID，默认 "CTP"
      account_id  str   账户 ID（用于日志/账户信息）
      cfg_file    str   wtpy 主配置文件路径（json/yaml），默认 "config/wt/config.json"
      cfg_folder  str   基础数据文件目录，默认 "config/wt/"
      td_module   str   交易模块名，默认 "TraderCTP"
      td_front    str   CTP 交易前置地址
      broker_id   str   经纪商 ID
      username    str   账户
      password    str   密码
      app_id      str   AppID（可选）
      auth_code   str   AuthCode（可选）
    """

    def __init__(self):
        super().__init__("WT")
        self._engine = None
        self._proxy: Optional[_GatewayHftProxy] = None
        self._account_id: str = ""
        self._trader_id: str = "CTP"
        self._login_event = threading.Event()
        self._lock = threading.Lock()
        self.latest_ticks: Dict[str, MarketData] = {}

    # ── 连接 ──────────────────────────────────────────────────────────────────

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from wtpy.WtEngine import WtEngine, EngineType
        except ImportError as e:
            raise ImportError(f"wtpy 未安装，请执行: pip install wtpy --no-deps\n{e}") from e

        self._account_id = config.get("account_id", config.get("username", ""))
        self._trader_id  = config.get("trader_id", "CTP")
        cfg_file   = config.get("cfg_file",   "config/wt/config.json")
        cfg_folder = config.get("cfg_folder", "config/wt/")

        self.status = TradingStatus.CONNECTING
        self._login_event.clear()

        try:
            self._engine = WtEngine(EngineType.ET_HFT)
            self._engine.init(cfg_folder, cfgfile=cfg_file)

            # 注册 CTP 交易通道
            trader_params = {
                "module":   config.get("td_module", "TraderCTP"),
                "front":    config.get("td_front",  config.get("td_server", "")),
                "broker":   config.get("broker_id", ""),
                "user":     config.get("username",  ""),
                "pass":     config.get("password",  ""),
                "appid":    config.get("app_id",    ""),
                "authcode": config.get("auth_code", ""),
            }
            self._engine.addTrader(self._trader_id, trader_params)

            # 注册代理 HFT 策略（持有 gateway 引用，转发所有回调）
            self._proxy = _GatewayHftProxy(self)
            self._engine.add_hft_strategy(self._proxy, trader=self._trader_id)

            # 后台启动引擎
            threading.Thread(
                target=self._engine.run,
                kwargs={"bAsync": True},
                daemon=True,
            ).start()

            logger.info("[WT] 引擎已启动，等待交易通道就绪（最长 30s）…")
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
        if self._proxy is None or self._proxy._ctx is None:
            logger.warning("[WT] HFT 上下文未就绪")
            return ""

        ctx = self._proxy._ctx
        is_buy  = signal.direction == Direction.LONG
        offset  = getattr(signal, "offset", OffsetFlag.OPEN)
        is_open = offset == OffsetFlag.OPEN

        try:
            if is_open:
                if is_buy:
                    ids = ctx.stra_buy(signal.symbol, signal.price, signal.volume)
                else:
                    ids = ctx.stra_sell(signal.symbol, signal.price, signal.volume)
            else:
                # 平仓：反向操作
                if is_buy:
                    # 空头平仓 → 买入
                    ids = ctx.stra_buy(signal.symbol, signal.price, signal.volume)
                else:
                    # 多头平仓 → 卖出
                    ids = ctx.stra_sell(signal.symbol, signal.price, signal.volume)
        except Exception as e:
            logger.error(f"[WT] 下单异常: {e}")
            raise GatewayError(f"下单失败: {e}") from e

        # ids 是 localid 列表（整数）
        order_id = str(ids[0]) if ids else ""
        if not order_id:
            logger.error("[WT] 下单未返回 localid")
            return ""

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
            f"[WT] 委托已发送: {signal.symbol} "
            f"{'买' if is_buy else '卖'}{'开' if is_open else '平'} "
            f"{signal.volume}@{signal.price} id={order_id}"
        )
        return order_id

    # ── 撤单 ──────────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if self._proxy is None or self._proxy._ctx is None:
            return False
        order = self.orders.get(order_id)
        if order is None or not order.is_active():
            return False
        try:
            self._proxy._ctx.stra_cancel(int(order_id))
            logger.info(f"[WT] 撤单已发送: {order_id}")
            return True
        except Exception as e:
            logger.error(f"[WT] 撤单异常: {e}")
            return False

    # ── 查询（wtpy HFT 模式下持仓/账户由回调推送，无主动查询接口）────────────

    def query_account(self) -> AccountInfo:
        return self.account

    def query_positions(self) -> List[Position]:
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        return list(self.orders.values())

    # ── 行情订阅 ───────────────────────────────────────────────────────────────

    def subscribe_market_data(self, symbols: List[str]):
        if self._proxy is None or self._proxy._ctx is None:
            logger.warning("[WT] 上下文未就绪，无法订阅行情")
            return
        for sym in symbols:
            try:
                self._proxy._ctx.stra_sub_ticks(sym)
                logger.info(f"[WT] 已订阅行情: {sym}")
            except Exception as e:
                logger.warning(f"[WT] 订阅行情失败 {sym}: {e}")


def create_wt_gateway() -> WtGateway:
    return WtGateway()
