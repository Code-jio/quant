"""
原生 CTP 交易网关
基于 openctp-ctp 实现，支持交易前置（Trader）和行情前置（MarketData）
登录流程：连接 → 认证(可选) → 登录 → 确认结算单 → 就绪
"""

import os
import logging
import threading
import time
from typing import Dict, Any, List, Optional
from datetime import datetime

from openctp_ctp import thosttraderapi as tdapi
from openctp_ctp import thostmduserapi as mdapi

from . import (
    GatewayBase, TradingStatus, AccountInfo, MarketData,
)
from ..strategy import Signal, Order, Trade, Direction, Position, OrderType, OrderStatus, OffsetFlag

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────────────────────────

EXCHANGE_MAP = {
    'IF': 'CFFEX', 'IC': 'CFFEX', 'IH': 'CFFEX', 'IM': 'CFFEX',
    'T': 'CFFEX', 'TF': 'CFFEX', 'TS': 'CFFEX',
    'CU': 'SHFE', 'AU': 'SHFE', 'AG': 'SHFE', 'RB': 'SHFE',
    'AL': 'SHFE', 'ZN': 'SHFE', 'PB': 'SHFE', 'NI': 'SHFE',
    'SN': 'SHFE', 'FU': 'SHFE', 'BU': 'SHFE', 'HC': 'SHFE',
    'SC': 'INE', 'NR': 'INE', 'BC': 'INE',
    'A': 'DCE', 'B': 'DCE', 'C': 'DCE', 'CS': 'DCE',
    'M': 'DCE', 'Y': 'DCE', 'P': 'DCE', 'L': 'DCE',
    'V': 'DCE', 'PP': 'DCE', 'J': 'DCE', 'JM': 'DCE',
    'I': 'DCE', 'EG': 'DCE', 'EB': 'DCE', 'PG': 'DCE',
    'CF': 'CZCE', 'SR': 'CZCE', 'TA': 'CZCE', 'MA': 'CZCE',
    'OI': 'CZCE', 'RM': 'CZCE', 'ZC': 'CZCE', 'FG': 'CZCE',
    'SA': 'CZCE', 'UR': 'CZCE', 'AP': 'CZCE', 'CJ': 'CZCE',
}


def get_exchange(symbol: str) -> str:
    """根据合约代码前缀推断交易所"""
    upper = symbol.upper()
    for prefix in sorted(EXCHANGE_MAP.keys(), key=len, reverse=True):
        if upper.startswith(prefix):
            return EXCHANGE_MAP[prefix]
    return 'SHFE'


def _ensure_flow_dir(path: str):
    os.makedirs(path, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 交易 SPI
# ──────────────────────────────────────────────────────────────────────────────

class _TraderSpi(tdapi.CThostFtdcTraderSpi):
    """交易 SPI 回调实现"""

    def __init__(self, gateway: "CTPNativeGateway"):
        super().__init__()
        self.gw = gateway

    # ── 连接 ──────────────────────────────────────────────────────────────────

    def OnFrontConnected(self):
        logger.info("[TD] 交易前置连接成功")
        self.gw._log("交易前置连接成功")
        if self.gw.app_id and self.gw.auth_code:
            self.gw._req_authenticate()
        else:
            self.gw._req_user_login_td()

    def OnFrontDisconnected(self, nReason: int):
        logger.warning(f"[TD] 交易前置断开, reason={nReason}")
        self.gw._log(f"交易前置断开 (reason={nReason})")
        self.gw.status = TradingStatus.ERROR

    # ── 认证 ──────────────────────────────────────────────────────────────────

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[TD] 认证失败: [{pRspInfo.ErrorID}] {msg}")
            self.gw._log(f"认证失败: [{pRspInfo.ErrorID}] {msg}")
            self.gw._login_event.set()
        else:
            logger.info("[TD] 认证成功，发起登录")
            self.gw._log("认证成功，发起登录")
            self.gw._req_user_login_td()

    # ── 登录 ──────────────────────────────────────────────────────────────────

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[TD] 登录失败: [{pRspInfo.ErrorID}] {msg}")
            self.gw._log(f"登录失败: [{pRspInfo.ErrorID}] {msg}")
            self.gw.status = TradingStatus.ERROR
            self.gw._login_event.set()
            return

        if pRspUserLogin:
            self.gw._front_id = pRspUserLogin.FrontID
            self.gw._session_id = pRspUserLogin.SessionID
            self.gw._order_ref = int(pRspUserLogin.MaxOrderRef) + 1
            trading_day = pRspUserLogin.TradingDay if hasattr(pRspUserLogin, 'TradingDay') else ''
            logger.info(f"[TD] 登录成功，交易日={trading_day}")
            self.gw._log(f"登录成功，交易日={trading_day}")

        self.gw._req_settlement_confirm()

    def OnRspSettlementInfoConfirm(self, pSettlementInfoConfirm, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.warning(f"[TD] 结算确认失败: {msg}")
        logger.info("[TD] 结算确认完成，交易就绪")
        self.gw._log("结算确认完成，交易就绪")
        self.gw.status = TradingStatus.CONNECTED
        self.gw._login_event.set()
        # 登录完成后查询账户和持仓
        threading.Thread(target=self.gw._post_login_query, daemon=True).start()

    # ── 订单回报 ───────────────────────────────────────────────────────────────

    def OnRtnOrder(self, pOrder):
        if not pOrder:
            return
        order_id = f"{pOrder.FrontID}:{pOrder.SessionID}:{int(pOrder.OrderRef)}"
        status_map = {
            tdapi.THOST_FTDC_OST_Unknown: OrderStatus.SUBMITTING,
            tdapi.THOST_FTDC_OST_NoTradeQueueing: OrderStatus.SUBMITTED,
            tdapi.THOST_FTDC_OST_PartTradedQueueing: OrderStatus.PARTFILLED,
            tdapi.THOST_FTDC_OST_AllTraded: OrderStatus.FILLED,
            tdapi.THOST_FTDC_OST_Canceled: OrderStatus.CANCELLED,
        }
        status = status_map.get(pOrder.OrderStatus, OrderStatus.SUBMITTED)
        direction = Direction.LONG if pOrder.Direction == tdapi.THOST_FTDC_D_Buy else Direction.SHORT

        # 解析开平仓标志
        offset_char = pOrder.CombOffsetFlag[0] if hasattr(pOrder, 'CombOffsetFlag') and pOrder.CombOffsetFlag else '0'
        offset_map = {
            tdapi.THOST_FTDC_OF_Open: OffsetFlag.OPEN,
            tdapi.THOST_FTDC_OF_Close: OffsetFlag.CLOSE,
            tdapi.THOST_FTDC_OF_CloseToday: OffsetFlag.CLOSE_TODAY,
            tdapi.THOST_FTDC_OF_CloseYesterday: OffsetFlag.CLOSE_YESTERDAY,
        }
        offset = offset_map.get(offset_char, OffsetFlag.OPEN)

        order = Order(
            order_id=order_id,
            symbol=pOrder.InstrumentID,
            direction=direction,
            order_type=OrderType.LIMIT if pOrder.OrderPriceType == tdapi.THOST_FTDC_OPT_LimitPrice else OrderType.MARKET,
            price=pOrder.LimitPrice,
            volume=pOrder.VolumeTotalOriginal,
            traded_volume=pOrder.VolumeTraded,
            status=status,
            offset=offset,
        )
        self.gw.orders[order_id] = order
        self.gw.on_order(order)

    def OnRtnTrade(self, pTrade):
        if not pTrade:
            return
        direction = Direction.LONG if pTrade.Direction == tdapi.THOST_FTDC_D_Buy else Direction.SHORT
        trade = Trade(
            trade_id=pTrade.TradeID.strip(),
            order_id=pTrade.OrderRef.strip(),
            symbol=pTrade.InstrumentID,
            direction=direction,
            price=pTrade.Price,
            volume=pTrade.Volume,
            commission=0.0,
            trade_time=datetime.now(),
        )
        self.gw.on_trade(trade)

    def OnErrRtnOrderInsert(self, pInputOrder, pRspInfo):
        if pRspInfo:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[TD] 报单错误: [{pRspInfo.ErrorID}] {msg}")
            self.gw._log(f"报单错误: [{pRspInfo.ErrorID}] {msg}")

    def OnErrRtnOrderAction(self, pOrderAction, pRspInfo):
        if pRspInfo:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[TD] 撤单错误: [{pRspInfo.ErrorID}] {msg}")
            self.gw._log(f"撤单错误: [{pRspInfo.ErrorID}] {msg}")

    # ── 查询回报 ───────────────────────────────────────────────────────────────

    def OnRspQryTradingAccount(self, pTradingAccount, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            return
        if pTradingAccount:
            account = AccountInfo(
                account_id=pTradingAccount.AccountID,
                balance=pTradingAccount.Balance,
                available=pTradingAccount.Available,
                margin=pTradingAccount.CurrMargin,
                commission=pTradingAccount.Commission,
                position_pnl=pTradingAccount.PositionProfit,
                total_pnl=pTradingAccount.CloseProfit + pTradingAccount.PositionProfit,
            )
            self.gw.account = account
            self.gw.on_account(account)

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            return
        if pInvestorPosition and pInvestorPosition.Volume > 0:
            direction = Direction.LONG if pInvestorPosition.PosiDirection == tdapi.THOST_FTDC_PD_Long else Direction.SHORT
            pos = Position(
                symbol=pInvestorPosition.InstrumentID,
                direction=direction,
                volume=pInvestorPosition.Volume,
                frozen=pInvestorPosition.ShortFrozen + pInvestorPosition.LongFrozen,
                price=pInvestorPosition.OpenCost / pInvestorPosition.Volume if pInvestorPosition.Volume else 0,
                cost=pInvestorPosition.OpenCost,
                pnl=pInvestorPosition.PositionProfit,
            )
            self.gw.positions[f"{pos.symbol}_{pos.direction.value}"] = pos
            self.gw.on_position(pos)

    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        if pRspInfo:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[TD] 错误回报: [{pRspInfo.ErrorID}] {msg}")
            self.gw._log(f"错误: [{pRspInfo.ErrorID}] {msg}")


# ──────────────────────────────────────────────────────────────────────────────
# 行情 SPI
# ──────────────────────────────────────────────────────────────────────────────

class _MdSpi(mdapi.CThostFtdcMdSpi):
    """行情 SPI 回调实现"""

    def __init__(self, gateway: "CTPNativeGateway"):
        super().__init__()
        self.gw = gateway

    def OnFrontConnected(self):
        logger.info("[MD] 行情前置连接成功")
        self.gw._log("行情前置连接成功")
        self.gw._req_user_login_md()

    def OnFrontDisconnected(self, nReason: int):
        logger.warning(f"[MD] 行情前置断开, reason={nReason}")

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[MD] 行情登录失败: [{pRspInfo.ErrorID}] {msg}")
            return
        logger.info("[MD] 行情登录成功")
        self.gw._log("行情登录成功")
        self.gw._md_logged_in = True
        # 如果有待订阅的合约，立即订阅
        if self.gw._pending_subscribe:
            self.gw._do_subscribe(list(self.gw._pending_subscribe))
            self.gw._pending_subscribe.clear()

    def OnRtnDepthMarketData(self, pDepthMarketData):
        if not pDepthMarketData:
            return
        try:
            symbol = pDepthMarketData.InstrumentID
            tick = MarketData(
                symbol=symbol,
                last_price=pDepthMarketData.LastPrice,
                bid_price_1=pDepthMarketData.BidPrice1,
                ask_price_1=pDepthMarketData.AskPrice1,
                bid_volume_1=pDepthMarketData.BidVolume1,
                ask_volume_1=pDepthMarketData.AskVolume1,
                volume=pDepthMarketData.Volume,
                turnover=pDepthMarketData.Turnover,
                timestamp=datetime.now(),
            )
            self.gw.latest_ticks[symbol] = tick
            self.gw.on_tick(tick)
        except Exception as e:
            logger.error(f"[MD] 处理行情数据异常: {e}")

    def OnRspSubMarketData(self, pSpecificInstrument, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[MD] 订阅行情失败: {msg}")
        elif pSpecificInstrument:
            logger.info(f"[MD] 已订阅: {pSpecificInstrument.InstrumentID}")

    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        if pRspInfo:
            msg = pRspInfo.ErrorMsg if hasattr(pRspInfo, 'ErrorMsg') else ''
            logger.error(f"[MD] 错误回报: [{pRspInfo.ErrorID}] {msg}")


# ──────────────────────────────────────────────────────────────────────────────
# 主网关类
# ──────────────────────────────────────────────────────────────────────────────

class CTPNativeGateway(GatewayBase):
    """原生 CTP 交易网关（基于 openctp-ctp）"""

    def __init__(self):
        super().__init__("CTP")
        self.broker_id: str = ""
        self.td_server: str = ""
        self.md_server: str = ""
        self.username: str = ""
        self.password: str = ""
        self.app_id: str = ""
        self.auth_code: str = ""

        self._td_api: Optional[tdapi.CThostFtdcTraderApi] = None
        self._md_api: Optional[mdapi.CThostFtdcMdApi] = None
        self._td_spi: Optional[_TraderSpi] = None
        self._md_spi: Optional[_MdSpi] = None

        self._front_id: int = 0
        self._session_id: int = 0
        self._order_ref: int = 1
        self._request_id: int = 0

        self._login_event = threading.Event()
        self._md_logged_in: bool = False
        self._pending_subscribe: set = set()

        self.latest_ticks: Dict[str, MarketData] = {}
        self._log_buffer: list = []

    # ── 日志辅助 ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._log_buffer.append(entry)
        if len(self._log_buffer) > 500:
            self._log_buffer = self._log_buffer[-500:]

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _next_order_ref(self) -> str:
        ref = self._order_ref
        self._order_ref += 1
        return str(ref)

    # ── 连接/断开 ──────────────────────────────────────────────────────────────

    def connect(self, config: Dict[str, Any]) -> bool:
        """连接 CTP 交易/行情服务器"""
        self.broker_id = config.get('broker_id', '')
        self.td_server = config.get('td_server', '')
        self.md_server = config.get('md_server', '')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.app_id = config.get('app_id', '')
        self.auth_code = config.get('auth_code', '')

        if not all([self.broker_id, self.td_server, self.username, self.password]):
            logger.error("CTP 连接参数不完整（需要 broker_id/td_server/username/password）")
            self._log("连接失败：参数不完整")
            self.status = TradingStatus.ERROR
            return False

        self.status = TradingStatus.CONNECTING
        self._login_event.clear()

        # 初始化交易 API
        flow_td = os.path.join("logs", "ctp_flow_td", "")
        _ensure_flow_dir(flow_td)
        self._td_api = tdapi.CThostFtdcTraderApi.CreateFtdcTraderApi(flow_td)
        self._td_spi = _TraderSpi(self)
        self._td_api.RegisterSpi(self._td_spi)
        self._td_api.SubscribePublicTopic(tdapi.THOST_TERT_QUICK)
        self._td_api.SubscribePrivateTopic(tdapi.THOST_TERT_QUICK)
        self._td_api.RegisterFront(self.td_server)
        self._td_api.Init()
        logger.info(f"[TD] 正在连接: {self.td_server}")
        self._log(f"正在连接交易前置: {self.td_server}")

        # 等待登录完成（最多 30 秒）
        if not self._login_event.wait(timeout=30):
            logger.error("[TD] 登录超时（30s）")
            self._log("登录超时（30s），请检查网络和服务器地址")
            self.status = TradingStatus.ERROR
            return False

        if self.status != TradingStatus.CONNECTED:
            return False

        # 启动行情 API（异步，不阻塞主流程）
        if self.md_server:
            threading.Thread(target=self._start_md, daemon=True).start()

        return True

    def _start_md(self):
        """在后台线程中启动行情 API"""
        try:
            flow_md = os.path.join("logs", "ctp_flow_md", "")
            _ensure_flow_dir(flow_md)
            self._md_api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi(flow_md)
            self._md_spi = _MdSpi(self)
            self._md_api.RegisterSpi(self._md_spi)
            self._md_api.RegisterFront(self.md_server)
            self._md_api.Init()
            logger.info(f"[MD] 正在连接: {self.md_server}")
            self._log(f"正在连接行情前置: {self.md_server}")
            self._md_api.Join()
        except Exception as e:
            logger.error(f"[MD] 行情 API 异常: {e}")

    def disconnect(self):
        """断开连接"""
        try:
            if self._md_api:
                self._md_api.Release()
                self._md_api = None
        except Exception:
            pass
        try:
            if self._td_api:
                self._td_api.Release()
                self._td_api = None
        except Exception:
            pass
        self.status = TradingStatus.STOPPED
        self._md_logged_in = False
        logger.info("[CTP] 已断开连接")
        self._log("已断开连接")

    # ── 请求方法 ───────────────────────────────────────────────────────────────

    def _req_authenticate(self):
        req = tdapi.CThostFtdcReqAuthenticateField()
        req.BrokerID = self.broker_id
        req.UserID = self.username
        req.AppID = self.app_id
        req.AuthCode = self.auth_code
        ret = self._td_api.ReqAuthenticate(req, self._next_request_id())
        if ret != 0:
            logger.error(f"[TD] ReqAuthenticate 失败: {ret}")

    def _req_user_login_td(self):
        req = tdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.broker_id
        req.UserID = self.username
        req.Password = self.password
        req.UserProductInfo = "openctp"
        ret = self._td_api.ReqUserLogin(req, self._next_request_id())
        if ret != 0:
            logger.error(f"[TD] ReqUserLogin 失败: {ret}")

    def _req_user_login_md(self):
        req = mdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.broker_id
        req.UserID = self.username
        req.Password = self.password
        ret = self._md_api.ReqUserLogin(req, self._next_request_id())
        if ret != 0:
            logger.error(f"[MD] ReqUserLogin 失败: {ret}")

    def _req_settlement_confirm(self):
        req = tdapi.CThostFtdcSettlementInfoConfirmField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.username
        ret = self._td_api.ReqSettlementInfoConfirm(req, self._next_request_id())
        if ret != 0:
            logger.warning(f"[TD] ReqSettlementInfoConfirm 失败: {ret}")
            # 部分仿真不需要确认，直接标记就绪
            self.status = TradingStatus.CONNECTED
            self._login_event.set()

    def _post_login_query(self):
        """登录完成后自动查询账户和持仓"""
        time.sleep(0.5)
        self.query_account()
        time.sleep(0.5)
        self.query_positions()

    # ── 行情订阅 ───────────────────────────────────────────────────────────────

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情"""
        if self._md_logged_in and self._md_api:
            self._do_subscribe(symbols)
        else:
            self._pending_subscribe.update(symbols)
            logger.info(f"[MD] 行情未就绪，已加入待订阅: {symbols}")

    def _do_subscribe(self, symbols: List[str]):
        if not self._md_api:
            return
        inst_list = [s.encode() for s in symbols]
        ret = self._md_api.SubscribeMarketData(inst_list, len(inst_list))
        if ret != 0:
            logger.error(f"[MD] SubscribeMarketData 失败: {ret}")
        else:
            logger.info(f"[MD] 已发起订阅: {symbols}")

    # ── 交易接口 ───────────────────────────────────────────────────────────────

    def send_order(self, signal: Signal) -> str:
        """发送委托"""
        if self.status != TradingStatus.CONNECTED:
            logger.warning("CTP 未就绪，无法下单")
            return ""
        if not self._td_api:
            return ""

        order_ref = self._next_order_ref()

        req = tdapi.CThostFtdcInputOrderField()
        req.BrokerID = self.broker_id
        req.InvestorID = self.username
        req.InstrumentID = signal.symbol
        req.OrderRef = order_ref
        req.UserID = self.username
        req.ExchangeID = get_exchange(signal.symbol)

        req.Direction = (
            tdapi.THOST_FTDC_D_Buy
            if signal.direction == Direction.LONG
            else tdapi.THOST_FTDC_D_Sell
        )

        if signal.order_type == OrderType.MARKET:
            req.OrderPriceType = tdapi.THOST_FTDC_OPT_AnyPrice
            req.LimitPrice = 0
            req.TimeCondition = tdapi.THOST_FTDC_TC_IOC
            req.VolumeCondition = tdapi.THOST_FTDC_VC_AV
        else:
            req.OrderPriceType = tdapi.THOST_FTDC_OPT_LimitPrice
            req.LimitPrice = signal.price
            req.TimeCondition = tdapi.THOST_FTDC_TC_GFD
            req.VolumeCondition = tdapi.THOST_FTDC_VC_AV

        req.VolumeTotalOriginal = signal.volume

        # 开平仓标志
        offset = getattr(signal, 'offset', OffsetFlag.OPEN)
        if offset == OffsetFlag.CLOSE:
            req.CombOffsetFlag = tdapi.THOST_FTDC_OF_Close
        elif offset == OffsetFlag.CLOSE_TODAY:
            req.CombOffsetFlag = tdapi.THOST_FTDC_OF_CloseToday
        elif offset == OffsetFlag.CLOSE_YESTERDAY:
            req.CombOffsetFlag = tdapi.THOST_FTDC_OF_CloseYesterday
        else:
            req.CombOffsetFlag = tdapi.THOST_FTDC_OF_Open

        req.CombHedgeFlag = tdapi.THOST_FTDC_HF_Speculation
        req.ContingentCondition = tdapi.THOST_FTDC_CC_Immediately
        req.ForceCloseReason = tdapi.THOST_FTDC_FCC_NotForceClose
        req.IsAutoSuspend = 0

        ret = self._td_api.ReqOrderInsert(req, self._next_request_id())
        if ret != 0:
            logger.error(f"[TD] ReqOrderInsert 失败: {ret}")
            return ""

        order_id = f"{self._front_id}:{self._session_id}:{order_ref}"
        order = Order(
            order_id=order_id,
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            status=OrderStatus.SUBMITTING,
            offset=getattr(signal, 'offset', OffsetFlag.OPEN),
        )
        self.orders[order_id] = order
        logger.info(f"[TD] 已发送委托: {signal.symbol} {signal.direction.value} {offset.value} {signal.volume}@{signal.price}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """撤销委托"""
        if not self._td_api:
            return False

        order = self.orders.get(order_id)
        if not order or not order.is_active():
            return False

        try:
            parts = order_id.split(":")
            front_id = int(parts[0]) if len(parts) >= 3 else self._front_id
            session_id = int(parts[1]) if len(parts) >= 3 else self._session_id
            order_ref = parts[2] if len(parts) >= 3 else order_id

            req = tdapi.CThostFtdcInputOrderActionField()
            req.BrokerID = self.broker_id
            req.InvestorID = self.username
            req.OrderRef = order_ref
            req.FrontID = front_id
            req.SessionID = session_id
            req.ActionFlag = tdapi.THOST_FTDC_AF_Delete
            req.InstrumentID = order.symbol
            req.ExchangeID = get_exchange(order.symbol)

            ret = self._td_api.ReqOrderAction(req, self._next_request_id())
            if ret != 0:
                logger.error(f"[TD] ReqOrderAction 失败: {ret}")
                return False
            return True
        except Exception as e:
            logger.error(f"[TD] 撤单异常: {e}")
            return False

    def query_account(self) -> AccountInfo:
        """查询资金账户"""
        if self._td_api and self.status == TradingStatus.CONNECTED:
            req = tdapi.CThostFtdcQryTradingAccountField()
            req.BrokerID = self.broker_id
            req.InvestorID = self.username
            self._td_api.ReqQryTradingAccount(req, self._next_request_id())
        return self.account

    def query_positions(self) -> List[Position]:
        """查询持仓"""
        if self._td_api and self.status == TradingStatus.CONNECTED:
            req = tdapi.CThostFtdcQryInvestorPositionField()
            req.BrokerID = self.broker_id
            req.InvestorID = self.username
            self._td_api.ReqQryInvestorPosition(req, self._next_request_id())
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        """返回本地订单缓存"""
        return list(self.orders.values())


def create_ctp_native_gateway() -> CTPNativeGateway:
    return CTPNativeGateway()
