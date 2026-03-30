"""
CtpPlus 期货交易网关
使用方式：继承 TraderApiBase / MdApiBase，重写回调方法。
C 扩展通过 __new__ 捕获构造参数，super().__init__() 不传参数。
回调数据格式为 dict。
"""

import logging
import os
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from CtpPlus.CTP.TraderApiBase import TraderApiBase
    from CtpPlus.CTP.MdApiBase import MdApiBase
    CTP_AVAILABLE = True
    _CTP_IMPORT_ERROR = ""
except Exception as _e:
    # 未安装或平台不支持时，用占位符，connect() 里会抛出 ImportError
    TraderApiBase = object
    MdApiBase = object
    CTP_AVAILABLE = False
    _CTP_IMPORT_ERROR = str(_e)

from . import GatewayBase, TradingStatus, AccountInfo, MarketData
from ..strategy import (
    Signal, Order, Trade, Direction, Position,
    OrderStatus as OrderStatusEnum, OrderType,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────

def _b(s) -> bytes:
    """str / int → bytes"""
    if isinstance(s, bytes):
        return s
    return str(s).encode()


def _s(b, enc: str = 'gbk') -> str:
    """bytes → str，去除尾部空字节"""
    if isinstance(b, bytes):
        return b.decode(enc, errors='ignore').rstrip('\x00').strip()
    return str(b) if b is not None else ''


def _strip_tcp(addr: str) -> str:
    """'tcp://ip:port' → 'ip:port'"""
    return addr.replace('tcp://', '').strip()


def _first_byte(val, default: bytes = b'\x00') -> bytes:
    """取 bytes 的第一个字节，用于枚举判断"""
    if isinstance(val, bytes) and val:
        return val[:1]
    return default


# ─────────────────────────────────────────────────────────
# 交易引擎（内部类）
# ─────────────────────────────────────────────────────────

class _TdEngine(TraderApiBase):
    """
    CTP 交易引擎。
    构造参数由 C 扩展的 __new__ 捕获，super().__init__() 不传参。
    创建后立即设置 _gateway 属性（网络连接有延迟，绝对安全）。
    """

    def __init__(self, broker_id, td_server, investor_id, password,
                 app_id, auth_code,
                 md_queue=None, flow_path='./log/',
                 private_resume_type=2, public_resume_type=2,
                 production_mode=True):
        super().__init__()   # C 扩展已通过 __new__ 拿到所有参数

    # 重写 Join，让主线程不阻塞；CTP 内部线程在后台继续运行
    def Join(self, *args, **kwargs):
        pass

    # ── 回调 ────────────────────────────────────────────

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        gw = getattr(self, '_gateway', None)
        if gw is None:
            return
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            gw._connect_error = (
                f"登录失败 [{pRspInfo['ErrorID']}]: "
                f"{_s(pRspInfo.get('ErrorMsg', b''))}"
                f"（请确认账户、密码和 BrokerID 正确）"
            )
            gw.status = TradingStatus.ERROR
        else:
            logger.info(f"CTP 交易登录成功，账户: {_s(pRspUserLogin.get('UserID', b''))}")
            gw.status = TradingStatus.CONNECTED

    def OnFrontDisconnected(self, nReason):
        gw = getattr(self, '_gateway', None)
        if gw and gw.status == TradingStatus.CONNECTED:
            logger.warning(f"CTP 交易前置断开，原因: {nReason}")
            gw.status = TradingStatus.ERROR

    def OnRtnOrder(self, pOrder):
        gw = getattr(self, '_gateway', None)
        if gw is None:
            return
        try:
            order_ref = _s(pOrder.get('OrderRef', b''))
            local_id = f"CTP_{order_ref}"

            if local_id not in gw.orders:
                direction = (Direction.LONG
                             if _first_byte(pOrder.get('Direction')) == b'0'
                             else Direction.SHORT)
                gw.orders[local_id] = Order(
                    order_id=local_id,
                    symbol=_s(pOrder.get('InstrumentID', b'')),
                    direction=direction,
                    order_type=OrderType.LIMIT,
                    price=float(pOrder.get('LimitPrice', 0)),
                    volume=int(pOrder.get('VolumeTotalOriginal', 0)),
                    status=OrderStatusEnum.SUBMITTED,
                )

            order = gw.orders[local_id]
            status_byte = _first_byte(pOrder.get('OrderStatus', b'3'), b'3')
            order.status = {
                b'0': OrderStatusEnum.FILLED,
                b'1': OrderStatusEnum.PARTIAL_FILLED,
                b'5': OrderStatusEnum.CANCELLED,
                b'3': OrderStatusEnum.SUBMITTED,
                b'4': OrderStatusEnum.REJECTED,
            }.get(status_byte, OrderStatusEnum.UNKNOWN)
            order.traded_volume = int(pOrder.get('VolumeTraded', 0))
            order.update_time = datetime.now()
            gw.on_order(order)
        except Exception as e:
            logger.error(f"OnRtnOrder 处理失败: {e}")

    def OnRtnTrade(self, pTrade):
        gw = getattr(self, '_gateway', None)
        if gw is None:
            return
        try:
            instrument_id = _s(pTrade.get('InstrumentID', b''))
            exchange = _s(pTrade.get('ExchangeID', b''))
            symbol = f"{exchange}.{instrument_id}" if exchange else instrument_id
            direction = (Direction.LONG
                         if _first_byte(pTrade.get('Direction')) == b'0'
                         else Direction.SHORT)
            trade = Trade(
                trade_id=_s(pTrade.get('TradeID', b'')),
                order_id=f"CTP_{_s(pTrade.get('OrderRef', b''))}",
                symbol=symbol,
                direction=direction,
                price=float(pTrade.get('Price', 0)),
                volume=int(pTrade.get('Volume', 0)),
                commission=0.0,
                trade_time=datetime.now(),
            )
            gw.on_trade(trade)
        except Exception as e:
            logger.error(f"OnRtnTrade 处理失败: {e}")

    def OnRspQryTradingAccount(self, pRspInvestorAccount, pRspInfo, nRequestID, bIsLast):
        gw = getattr(self, '_gateway', None)
        if gw is None or not pRspInvestorAccount:
            return
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            logger.error(f"查询资金失败: {_s(pRspInfo.get('ErrorMsg', b''))}")
            return
        acct = AccountInfo(
            account_id=_s(pRspInvestorAccount.get('AccountID', b'')),
            balance=float(pRspInvestorAccount.get('Balance', 0)),
            available=float(pRspInvestorAccount.get('Available', 0)),
            margin=float(pRspInvestorAccount.get('CurrMargin', 0)),
            commission=float(pRspInvestorAccount.get('Commission', 0)),
            position_pnl=float(pRspInvestorAccount.get('PositionProfit', 0)),
            total_pnl=(float(pRspInvestorAccount.get('CloseProfit', 0))
                       + float(pRspInvestorAccount.get('PositionProfit', 0))),
        )
        gw.account = acct
        gw.on_account(acct)

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast):
        gw = getattr(self, '_gateway', None)
        if gw is None or not pInvestorPosition:
            return
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            logger.error(f"查询持仓失败: {_s(pRspInfo.get('ErrorMsg', b''))}")
            return
        instrument_id = _s(pInvestorPosition.get('InstrumentID', b''))
        pos_dir = _first_byte(pInvestorPosition.get('PosiDirection', b'2'), b'2')
        direction = Direction.LONG if pos_dir == b'2' else Direction.SHORT
        volume = int(pInvestorPosition.get('Position', 0))
        open_cost = float(pInvestorPosition.get('OpenCost', 0))
        position = Position(
            symbol=instrument_id,
            direction=direction,
            volume=volume,
            price=open_cost / volume if volume > 0 else 0.0,
            pnl=float(pInvestorPosition.get('PositionProfit', 0)),
        )
        gw.positions[instrument_id] = position
        gw.on_position(position)

    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            logger.error(
                f"CTP 错误 [{pRspInfo['ErrorID']}]: "
                f"{_s(pRspInfo.get('ErrorMsg', b''))}"
            )


# ─────────────────────────────────────────────────────────
# 行情引擎（内部类）
# ─────────────────────────────────────────────────────────

class _MdEngine(MdApiBase):
    """
    CTP 行情引擎。
    instrument_id_list 须为 bytes 列表，例如 [b'rb2505']。
    """

    def __init__(self, broker_id, md_server, investor_id, password,
                 app_id, auth_code,
                 instrument_id_list=None,
                 md_queue_list=None, page_dir='./log/',
                 using_udp=False, multicast=False,
                 production_mode=True, period='1m'):
        super().__init__()   # C 扩展已通过 __new__ 拿到所有参数

    def Join(self, *args, **kwargs):
        pass

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            logger.error(f"CTP 行情登录失败: {_s(pRspInfo.get('ErrorMsg', b''))}")
        else:
            logger.info("CTP 行情登录成功")

    def OnRtnDepthMarketData(self, pDepthMarketData):
        gw = getattr(self, '_gateway', None)
        if gw is None:
            return
        try:
            instrument_id = _s(pDepthMarketData.get('InstrumentID', b''))
            exchange = _s(pDepthMarketData.get('ExchangeID', b''))
            symbol = f"{exchange}.{instrument_id}" if exchange else instrument_id
            tick = MarketData(
                symbol=symbol,
                last_price=float(pDepthMarketData.get('LastPrice', 0)),
                bid_price_1=float(pDepthMarketData.get('BidPrice1', 0)),
                ask_price_1=float(pDepthMarketData.get('AskPrice1', 0)),
                bid_volume_1=int(pDepthMarketData.get('BidVolume1', 0)),
                ask_volume_1=int(pDepthMarketData.get('AskVolume1', 0)),
                volume=int(pDepthMarketData.get('Volume', 0)),
                turnover=float(pDepthMarketData.get('Turnover', 0)),
                timestamp=datetime.now(),
            )
            gw.tick_cache[symbol] = tick
            gw.on_tick(tick)
        except Exception as e:
            logger.error(f"OnRtnDepthMarketData 处理失败: {e}")

    def OnRspSubMarketData(self, pSpecificInstrument, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.get('ErrorID', 0) != 0:
            logger.error(f"订阅行情失败: {_s(pRspInfo.get('ErrorMsg', b''))}")
        else:
            instrument = _s(pSpecificInstrument.get('InstrumentID', b'')) if pSpecificInstrument else ''
            logger.info(f"订阅行情成功: {instrument}")


# ─────────────────────────────────────────────────────────
# 网关主类
# ─────────────────────────────────────────────────────────

class CtpPlusGateway(GatewayBase):
    """CtpPlus 期货交易网关（支持 SimNow 及生产环境）"""

    def __init__(self):
        super().__init__("CTPPLUS")

        # 连接参数
        self.broker_id: str = "9999"
        self.td_server: str = ""
        self.md_server: str = ""
        self.username: str = ""
        self.password: str = ""
        self.app_id: str = ""
        self.auth_code: str = ""

        # 连接错误信息（由回调填写）
        self._connect_error: str = ""

        # 内部引擎
        self._td_engine: Optional[_TdEngine] = None
        self._md_engine: Optional[_MdEngine] = None

        # 行情缓存
        self.tick_cache: Dict[str, MarketData] = {}

        # 线程锁（供查询请求使用）
        self._req_lock = threading.Lock()

    # ── 连接 / 断连 ────────────────────────────────────

    def connect(self, config: Dict[str, Any]) -> bool:
        """连接到 CTP 服务器（SimNow 或生产）"""
        self.status = TradingStatus.CONNECTING
        self._connect_error = ""

        # 读取配置
        self.broker_id  = str(config.get('broker_id', '9999'))
        self.td_server  = config.get('td_server', '')
        self.md_server  = config.get('md_server', '')
        self.username   = config.get('username', '')
        self.password   = config.get('password', '')
        self.app_id     = config.get('app_id', '')
        self.auth_code  = config.get('auth_code', '')

        # 参数校验
        if not self.username or not self.password:
            self.status = TradingStatus.ERROR
            raise ValueError("CTP 登录失败：username 和 password 不能为空")
        if not self.app_id or not self.auth_code:
            self.status = TradingStatus.ERROR
            raise ValueError("CTP 登录失败：app_id 和 auth_code 不能为空")
        if not self.td_server:
            self.status = TradingStatus.ERROR
            raise ValueError("CTP 登录失败：td_server 不能为空")

        if not CTP_AVAILABLE:
            self.status = TradingStatus.ERROR
            raise ImportError(
                f"CtpPlus 库导入失败，无法连接 CTP 服务器。\n"
                f"错误详情: {_CTP_IMPORT_ERROR}\n"
                f"请确认安装: pip install ctpplus"
            )

        # 创建流文件目录
        os.makedirs('./log', exist_ok=True)

        logger.info(f"正在连接 CTP 交易前置: {self.td_server} (BrokerID={self.broker_id})")

        # 创建交易引擎（C 扩展在 __new__ 时捕获参数并在后台发起连接）
        self._td_engine = _TdEngine(
            broker_id=self.broker_id,
            td_server=_strip_tcp(self.td_server),
            investor_id=self.username,
            password=self.password,
            app_id=self.app_id,
            auth_code=self.auth_code,
            flow_path='./log/',
            production_mode=True,
        )
        # 注入 gateway 引用（网络连接有延迟，此处赋值早于任何回调）
        self._td_engine._gateway = self

        # 等待登录完成（最多 15 秒）
        timeout = 15.0
        elapsed = 0.0
        while elapsed < timeout:
            if self.status == TradingStatus.CONNECTED:
                break
            if self.status == TradingStatus.ERROR:
                break
            time.sleep(0.2)
            elapsed += 0.2

        if self.status != TradingStatus.CONNECTED:
            detail = (f"\n原因: {self._connect_error}" if self._connect_error
                      else f"\n请检查服务器地址、BrokerID ({self.broker_id})、账号和密码是否正确。")
            self.status = TradingStatus.ERROR
            raise ConnectionError(f"CTP 登录失败：{timeout:.0f} 秒内未完成。{detail}")

        # 连接行情服务器（有则创建 MD 引擎）
        if self.md_server:
            symbol = config.get('symbol', '')
            instrument_list = [_b(symbol.split('.')[-1])] if symbol else []
            logger.info(f"正在连接 CTP 行情前置: {self.md_server}")
            self._md_engine = _MdEngine(
                broker_id=self.broker_id,
                md_server=_strip_tcp(self.md_server),
                investor_id=self.username,
                password=self.password,
                app_id=self.app_id,
                auth_code=self.auth_code,
                instrument_id_list=instrument_list,
                page_dir='./log/',
                production_mode=True,
            )
            self._md_engine._gateway = self

        logger.info("CTP 网关连接成功")
        return True

    def disconnect(self):
        """断开连接"""
        logger.info("正在断开 CTP 连接...")
        self._td_engine = None
        self._md_engine = None
        self.status = TradingStatus.STOPPED
        logger.info("CTP 连接已断开")

    # ── 交易操作 ────────────────────────────────────────

    def send_order(self, signal: Signal) -> str:
        """发送订单"""
        if self.status != TradingStatus.CONNECTED or not self._td_engine:
            raise RuntimeError("CTP 网关未连接，无法发送订单")

        instrument = signal.symbol.split('.')[-1] if '.' in signal.symbol else signal.symbol
        exchange = self._get_exchange_by_product(instrument)
        exchange_b = _b(exchange)
        instrument_b = _b(instrument)
        price = float(signal.price)
        vol = abs(int(signal.volume))

        # 根据方向和开平确定操作
        if signal.direction == Direction.LONG and signal.volume > 0:
            ret = self._td_engine.buy_open(exchange_b, instrument_b, price, vol)
            action = '买开'
        elif signal.direction == Direction.LONG and signal.volume < 0:
            ret = self._td_engine.buy_close(exchange_b, instrument_b, price, vol)
            action = '买平'
        elif signal.direction == Direction.SHORT and signal.volume > 0:
            ret = self._td_engine.sell_open(exchange_b, instrument_b, price, vol)
            action = '卖开'
        elif signal.direction == Direction.SHORT and signal.volume < 0:
            ret = self._td_engine.sell_close(exchange_b, instrument_b, price, vol)
            action = '卖平'
        else:
            logger.warning(f"无法识别的信号方向/数量: {signal.direction} {signal.volume}")
            return ""

        # CTP 约定：返回 0 表示成功，非零为错误码
        if ret:
            raise RuntimeError(f"CTP 报单失败，错误码: {ret}（{action} {instrument} {vol}@{price}）")

        # order_ref 由 C 扩展内部维护，将通过 OnRtnOrder 回调得到
        order_id = f"CTP_{instrument}_{int(time.time() * 1000)}"
        logger.info(f"报单已发送: {action} {instrument} {vol}@{price}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if not self._td_engine:
            return False
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False

        order = self.orders[order_id]
        if not order.is_active():
            logger.warning(f"订单已非活动状态，无法撤销: {order_id}")
            return False

        instrument = order.symbol.split('.')[-1] if '.' in order.symbol else order.symbol
        exchange = self._get_exchange_by_product(instrument)

        # OrderRef 存在于 order_id 里（格式 CTP_<ref>）
        order_ref_part = order_id.replace('CTP_', '', 1)
        ret = self._td_engine.cancel_order(
            _b(exchange), _b(instrument), _b(order_ref_part)
        )
        if ret:
            logger.error(f"撤单失败，错误码: {ret}")
            return False
        logger.info(f"撤单请求已发送: {order_id}")
        return True

    # ── 查询 ───────────────────────────────────────────

    def query_account(self) -> AccountInfo:
        """查询账户资金（异步，结果通过 on_account 回调返回）"""
        if self._td_engine:
            with self._req_lock:
                self._td_engine.query_trading_account()
        return self.account

    def query_positions(self) -> List[Position]:
        """查询持仓（异步，结果通过 on_position 回调返回）"""
        if self._td_engine:
            with self._req_lock:
                self._td_engine.query_position()
        return list(self.positions.values())

    def query_orders(self) -> List[Order]:
        """查询当日订单（异步）"""
        if self._td_engine:
            with self._req_lock:
                self._td_engine.query_order()
        return list(self.orders.values())

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情（如 MD 引擎已创建则直接订阅）"""
        if not self._md_engine:
            logger.warning("行情引擎未初始化，无法订阅")
            return
        instrument_list = [_b(s.split('.')[-1]) for s in symbols]
        self._md_engine.SubscribeMarketData(instrument_list)
        logger.info(f"已发送行情订阅请求: {[s.split('.')[-1] for s in symbols]}")

    # ── 交易所识别 ──────────────────────────────────────

    def _get_exchange_by_product(self, instrument_id: str) -> str:
        """根据合约代码推断交易所（按品种前缀匹配）"""
        prefix = ''.join(c for c in instrument_id if c.isalpha()).upper()

        CFFEX = {'IF', 'IC', 'IH', 'IM', 'T', 'TF', 'TS'}
        INE   = {'SC', 'NR', 'LU', 'BC'}
        SHFE  = {'CU', 'AL', 'ZN', 'PB', 'NI', 'SN', 'AU', 'AG',
                 'RB', 'WR', 'HC', 'FU', 'BU', 'RU', 'SS', 'SP', 'AO', 'BR'}
        DCE   = {'A', 'B', 'C', 'CS', 'EB', 'EG', 'I', 'J', 'JD', 'JM',
                 'L', 'LH', 'M', 'P', 'PG', 'PP', 'RR', 'V', 'Y', 'FB', 'BB', 'PF'}
        CZCE  = {'AP', 'CF', 'CJ', 'CY', 'FG', 'JR', 'LR', 'MA', 'OI',
                 'PK', 'PM', 'RI', 'RM', 'RS', 'SA', 'SF', 'SM', 'SR',
                 'TA', 'UR', 'WH', 'ZC'}

        if prefix in CFFEX: return 'CFFEX'
        if prefix in INE:   return 'INE'
        if prefix in SHFE:  return 'SHFE'
        if prefix in DCE:   return 'DCE'
        if prefix in CZCE:  return 'CZCE'
        return 'SHFE'


def create_ctp_plus_gateway() -> CtpPlusGateway:
    """创建 CtpPlus 网关实例"""
    return CtpPlusGateway()
