"""
实盘交易模块 - 交易执行、持仓管理
统一导出所有交易相关类
"""

from .errors import TradingError, GatewayError
from .types import TradingStatus, AccountInfo, MarketData
from .gateway import GatewayBase, create_gateway, GATEWAY_REGISTRY
from .vnpy_gateway import VnpyGateway, create_vnpy_gateway
from .engine import TradingEngine
from .risk import RiskConfig, RiskCheckResult, RiskManager
from .order_manager import (
    OrderManager, PreOrder, PreOrderType, PreOrderStatus
)

__all__ = [
    "TradingError", "GatewayError",
    "TradingStatus", "AccountInfo", "MarketData",
    "GatewayBase", "create_gateway", "GATEWAY_REGISTRY",
    "VnpyGateway", "create_vnpy_gateway",
    "TradingEngine",
    "RiskConfig", "RiskCheckResult", "RiskManager",
    "OrderManager", "PreOrder", "PreOrderType", "PreOrderStatus",
]
