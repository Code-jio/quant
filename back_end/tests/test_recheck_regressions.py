"""Account, strategy timing and input validation regressions. Offline gateways only."""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.api.security import session_store
from src.strategy import Direction, create_strategy
from src.watch.kline import _calc_rsi
from test_api_auth import FakeVnpyGateway


@pytest.fixture
def client(monkeypatch):
    class AccountGateway(FakeVnpyGateway):
        def connect(self, config):
            super().connect(config)
            self.account.account_id = config['username']
            self.account.balance = self.account.available = 100000 if config['username'] == 'A' else 10000
            self.login_config = config
            return True

    monkeypatch.setenv('QUANT_ENV', 'test')
    monkeypatch.setenv('QUANT_LEDGER_PATH', ':memory:')
    api.trading_state.clear_main()
    session_store.revoke_all()
    monkeypatch.setattr('src.trading.create_gateway', lambda *_: AccountGateway())
    with TestClient(api.create_app(), raise_server_exceptions=False) as http:
        yield http


def login(client, name='A', **config):
    return client.post('/auth/login', json={'username': name, 'password': 'fixture', **config})


@pytest.mark.parametrize('logout', [False, True])
def test_account_transition_discards_old_equity(client, logout):
    assert login(client).status_code == 200
    for _ in range(10):
        api.trading_state.push_equity(1234, 100000)
    if logout:
        assert client.post('/auth/logout').status_code == 200
    assert login(client, 'B').status_code == 200
    for _ in range(10):
        api.trading_state.push_equity(0, 10000)
    metrics = client.get('/dashboard/metrics').json()
    assert metrics['account_id'] == 'B'
    assert metrics['max_drawdown_pct'] == 0
    assert all(point['v'] == 0 for point in metrics['equity_curve'])


def test_late_account_snapshot_and_queued_broadcast_are_discarded(client):
    assert login(client).status_code == 200
    old_generation = api.trading_state.generation
    assert login(client, 'B').status_code == 200
    assert not api.trading_state.push_equity(1234, 100000, generation=old_generation)
    socket = AsyncMock()
    manager = api.ConnectionManager('test')
    manager._connections.add(socket)
    asyncio.run(manager.broadcast({'private': 'account A'}, generation=old_generation))
    socket.send_text.assert_not_awaited()


@pytest.mark.parametrize('risk', [
    {'max_order_volume': 0}, {'enabled': 'false'}, {'max_daily_loss_ratio': 2},
    {'contract_multipliers': []}, {'allowed_symbols': 'A'}, {'max_order_volume': None},
])
def test_invalid_risk_update_returns_422_and_preserves_current_and_saved_config(client, risk):
    assert login(client).status_code == 200
    assert client.put('/risk/config', json={'risk': {'max_order_volume': 7}}).status_code == 200
    engine = api.trading_state.primary_engine()
    saved = engine.ledger.load_setting('risk')
    current = engine.risk_manager.status()
    response = client.put('/risk/config', json={'risk': risk})
    assert response.status_code == 422
    assert response.json()['detail']
    assert engine.risk_manager.status() == current
    assert engine.ledger.load_setting('risk') == saved


def test_login_passes_explicit_margin_rates_to_gateway(client):
    assert login(client, contract_margin_rates={'A': .12}).status_code == 200
    assert api.trading_state.primary_engine().gateway.login_config['contract_margin_rates'] == {'A': .12}


@pytest.mark.parametrize('rates', [{'A': 0}, {'A': 1.1}, {'A': True}, {' ': .1}])
def test_invalid_margin_configuration_does_not_replace_account(client, rates):
    assert login(client).status_code == 200
    engine = api.trading_state.primary_engine()
    assert login(client, 'B', contract_margin_rates=rates).status_code == 422
    assert api.trading_state.primary_engine() is engine


def prepared_strategy(name, **params):
    strategy = create_strategy(name, {'symbol': 'A', **params})
    strategy.on_init()
    strategy.contract_specs = {'A': {'size': 10, 'margin_rate': .1, 'min_volume': 1}}
    strategy.current_capital = 1000
    strategy.current_date = datetime(2026, 1, 4)
    return strategy


def test_breakout_uses_the_last_n_prior_bars_without_skipping_the_latest():
    strategy = prepared_strategy('breakout', lookback_period=2)
    strategy.data['A'] = pd.DataFrame({'high': [110, 110, 200], 'low': [90, 90, 90], 'close': [100, 100, 190]})
    strategy.on_bar(pd.Series({'close': 150}))
    assert not strategy.signals
    strategy.on_bar(pd.Series({'close': 201}))
    assert len(strategy.signals) == 1
    assert strategy.signals[0].direction == Direction.LONG


def test_ma_uses_current_completed_close_without_mutating_history():
    strategy = prepared_strategy('ma_cross', fast_period=1, slow_period=2)
    strategy.data['A'] = pd.DataFrame({'close': [100, 100, 100]})
    strategy.on_bar(pd.Series({'close': 110}))
    assert len(strategy.signals) == 1
    assert strategy.signals[0].direction == Direction.LONG
    assert strategy.signals[0].price == 110
    assert strategy.data['A']['close'].tolist() == [100, 100, 100]


def test_rsi_strategy_uses_current_completed_close():
    strategy = prepared_strategy('rsi', rsi_period=3)
    strategy.data['A'] = pd.DataFrame({'close': [100, 100, 100]})
    strategy.on_bar(pd.Series({'close': 90}))
    assert len(strategy.signals) == 1
    assert strategy.signals[0].direction == Direction.LONG


@pytest.mark.parametrize('name,params', [
    ('ma_cross', {'fast_period': 1, 'slow_period': 2}),
    ('rsi', {'rsi_period': 3}),
    ('breakout', {'lookback_period': 2}),
])
def test_builtin_decision_times_match_minute_replay_and_backtest(name, params):
    from src.backtest import BacktestConfig, BacktestEngine
    from src.trading import TradingEngine
    from test_trading_engine_auto_strategy import RecordingGateway, make_tick

    start = datetime(2026, 1, 5, 9)
    prices = [100, 100, 100, 110, 90, 95, 95, 120, 80]
    frame = pd.DataFrame({key: prices for key in ('open', 'high', 'low', 'close')},
                         index=pd.date_range(start, periods=len(prices), freq='min'))
    frame['volume'] = 1

    class Source:
        def get_bars(self, *args, **kwargs):
            return frame

    def strategy_with_decisions():
        strategy = create_strategy(name, {'symbol': 'A', 'timeframe': '1m', **params})
        decisions = []

        def record(symbol, price, target=None):
            if target is not None:
                decisions.append((strategy.current_date, target, price))

        strategy.rebalance_target = record
        return strategy, decisions

    research, research_decisions = strategy_with_decisions()
    backtest = BacktestEngine(BacktestConfig(start_date='2026-01-05', end_date='2026-01-05', timeframe='1m'))
    backtest.set_strategy(research)
    backtest.set_data_manager(Source())
    assert backtest.run().status == 'completed'

    live, live_decisions = strategy_with_decisions()
    engine = TradingEngine(RecordingGateway())
    engine.set_strategy(live)
    try:
        assert engine.start({'initial_capital': 100000})
        # The extra tick closes the final minute without being used in its decision.
        for i, price in enumerate([*prices, 500]):
            tick = make_tick('A', float(price), start + timedelta(minutes=i))
            tick.volume = i + 1
            engine.on_tick(tick)
        assert live_decisions == research_decisions
        assert research_decisions[0][0] == start + timedelta(minutes=3)
    finally:
        engine.close()


RSI_CASES = json.loads((Path(__file__).resolve().parents[2] / 'test_fixtures/rsi_cases.json').read_text())


@pytest.mark.parametrize('case', RSI_CASES, ids=lambda case: case['name'])
def test_server_rsi_matches_shared_wilder_fixtures(case):
    actual = _calc_rsi(pd.Series(case['closes'], dtype=float), case['period']).tolist()
    for value, expected in zip(actual, case['expected'], strict=True):
        if expected is None:
            assert pd.isna(value)
        else:
            assert value == pytest.approx(expected)
