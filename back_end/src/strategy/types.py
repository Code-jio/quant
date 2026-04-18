"""
策略模块数据类型定义
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(Enum):
    LONG = "long"
    SHORT = "short"
    NET = "net"


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class OffsetFlag(Enum):
    OPEN = "open"
    CLOSE = "close"
    CLOSE_TODAY = "close_today"
    CLOSE_YESTERDAY = "close_yesterday"


class OrderStatus(Enum):
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PARTFILLED = "partfilled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Signal:
    """交易信号"""
    symbol: str
    datetime: datetime
    direction: Direction
    price: float
    volume: int
    order_type: OrderType = OrderType.MARKET
    offset: OffsetFlag = OffsetFlag.OPEN
    stop_price: Optional[float] = None
    comment: str = ""

    def validate(self) -> bool:
        """验证信号有效性"""
        if not self.symbol:
            return False
        if self.order_type != OrderType.MARKET and self.price <= 0:
            return False
        if self.volume <= 0:
            return False
        return True
@dataclass
class Order:
    """订单"""
    order_id: str
    symbol: str
    direction: Direction
    order_type: OrderType
    price: float
    volume: int
    traded_volume: int = 0
    status: OrderStatus = OrderStatus.SUBMITTING
    offset: OffsetFlag = OffsetFlag.OPEN
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)
    error_msg: str = ""

    def is_active(self) -> bool:
        return self.status in [OrderStatus.SUBMITTING, OrderStatus.SUBMITTED, OrderStatus.PARTFILLED]

    def validate(self) -> bool:
        """验证订单有效性"""
        if not self.symbol:
            return False
        if self.price <= 0:
            return False
        if self.volume <= 0:
            return False
        return True


@dataclass
class Trade:
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    direction: Direction
    price: float
    volume: int
    commission: float = 0.0
    pnl: float = 0.0
    trade_time: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """持仓"""
    symbol: str
    direction: Direction
    volume: int
    frozen: int = 0
    price: float = 0.0
    cost: float = 0.0
    pnl: float = 0.0

    def __post_init__(self):
        if self.direction == Direction.NET:
            self.volume = abs(self.volume)

    @property
    def is_long(self) -> bool:
        return self.direction == Direction.LONG or (self.direction == Direction.NET and self.volume > 0)

    @property
    def is_short(self) -> bool:
        return self.direction == Direction.SHORT or (self.direction == Direction.NET and self.volume < 0)

    @property
    def is_empty(self) -> bool:
        return self.volume == 0
