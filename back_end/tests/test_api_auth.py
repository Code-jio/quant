from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from src.api import create_app
from src.api.security import SESSION_COOKIE_NAME, session_store
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


def test_websocket_requires_session():
    app = create_app()

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/system"):
                pass


def test_websocket_query_token_is_disabled_by_default():
    app = create_app()
    token = session_store.create()

    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws/system?token={token}"):
                    pass
    finally:
        session_store.revoke(token)


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
        body = login_response.json()
        assert body["success"] is True
        assert "token" not in body
        assert SESSION_COOKIE_NAME in client.cookies

        status_response = client.get("/system/status")
        assert status_response.status_code == 200
        assert status_response.json()["gateway_status"] in {"connected", "trading", "stopped"}

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200
        assert logout_response.json()["success"] is True


def test_login_can_auto_start_strategy_runtime(monkeypatch):
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
                "auto_start_strategy": True,
                "strategy_name": "ma_cross",
                "strategy_params": {
                    "symbol": "rb2505",
                    "fast_period": 5,
                    "slow_period": 10,
                    "position_ratio": 0.2,
                },
            },
        )

        assert login_response.status_code == 200
        body = login_response.json()
        assert body["strategy_started"] is True
        assert body["strategy_id"] == "ma_cross_main"

        strategies = client.get("/strategies")
        assert strategies.status_code == 200
        assert strategies.json()[0]["strategy_id"] == "ma_cross_main"
        assert strategies.json()[0]["status"] == "running"

        client.post("/auth/logout")


def test_emergency_stop_blocks_manual_orders_and_can_resume(monkeypatch):
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

        stop_response = client.post(
            "/risk/emergency-stop",
            json={"reason": "operator test", "cancel_orders": True, "stop_strategies": False},
        )
        assert stop_response.status_code == 200
        assert stop_response.json()["emergency_stop"] is True

        order_response = client.post(
            "/orders",
            json={
                "symbol": "rb2505",
                "direction": "long",
                "offset": "open",
                "price": 0,
                "volume": 1,
                "order_type": "market",
            },
        )
        assert order_response.status_code == 400
        assert "Emergency stop" in order_response.json()["detail"]

        resume_response = client.post("/risk/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["emergency_stop"] is False


def test_runtime_risk_config_can_be_tightened(monkeypatch):
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

        config_response = client.put("/risk/config", json={"risk": {"max_order_volume": 1}})
        assert config_response.status_code == 200
        assert config_response.json()["risk"]["max_order_volume"] == 1

        order_response = client.post(
            "/orders",
            json={
                "symbol": "rb2505",
                "direction": "long",
                "offset": "open",
                "price": 0,
                "volume": 2,
                "order_type": "market",
            },
        )

    assert order_response.status_code == 400
    assert "volume" in order_response.json()["detail"].lower()


def test_default_runtime_risk_blocks_market_orders(monkeypatch):
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

        order_response = client.post(
            "/orders",
            json={
                "symbol": "rb2505",
                "direction": "long",
                "offset": "open",
                "price": 0,
                "volume": 1,
                "order_type": "market",
            },
        )

    assert order_response.status_code == 400
    assert "Market orders are disabled" in order_response.json()["detail"]


def test_trading_reconcile_reports_account_orders_and_positions(monkeypatch):
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

        response = client.get("/trading/reconcile")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["account"]["account_id"] == "TEST001"
    assert "orders" in body
    assert "positions" in body


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
