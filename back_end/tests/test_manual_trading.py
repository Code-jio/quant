from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.strategy import Direction, OffsetFlag, Order, OrderStatus, OrderType, Position
from src.trading import GatewayBase
from src.trading.types import AccountInfo, TradingStatus


class RecordingGateway(GatewayBase):
    def __init__(self):
        super().__init__("VNPY_CTP")
        self.sent_signals = []
        self.cancelled_order_ids = []

    def connect(self, config):
        self.status = TradingStatus.CONNECTED
        self.account = AccountInfo(account_id="TEST001", balance=100000.0, available=100000.0)
        return True

    def disconnect(self):
        self.status = TradingStatus.STOPPED

    def send_order(self, signal):
        self.sent_signals.append(signal)
        return f"TEST_ORDER_{len(self.sent_signals)}"

    def cancel_order(self, order_id):
        self.cancelled_order_ids.append(order_id)
        return True

    def query_account(self):
        return self.account

    def query_positions(self):
        return list(self.positions.values())

    def query_orders(self):
        return list(self.orders.values())


def install_gateway(monkeypatch):
    import src.trading

    gateway = RecordingGateway()
    monkeypatch.setattr(src.trading, "create_gateway", lambda gateway_type="vnpy": gateway)
    return gateway


def login(client):
    response = client.post(
        "/auth/login",
        json={
            "username": "test-account",
            "password": "test-password",
            "broker_id": "2071",
            "gateway_type": "vnpy",
        },
    )
    assert response.status_code == 200


def allow_market_orders(client):
    response = client.put(
        "/risk/config",
        json={"risk": {"allow_market_orders": True, "max_market_data_age_seconds": 0}},
    )
    assert response.status_code == 200


def teardown_function():
    trading_state.clear_main()


def test_manual_order_rejects_invalid_payloads(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        base = {
            "symbol": "rb2505",
            "direction": "long",
            "offset": "open",
            "price": 0,
            "volume": 1,
            "order_type": "market",
        }

        invalid_cases = [
            ({**base, "symbol": "  "}, "合约代码不能为空"),
            ({**base, "direction": "buy"}, "买卖方向无效"),
            ({**base, "offset": "bad_offset"}, "开平方向无效"),
            ({**base, "order_type": "stop"}, "订单类型无效"),
            ({**base, "volume": 0}, "委托数量必须大于 0"),
            ({**base, "order_type": "limit", "price": 0}, "限价单价格必须大于 0"),
        ]

        for payload, message in invalid_cases:
            response = client.post("/orders", json=payload)
            assert response.status_code == 400
            assert message in response.json()["detail"]

    assert gateway.sent_signals == []


def test_manual_market_order_forces_zero_price_and_strips_symbol(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        response = client.post(
            "/orders",
            json={
                "symbol": " rb2505 ",
                "direction": "long",
                "offset": "open",
                "price": 3888.0,
                "volume": 2,
                "order_type": "market",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "rb2505"
    assert body["price"] == 0
    assert body["order_type"] == "market"

    signal = gateway.sent_signals[0]
    assert signal.symbol == "rb2505"
    assert signal.price == 0
    assert signal.volume == 2
    assert signal.order_type == OrderType.MARKET
    assert signal.offset == OffsetFlag.OPEN


def test_quick_close_short_position_uses_buy_direction_and_requested_offset(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        gateway.positions["rb2505.short"] = Position(symbol="rb2505", direction=Direction.SHORT, volume=2)

        response = client.post(
            "/positions/rb2505/close",
            json={
                "direction": "short",
                "volume": 1,
                "price": 3880.5,
                "offset": "close_yesterday",
                "order_type": "limit",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["direction"] == "long"
    assert body["volume"] == 1
    assert body["price"] == 3880.5
    assert body["offset"] == "close_yesterday"

    signal = gateway.sent_signals[0]
    assert signal.direction == Direction.LONG
    assert signal.offset == OffsetFlag.CLOSE_YESTERDAY
    assert signal.order_type == OrderType.LIMIT


def test_quick_close_rejects_ambiguous_direction_and_over_volume(monkeypatch):
    install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        engine = trading_state.primary_engine()
        engine.gateway.positions["rb2505.long"] = Position(symbol="rb2505", direction=Direction.LONG, volume=1)
        engine.gateway.positions["rb2505.short"] = Position(symbol="rb2505", direction=Direction.SHORT, volume=1)

        ambiguous = client.post("/positions/rb2505/close", json={"volume": 0, "price": 0})
        assert ambiguous.status_code == 400
        assert "多个方向持仓" in ambiguous.json()["detail"]

        over_volume = client.post(
            "/positions/rb2505/close",
            json={"direction": "long", "volume": 3, "price": 0},
        )
        assert over_volume.status_code == 400
        assert "超过当前持仓" in over_volume.json()["detail"]


def test_cancel_all_counts_active_orders_once(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        allow_market_orders(client)
        gateway.orders["A1"] = Order(
            order_id="A1",
            symbol="rb2505",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3880,
            volume=1,
            status=OrderStatus.SUBMITTED,
        )
        gateway.orders["D1"] = Order(
            order_id="D1",
            symbol="rb2505",
            direction=Direction.LONG,
            order_type=OrderType.LIMIT,
            price=3880,
            volume=1,
            status=OrderStatus.CANCELLED,
        )

        response = client.post("/orders/cancel-all")

    assert response.status_code == 200
    assert response.json()["cancelled"] == 1
    assert response.json()["failed"] == 0
    assert gateway.cancelled_order_ids == ["A1"]
