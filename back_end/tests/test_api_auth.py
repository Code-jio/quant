from fastapi.testclient import TestClient

from src.api import create_app
from src.api.security import SESSION_COOKIE_NAME
from src.trading import GatewayBase
from src.trading.types import AccountInfo, TradingStatus


class FakeVnpyGateway(GatewayBase):
    def __init__(self):
        super().__init__("VNPY_CTP")

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


def install_fake_vnpy_gateway(monkeypatch):
    import src.trading

    monkeypatch.setattr(src.trading, "create_gateway", lambda gateway_type="vnpy": FakeVnpyGateway())


def test_protected_endpoint_requires_session():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/system/status")

    assert response.status_code == 401


def test_health_and_metrics_are_public_and_structured():
    app = create_app()

    with TestClient(app) as client:
        health = client.get("/health")
        metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "X-Request-ID" in health.headers
    assert metrics.status_code == 200
    assert "quant_uptime_seconds" in metrics.text


def test_vnpy_login_sets_cookie_and_allows_protected_endpoint(monkeypatch):
    install_fake_vnpy_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login",
            json={
                "username": "test-account",
                "password": "test-password",
                "broker_id": "2071",
                "gateway_type": "vnpy",
            },
        )
        assert login_response.status_code == 200
        assert login_response.json()["success"] is True
        assert SESSION_COOKIE_NAME in client.cookies

        status_response = client.get("/system/status")
        assert status_response.status_code == 200
        assert status_response.json()["gateway_status"] in {"connected", "trading", "stopped"}

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200
        assert logout_response.json()["success"] is True


def test_audit_events_are_available_after_login(monkeypatch):
    install_fake_vnpy_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "username": "test-account",
                "password": "test-password",
                "broker_id": "2071",
                "gateway_type": "vnpy",
            },
        )

        response = client.get("/audit/events?event_type=auth")

    assert response.status_code == 200
    events = response.json()["events"]
    assert any(event["action"] == "login" and event["status"] == "success" for event in events)
