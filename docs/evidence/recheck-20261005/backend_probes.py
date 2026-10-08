"""Offline review probes. No native CTP imports or broker connections."""
import os
import sys
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path('D:/mine/quant')
OUT = Path('D:/mine/quant-recheck-20261005')
sys.path[:0] = [str(ROOT / 'back_end'), str(ROOT / 'back_end/tests'), 'D:/mine/quant-audit-20261004/python-deps']
os.environ['QUANT_ENV'] = 'test'
os.environ['QUANT_LEDGER_PATH'] = ':memory:'
os.environ['QUANT_AUDIT_PATH'] = str(OUT / 'probe-audit.db')

import pandas as pd
from fastapi.testclient import TestClient
import src.api as api
from src.api.security import session_store
from src.strategy import create_strategy, Signal, Direction, OrderType
from src.trading import TradingEngine
from src.trading.vnpy_gateway import VnpyGateway
from src.watch.kline import _calc_rsi
from test_trading_engine_auto_strategy import RecordingGateway
from test_api_auth import FakeVnpyGateway

findings = {}

# Use actual CTP metadata conversion with a local object; connect() is never called.
gw = RecordingGateway()
gw.connect({})
adapter = VnpyGateway()
adapter._on_vnpy_contract(SimpleNamespace(data=SimpleNamespace(
    symbol='A', name='A', exchange=SimpleNamespace(value='TEST'), size=10, pricetick=1, min_volume=1)))
gw.contract_specs = adapter.contract_specs
engine = TradingEngine(gw)
engine.configure_risk({'initial_capital': 100000})
sig = Signal(symbol='A', datetime=datetime.now(), direction=Direction.LONG, price=100.0, volume=1, order_type=OrderType.LIMIT)
oid = engine.send_signal(sig)
findings['margin_missing_from_browser_login'] = {
    'contract_margin_rate': gw.contract_specs['A']['margin_rate'],
    'order_id': oid, 'rejection': engine.last_reject_reason,
    'fake_gateway_submit_count': len(gw.sent_signals),
}
gw.contract_specs['A']['margin_rate'] = .1
findings['margin_missing_from_browser_login']['positive_control_order_id_with_verified_rate'] = engine.send_signal(sig)
engine.close()

# Current engine contract gives strategy.data only rows before current_date.
strategy = create_strategy('breakout', {'symbol': 'A', 'lookback_period': 2})
strategy.on_init()
strategy.contract_specs = {'A': {'size': 10, 'margin_rate': .1, 'min_volume': 1}}
strategy.current_capital = 1000
history = pd.DataFrame({'high': [110, 110, 200], 'low': [90, 90, 90], 'close': [100, 100, 190]},
                       index=pd.date_range('2026-01-01', periods=3))
strategy.data['A'] = history
strategy.current_date = pd.Timestamp('2026-01-04')
strategy.on_bar(pd.Series({'close': 150.0}))
findings['breakout_excludes_latest_completed_bar'] = {
    'previous_two_bar_high': float(history.high.iloc[-2:].max()), 'current_close': 150,
    'actual_signal_directions': [s.direction.value for s in strategy.signals],
    'expected_signal_directions': [],
}

ma = create_strategy('ma_cross', {'symbol': 'A', 'fast_period': 1, 'slow_period': 2})
ma.on_init()
ma.contract_specs = strategy.contract_specs
ma.current_capital = 1000
ma.data['A'] = pd.DataFrame({'close': [100.0, 100.0, 100.0]}, index=pd.date_range('2026-01-01', periods=3))
ma.current_date = pd.Timestamp('2026-01-04')
ma.on_bar(pd.Series({'close': 110.0}))
at_cross = len(ma.signals)
ma.data['A'].loc[ma.current_date] = 110.0
ma.current_date = pd.Timestamp('2026-01-05')
ma.on_bar(pd.Series({'close': 90.0}))
findings['moving_average_uses_previous_bar_cross'] = {
    'signals_at_actual_upward_cross': at_cross,
    'next_bar_price': 90.0,
    'next_bar_signal_directions': [s.direction.value for s in ma.signals],
}

uptrend = pd.Series(range(100, 130), dtype=float)
mixed = pd.Series([100, 101, 104, 99, 102, 101, 107, 109, 106, 110, 108, 111, 107, 109, 113, 112, 110, 109, 108, 107], dtype=float)
findings['server_rsi'] = {
    'uptrend_last': float(_calc_rsi(uptrend, 14).iloc[-1]),
    'mixed_last': float(_calc_rsi(mixed, 14).iloc[-1]),
    'uptrend_expected': 100,
}

# Exercise real login/logout routes with account-specific fake gateways.
class AccountGateway(FakeVnpyGateway):
    def connect(self, config):
        super().connect(config)
        self.account.account_id = config['username']
        self.account.balance = self.account.available = 100000 if config['username'] == 'A' else 10000
        return True

api.trading_state.clear_main()
api.trading_state._equity_curve.clear()
session_store.revoke_all()
with patch('src.trading.create_gateway', lambda *_: AccountGateway()):
    with TestClient(api.create_app(), raise_server_exceptions=False) as client:
        assert client.post('/auth/login', json={'username': 'A', 'password': 'offline'}).status_code == 200
        for i in range(10):
            api.trading_state.push_equity(1234, 100000)
        assert client.post('/auth/logout').status_code == 200
        assert client.post('/auth/login', json={'username': 'B', 'password': 'offline'}).status_code == 200
        api.trading_state.push_equity(0, 10000)
        response = client.get('/dashboard/metrics')
        data = response.json()
        findings['cross_account_equity_after_logout'] = {
            'status': response.status_code, 'account_id': data['account_id'],
            'old_account_pnl_in_new_curve': any(p['v'] == 1234 for p in data['equity_curve']),
            'max_drawdown_pct': data['max_drawdown_pct'], 'new_account_actual_pnl': 0,
        }
        response = client.put('/risk/config', json={'risk': {'max_order_volume': 0}})
        findings['invalid_risk_config_response'] = {'http_status': response.status_code, 'body': response.text}
        client.post('/auth/logout')
api.trading_state.clear_main()
api.trading_state._equity_curve.clear()
session_store.revoke_all()

output = json.dumps(findings, ensure_ascii=False, indent=2)
(OUT / 'backend-probes.json').write_text(output, encoding='utf-8')
print(output)
