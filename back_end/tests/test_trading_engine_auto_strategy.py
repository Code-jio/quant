from datetime import datetime, timedelta

import pandas as pd

from src.strategy import Direction, Order, OrderStatus, OrderType, StrategyBase
from src.trading import GatewayBase, TradingEngine
from src.trading.types import AccountInfo, MarketData, TradingStatus


class RecordingGateway(GatewayBase):
    def __init__(self):
        super().__init__("TEST")
        self.sent_signals = []

    def connect(self, config):
        self.status = TradingStatus.CONNECTED
        self.account = AccountInfo(account_id="TEST001", balance=100000.0, available=100000.0)
        return True

    def disconnect(self):
        self.status = TradingStatus.STOPPED

    def send_order(self, signal):
        self.sent_signals.append(signal)
        return f"ORDER_{len(self.sent_signals)}"

    def cancel_order(self, order_id):
        return True

    def query_account(self):
        return self.account

    def query_positions(self):
        return list(self.positions.values())

    def query_orders(self):
        return list(self.orders.values())


class LiveDataSignalStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self._initialized = True

    def on_bar(self, bar: pd.Series):
        data = self.get_data(self.symbol)
        if data is None or len(data) < 2 or self.signals:
            return
        self.buy(self.symbol, float(bar["close"]), 1, OrderType.LIMIT)


class LiveBiasProbeStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")
        self.observed_lengths = []
        self.current_seen = []

    def on_bar(self, bar: pd.Series):
        data = self.get_data(self.symbol)
        self.observed_lengths.append(0 if data is None else len(data))
        self.current_seen.append(False if data is None else self.current_date in data.index)


def make_tick(symbol: str, price: float, timestamp: datetime) -> MarketData:
    return MarketData(
        symbol=symbol,
        last_price=price,
        bid_price_1=price - 1,
        ask_price_1=price + 1,
        bid_volume_1=10,
        ask_volume_1=10,
        volume=100,
        turnover=price * 100,
        timestamp=timestamp,
    )


def test_live_ticks_update_strategy_data_and_dispatch_new_signals_once():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveDataSignalStrategy("live_test", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    start = datetime(2026, 4, 29, 9, 30)
    engine.on_tick(make_tick("rb2505", 3800.0, start))
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=1)))
    engine.on_tick(make_tick("rb2505", 3820.0, start + timedelta(seconds=2)))

    assert list(strategy.data) == ["rb2505"]
    assert len(strategy.data["rb2505"]) == 3
    assert len(strategy.signals) == 1
    assert len(gateway.sent_signals) == 1
    assert gateway.sent_signals[0].symbol == "rb2505"
    assert gateway.sent_signals[0].price == 3820.0
    assert gateway.orders["ORDER_1"].status == OrderStatus.SUBMITTING


def test_live_on_bar_sees_only_prior_ticks_in_strategy_data():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveBiasProbeStrategy("live_bias", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    start = datetime(2026, 4, 29, 9, 30)
    engine.on_tick(make_tick("rb2505", 3800.0, start))
    engine.on_tick(make_tick("rb2505", 3810.0, start + timedelta(seconds=1)))

    assert strategy.observed_lengths == [0, 1]
    assert strategy.current_seen == [False, False]


def test_broker_order_callback_updates_order_manager_books():
    gateway = RecordingGateway()
    engine = TradingEngine(gateway)
    strategy = LiveDataSignalStrategy("live_test", {"symbol": "rb2505"})
    engine.set_strategy(strategy)

    assert engine.start({"initial_capital": 100000.0}) is True

    order = Order(
        order_id="ORDER_1",
        symbol="rb2505",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        price=3800,
        volume=1,
        status=OrderStatus.FILLED,
    )
    gateway.on_order(order)

    assert "ORDER_1" not in engine.order_manager.active_orders
    assert engine.order_manager.completed_orders["ORDER_1"].status == OrderStatus.FILLED
