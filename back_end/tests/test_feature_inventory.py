"""Functional acceptance with temporary databases and a recording gateway only."""
import json
import logging
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api import create_app, trading_state
from src.api.security import session_store
from src.data.db import DatabaseManager
from test_manual_trading import install_gateway, login


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    trading_state.clear_main()
    session_store.revoke_all()
    install_gateway(monkeypatch)
    with TestClient(create_app()) as value:
        yield value
    trading_state.clear_main()
    session_store.revoke_all()


def test_strategy_detail_parameter_weight_and_start_stop_workflow(client):
    response = client.post('/auth/login', json={
        'username': 'fixture', 'password': 'fixture', 'auto_start_strategy': True,
        'strategy_name': 'ma_cross', 'strategy_params': {'symbol': 'AUDIT', 'fast_period': 5, 'slow_period': 10},
    })
    assert response.status_code == 200
    sid = response.json()['strategy_id']
    assert client.get('/strategies').json()[0]['status'] == 'running'
    path = f'/strategies/{sid}'
    assert client.get(path).json()['params']['fast_period'] == 5
    assert client.put(path + '/params', json={'params': {'fast_period': 3}}).status_code == 409
    assert client.post(f'/strategy/{sid}/action', json={'action': 'stop'}).status_code == 200
    assert client.put(path + '/params', json={'params': {'fast_period': 3}}).status_code == 200
    assert client.get(path).json()['params']['fast_period'] == 3
    assert client.put('/strategies/weights', json={'weights': {sid: .4}}).status_code == 200
    assert client.get(path).json()['weight'] == .4
    assert client.put('/strategies/weights', json={'weights': {sid: 1.1}}).status_code == 422
    assert client.get(path).json()['weight'] == .4
    assert client.put('/strategies/weights', json={'weights': {'missing': .2}}).status_code == 404
    assert client.get('/strategies/missing').status_code == 404
    assert client.post(f'/strategy/{sid}/action', json={'action': 'start'}).status_code == 200
    assert client.get(path).json()['status'] == 'running'
    assert trading_state.primary_engine().gateway.sent_signals == []


def test_dashboard_account_snapshots_and_log_search(client):
    catalog = client.get('/backtest/strategies')
    assert catalog.status_code == 200 and len(catalog.json()['strategies']) == 3
    login(client)
    demo = client.get('/ws-demo')
    assert demo.status_code == 200 and demo.headers['content-type'].startswith('text/html')
    for path in ('/system/status', '/dashboard/metrics', '/risk/status', '/trading/reconcile', '/audit/events'):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.json() is not None
    for path in ('/positions', '/orders', '/trades'):
        assert client.get(path).json() == []
    snapshot = client.get('/trading/snapshot').json()
    assert snapshot['revision'] > 0 and snapshot['orders'] == [] and snapshot['positions'] == []
    logging.getLogger('feature-audit').warning('fixture-log-search-marker')
    result = client.get('/system/logs', params={'level': 'WARNING', 'q': 'fixture-log-search-marker'}).json()
    assert result['total'] >= 1
    assert all(item['level'] == 'WARNING' and 'fixture-log-search-marker' in str(item) for item in result['logs'])


def test_kline_indicators_pagination_quality_and_cache_command(client):
    frame = pd.DataFrame({'datetime': pd.date_range('2024-01-01', periods=70),
                          'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 1000.})
    DatabaseManager().save_bars(frame, 'FEATURE_AUDIT', '1d', data_source='named_fixture')
    params = {'symbol': 'FEATURE_AUDIT', 'interval': '1d', 'limit': 10,
              'indicators': 'ma20,ema12,macd,rsi14,kdj,boll20,vol_ma5'}
    response = client.get('/watch/kline', params=params)
    assert response.status_code == 200
    result = response.json()
    assert result['total'] == 10 and result['has_more'] and result['data_source'] == 'named_fixture'
    row = result['data'][-1]
    expected = {'ma20': 100, 'ema12': 100, 'macd': 0, 'macd_signal': 0, 'macd_hist': 0,
                'rsi14': 50, 'k': 50, 'd': 50, 'j': 50, 'boll20_mid': 100,
                'boll20_upper': 100, 'boll20_lower': 100, 'vol_ma5': 1000}
    for key, value in expected.items():
        assert row[key] == pytest.approx(value), key
    older = client.get('/watch/kline', params={**params, 'before': result['next_before']}).json()
    assert older['data'][-1]['timestamp'] < result['data'][0]['timestamp']
    assert client.get('/watch/kline', params={**params, 'interval': '7m'}).status_code == 400
    assert client.get('/watch/kline', params={**params, 'indicators': 'ma0'}).status_code == 400
    assert client.get('/data/quality', params={'symbol': 'FEATURE_AUDIT'}).status_code == 401
    assert client.delete('/watch/kline/cache').status_code == 401
    login(client)
    quality = client.get('/data/quality', params={'symbol': 'FEATURE_AUDIT', 'timeframe': '1d'})
    assert quality.status_code == 200 and quality.json()['quality']['rows'] == 70
    assert client.delete('/watch/kline/cache').json()['code'] == 0


@pytest.mark.parametrize('channel', ['system', 'orders', 'positions', 'dashboard', 'logs', 'watch'])
def test_every_websocket_channel_rejects_anonymous_access(client, channel):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect('/ws/' + channel):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize('channel', ['system', 'positions', 'dashboard', 'logs'])
def test_authenticated_websocket_initial_snapshot(client, channel):
    login(client)
    with client.websocket_connect('/ws/' + channel) as ws:
        payload = ws.receive_json()
        assert isinstance(payload, dict)
        if channel == 'logs':
            assert payload['type'] == 'log_history'


def test_watch_websocket_heartbeat_and_subscription(client):
    login(client)
    with client.websocket_connect('/ws/watch') as ws:
        ws.send_text('ping')
        assert ws.receive_text() == 'pong'
        ws.send_json({'type': 'subscribe', 'symbols': ['AUDIT'], 'channels': ['tick']})
        assert ws.receive_json() == {'type': 'subscribed', 'symbols': ['AUDIT']}
        ws.send_json({'type': 'unsubscribe', 'symbols': ['AUDIT']})
        ws.send_text('ping')
        assert ws.receive_text() == 'pong'


def test_maintenance_cli_import_backup_restore_round_trip(monkeypatch, tmp_path, capsys):
    from src.maintenance import main
    monkeypatch.chdir(tmp_path)
    csv = tmp_path / 'bars.csv'
    csv.write_text('datetime,open,high,low,close,volume\n2024-01-02,100,102,99,101,10\n', encoding='utf-8')
    database, backup, restored = (tmp_path / name for name in ('source.db', 'backup.db', 'restored.db'))
    monkeypatch.setattr(sys, 'argv', ['maintenance', 'import-bars', str(csv), '--symbol', 'AUDIT',
                                   '--timeframe', '1d', '--source', 'named_fixture', '--database', str(database)])
    main()
    assert json.loads(capsys.readouterr().out)['rows'] == 1
    for command, source, target in (('backup', database, backup), ('restore', backup, restored)):
        monkeypatch.setattr(sys, 'argv', ['maintenance', command, str(source), str(target)])
        main()
    data = DatabaseManager(str(restored)).load_recent_bars('AUDIT', '1d', 10)
    assert data.iloc[0]['close'] == 101 and data.iloc[0]['data_source'] == 'named_fixture'


def test_json_performance_report_contains_finite_metrics():
    from src.analysis import Analyzer
    from src.analysis.report import JsonReportFormatter, TextReportFormatter
    analyzer = Analyzer()
    analyzer.set_data([{'date': pd.Timestamp('2024-01-01') + pd.Timedelta(days=i), 'capital': 100 + i}
                       for i in range(60)], [])
    result = analyzer.analyze()
    report = json.loads(JsonReportFormatter().format(result), parse_constant=lambda value: pytest.fail(value))
    assert report['performance']['total_return'] == pytest.approx(.59)
    assert '量化策略绩效报告' in TextReportFormatter().format(result)
