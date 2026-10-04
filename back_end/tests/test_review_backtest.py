import json
import pandas as pd
import pytest
from datetime import datetime
from src.backtest import BacktestConfig, BacktestEngine
from src.strategy import StrategyBase, Direction, OrderType, OrderStatus, Trade, OffsetFlag
from src.analysis import Analyzer
from test_backtest_accounting import SingleSymbolDataManager


class BuyOnce(StrategyBase):
    def on_init(self): self.symbol='A';self.seen=[]
    def on_bar(self,bar):
        self.seen.append(self.get_position('A').volume)
        if not self.get_position('A').volume:
            self.buy('A',100,1,OrderType.LIMIT)


def run(strategy, prices=(100,100,100,100)):
    df=pd.DataFrame({'open':prices,'high':prices,'low':prices,'close':prices,'volume':[100]*len(prices)},
        index=pd.date_range('2026-01-01',periods=len(prices)))
    e=BacktestEngine(BacktestConfig(start_date='2026-01-01',end_date='2026-01-10',commission_rate=0,slip_rate=0))
    e.set_strategy(strategy);e.set_data_manager(SingleSymbolDataManager(df));e.run()
    return e


def test_fills_on_next_bar_and_updates_strategy_position():
    s=BuyOnce('probe',{'symbol':'A'})
    e=run(s)
    assert s.seen == [0,1,1,1]
    assert len(e.trades)==1
    t=e.trades[0]
    assert t.trade_time == pd.Timestamp('2026-01-02')
    assert t.order_id and e.orders[t.order_id].status == OrderStatus.FILLED
    assert e.orders[t.order_id].traded_volume==1


def test_limit_that_never_crosses_does_not_fill():
    e=run(BuyOnce('probe',{'symbol':'A'}),prices=(200,200,200,200))
    assert e.trades==[]


def test_drawdown_unit_and_short_sample_json():
    a=Analyzer(initial_capital=100)
    a.set_data([{'date':'2026-01-01','capital':100},{'date':'2026-01-02','capital':90}],[])
    raw=a.analyze().to_dict()
    assert raw['risk']['max_drawdown_pct']==pytest.approx(.1)
    json.dumps(raw,allow_nan=False)
    assert '10.00%' in a.generate_report()


def test_round_trip_net_of_both_fees_is_one_trade():
    a=Analyzer(initial_capital=100)
    a.set_data([{'date':'2026-01-01','capital':100},{'date':'2026-01-02','capital':108}],
        [Trade('1','1','A',Direction.LONG,100,1,commission=1,offset=OffsetFlag.OPEN),
         Trade('2','2','A',Direction.SHORT,110,1,commission=1,pnl=9,offset=OffsetFlag.CLOSE)])
    p=a.analyze().performance
    assert p.total_trades==1 and p.win_rate==1 and p.avg_win==8


def test_strategy_error_does_not_report_complete_backtest():
    class Broken(BuyOnce):
        def on_bar(self,bar): raise RuntimeError('broken strategy')
    e=run(Broken('probe',{'symbol':'A'}))
    assert e.result.status=='failed' and e.result.errors


@pytest.mark.parametrize('kwargs',[{'initial_capital':0},{'margin_rate':0},{'commission_rate':-1},
    {'start_date':'2026-01-02','end_date':'2026-01-01'},{'contract_multiplier':float('nan')}])
def test_invalid_backtest_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError): BacktestConfig(**kwargs)
