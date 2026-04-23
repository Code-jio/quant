"""
实盘交易模块 - 交易执行、持仓管理
统一导出所有交易相关类
"""

from .errors import TradingError, GatewayError
from .types import TradingStatus, AccountInfo, MarketData
from .gateway import GatewayBase, SimulatedGateway, create_gateway, GATEWAY_REGISTRY
from .wt_gateway import WtGateway, create_wt_gateway
from .engine import TradingEngine
from .order_manager import (
    OrderManager, PreOrder, PreOrderType, PreOrderStatus
)

__all__ = [
    "TradingError", "GatewayError",
    "TradingStatus", "AccountInfo", "MarketData",
    "GatewayBase", "SimulatedGateway", "create_gateway", "GATEWAY_REGISTRY",
    "WtGateway", "create_wt_gateway",
    "TradingEngine",
    "OrderManager", "PreOrder", "PreOrderType", "PreOrderStatus",
]
