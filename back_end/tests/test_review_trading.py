from dataclasses import replace
from datetime import datetime, timedelta
import pytest

from src.strategy import Direction, OffsetFlag, Order, OrderType, OrderStatus, Position, Signal, Trade, StrategyBase
from src.trading import TradingEngine, RiskManager
from src.trading.order_manager import PreOrder, PreOrderType
from src.trading.types import AccountInfo, TradingStatus
from src.common.exceptions import ExceptionHandler
from test_trading_engine_auto_strategy import RecordingGateway, make_tick


def signal(**kw):
    return Signal(**dict(symbol='A', datetime=datetime.now(), direction=Direction.LONG,
        price=100., volume=1, order_type=OrderType.LIMIT, **kw))


def connected_engine():
    gw=RecordingGateway()
    gw.connect({})
    engine=TradingEngine(gw)
    engine.configure_risk({'initial_capital':100000., 'risk':{'max_daily_loss_ratio':0}})
    return engine,gw


def test_emergency_stop_is_independent_of_optional_limits():
    risk=RiskManager({'enabled':False})
    risk.set_emergency_stop(True)
    assert not risk.check_signal(signal()).allowed


@pytest.mark.parametrize('price',[float('nan'),float('inf'),-float('inf')])
def test_nonfinite_signal_is_rejected(price):
    s=signal();s.price=price
    assert not s.validate()


def test_net_short_preserves_sign():
    p=Position('A',Direction.NET,-3)
    assert p.is_short and p.volume == -3


def test_pending_opens_reserve_position_capacity():
    engine,gw=connected_engine()
    engine.configure_risk({'risk':{'max_position_volume':2, 'max_daily_loss_ratio':0}})
    s=signal();s.volume=2
    assert engine.send_signal(s)
    assert not engine.send_signal(s)
    assert len(gw.sent_signals)==1


def test_close_selects_opposite_leg_and_deducts_frozen():
    r=RiskManager({'max_daily_loss_ratio':0})
    positions={'long':Position('A',Direction.LONG,1),'short':Position('A',Direction.SHORT,5,frozen=2)}
    s=signal(offset=OffsetFlag.CLOSE);s.volume=3
    assert r.check_signal(s,positions=positions).allowed
    s.volume=4
    assert not r.check_signal(s,positions=positions).allowed


def test_loss_limit_allows_reduction_but_unknown_account_blocks_open():
    r=RiskManager({'max_daily_loss_ratio':.1, 'require_account':True})
    r.set_day_open_balance(100)
    s=signal(offset=OffsetFlag.CLOSE)
    assert r.check_signal(s,positions={'A':Position('A',Direction.SHORT,1)},account=AccountInfo(balance=80)).allowed
    assert not r.check_signal(signal(),account=AccountInfo(balance=0)).allowed
    assert not r.check_signal(signal(),account=None).allowed


def test_preorder_cannot_bypass_emergency_or_open_instead_of_close():
    engine,gw=connected_engine()
    engine.risk_manager.set_emergency_stop(True)
    pre=PreOrder(PreOrderType.STOP_LOSS,'A',Direction.SHORT,1,100)
    engine.place_pre_order(pre)
    engine.update_market_data('A',{'last_price':90})
    assert not gw.sent_signals
    assert pre.offset == OffsetFlag.CLOSE


def test_cancel_waits_for_broker_and_modify_preserves_remaining_close():
    engine,gw=connected_engine()
    gw.positions={'A':Position('A',Direction.SHORT,3)}
    s=signal(offset=OffsetFlag.CLOSE);s.volume=3
    oid=engine.send_signal(s)
    original=replace(gw.orders[oid],status=OrderStatus.PARTFILLED,traded_volume=2)
    gw.on_order(original)
    assert engine.order_manager.modify_order(oid,new_price=101)
    assert original.status==OrderStatus.PARTFILLED
    assert len(gw.sent_signals)==1
    gw.on_order(replace(original,status=OrderStatus.CANCELLED))
    assert len(gw.sent_signals)==2
    assert gw.sent_signals[-1].offset == OffsetFlag.CLOSE
    assert gw.sent_signals[-1].volume == 1


class Observer(StrategyBase):
    def on_init(self):
        self.symbol='A';self.observed=[]
    def on_bar(self,bar):
        self.observed.append(bar)


def test_strategy_filters_symbols_and_stopped_callbacks():
    engine,gw=connected_engine()
    strategy=Observer('observer',{'symbol':'A'})
    strategy.on_init();engine.set_strategy(strategy)
    engine.on_tick(make_tick('A',100,datetime.now()))
    engine.status=TradingStatus.TRADING
    engine.on_tick(make_tick('B',100,datetime.now()))
    assert strategy.observed==[]


def test_duplicate_trade_is_retained_once_and_applied_once():
    engine,_=connected_engine()
    strategy=Observer('observer');strategy.on_init();engine.set_strategy(strategy)
    t=Trade('T1','O1','A',Direction.LONG,100,1)
    engine._on_trade(t);engine._on_trade(t)
    assert len(engine.trades)==1
    assert len(strategy.trades)==1
    assert strategy.positions['A'].volume==1


@pytest.mark.parametrize('method',['handle_network_request','handle_trading_operation'])
def test_exception_wrapper_executes_target(method):
    calls=[]
    result=getattr(ExceptionHandler(),method)(lambda: calls.append(1) or 42)
    assert result==42 and calls==[1]
