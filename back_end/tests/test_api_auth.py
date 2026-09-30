from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from src.api import create_app, trading_state
from src.api import _redact_connection_text
from src.api.security import SESSION_COOKIE_NAME, session_store
from src.trading import GatewayBase, TradingEngine
from src.trading.types import AccountInfo, TradingStatus


class FakeVnpyGateway(GatewayBase):
    def __init__(self):
        super().__init__("VNPY_CTP")
        self.reconciliation_requests = 0
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self, config):
        self.connect_calls += 1
        self.status = TradingStatus.CONNECTED
        self.account = AccountInfo(account_id="TEST001", balance=100000.0, available=100000.0)
        return True

    def disconnect(self):
        self.disconnect_calls += 1
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

    def refresh_reconciliation(self, timeout_seconds=8.0):
        self.reconciliation_requests += 1
        self.last_reconciliation = {
            "ok": True,
            "fresh": True,
            "failure_code": "",
            "duration_seconds": 0.0,
        }
        return dict(self.last_reconciliation)

    def connection_snapshot(self):
        return {
            "td_connected": True,
            "md_connected": True,
            "fully_connected": True,
            "contracts_ready": True,
            "reconciliation_ready": bool(
                self.last_reconciliation.get("ok")
                and self.last_reconciliation.get("fresh")
            ),
            "trading_day": "2026-08-14",
            "order_entry_ready": True,
        }


def install_fake_vnpy_gateway(monkeypatch):
    import src.trading

    gateways = []

    def create_gateway(gateway_type="vnpy"):
        gateway = FakeVnpyGateway()
        gateways.append(gateway)
        return gateway

    monkeypatch.setattr(src.trading, "create_gateway", create_gateway)
    return gateways


@pytest.fixture
def login_payload():
    return {
        "username": "test-account",
        "password": "test-password",
        "broker_id": "2071",
        "gateway_type": "vnpy",
    }


def test_protected_endpoint_requires_session():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/system/status")

    assert response.status_code == 401


def test_logout_cannot_disconnect_live_trading_without_a_session():
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/auth/logout")

    assert response.status_code == 401


def test_connection_error_redaction_never_echoes_credential_values():
    password = "SENSITIVE_PASSWORD_SENTINEL"
    auth_code = "SENSITIVE_AUTH_SENTINEL"
    app_id = "SENSITIVE_APP_SENTINEL"
    redacted = _redact_connection_text(
        f"password={password}; 授权编码:{auth_code}; app_id={app_id}",
        (password, auth_code, app_id),
    )

    assert password not in redacted
    assert auth_code not in redacted
    assert app_id not in redacted
    assert "***" in redacted


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


def test_anonymous_login_cannot_replace_existing_engine_or_revoke_sessions(monkeypatch, login_payload):
    gateways = install_fake_vnpy_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as owner:
        assert owner.post("/auth/login", json=login_payload).status_code == 200
        original_engine = trading_state.primary_engine()
        original_token = owner.cookies[SESSION_COOKIE_NAME]
        revoke = Mock(wraps=session_store.revoke)
        revoke_all = Mock(wraps=session_store.revoke_all)
        create_session = Mock(wraps=session_store.create)

        with monkeypatch.context() as auth_patch:
            auth_patch.setattr(session_store, "revoke", revoke)
            auth_patch.setattr(session_store, "revoke_all", revoke_all)
            auth_patch.setattr(session_store, "create", create_session)
            # Only the owner runs the app lifespan; this client has no cookie.
            anonymous = TestClient(app)
            try:
                response = anonymous.post("/auth/login", json=login_payload)
                assert SESSION_COOKIE_NAME not in anonymous.cookies
            finally:
                anonymous.close()

            assert response.status_code == 409
            assert trading_state.primary_engine() is original_engine
            assert len(gateways) == 1
            assert gateways[0].connect_calls == 1
            assert gateways[0].disconnect_calls == 0
            revoke.assert_not_called()
            revoke_all.assert_not_called()
            create_session.assert_not_called()
            assert session_store.is_valid(original_token)
            assert owner.get("/system/status").status_code == 200

        assert owner.post("/auth/logout").status_code == 200


def test_authenticated_login_requires_logout_before_new_connection(monkeypatch, login_payload):
    gateways = install_fake_vnpy_gateway(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        assert client.post("/auth/login", json=login_payload).status_code == 200
        original_engine = trading_state.primary_engine()
        original_token = client.cookies[SESSION_COOKIE_NAME]

        response = client.post("/auth/login", json=login_payload)

        assert response.status_code == 409
        assert trading_state.primary_engine() is original_engine
        assert len(gateways) == 1
        assert gateways[0].connect_calls == 1
        assert gateways[0].disconnect_calls == 0
        assert client.cookies[SESSION_COOKIE_NAME] == original_token
        assert session_store.is_valid(original_token)
        assert client.get("/system/status").status_code == 200

        assert client.post("/auth/logout").status_code == 200
        assert gateways[0].status == TradingStatus.STOPPED
        assert not session_store.is_valid(original_token)
        assert trading_state.primary_engine() is None

        assert client.post("/auth/login", json=login_payload).status_code == 200
        assert len(gateways) == 2
        assert trading_state.primary_engine() is not original_engine
        assert client.cookies[SESSION_COOKIE_NAME] != original_token
        assert client.post("/auth/logout").status_code == 200


@pytest.mark.parametrize(
    "strategy_config",
    [
        {},
        {"strategy_params": {"symbol": "", "symbols": []}},
        {"strategy_name": "unknown-strategy", "strategy_params": {"symbol": "rb2505"}},
        {"strategy_params": {"symbol": "rb2505", "max_errors": "not-an-integer"}},
    ],
    ids=["missing-symbol", "empty-symbols", "unknown-strategy", "invalid-constructor-parameter"],
)
def test_invalid_auto_start_configuration_is_rejected_before_connect(monkeypatch, login_payload, strategy_config):
    gateways = install_fake_vnpy_gateway(monkeypatch)
    create_session = Mock(wraps=session_store.create)
    monkeypatch.setattr(session_store, "create", create_session)
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={**login_payload, "auto_start_strategy": True, **strategy_config},
        )

        assert response.status_code == 400
        assert gateways == []
        assert trading_state.primary_engine() is None
        assert SESSION_COOKIE_NAME not in client.cookies
        create_session.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["configure_risk", "start"])
def test_login_initialization_failure_cleans_up_and_allows_retry(monkeypatch, login_payload, failure_stage):
    gateways = install_fake_vnpy_gateway(monkeypatch)
    created_tokens = []
    original_create = session_store.create

    def create_session(*args, **kwargs):
        token = original_create(*args, **kwargs)
        created_tokens.append(token)
        return token

    monkeypatch.setattr(session_store, "create", create_session)
    payload = {
        **login_payload,
        "auto_start_strategy": True,
        "strategy_params": {"symbol": "rb2505"},
    }
    app = create_app()

    with TestClient(app) as client:
        with monkeypatch.context() as failure_patch:
            failure = (
                Mock(side_effect=RuntimeError("risk configuration failed"))
                if failure_stage == "configure_risk"
                else Mock(return_value=False)
            )
            failure_patch.setattr(TradingEngine, failure_stage, failure)
            response = client.post("/auth/login", json=payload)

        assert response.status_code == 500
        failure.assert_called_once()
        assert len(gateways) == 1
        assert gateways[0].connect_calls == 1
        assert gateways[0].disconnect_calls == 1
        assert gateways[0].status == TradingStatus.STOPPED
        assert trading_state.primary_engine() is None
        assert trading_state.all_entries() == []
        assert SESSION_COOKIE_NAME not in client.cookies
        assert "token" not in response.json()
        assert len(created_tokens) == (0 if failure_stage == "configure_risk" else 1)
        assert all(not session_store.is_valid(token) for token in created_tokens)
        assert client.get("/system/status").status_code == 401

        # The same app and request must remain usable after releasing the transition lock.
        retry = client.post("/auth/login", json=payload)

        assert retry.status_code == 200
        assert retry.json()["strategy_started"] is True
        assert len(gateways) == 2
        assert trading_state.primary_engine().gateway is gateways[1]
        assert session_store.is_valid(client.cookies[SESSION_COOKIE_NAME])
        assert client.post("/auth/logout").status_code == 200


def test_concurrent_logins_only_construct_and_connect_one_gateway(monkeypatch, login_payload):
    gateways = install_fake_vnpy_gateway(monkeypatch)
    connect_entered = threading.Event()
    release_connect = threading.Event()
    original_connect = FakeVnpyGateway.connect

    def blocking_connect(gateway, config):
        connect_entered.set()
        if not release_connect.wait(timeout=10):
            raise RuntimeError("test did not release gateway connection")
        return original_connect(gateway, config)

    monkeypatch.setattr(FakeVnpyGateway, "connect", blocking_connect)
    app = create_app()

    with TestClient(app) as first_client:
        second_client = TestClient(app)
        try:
            with ThreadPoolExecutor(max_workers=2) as requests:
                first_request = requests.submit(first_client.post, "/auth/login", json=login_payload)
                try:
                    assert connect_entered.wait(timeout=5), "first login never reached gateway.connect"
                    second_request = requests.submit(second_client.post, "/auth/login", json=login_payload)
                    conflict = second_request.result(timeout=5)

                    assert conflict.status_code == 409
                    assert len(gateways) == 1
                    assert SESSION_COOKIE_NAME not in second_client.cookies
                    assert trading_state.primary_engine() is None
                finally:
                    release_connect.set()

                first_response = first_request.result(timeout=5)

            assert first_response.status_code == 200
            assert len(gateways) == 1
            assert gateways[0].connect_calls == 1
            assert gateways[0].disconnect_calls == 0
            assert trading_state.primary_engine().gateway is gateways[0]
            assert session_store.is_valid(first_client.cookies[SESSION_COOKIE_NAME])
            assert first_client.post("/auth/logout").status_code == 200
        finally:
            second_client.close()


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


def test_runtime_hard_risk_config_cannot_be_changed_from_the_client(monkeypatch):
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

        for forbidden_patch in (
            {"max_order_volume": 1},
            {"enabled": False},
            {"allowed_symbols": []},
            {"allow_market_orders": True},
        ):
            config_response = client.put("/risk/config", json={"risk": forbidden_patch})
            assert config_response.status_code == 422


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
        engine = trading_state.primary_engine()
        assert engine is not None
        assert engine.gateway.reconciliation_requests == 1

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["account"]["account_id"] == "TEST001"
    assert body["reconciliation"]["ok"] is True
    assert body["reconciliation"]["fresh"] is True
    assert "orders" in body
    assert "trades" in body
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
