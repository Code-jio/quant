"""Account presentation must not manufacture balances, returns or exposure."""
from types import SimpleNamespace

import pytest

import src.api as api
from src.strategy import Direction, Position
from src.trading.types import AccountInfo, TradingStatus


@pytest.fixture
def engine(monkeypatch):
    account = AccountInfo(account_id='FIXTURE', balance=100000, available=90000,
                          total_pnl=0, position_pnl=-300, trading_day='20261015')
    gateway = SimpleNamespace(name='fixture', status=TradingStatus.CONNECTED, ready=True,
                              account=account, positions={}, contract_specs={})
    value = SimpleNamespace(gateway=gateway, get_account=lambda: gateway.account, ledger=None)
    monkeypatch.setattr(api.trading_state, 'primary_engine', lambda: value)
    monkeypatch.setattr(api.trading_state, 'all_entries', lambda: [])
    monkeypatch.setattr(api.trading_state, '_main_config', {'initial_capital': 1000000})
    monkeypatch.setattr(api.trading_state, '_day_open_balance', 100000)
    monkeypatch.setattr(api.trading_state, '_equity_curve', [])
    return value


def test_broker_zero_is_not_replaced_and_returns_have_no_invented_baseline(engine, monkeypatch):
    monkeypatch.setattr(api.trading_state, 'all_entries', lambda: [SimpleNamespace(status='stopped', config={})])
    monkeypatch.setattr(api, '_calc_strategy_pnl', lambda _: 12345)
    for build in (api._build_system_snapshot, api._build_dashboard_metrics):
        metrics = build()
        assert metrics['account_known'] is True
        assert metrics['total_pnl'] == 0
        assert metrics['position_pnl'] == -300
        assert metrics['return_rate'] is None
        assert metrics['account_source'] == 'gateway'
    metrics = api._build_dashboard_metrics()
    assert metrics['initial_capital'] is None
    assert metrics['today_return'] is None
    assert metrics['session_balance_change_pct'] == 0
    assert metrics['max_drawdown_pct'] is None


@pytest.mark.parametrize('problem', ['missing', 'error', 'unready', 'disconnected', 'nonfinite'])
def test_unavailable_account_stays_unknown(engine, problem):
    if problem == 'missing':
        engine.gateway.account.fields_known = False
    elif problem == 'error':
        engine.gateway.account.error_msg = 'fixture unavailable'
    elif problem == 'unready':
        engine.gateway.ready = False
    elif problem == 'disconnected':
        engine.gateway.status = TradingStatus.STOPPED
    else:
        engine.gateway.account.balance = float('nan')
    for build in (api._build_system_snapshot, api._build_dashboard_metrics):
        metrics = build()
        assert metrics['account_known'] is False
        assert all(metrics[key] is None for key in ('balance', 'total_pnl', 'position_pnl', 'return_rate'))
    assert api._build_dashboard_metrics()['session_balance_change_pct'] is None


def test_cost_notional_uses_contract_multiplier_and_position_symbol(engine):
    engine.gateway.positions = {'au2612_long': Position(symbol='au2612', direction=Direction.LONG,
                                                       volume=2, cost=800, pnl=500)}
    engine.gateway.contract_specs = {'au2612': {'size': 1000}}
    metrics = api._build_dashboard_metrics()
    assert metrics['positions'][0]['symbol'] == 'au2612'
    assert metrics['positions'][0]['notional'] == 1600000
    assert metrics['total_exposure'] == 1600000
    assert metrics['exposure_pct'] == 1600
    engine.gateway.contract_specs.clear()
    metrics = api._build_dashboard_metrics()
    assert metrics['positions'][0]['notional'] is None
    assert metrics['total_exposure'] is None
    assert metrics['exposure_pct'] is None


def test_empty_positions_require_a_completed_snapshot(engine):
    assert api._build_dashboard_metrics()['total_exposure'] == 0
    engine.gateway.connection_state = {'positions': False}
    metrics = api._build_dashboard_metrics()
    assert not metrics['positions_known']
    assert metrics['total_exposure'] is None


@pytest.mark.parametrize('pnl,balance', [(None, 100), (0, None), (0, float('nan')), (float('inf'), 100)])
def test_invalid_equity_is_not_recorded(engine, pnl, balance):
    assert not api.trading_state.push_equity(pnl, balance)
    assert api.trading_state.get_equity_data() == []


def test_equity_curve_plots_equity_not_pnl(engine):
    for balance in [100000] * 9 + [90000]:
        assert api.trading_state.push_equity(500, balance)
    metrics = api._build_dashboard_metrics()
    assert metrics['equity_curve'][-1]['v'] == 90000
    assert metrics['max_drawdown_pct'] == 10
