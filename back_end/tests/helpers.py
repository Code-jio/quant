"""Shared test helpers for trading gateway tests."""

from src.strategy import Direction, OrderStatus, OrderType, Position
from src.trading import GatewayBase
from src.trading.types import AccountInfo, TradingStatus


class RecordingGateway(GatewayBase):
    """A GatewayBase stub that records sent signals and cancelled orders."""

    def __init__(self, name: str = "VNPY_CTP"):
        super().__init__(name)
        self.sent_signals = []
        self.cancelled_order_ids = []

    def connect(self, config):
        self.status = TradingStatus.CONNECTED
        self.account = AccountInfo(
            account_id="TEST001", balance=100000.0, available=100000.0
        )
        return True

    def disconnect(self):
        self.status = TradingStatus.STOPPED

    def send_order(self, signal):
        self.sent_signals.append(signal)
        return f"ORDER_{len(self.sent_signals)}"

    def cancel_order(self, order_id):
        self.cancelled_order_ids.append(order_id)
        if order_id in self.orders:
            self.orders[order_id].status = OrderStatus.CANCELLED
        return True

    def query_account(self):
        return self.account

    def query_positions(self):
        return list(self.positions.values())

    def query_orders(self):
        return list(self.orders.values())
