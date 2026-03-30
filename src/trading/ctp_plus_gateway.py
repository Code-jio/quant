"""
CtpPlus 期货交易网关 - 适用于 SimNow 模拟环境
使用 CtpPlus 库实现 CTP 接口连接
"""

import logging
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
import threading
import time

# 尝试导入真实的CtpPlus，如果失败则使用模拟实现
try:
    from ctpplus import TdApi, MdApi, ApiStruct
    CTP_AVAILABLE = True
    logger = logging.getLogger(__name__)
except ImportError:
    # 如果真实库不可用，使用模拟实现
    from .ctp_plus_api_mock import TdApi, MdApi, ApiStruct
    CTP_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("CtpPlus 库不可用，使用模拟实现。实盘交易时请确保安装正确的 CtpPlus 库。")

from . import GatewayBase, TradingStatus, AccountInfo, MarketData
from ..strategy import Signal, Order, Trade, Direction, Position, OrderStatus as OrderStatusEnum, OrderType

logger = logging.getLogger(__name__)


class CtpPlusGateway(GatewayBase):
    """CtpPlus 期货交易网关 - 支持 SimNow 模拟环境"""

    def __init__(self):
        super().__init__("CTPPLUS")
        self.td_api: Optional[TdApi] = None
        self.md_api: Optional[MdApi] = None
        self.request_id = 0
        self.order_ref = 0

        # 配置参数
        self.broker_id: str = "9999"  # SimNow 模拟环境默认 BrokerID
        self.td_server: str = "tcp://180.168.146.187:10100"  # SimNow 交易前置地址
        self.md_server: str = "tcp://180.168.146.187:10110"  # SimNow 行情前置地址
        self.username: str = ""       # 投资者账号
        self.password: str = ""       # 密码
        self.user_product_info: str = ""
        self.auth_code: str = ""
        self.app_id: str = ""

        # 会话信息
        self.front_id: int = 0
        self.session_id: int = 0

        # 数据缓存
        self.sys_order_id_to_local: Dict[str, str] = {}  # 系统订单号 -> 本地订单号
        self.local_order_id_to_sys: Dict[str, str] = {}  # 本地订单号 -> 系统订单号
        self.instruments: Dict[str, Any] = {}           # 合约信息
        self.tick_cache: Dict[str, MarketData] = {}     # 行情缓存

        # 线程锁
        self.order_lock = threading.Lock()
        self.req_lock = threading.Lock()

    def connect(self, config: Dict[str, Any]) -> bool:
        """连接到 CtpPlus 服务（SimNow 模拟环境）"""
        self.status = TradingStatus.CONNECTING
        logger.info("正在连接到 CtpPlus SimNow 模拟环境...")

        # 从配置加载参数
        self.broker_id = config.get('broker_id', '9999')
        self.td_server = config.get('td_server', 'tcp://180.168.146.187:10100')
        self.md_server = config.get('md_server', 'tcp://180.168.146.187:10110')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.user_product_info = config.get('user_product_info', '')
        self.auth_code = config.get('auth_code', '0000000000000000')  # SimNow 测试认证码
        self.app_id = config.get('app_id', 'simnow_client_test')      # SimNow 测试 AppID

        # 验证必要参数
        if not self.username or not self.password:
            logger.error("用户名和密码不能为空")
            self.status = TradingStatus.ERROR
            return False

        if not CTP_AVAILABLE:
            logger.warning("CtpPlus 不可用，使用模拟模式进行连接测试")
            # 在模拟模式下，直接设置为已连接状态
            self.status = TradingStatus.CONNECTED
            return True

        try:
            # 初始化交易API
            self.td_api = TdApi()
            self.td_api.Create()

            # 设置日志输出路径（可选）
            # self.td_api.SetLogPrintLevel(2)  # 设置日志级别

            # 初始化行情API
            self.md_api = MdApi()
            self.md_api.Create()

            # 注册回调函数
            self._register_callbacks()

            # 连接到前置机
            logger.info(f"正在连接到交易前置: {self.td_server}")
            self.td_api.RegisterFront(self.td_server.replace('tcp://', ''))
            self.td_api.Init()

            logger.info(f"正在连接到行情前置: {self.md_server}")
            self.md_api.RegisterFront(self.md_server.replace('tcp://', ''))
            self.md_api.Init()

            # 等待连接成功（这里只是简单等待，实际应该通过回调确认连接状态）
            time.sleep(3)

            if self.status != TradingStatus.CONNECTED:
                self.status = TradingStatus.ERROR
                logger.error("连接超时")
                return False

            logger.info("CtpPlus SimNow 模拟环境连接成功")
            return True

        except Exception as e:
            logger.error(f"CtpPlus 连接失败: {e}")
            self.status = TradingStatus.ERROR
            return False

    def disconnect(self):
        """断开连接"""
        logger.info("正在断开 CtpPlus 连接...")

        if not CTP_AVAILABLE:
            logger.info("CtpPlus 模拟模式，断开连接")
            self.status = TradingStatus.STOPPED
            return

        try:
            if self.td_api:
                self.td_api.Release()
                self.td_api = None
            if self.md_api:
                self.md_api.Release()
                self.md_api = None

            self.status = TradingStatus.STOPPED
            logger.info("CtpPlus 连接已断开")

        except Exception as e:
            logger.error(f"断开连接时出错: {e}")

    def send_order(self, signal: Signal) -> str:
        """发送订单到 CTPPlus"""
        if self.status != TradingStatus.CONNECTED:
            logger.warning("CtpPlus 未连接，无法发送订单")
            return ""

        with self.order_lock:
            try:
                self.order_ref += 1
                local_order_id = f"CTPPLUS_{self.order_ref}_{int(time.time())}"

                # 创建订单对象
                order = Order(
                    order_id=local_order_id,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    order_type=signal.order_type,
                    price=signal.price,
                    volume=signal.volume,
                    status=OrderStatusEnum.SUBMITTED
                )

                # 存储本地订单
                self.orders[local_order_id] = order

                if not CTP_AVAILABLE:
                    # 模拟模式下直接返回订单ID
                    logger.info(f"[模拟] 订单已发送: {signal.symbol} {signal.direction.value} {abs(signal.volume)}@{signal.price}")
                    return local_order_id

                # 准备订单请求
                input_order_field = ApiStruct.InputOrderField()
                input_order_field.BrokerID = self.broker_id.encode('utf-8')
                input_order_field.InvestorID = self.username.encode('utf-8')
                input_order_field.InstrumentID = self._extract_symbol(signal.symbol).encode('utf-8')

                # 设置方向和开平标志
                if signal.direction == Direction.LONG:
                    input_order_field.Direction = ApiStruct.Direction.D_Buy
                    # 判断是开仓还是平仓
                    if signal.volume > 0:  # 开多
                        input_order_field.CombOffsetFlag = ApiStruct.OffsetFlag.OF_Open
                    else:  # 平多
                        input_order_field.CombOffsetFlag = ApiStruct.OffsetFlag.OF_Close
                else:  # SHORT
                    input_order_field.Direction = ApiStruct.Direction.D_Sell
                    # 判断是开仓还是平仓
                    if signal.volume > 0:  # 开空
                        input_order_field.CombOffsetFlag = ApiStruct.OffsetFlag.OF_Open
                    else:  # 平空
                        input_order_field.CombOffsetFlag = ApiStruct.OffsetFlag.OF_Close

                # 订单类型
                if signal.order_type == OrderType.MARKET:
                    input_order_field.OrderPriceType = ApiStruct.OrderPriceType.OPT_AnyPrice
                    input_order_field.LimitPrice = 0
                else:  # LIMIT ORDER
                    input_order_field.OrderPriceType = ApiStruct.OrderPriceType.OPT_LimitPrice
                    input_order_field.LimitPrice = signal.price

                input_order_field.VolumeTotalOriginal = abs(signal.volume)
                input_order_field.TimeCondition = ApiStruct.TimeCondition.TC_GFD  # 当日有效
                input_order_field.VolumeCondition = ApiStruct.VolumeCondition.VC_AV  # 任何数量
                input_order_field.ContingentCondition = ApiStruct.ContingentCondition.CC_Immediately  # 立即发单
                input_order_field.OrderSysID = b""  # 系统单号，下单后由系统分配
                input_order_field.UserID = self.username.encode('utf-8')
                input_order_field.OrderLocalID = str(self.order_ref).encode('utf-8')

                # 发送订单
                with self.req_lock:
                    self.request_id += 1
                    ret = self.td_api.ReqOrderInsert(input_order_field, self.request_id)

                if ret == 0:
                    # 订单提交成功，保存映射关系
                    self.sys_order_id_to_local[input_order_field.OrderLocalID.decode()] = local_order_id
                    self.local_order_id_to_sys[local_order_id] = input_order_field.OrderLocalID.decode()

                    logger.info(f"订单已发送: {signal.symbol} {signal.direction.value} {abs(signal.volume)}@{signal.price}")
                    return local_order_id
                else:
                    logger.error(f"订单发送失败: 错误码 {ret}")
                    return ""

            except Exception as e:
                logger.error(f"发送订单失败: {e}")
                return ""

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False

        if not CTP_AVAILABLE:
            # 模拟模式下，更新订单状态
            order = self.orders[order_id]
            order.status = OrderStatusEnum.CANCELLED
            self.on_order(order)
            logger.info(f"[模拟] 撤销订单: {order_id}")
            return True

        try:
            order = self.orders[order_id]

            if not order.is_active():
                logger.warning(f"订单非活动状态，无法撤销: {order_id}")
                return False

            # 查找系统订单号
            sys_order_id = self.local_order_id_to_sys.get(order_id)
            if not sys_order_id:
                logger.error(f"找不到对应的系统订单号: {order_id}")
                return False

            # 准备撤单请求
            input_order_action = ApiStruct.InputOrderActionField()
            input_order_action.BrokerID = self.broker_id.encode('utf-8')
            input_order_action.InvestorID = self.username.encode('utf-8')
            input_order_action.OrderActionRef = self.request_id
            input_order_action.OrderRef = sys_order_id.encode('utf-8')  # 被撤单的订单引用
            input_order_action.RequestID = self.request_id
            input_order_action.FrontID = self.front_id
            input_order_action.SessionID = self.session_id
            input_order_action.ActionFlag = ApiStruct.ActionFlag.AF_Delete  # 撤单操作
            input_order_action.UserID = self.username.encode('utf-8')
            input_order_action.InstrumentID = self._extract_symbol(order.symbol).encode('utf-8')

            # 发送撤单请求
            with self.req_lock:
                self.request_id += 1
                ret = self.td_api.ReqOrderAction(input_order_action, self.request_id)

            if ret == 0:
                logger.info(f"撤单请求已发送: {order_id}")
                return True
            else:
                logger.error(f"撤单请求发送失败: 错误码 {ret}")
                return False

        except Exception as e:
            logger.error(f"撤销订单失败: {e}")
            return False

    def query_account(self) -> AccountInfo:
        """查询账户资金"""
        if not CTP_AVAILABLE:
            # 模拟模式下返回模拟账户信息
            logger.info("[模拟] 查询账户资金")
            return AccountInfo(
                account_id=self.username,
                balance=1000000.0,
                available=900000.0,
                margin=100000.0,
                commission=0.0,
                position_pnl=0.0,
                total_pnl=0.0
            )

        try:
            if not self.td_api:
                return AccountInfo(error_msg="未连接")

            # 查询资金账户
            req = ApiStruct.QryTradingAccountField()
            req.BrokerID = self.broker_id.encode('utf-8')
            req.InvestorID = self.username.encode('utf-8')

            with self.req_lock:
                self.request_id += 1
                ret = self.td_api.ReqQryTradingAccount(req, self.request_id)

            if ret != 0:
                logger.error(f"查询账户资金失败: 错误码 {ret}")
                return AccountInfo(error_msg=f"查询失败: {ret}")

            # 由于是异步查询，这里先返回上次查询结果
            return self.account

        except Exception as e:
            logger.error(f"查询账户异常: {e}")
            return AccountInfo(error_msg=str(e))

    def query_positions(self) -> List[Position]:
        """查询持仓"""
        if not CTP_AVAILABLE:
            # 模拟模式下返回空持仓
            logger.info("[模拟] 查询持仓")
            return []

        try:
            if not self.td_api:
                return []

            # 查询持仓
            req = ApiStruct.QryInvestorPositionField()
            req.BrokerID = self.broker_id.encode('utf-8')
            req.InvestorID = self.username.encode('utf-8')
            # 不指定合约则查询全部持仓
            req.InstrumentID = b""

            with self.req_lock:
                self.request_id += 1
                ret = self.td_api.ReqQryInvestorPosition(req, self.request_id)

            if ret != 0:
                logger.error(f"查询持仓失败: 错误码 {ret}")
                return []

            # 由于是异步查询，这里返回缓存的持仓
            return list(self.positions.values())

        except Exception as e:
            logger.error(f"查询持仓异常: {e}")
            return []

    def query_orders(self) -> List[Order]:
        """查询订单"""
        if not CTP_AVAILABLE:
            # 模拟模式下返回当前订单
            logger.info("[模拟] 查询订单")
            return list(self.orders.values())

        try:
            if not self.td_api:
                return []

            # 查询订单
            req = ApiStruct.QryOrderField()
            req.BrokerID = self.broker_id.encode('utf-8')
            req.InvestorID = self.username.encode('utf-8')
            req.InstrumentID = b""

            with self.req_lock:
                self.request_id += 1
                ret = self.td_api.ReqQryOrder(req, self.request_id)

            if ret != 0:
                logger.error(f"查询订单失败: 错误码 {ret}")
                return []

            # 返回本地订单缓存
            return list(self.orders.values())

        except Exception as e:
            logger.error(f"查询订单异常: {e}")
            return []

    def subscribe_market_data(self, symbols: List[str]):
        """订阅行情"""
        if not CTP_AVAILABLE:
            # 模拟模式下记录订阅
            logger.info(f"[模拟] 订阅行情: {symbols}")
            return

        try:
            if not self.md_api:
                return

            # 转换合约列表格式
            symbol_list = []
            for symbol in symbols:
                # 提取纯合约代码（去掉交易所前缀）
                pure_symbol = self._extract_symbol(symbol)
                symbol_list.append(pure_symbol.encode('utf-8'))

            ret = self.md_api.SubscribeMarketData(symbol_list)
            if ret == 0:
                logger.info(f"已订阅行情: {symbol_list}")
            else:
                logger.error(f"订阅行情失败: 错误码 {ret}")

        except Exception as e:
            logger.error(f"订阅行情异常: {e}")

    def _extract_symbol(self, full_symbol: str) -> str:
        """
        从完整合约名提取纯合约代码
        如: 'SHFE.rb2405' -> 'rb2405'
        """
        if '.' in full_symbol:
            return full_symbol.split('.', 1)[1]
        return full_symbol

    def _register_callbacks(self):
        """注册回调函数"""
        if not CTP_AVAILABLE:
            # 在模拟模式下，不需要注册真实的回调函数
            return

        if self.td_api:
            # 交易相关回调
            self.td_api.OnRspAuthenticate = self._on_rsp_authenticate
            self.td_api.OnRspUserLogin = self._on_rsp_user_login
            self.td_api.OnRspOrderInsert = self._on_rsp_order_insert
            self.td_api.OnRspOrderAction = self._on_rsp_order_action
            self.td_api.OnRtnOrder = self._on_rtn_order
            self.td_api.OnRtnTrade = self._on_rtn_trade
            self.td_api.OnRspQryTradingAccount = self._on_rsp_qry_trading_account
            self.td_api.OnRspQryInvestorPosition = self._on_rsp_qry_investor_position
            self.td_api.OnRspQryOrder = self._on_rsp_qry_order
            self.td_api.OnFrontConnected = self._on_front_connected
            self.td_api.OnFrontDisconnected = self._on_front_disconnected
            self.td_api.OnRspError = self._on_rsp_error

        if self.md_api:
            # 行情相关回调
            self.md_api.OnFrontConnected = self._on_md_front_connected
            self.md_api.OnFrontDisconnected = self._on_md_front_disconnected
            self.md_api.OnRspUserLogin = self._on_md_rsp_user_login
            self.md_api.OnRtnDepthMarketData = self._on_rtn_depth_market_data
            self.md_api.OnRspSubMarketData = self._on_rsp_sub_market_data
            self.md_api.OnRspError = self._on_rsp_error

    def _on_front_connected(self):
        """交易前置连接成功"""
        logger.info("CTPPlus 交易前置连接成功")

        # 发起登录
        req = ApiStruct.ReqUserLoginField()
        req.BrokerID = self.broker_id.encode('utf-8')
        req.UserID = self.username.encode('utf-8')
        req.Password = self.password.encode('utf-8')

        with self.req_lock:
            self.request_id += 1
            self.td_api.ReqUserLogin(req, self.request_id)

    def _on_front_disconnected(self, nReason: int):
        """交易前置断开"""
        logger.warning(f"CTPPlus 交易前置断开，原因: {nReason}")
        self.status = TradingStatus.ERROR

    def _on_md_front_connected(self):
        """行情前置连接成功"""
        logger.info("CTPPlus 行情前置连接成功")

        # 发起登录
        req = ApiStruct.ReqUserLoginField()
        req.BrokerID = self.broker_id.encode('utf-8')
        req.UserID = self.username.encode('utf-8')
        req.Password = self.password.encode('utf-8')

        with self.req_lock:
            self.request_id += 1
            self.md_api.ReqUserLogin(req, self.request_id)

    def _on_md_front_disconnected(self, nReason: int):
        """行情前置断开"""
        logger.warning(f"CTPPlus 行情前置断开，原因: {nReason}")

    def _on_rsp_user_login(self, pRspUserLogin: ApiStruct.RspUserLoginField, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """交易用户登录应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"交易登录失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
            self.status = TradingStatus.ERROR
            return

        logger.info(f"交易登录成功 - FrontID: {getattr(pRspUserLogin, 'FrontID', 0)}, SessionID: {getattr(pRspUserLogin, 'SessionID', 0)}")
        self.front_id = getattr(pRspUserLogin, 'FrontID', 0)
        self.session_id = getattr(pRspUserLogin, 'SessionID', 0)

        # 如果行情也已登录，则整个连接过程完成
        if self.status == TradingStatus.CONNECTING:
            self.status = TradingStatus.CONNECTED

    def _on_md_rsp_user_login(self, pRspUserLogin: ApiStruct.RspUserLoginField, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """行情用户登录应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"行情登录失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
            self.status = TradingStatus.ERROR
            return

        logger.info("行情登录成功")

        # 如果交易也已登录，则整个连接过程完成
        if self.status == TradingStatus.CONNECTING:
            self.status = TradingStatus.CONNECTED

    def _on_rsp_order_insert(self, pInputOrder: ApiStruct.InputOrderField, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """报单录入应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"订单提交失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
            # 在这里可以更新订单状态为失败
            sys_order_id = pInputOrder.OrderLocalID.decode() if hasattr(pInputOrder.OrderLocalID, 'decode') else str(pInputOrder.OrderLocalID)
            local_order_id = self.sys_order_id_to_local.get(sys_order_id, "")
            if local_order_id in self.orders:
                order = self.orders[local_order_id]
                order.status = OrderStatusEnum.REJECTED
                order.error_msg = pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)
                self.on_order(order)
        else:
            logger.info(f"订单提交成功: {pInputOrder.OrderLocalID}")

    def _on_rtn_order(self, pOrder: ApiStruct.OrderField):
        """报单回报"""
        try:
            # 获取本地订单ID
            sys_order_id = pOrder.OrderLocalID.decode() if hasattr(pOrder.OrderLocalID, 'decode') else str(pOrder.OrderLocalID)
            local_order_id = self.sys_order_id_to_local.get(sys_order_id, "")

            if local_order_id not in self.orders:
                logger.warning(f"收到未知订单回报: {sys_order_id}")
                return

            order = self.orders[local_order_id]

            # 更新订单状态
            if pOrder.OrderStatus == ApiStruct.OrderStatus.OST_AllTraded:
                order.status = OrderStatusEnum.FILLED
                order.traded_volume = getattr(pOrder, 'VolumeTraded', 0)
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_PartTradedQueueing:
                order.status = OrderStatusEnum.PARTIAL_FILLED
                order.traded_volume = getattr(pOrder, 'VolumeTraded', 0)
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_Canceled:
                order.status = OrderStatusEnum.CANCELLED
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_NoTradeQueueing:
                order.status = OrderStatusEnum.SUBMITTED
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_PartTradedNotQueueing:
                order.status = OrderStatusEnum.PARTIAL_CANCELLED
                order.traded_volume = getattr(pOrder, 'VolumeTraded', 0)
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_NoTradeNotQueueing:
                order.status = OrderStatusEnum.REJECTED
            elif pOrder.OrderStatus == ApiStruct.OrderStatus.OST_Unknown:
                order.status = OrderStatusEnum.UNKNOWN

            order.update_time = datetime.now()

            self.on_order(order)
            logger.info(f"订单状态更新: {order.order_id} -> {order.status.value}")

        except Exception as e:
            logger.error(f"处理订单回报失败: {e}")

    def _on_rtn_trade(self, pTrade: ApiStruct.TradeField):
        """成交通知"""
        try:
            # 获取对应的本地订单ID
            sys_order_id = pTrade.OrderLocalID.decode() if hasattr(pTrade.OrderLocalID, 'decode') else str(pTrade.OrderLocalID)
            local_order_id = self.sys_order_id_to_local.get(sys_order_id, "")

            # 创建成交对象
            direction = Direction.LONG if pTrade.Direction == ApiStruct.Direction.D_Buy else Direction.SHORT
            instrument_id = pTrade.InstrumentID.decode() if hasattr(pTrade.InstrumentID, 'decode') else str(pTrade.InstrumentID)

            trade = Trade(
                trade_id=pTrade.TradeID.decode() if hasattr(pTrade.TradeID, 'decode') else str(pTrade.TradeID),
                order_id=local_order_id,
                symbol=f"{self._get_exchange_by_product(instrument_id)}.{instrument_id}",
                direction=direction,
                price=pTrade.Price,
                volume=pTrade.Volume,
                commission=0.0,  # 佣金需要另行计算
                trade_time=datetime.now()
            )

            # 计算佣金（根据品种不同规则不同）
            # 这里简化处理
            multiplier = self._get_contract_multiplier(instrument_id)
            trade.commission = pTrade.Price * pTrade.Volume * multiplier * 0.0003  # 简化的手续费计算

            self.on_trade(trade)
            logger.info(f"新成交: {trade.symbol} {trade.direction.value} {trade.volume}@{trade.price}")

        except Exception as e:
            logger.error(f"处理成交回报失败: {e}")

    def _on_rsp_qry_trading_account(self, pRspInvestorAccount: ApiStruct.AccountField, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """资金账户查询应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"查询资金账户失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
            return

        if pRspInvestorAccount:
            self.account = AccountInfo(
                account_id=self.username,
                balance=getattr(pRspInvestorAccount, 'Balance', 0),
                available=getattr(pRspInvestorAccount, 'Available', 0),
                margin=getattr(pRspInvestorAccount, 'CurrMargin', 0),
                commission=getattr(pRspInvestorAccount, 'Commission', 0),
                position_pnl=getattr(pRspInvestorAccount, 'PositionProfit', 0),
                total_pnl=getattr(pRspInvestorAccount, 'CloseProfit', 0) + getattr(pRspInvestorAccount, 'PositionProfit', 0)
            )

            self.on_account(self.account)
            logger.info(f"账户信息更新 - 余额: {self.account.balance}, 可用: {self.account.available}")

    def _on_rsp_qry_investor_position(self, pInvestorPosition: ApiStruct.InvestorPositionField, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """投资者持仓查询应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"查询持仓失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
            return

        if pInvestorPosition:
            instrument_id = pInvestorPosition.InstrumentID.decode() if hasattr(pInvestorPosition.InstrumentID, 'decode') else str(pInvestorPosition.InstrumentID)
            symbol = f"{self._get_exchange_by_product(instrument_id)}.{instrument_id}"

            # 根据多空方向创建持仓对象
            pos_direction = pInvestorPosition.PosiDirection
            if pos_direction == ApiStruct.PosiDirection.PD_Long:
                position = Position(
                    symbol=symbol,
                    direction=Direction.LONG,
                    volume=pInvestorPosition.Position,
                    price=pInvestorPosition.OpenCost / pInvestorPosition.Position if pInvestorPosition.Position > 0 else 0,
                    pnl=pInvestorPosition.PositionProfit
                )
            elif pos_direction == ApiStruct.PosiDirection.PD_Short:
                position = Position(
                    symbol=symbol,
                    direction=Direction.SHORT,
                    volume=pInvestorPosition.Position,
                    price=pInvestorPosition.OpenCost / pInvestorPosition.Position if pInvestorPosition.Position > 0 else 0,
                    pnl=pInvestorPosition.PositionProfit
                )
            else:  # 净持仓
                position = Position(
                    symbol=symbol,
                    direction=Direction.NET,
                    volume=pInvestorPosition.Position,
                    price=pInvestorPosition.OpenCost / abs(pInvestorPosition.Position) if pInvestorPosition.Position != 0 else 0,
                    pnl=pInvestorPosition.PositionProfit
                )

            self.positions[symbol] = position
            self.on_position(position)
            logger.info(f"持仓更新: {symbol} {position.direction.value} {position.volume}")

    def _on_rtn_depth_market_data(self, pDepthMarketData: ApiStruct.DepthMarketDataField):
        """深度行情推送"""
        try:
            instrument_id = pDepthMarketData.InstrumentID.decode() if hasattr(pDepthMarketData.InstrumentID, 'decode') else str(pDepthMarketData.InstrumentID)
            # 构造合约名称（加上交易所前缀）
            exchange = self._get_exchange_by_product(instrument_id)
            symbol = f"{exchange}.{instrument_id}"

            # 创建市场数据对象
            tick = MarketData(
                symbol=symbol,
                last_price=pDepthMarketData.LastPrice,
                bid_price_1=getattr(pDepthMarketData, 'BidPrice1', 0),
                ask_price_1=getattr(pDepthMarketData, 'AskPrice1', 0),
                bid_volume_1=getattr(pDepthMarketData, 'BidVolume1', 0),
                ask_volume_1=getattr(pDepthMarketData, 'AskVolume1', 0),
                volume=getattr(pDepthMarketData, 'Volume', 0),
                turnover=getattr(pDepthMarketData, 'Turnover', 0),
                timestamp=datetime.now()
            )

            # 缓存最新行情
            self.tick_cache[symbol] = tick

            # 触发行情回调
            self.on_tick(tick)

            logger.debug(f"行情更新: {tick.symbol} {tick.last_price}")

        except Exception as e:
            logger.error(f"处理行情数据失败: {e}")

    def _get_exchange_by_product(self, instrument_id: str) -> str:
        """根据合约代码推断交易所"""
        # 根据合约代码首字母判断交易所
        if instrument_id[:2].upper() in ['IF', 'IC', 'IH', 'T', 'TF', 'TS']:
            return 'CFFEX'
        elif instrument_id[:1].upper() in ['M', 'A', 'Y', 'C', 'P', 'J', 'JM', 'I', 'L', 'V', 'PP', 'EG', 'EB', 'PG']:
            return 'DCE'
        elif instrument_id[:2].upper() in ['CU', 'AL', 'ZN', 'PB', 'NI', 'SN', 'AU', 'AG', 'RB', 'WR', 'HC', 'FU', 'SC', 'BU', 'RU', 'NR', 'SS', 'SP']:
            return 'SHFE'
        elif instrument_id[:2].upper() in ['TA', 'MA', 'EG', 'PF', 'SA', 'UR', 'RM', 'SR', 'CF', 'CY', 'AP', 'CJ', 'FX', 'RI', 'LR', 'SF', 'SM', 'SP', 'UR']:
            return 'CZCE'
        elif instrument_id[:2].upper() in ['SC', 'NR']:
            return 'INE'
        else:
            # 默认上海期货交易所
            return 'SHFE'

    def _get_contract_multiplier(self, instrument_id: str) -> float:
        """获取合约乘数"""
        # 根据合约代码返回对应乘数
        multipliers = {
            'IF': 300,   # 沪深300指数期货
            'IC': 200,   # 中证500指数期货
            'IH': 300,   # 上证50指数期货
            'T': 10000,  # 十年期国债期货
            'TF': 10000, # 五年期国债期货
            'TS': 20000, # 两年期国债期货
            'CU': 5,     # 铜
            'AL': 5,     # 铝
            'ZN': 5,     # 锌
            'PB': 5,     # 铅
            'NI': 1,     # 镍
            'SN': 1,     # 锡
            'AU': 1000,  # 黄金
            'AG': 15,    # 白银
            'RB': 10,    # 螺纹钢
            'HC': 10,    # 热卷
            'SS': 5,     # 不锈钢
            'SC': 1000,  # 原油
            'NR': 10,    # 20号胶
            'RU': 10,    # 天然橡胶
            'M': 10,     # 豆粕
            'Y': 10,     # 豆油
            'A': 10,     # 黄大豆1号
            'C': 10,     # 玉米
            'P': 10,     # 棕榈油
            'J': 100,    # 焦炭
            'JM': 60,    # 焦煤
            'I': 100,    # 铁矿石
            'L': 5,      # 线性低密度聚乙烯
            'V': 5,      # 聚氯乙烯
            'PP': 5,     # 聚丙烯
            'EB': 5,     # 苯乙烯
            'EG': 10,    # 乙二醇
            'TA': 5,     # 精对苯二甲酸
            'MA': 10,    # 甲醇
            'SA': 20,    # 纯碱
            'FG': 20,    # 玻璃
            'CF': 5,     # 郑棉
            'SR': 10,    # 白糖
            'OI': 10,    # 菜籽油
            'RI': 20,    # 早籼稻
            'WH': 20,    # 强麦
            'PM': 50,    # 普麦
            'RS': 10,    # 油菜籽
            'JR': 20,    # 粳稻
            'LR': 20,    # 晚籼稻
            'SF': 5,     # 硅铁
            'SM': 5,     # 锰硅
            'CY': 5,     # 棉纱
            'AP': 10,    # 苹果
            'UR': 20,    # 尿素
            'RR': 20,    # 粳米
        }

        # 尝试匹配合约前缀
        for prefix, multiplier in multipliers.items():
            if instrument_id.upper().startswith(prefix):
                return multiplier

        # 默认为10
        return 10.0

    def _on_rsp_error(self, pRspInfo: ApiStruct.RspInfoField, nRequestID: int, bIsLast: bool):
        """错误应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            error_msg = pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)
            logger.error(f"收到错误应答: [{pRspInfo.ErrorID}] {error_msg}")

    def _on_rsp_authenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast):
        """客户端认证应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"客户端认证失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
        else:
            logger.info("客户端认证成功")

    def _on_rsp_sub_market_data(self, pSpecificInstrument, pRspInfo, nRequestID, bIsLast):
        """订阅行情应答"""
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"订阅行情失败: {pRspInfo.ErrorMsg.decode('gbk') if hasattr(pRspInfo.ErrorMsg, 'decode') else str(pRspInfo.ErrorMsg)}")
        else:
            logger.info("订阅行情成功")


def create_ctp_plus_gateway() -> CtpPlusGateway:
    """创建 CtpPlus 网关实例"""
    return CtpPlusGateway()