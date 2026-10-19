"""EDB imports use deterministic CSV fixtures, never a live trading gateway."""
from datetime import date
from io import BytesIO
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

from src.data.edb import EdbError, EdbQuery, fetch_csv, import_history, parse_csv
from src.data import DataManager

TODAY = date(2026, 10, 19)
HEADER = 'datetime_nano,open,high,low,close,volume,open_oi,close_oi\n'


def csv_data(times=('2026-10-15 09:00', '2026-10-15 09:01')):
    return HEADER + ''.join(f'{pd.Timestamp(t, tz="Asia/Shanghai").value},100,102,99,101,12,50,51\n' for t in times)


def query(**changes):
    values: dict[str, Any] = dict(exchange='SHFE', symbol='au2612', timeframe='1m', start_date=date(2026, 10, 15), end_date=date(2026, 10, 15))
    return EdbQuery(**(values | changes)).validated(TODAY)


def test_exact_nanoseconds_local_timezone_and_oi():
    df = parse_csv(csv_data(), query())
    assert list(df.datetime) == list(pd.date_range('2026-10-15 09:00', periods=2, freq='min'))
    assert list(df.open_interest) == [51, 51]
    assert list(df.volume) == [12, 12]


def test_daily_keeps_provider_trading_date_and_normalizes_symbol():
    q = query(symbol='SHFE.AU2612', timeframe='1d')
    assert q.symbol == 'au2612' and q.vendor_symbol == 'SHFE.au2612'
    assert parse_csv(csv_data(['2026-10-15 00:00']), q).datetime.iloc[0] == pd.Timestamp('2026-10-15')
    assert query(exchange='CZCE', symbol='sr701').symbol == 'SR701'


@pytest.mark.parametrize('changes', [
    {'exchange': 'CME'}, {'symbol': 'KQ.m@SHFE.au'}, {'symbol': 'au9999'},
    {'symbol': 'au2612C1000'}, {'symbol': 'DCE.au2612'}, {'symbol': 'IF2612'},
    {'exchange': 'CFFEX', 'symbol': 'USDCNY2612'}, {'timeframe': '5m'},
    {'start_date': date(2025, 10, 18)}, {'end_date': date(2026, 10, 19)},
    {'start_date': date(2026, 9, 1)}, {'start_date': date(2026, 10, 16)},
])
def test_invalid_scope_rejected_before_network(changes):
    with pytest.raises(EdbError):
        query(**changes)


@pytest.mark.parametrize('data', [
    '403,免费用户仅支持近一年分钟线', '<html>Bad gateway</html>',
    HEADER.replace('close_oi', 'missing') + csv_data().split('\n', 1)[1],
    csv_data().replace(',102,', ',99,'), csv_data().replace(',12,', ',-1,'),
    csv_data().replace(',101,', ',NaN,'), csv_data().replace(',51\n', ',1.5\n'),
    csv_data(['2026-10-15 09:00', '2026-10-15 09:00']),
    csv_data(['2026-10-14 09:00']), csv_data(['2026-10-15 09:00:30']),
    csv_data().replace('100,102,99,101,12,50,51', '100,102,99,101,12,50,51,123'),
])
def test_corrupt_or_error_csv_is_not_successful_history(data):
    with pytest.raises(EdbError):
        parse_csv(data, query())


def test_empty_and_failed_download_do_not_modify_local_data(tmp_path):
    dm = DataManager(str(tmp_path / 'quotes.db'))
    result = import_history(query(), manager=dm, downloader=lambda _: HEADER)
    assert result['status'] == 'empty' and result['rows'] == 0
    assert dm.db.get_available_symbols() == []
    with pytest.raises(EdbError):
        import_history(query(), manager=dm, downloader=lambda _: csv_data().replace(',102,', ',99,'))
    assert dm.db.get_available_symbols() == []


def test_import_is_idempotent_and_shared_by_chart_and_backtest(tmp_path, monkeypatch):
    dm = DataManager(str(tmp_path / 'quotes.db'))
    for _ in range(2):
        result = import_history(query(), manager=dm, downloader=lambda _: csv_data())
    df = dm.get_bars('au2612', '2026-10-15', '2026-10-15', '1m')
    assert len(df) == 2 and set(df.data_source) == {'tianqin_edb'}
    assert result['vendor_symbol'] == 'SHFE.au2612' and result['synthetic_data_used'] is False
    assert len(result['sha256']) == 64
    from src.watch.kline import get_kline
    monkeypatch.setattr('src.data.DataManager', lambda: dm)
    chart = get_kline('au2612', '1m')
    assert chart['total'] == 2 and chart['data_source'] == 'tianqin_edb'
    assert dm.db.get_metadata('au2612', '1m')['row_count'] == 2


class Response(BytesIO):
    status = 200
    headers = {'Content-Type': 'text/csv'}


def test_http_request_is_fixed_https_bounded_and_unauthenticated(monkeypatch):
    def open_url(request, timeout):
        assert request.full_url.startswith('https://edb.shinnytech.com/md/kline?')
        assert 'period=60' in request.full_url and 'symbol=SHFE.au2612' in request.full_url
        assert 'token' not in request.full_url and 'Authorization' not in request.headers
        assert request.headers['Accept'] == '*/*'
        assert timeout <= 15
        return Response(csv_data().encode())
    monkeypatch.setattr('src.data.edb.urlopen', open_url)
    assert fetch_csv(query()) == csv_data()


@pytest.mark.parametrize('failure', [
    TimeoutError(), URLError('offline'), HTTPError('https://edb.shinnytech.com/md/kline', 403, 'Forbidden', Message(), None),
])
def test_transport_errors_are_explicit(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr('src.data.edb.urlopen', fail)
    with pytest.raises(EdbError):
        fetch_csv(query())


def test_truncated_and_oversized_transport_is_rejected(monkeypatch):
    for length in ('9999999999', '9999'):
        response = Response(csv_data().encode())
        response.headers = {'Content-Length': length}
        monkeypatch.setattr('src.data.edb.urlopen', lambda *args, **kwargs: response)
        with pytest.raises(EdbError):
            fetch_csv(query())


def test_import_failures_preserve_existing_rows(tmp_path):
    dm = DataManager(str(tmp_path / 'quotes.db'))
    import_history(query(), manager=dm, downloader=lambda _: csv_data())
    before = dm.get_bars('au2612', '2026-10-15', '2026-10-15', '1m', use_cache=False)
    with pytest.raises(EdbError):
        import_history(query(), manager=dm, downloader=lambda _: csv_data() + 'broken,row\n')
    pd.testing.assert_frame_equal(before, dm.get_bars('au2612', '2026-10-15', '2026-10-15', '1m', use_cache=False))


def test_api_session_validation_real_storage_and_backtest(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state
    from test_manual_trading import install_gateway, login
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('src.data.edb.today_shanghai', lambda: TODAY)
    monkeypatch.setattr('src.data.edb.fetch_csv', lambda q: csv_data(pd.bdate_range('2026-09-01', '2026-10-15')))
    gateway = install_gateway(monkeypatch)
    payload = dict(exchange='SHFE', symbol='au2612', timeframe='1d', start_date='2026-09-01', end_date='2026-10-15')
    try:
        with TestClient(create_app()) as client:
            assert client.get('/data/history/edb').json()['requires_vendor_login'] is False
            assert client.post('/data/history/edb/import', json=payload).status_code == 401
            login(client)
            assert client.post('/data/history/edb/import', json=payload | {'symbol': 'KQ.m@SHFE.au'}).status_code == 422
            response = client.post('/data/history/edb/import', json=payload)
            assert response.status_code == 200 and response.json()['rows'] == 33
            chart = client.get('/watch/kline', params={'symbol': 'au2612'}).json()
            assert chart['total'] == 33 and chart['synthetic_data_used'] is False
            backtest = client.post('/backtest/run', json={'strategy_name': 'ma_cross',
                'strategy_params': {'symbol': 'au2612', 'timeframe': '1d'},
                'start_date': payload['start_date'], 'end_date': payload['end_date'],
                'allow_synthetic_data': False}).json()
            assert backtest['success'] and backtest['data_source'] == 'tianqin_edb'
            assert not gateway.sent_signals
    finally:
        trading_state.clear_main()


def test_slow_download_does_not_block_account_actions_and_duplicate_is_rejected(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state
    from test_manual_trading import install_gateway, login
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('src.data.edb.today_shanghai', lambda: TODAY)
    started, release = Event(), Event()

    def slow(q):
        started.set()
        assert release.wait(10)
        return csv_data()

    monkeypatch.setattr('src.data.edb.fetch_csv', slow)
    gateway = install_gateway(monkeypatch)
    payload = dict(exchange='SHFE', symbol='au2612', timeframe='1m', start_date='2026-10-15', end_date='2026-10-15')
    try:
        with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=2) as executor:
            login(client)
            download = executor.submit(client.post, '/data/history/edb/import', json=payload)
            try:
                assert started.wait(5)
                duplicate = executor.submit(client.post, '/data/history/edb/import', json=payload).result(3)
                assert duplicate.status_code == 409
                assert executor.submit(client.get, '/risk/status').result(3).status_code == 200
                assert executor.submit(client.post, '/auth/session/logout').result(3).status_code == 200
            finally:
                release.set()
            assert download.result(5).status_code == 200
            assert not gateway.sent_signals
    finally:
        trading_state.clear_main()
