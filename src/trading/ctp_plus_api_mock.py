"""
CtpPlus 兼容层实现
用于处理无法直接安装CtpPlus的情况
提供与CtpPlus相似的API接口
"""

import logging
from abc import ABC, abstractmethod
import threading
import time
from datetime import datetime
from typing import Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

# 模拟CTP结构体定义
class ApiStruct:
    # 订单状态
    class OrderStatus:
        OST_AllTraded = b'a'  # 全部成交
        OST_PartTradedQueueing = b'b'  # 部分成交正在队列中
        OST_PartTradedNotQueueing = b'c'  # 部分成交不在队列中
        OST_NoTradeQueueing = b'd'  # 未成交正在队列中
        OST_NoTradeNotQueueing = b'e'  # 未成交不在队列中
        OST_Canceled = b'f'  # 撤单
        OST_Unknown = b'g'  # 未知

    # 开平标志
    class OffsetFlag:
        OF_Open = b'0'  # 开仓
        OF_Close = b'1'  # 平仓
        OF_CloseToday = b'3'  # 平今
        OF_CloseYesterday = b'4'  # 平昨

    # 买卖方向
    class Direction:
        D_Buy = b'0'  # 买
        D_Sell = b'1'  # 卖

    # 报单价格条件
    class OrderPriceType:
        OPT_AnyPrice = b'1'  # 市价
        OPT_LimitPrice = b'2'  # 限价
        OPT_BestPrice = b'3'  # 最优价
        OPT_LastPrice = b'4'  # 最新价

    # 有效期类型
    class TimeCondition:
        TC_IOC = b'1'  # 立即完成否则撤销
        TC_GFS = b'2'  # 本节有效
        TC_GFD = b'3'  # 当日有效
        TC_GTD = b'4'  # 指定日期前有效
        TC_GTC = b'5'  # 撤销前有效
        TC_GFA = b'6'  # 集合竞价有效

    # 成交量类型
    class VolumeCondition:
        VC_AV = b'1'  # 任何数量
        VC_MV = b'2'  # 最小数量
        VC_CV = b'3'  # 全部数量

    # 触发条件
    class ContingentCondition:
        CC_Immediately = b'1'  # 立即
        CC_Touch = b'2'  # 止损
        CC_TouchProfit = b'3'  # 止赢
        CC_ParkedOrder = b'4'  # 预埋单
        CC_LastPriceGreaterThanStopPrice = b'5'  # 最新价大于条件价
        CC_LastPriceGreaterEqualStopPrice = b'6'  # 最新价大于等于条件价
        CC_LastPriceLesserThanStopPrice = b'7'  # 最新价小于条件价
        CC_LastPriceLesserEqualStopPrice = b'8'  # 最新价小于等于条件价
        CC_AskPriceGreaterThanStopPrice = b'9'  # 卖一价大于条件价
        CC_AskPriceGreaterEqualStopPrice = b'A'  # 卖一价大于等于条件价
        CC_AskPriceLesserThanStopPrice = b'B'  # 卖一价小于条件价
        CC_AskPriceLesserEqualStopPrice = b'C'  # 卖一价小于等于条件价
        CC_BidPriceGreaterThanStopPrice = b'D'  # 买一价大于条件价
        CC_BidPriceGreaterEqualStopPrice = b'E'  # 买一价大于等于条件价
        CC_BidPriceLesserThanStopPrice = b'F'  # 买一价小于条件价
        CC_BidPriceLesserEqualStopPrice = b'H'  # 买一价小于等于条件价

    # 操作标志
    class ActionFlag:
        AF_Delete = b'0'  # 删除
        AF_Modify = b'3'  # 修改

    # 持仓多空方向
    class PosiDirection:
        PD_Net = b'1'  # 净
        PD_Long = b'2'  # 多
        PD_Short = b'3'  # 空

    # 字段结构类
    class Field:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class InputOrderField(Field):
        """输入报单"""
        pass

    class InputOrderActionField(Field):
        """输入报单操作"""
        pass

    class QryTradingAccountField(Field):
        """查询资金账户"""
        pass

    class QryInvestorPositionField(Field):
        """查询投资者持仓"""
        pass

    class QryOrderField(Field):
        """查询报单"""
        pass

    class RspUserLoginField(Field):
        """响应用户登录"""
        pass

    class RspInfoField(Field):
        """响应信息"""
        pass

    class AccountField(Field):
        """资金账户"""
        pass

    class InvestorPositionField(Field):
        """投资者持仓"""
        pass

    class OrderField(Field):
        """报单"""
        pass

    class TradeField(Field):
        """成交"""
        pass

    class DepthMarketDataField(Field):
        """深度行情"""
        pass

    class ReqUserLoginField(Field):
        """请求用户登录"""
        pass

# 模拟TdApi和MdApi
class TdApi:
    """交易API模拟实现"""
    def __init__(self):
        self.OnRspAuthenticate = None
        self.OnRspUserLogin = None
        self.OnRspOrderInsert = None
        self.OnRspOrderAction = None
        self.OnRtnOrder = None
        self.OnRtnTrade = None
        self.OnRspQryTradingAccount = None
        self.OnRspQryInvestorPosition = None
        self.OnRspQryOrder = None
        self.OnFrontConnected = None
        self.OnFrontDisconnected = None
        self.OnRspError = None

    def Create(self):
        logger.info("TdApi created")
        return 0

    def Release(self):
        logger.info("TdApi released")
        return 0

    def RegisterFront(self, front_addr: str):
        logger.info(f"Registered front: {front_addr}")
        return 0

    def Init(self):
        logger.info("TdApi initialized")
        # 模拟连接成功
        if self.OnFrontConnected:
            self.OnFrontConnected()
        return 0

    def ReqUserLogin(self, pReqUserLoginField, nRequestID):
        logger.info(f"Sending login request: {pReqUserLoginField}")
        # 模拟登录成功
        if self.OnRspUserLogin:
            rsp = ApiStruct.RspUserLoginField(FrontID=1, SessionID=1)
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Login Success")
            self.OnRspUserLogin(rsp, info, nRequestID, True)
        return 0

    def ReqOrderInsert(self, pInputOrder, nRequestID):
        logger.info(f"Sending order: {pInputOrder}")
        if self.OnRspOrderInsert:
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Order Insert Success")
            self.OnRspOrderInsert(pInputOrder, info, nRequestID, True)
        return 0

    def ReqOrderAction(self, pInputOrderAction, nRequestID):
        logger.info(f"Canceling order: {pInputOrderAction}")
        if self.OnRspOrderAction:
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Order Action Success")
            self.OnRspOrderAction(pInputOrderAction, info, nRequestID, True)
        return 0

    def ReqQryTradingAccount(self, pQryTradingAccount, nRequestID):
        logger.info(f"Querying account: {pQryTradingAccount}")
        if self.OnRspQryTradingAccount:
            # 模拟返回账户信息
            account = ApiStruct.AccountField(
                Balance=1000000.0,
                Available=900000.0,
                CurrMargin=100000.0,
                Commission=0.0,
                PositionProfit=0.0,
                CloseProfit=0.0
            )
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Query Success")
            self.OnRspQryTradingAccount(account, info, nRequestID, True)
        return 0

    def ReqQryInvestorPosition(self, pQryInvestorPosition, nRequestID):
        logger.info(f"Querying position: {pQryInvestorPosition}")
        if self.OnRspQryInvestorPosition:
            # 模拟返回持仓信息
            position = ApiStruct.InvestorPositionField(
                InstrumentID=getattr(pQryInvestorPosition, 'InstrumentID', b''),
                PosiDirection=ApiStruct.PosiDirection.PD_Net,
                Position=0,
                OpenCost=0.0,
                PositionProfit=0.0
            )
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Query Success")
            self.OnRspQryInvestorPosition(position, info, nRequestID, True)
        return 0

    def ReqQryOrder(self, pQryOrder, nRequestID):
        logger.info(f"Querying orders: {pQryOrder}")
        if self.OnRspQryOrder:
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Query Success")
            self.OnRspQryOrder(None, info, nRequestID, True)
        return 0

class MdApi:
    """行情API模拟实现"""
    def __init__(self):
        self.OnFrontConnected = None
        self.OnFrontDisconnected = None
        self.OnRspUserLogin = None
        self.OnRtnDepthMarketData = None
        self.OnRspSubMarketData = None
        self.OnRspError = None

    def Create(self):
        logger.info("MdApi created")
        return 0

    def Release(self):
        logger.info("MdApi released")
        return 0

    def RegisterFront(self, front_addr: str):
        logger.info(f"MdApi registered front: {front_addr}")
        return 0

    def Init(self):
        logger.info("MdApi initialized")
        # 模拟连接成功
        if self.OnFrontConnected:
            self.OnFrontConnected()
        return 0

    def SubscribeMarketData(self, instruments: list):
        logger.info(f"Subscribing to: {instruments}")
        if self.OnRspSubMarketData:
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Subscribe Success")
            self.OnRspSubMarketData(info, 0, True)
        return 0

    def ReqUserLogin(self, pReqUserLoginField, nRequestID):
        logger.info(f"MdApi login: {pReqUserLoginField}")
        if self.OnRspUserLogin:
            info = ApiStruct.RspInfoField(ErrorID=0, ErrorMsg=b"Login Success")
            self.OnRspUserLogin(ApiStruct.RspUserLoginField(), info, nRequestID, True)
        return 0