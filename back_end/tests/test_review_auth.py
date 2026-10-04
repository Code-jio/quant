"""Account isolation regressions from the global review. No CTP connections."""
import importlib
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import src.api as api
from src.api.security import SESSION_COOKIE_NAME, session_store
from test_api_auth import FakeVnpyGateway


@pytest.fixture
def clients(monkeypatch):
    api.trading_state.clear_main()
    session_store._sessions.clear()
    monkeypatch.setattr('src.trading.create_gateway', lambda *_: FakeVnpyGateway())
    with TestClient(api.create_app()) as owner, TestClient(api.create_app()) as stranger:
        yield owner, stranger
    api.trading_state.clear_main()
    session_store._sessions.clear()


def login(client, username='A', **extra):
    return client.post('/auth/login', json={'username':username, 'password':'offline', **extra})


def test_cli_imports_and_initializes_logging():
    module = importlib.import_module('main')
    assert callable(module.configure_logging)
    assert module.DEFAULT_CONFIG['trading']['auth_code'] == ''


def test_alternate_entrypoint_is_same_factory():
    from src.api.app import create_app
    assert create_app is api.create_app


def test_anonymous_mutations_do_not_disconnect_account(clients):
    owner, stranger = clients
    assert login(owner).status_code == 200
    gateway = api.trading_state.primary_engine().gateway
    assert stranger.post('/auth/logout').status_code == 401
    assert login(stranger, gateway_type='invalid').status_code in (400, 401, 409)
    assert gateway.status.value == 'connected'
    assert login(stranger, 'B').status_code in (401, 409)
    assert api.trading_state.primary_engine().gateway is gateway


def test_switch_revokes_old_cookie_and_websocket(clients):
    owner, stranger = clients
    assert login(owner).status_code == 200
    old = owner.cookies[SESSION_COOKIE_NAME]
    stranger.cookies.set(SESSION_COOKIE_NAME, old)
    with stranger.websocket_connect('/ws/system') as ws:
        ws.receive_json()
        assert login(owner, 'B').status_code == 200
        assert stranger.get('/system/status').status_code == 401
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


def test_failed_authenticated_switch_preserves_account(clients):
    owner, _ = clients
    assert login(owner).status_code == 200
    gateway = api.trading_state.primary_engine().gateway
    assert login(owner, gateway_type='invalid').status_code == 400
    assert api.trading_state.primary_engine().gateway is gateway
    assert owner.get('/system/status').status_code == 200


def test_public_status_and_logs_do_not_disclose_account(clients):
    owner, stranger = clients
    assert login(owner).status_code == 200
    body = stranger.get('/auth/status').json()
    assert body['logged_in'] is False
    assert body['account_id'] == '' and body['connect_log'] == []
    assert stranger.get('/system/logs').status_code == 401


def test_cors_preflight_is_processed_before_auth(clients):
    _, stranger = clients
    response = stranger.options('/orders', headers={'Origin':'http://localhost:5173',
        'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
    assert stranger.post('/orders', json={}).status_code == 401


def test_offline_research_does_not_require_ctp(clients):
    _, stranger = clients
    with patch.object(api, 'run_backtest_sync', return_value={'success':True, 'metrics':{}}):
        response = stranger.post('/backtest/run',json={})
    assert response.status_code == 200
    assert response.json()['success'] is True
