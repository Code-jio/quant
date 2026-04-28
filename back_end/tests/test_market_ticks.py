from fastapi.testclient import TestClient

from src.api import create_app, trading_state
from src.trading import GatewayBase
from src.trading.types import AccountInfo, TradingStatus


class TickGateway(GatewayBase):
    def __init__(self):
        super().__init__("VNPY_CTP")
        self.latest_tick_snapshots = {}
        self.subscribed_symbols = []

    def connect(self, config):
        self.status = TradingStatus.CONNECTED
        self.account = AccountInfo(account_id="TEST001", balance=100000.0, available=100000.0)
        return True

    def disconnect(self):
        self.status = TradingStatus.STOPPED

    def send_order(self, signal):
        return "TEST_ORDER_1"

    def cancel_order(self, order_id):
        return True

    def query_account(self):
        return self.account

    def query_positions(self):
        return []

    def query_orders(self):
        return []

    def subscribe_market_data(self, symbols):
        self.subscribed_symbols.extend(symbols)


def install_gateway(monkeypatch):
    import src.trading

    gateway = TickGateway()
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


def teardown_function():
    trading_state.clear_main()


def test_watch_tick_requires_connected_gateway():
    trading_state.clear_main()
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/watch/tick?symbols=rb2505")

    assert response.status_code == 503
    assert response.json()["ticks"] == {}
    assert "行情网关未连接" in response.json()["msg"]


def test_watch_tick_returns_real_cached_ticks_and_subscribes(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        gateway.latest_tick_snapshots["rb2505"] = {
            "type": "tick",
            "source": "vnpy",
            "symbol": "rb2505",
            "last": 3888.5,
            "pre_close": 3800,
            "change": 88.5,
            "change_rate": 2.3289,
            "timestamp": "2026-04-29T09:30:00",
        }
        gateway.latest_tick_snapshots["au2506"] = {
            "type": "tick",
            "source": "vnpy",
            "symbol": "au2506",
            "last": 612.34,
            "pre_close": 610,
            "change": 2.34,
            "change_rate": 0.3836,
            "timestamp": "2026-04-29T09:30:01",
        }

        response = client.get("/watch/tick?symbols=rb2505,au2506")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["missing_symbols"] == []
    assert body["ticks"]["rb2505"]["source"] == "vnpy"
    assert body["ticks"]["rb2505"]["last"] == 3888.5
    assert body["ticks"]["au2506"]["last"] == 612.34
    assert gateway.subscribed_symbols == ["rb2505", "au2506"]


def test_watch_tick_reports_missing_without_fabricated_price(monkeypatch):
    gateway = install_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login(client)
        gateway.latest_tick_snapshots["rb2505"] = {
            "type": "tick",
            "source": "vnpy",
            "symbol": "rb2505",
            "last": 3888.5,
            "timestamp": "2026-04-29T09:30:00",
        }

        response = client.get("/watch/tick?symbols=rb2505,missing2505")

    assert response.status_code == 200
    body = response.json()
    assert body["ticks"]["rb2505"]["last"] == 3888.5
    assert "missing2505" not in body["ticks"]
    assert body["missing_symbols"] == ["missing2505"]
    assert "尚未收到实时行情" in body["msg"]
    assert gateway.subscribed_symbols == ["rb2505", "missing2505"]
