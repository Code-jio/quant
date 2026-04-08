"""
交易模块数据类型定义
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional, Callable


class TradingStatus(Enum):
    """交易状态"""
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    TRADING = "trading"
    ERROR = "error"


@dataclass
class AccountInfo:
    """账户信息"""
    account_id: str = ""
    balance: float = 0.0
    available: float = 0.0
    margin: float = 0.0
    commission: float = 0.0
    position_pnl: float = 0.0
    total_pnl: float = 0.0
    error_msg: str = ""


@dataclass
class MarketData:
    """行情数据"""
    symbol: str
    last_price: float
    bid_price_1: float
    ask_price_1: float
    bid_volume_1: int
    ask_volume_1: int
    volume: int
    turnover: float
    timestamp: datetime = field(default_factory=datetime.now)
    error_msg: str = ""
