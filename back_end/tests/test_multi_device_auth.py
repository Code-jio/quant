"""Independent device sessions sharing one executor; no broker connection."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api as api
from src.api.security import SESSION_COOKIE_NAME, session_store
from test_api_auth import FakeVnpyGateway


@pytest.fixture
def devices(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    session_store.revoke_all()
    api.trading_state.clear_main()
    created = []

    def factory(*_):
        gateway = FakeVnpyGateway()
        created.append(gateway)
        return gateway

    monkeypatch.setattr('src.trading.create_gateway', factory)
    app = api.create_app()
    with TestClient(app) as first, TestClient(app) as second:
        yield first, second, created
    session_store.revoke_all()
    api.trading_state.clear_main()


def login(client, **overrides):
    return client.post('/auth/login', json={'username': 'same-account', 'password': 'offline-secret', **overrides})


def test_two_devices_share_engine_ledger_and_risk_without_reconnecting(devices):
    first, second, created = devices
    assert login(first).status_code == 200
    token = first.cookies[SESSION_COOKIE_NAME]
    engine = api.trading_state.primary_engine()
    generation = api.trading_state.generation
    engine.risk_manager.emergency_stop = True
    response = login(second)
    assert response.status_code == 200
    assert response.json()['connection_reused'] is True
    assert second.cookies[SESSION_COOKIE_NAME] != token
    assert first.get('/system/status').status_code == second.get('/system/status').status_code == 200
    assert api.trading_state.primary_engine() is engine
    assert api.trading_state.generation == generation
    assert engine.risk_manager.emergency_stop is True
    assert len(created) == 1
    assert first.get('/auth/status').json()['active_sessions'] == 2
    assert first.get('/auth/status').json()['multi_device_supported'] is True
    assert 'offline-secret' not in repr(session_store._credential)


@pytest.mark.parametrize('changes, expected', [
    ({'password': 'incorrect'}, 401),
    ({'username': 'another-account'}, 409),
    ({'broker_id': 'other-broker'}, 409),
    ({'environment': '测试'}, 409),
    ({'td_server': 'tcp://other:1'}, 409),
    ({'app_id': 'another-app'}, 409),
    ({'auth_code': 'another-code'}, 409),
    ({'risk': {'max_order_volume': 1}}, 409),
    ({'auto_start_strategy': True, 'strategy_params': {'symbol': 'FIXTURE'}}, 409),
])
def test_join_cannot_authenticate_or_reconfigure_with_invalid_credentials(devices, changes, expected):
    first, second, created = devices
    assert login(first).status_code == 200
    engine = api.trading_state.primary_engine()
    assert login(second, **changes).status_code == expected
    assert second.get('/system/status').status_code == 401
    assert first.get('/system/status').status_code == 200
    assert api.trading_state.primary_engine() is engine and len(created) == 1


def test_device_logout_closes_own_socket_only_and_last_logout_keeps_account(devices):
    first, second, created = devices
    assert login(first).status_code == login(second).status_code == 200
    engine = api.trading_state.primary_engine()
    old = first.cookies[SESSION_COOKIE_NAME]
    with first.websocket_connect('/ws/watch') as own, second.websocket_connect('/ws/watch') as peer:
        own.send_text('ping')
        assert own.receive_text() == 'pong'
        assert first.post('/auth/session/logout').status_code == 200
        assert not session_store.is_valid(old)
        with pytest.raises(WebSocketDisconnect):
            own.send_text('ping')
            own.receive_text()
        peer.send_text('ping')
        assert peer.receive_text() == 'pong'
        assert second.get('/system/status').status_code == 200
    assert second.post('/auth/session/logout').status_code == 200
    assert session_store.active_count() == 0
    assert api.trading_state.primary_engine() is engine
    assert login(first).status_code == 200
    assert len(created) == 1


def test_global_disconnect_revokes_every_device_and_credential(devices):
    first, second, _ = devices
    assert login(first).status_code == login(second).status_code == 200
    assert first.post('/auth/logout').status_code == 200
    assert second.get('/system/status').status_code == 401
    assert api.trading_state.primary_engine() is None
    assert session_store._credential is None
    assert session_store.active_count() == 0


def test_relogin_rotates_only_current_cookie_and_expired_session_can_rejoin(devices):
    first, second, created = devices
    assert login(first).status_code == login(second).status_code == 200
    old = first.cookies[SESSION_COOKIE_NAME]
    peer_token = second.cookies[SESSION_COOKIE_NAME]
    assert login(first).status_code == 200
    assert not session_store.is_valid(old) and session_store.is_valid(peer_token)
    assert session_store.active_count() == 2
    session_store._sessions[peer_token] = datetime.now() - timedelta(seconds=1)
    assert second.get('/auth/status').json()['logged_in'] is False
    assert login(second).status_code == 200
    assert len(created) == 1


def test_failed_password_attempts_are_limited_and_do_not_lock_existing_session(devices):
    first, second, _ = devices
    assert login(first).status_code == 200
    for _ in range(5):
        assert login(second, password='wrong').status_code == 401
    limited = login(second, password='wrong')
    assert limited.status_code == 429
    assert int(limited.headers['retry-after']) > 0
    assert first.get('/system/status').status_code == 200


def test_session_limit_allows_rotation_but_not_unbounded_new_devices(devices, monkeypatch):
    first, second, _ = devices
    monkeypatch.setattr(session_store, 'max_sessions', 1)
    assert login(first).status_code == 200
    assert login(first).status_code == 200
    assert login(second).status_code == 429
    assert session_store.active_count() == 1


def test_anonymous_logout_and_oversized_password_do_not_touch_connection(devices):
    first, second, created = devices
    assert login(first).status_code == 200
    assert second.post('/auth/session/logout').status_code == 401
    assert login(second, password='x' * 1025).status_code == 422
    assert first.get('/system/status').status_code == 200 and len(created) == 1
