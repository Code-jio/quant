from datetime import datetime, timedelta
from dataclasses import replace
import pytest
from src.strategy import Direction, OffsetFlag, Trade, Position, create_strategy
from src.trading import TradingEngine
from test_trading_engine_auto_strategy import RecordingGateway, make_tick


def test_event_time_bar_aggregation_and_duplicates():
    from src.trading.bars import BarAggregator
    bars=BarAggregator('1m')
    t=datetime(2026,10,2,9,0)
    a=make_tick('A',100,t);a.volume=100
    b=make_tick('A',103,t+timedelta(seconds=30));b.volume=105
    c=make_tick('A',102,t+timedelta(minutes=1));c.volume=107
    assert bars.update(a) is None
    assert bars.update(b) is None
    assert bars.update(b) is None
    completed=bars.update(c)
    assert (completed['open'],completed['high'],completed['low'],completed['close'],completed['volume'])==(100,103,100,103,5)
    assert bars.current['volume']==2


def test_ledger_survives_restart_and_deduplicates(tmp_path):
    from src.trading.ledger import ExecutionLedger
    path=tmp_path/'execution.db'
    trade=Trade('T1','O1','A',Direction.LONG,100,1,account_id='account',trading_day='20261002',offset=OffsetFlag.OPEN)
    ledger=ExecutionLedger(str(path),'account')
    assert ledger.record_trade(trade)
    restored=ExecutionLedger(str(path),'account')
    assert not restored.record_trade(trade)
    assert restored.trades()[0].offset==OffsetFlag.OPEN
    assert ExecutionLedger(str(path),'other').trades()==[]
    assert restored.day_baseline('20261002',100)==100
    assert restored.day_baseline('20261002',80)==100
    assert restored.day_baseline('20261005',90)==90


def test_futures_sizing_uses_multiplier_margin_and_weight():
    s=create_strategy('ma_cross',{'symbol':'A','position_ratio':.2})
    s.current_capital=10000
    s.contract_specs={'A':{'size':10,'margin_rate':.1,'min_volume':1}}
    s.allocation_weight=.5
    assert s.order_volume('A',100)==10
    assert s.order_volume('UNKNOWN',100)==0


def test_strategy_parameters_reject_internal_attributes():
    with pytest.raises(ValueError):
        create_strategy('ma_cross',{'on_bar':'break'})
    with pytest.raises(ValueError):
        create_strategy('rsi',{'rsi_period':0})


def test_close_today_needs_known_inventory():
    from src.trading import RiskManager
    from test_review_trading import signal
    s=signal(offset=OffsetFlag.CLOSE_TODAY)
    assert not RiskManager().check_signal(s,positions={'A':Position('A',Direction.SHORT,3)}).allowed


def test_stop_strategy_preserves_account_connection():
    gw=RecordingGateway();engine=TradingEngine(gw)
    engine.start({'initial_capital':100000})
    engine.stop()
    assert gw.status.value=='connected'
