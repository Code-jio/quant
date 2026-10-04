"""
回测引擎模块
"""

from __future__ import annotations

from ..strategy import OffsetFlag
from ..analysis.round_trips import completed_trades

import logging
import traceback
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..strategy import (
    Order, Trade, Direction, OrderType, OrderStatus, Position
)

from .config import BacktestConfig
from .result import BacktestResult
from .errors import BacktestError

logger = logging.getLogger(__name__)


class BacktestEngine:
    """回测引擎"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.strategy = None
        self.data_manager = None
        self.result = BacktestResult()
        self.orders = {}
        self.trades = []
        self.equity_curve = {}

        self.current_capital = config.initial_capital
        self.available_capital = config.initial_capital
        self.positions = {}
        self.position_margins: Dict[str, float] = {}
        self.order_id_counter = 0
        self.trade_id_counter = 0

        self._pending_signals = {}
        self._fill_order_id = ''
        self.cancel_event = None
        self._has_run = False
        self._current_date = None
        self._error_count = 0
        self._max_errors = max(1, int(getattr(config, "max_errors", 100)))

    def set_data_manager(self, data_manager):
        """设置数据管理器"""
        if data_manager is None:
            raise BacktestError("数据管理器不能为空")
        self.data_manager = data_manager

    def set_strategy(self, strategy):
        """设置策略"""
        if strategy is None:
            raise BacktestError("策略不能为空")
        self.strategy = strategy
        self.strategy.initial_capital = self.config.initial_capital
        self.strategy.current_capital = self.config.initial_capital
        self.strategy.set_position_source(self.positions)
        symbol = self.strategy.params.get('symbol','IF9999')
        self.strategy.contract_specs = {symbol: {'size':self.config.contract_multiplier,
            'margin_rate':self.config.margin_rate, 'min_volume':1}}

    def run(self) -> BacktestResult:
        """运行回测"""
        if self._has_run:
            raise BacktestError('Create a new engine for each run')
        self._has_run = True
        self.result.status = 'running'
        try:
            logger.info(f"开始回测: {self.config.start_date} ~ {self.config.end_date}")
            logger.info(f"初始资金: {self.config.initial_capital:.2f}")

            self.strategy.on_init()
            self.strategy.on_start()

            symbols = list(set(self.strategy.params.get('symbols', [self.strategy.params.get('symbol', 'IF9999')])))

            all_data = {}
            for symbol in symbols:
                df = self.data_manager.get_bars(
                    symbol,
                    self.config.start_date,
                    self.config.end_date
                )
                if df is not None and not df.empty:
                    all_data[symbol] = df

            if not all_data:
                logger.warning("没有加载到数据，回测结束")
                self.result.status = "failed"
                self.result.errors.append("No data in requested range")
                return self.result

            common_dates = None
            for df in all_data.values():
                if common_dates is None:
                    common_dates = set(df.index)
                else:
                    common_dates = common_dates.intersection(set(df.index))

            if not common_dates:
                logger.warning("没有共同的交易日期")
                self.result.status = "failed"
                self.result.errors.append("No data in requested range")
                return self.result

            sorted_dates = sorted(list(common_dates))

            for date in sorted_dates:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.result.status = 'cancelled'
                    break
                try:
                    self._current_date = date

                    for symbol, df in all_data.items():
                        self.strategy.data[symbol] = df.loc[df.index < date]

                    self.strategy.current_date = date

                    bar = {}
                    for symbol, df in all_data.items():
                        if date in df.index:
                            bar[symbol] = df.loc[date]

                    # Only orders created by earlier bars can execute now.
                    self._match_pending(bar)
                    for symbol, series in bar.items():
                        self.strategy.on_bar(series)

                    self._process_signals()
                    self._update_positions(bar)
                    self._record_equity(date, bar)
                    self.strategy.current_capital = self.equity_curve[date]['capital']
                    self.result.processed_bars += 1

                except Exception as e:
                    self._error_count += 1
                    self.result.errors.append(f'{date}: {e}')
                    self.result.status = 'partial' if self.equity_curve else 'failed'
                    logger.error(f"处理日期 {date} 时发生错误: {e}")
                    if self._error_count >= self._max_errors:
                        logger.error(f"错误次数过多 ({self._error_count}), 停止回测")
                        break
                    continue

            self.strategy.on_stop()
            self._calculate_result(list(self.equity_curve))
            if self.result.status == 'running':
                self.result.status = 'completed'
            for order in self.orders.values():
                if order.is_active():
                    order.status = OrderStatus.CANCELLED
                    order.error_msg = 'End of data'
                    self.strategy.on_order(order)

            logger.info(f"回测完成: 总收益={self.result.total_return:.2%}, "
                       f"夏普={self.result.sharpe_ratio:.2f}, "
                       f"胜率={self.result.win_rate:.2%}")

            return self.result

        except Exception as e:
            logger.error(f"回测执行失败: {e}\n{traceback.format_exc()}")
            raise BacktestError(f"回测执行失败: {e}")

    def _process_signals(self):
        """处理信号"""
        signals = self.strategy.signals
        self.strategy.signals = []

        for signal in signals:
            try:
                order = self._create_order(signal)
                self._pending_signals[order.order_id] = signal
            except Exception as e:
                raise BacktestError(f"Invalid strategy signal: {e}") from e

    def _create_order(self, signal):
        """创建订单"""
        if not signal.validate():
            raise ValueError('Invalid signal')
        self.order_id_counter += 1
        order = Order(
            order_id=f"ORDER_{self.order_id_counter}",
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            status=OrderStatus.SUBMITTED,
            offset=signal.offset,
        )
        self.orders[order.order_id] = order
        return order

    def _contract_value(self, price: float, volume: int) -> float:
        return float(price) * abs(int(volume)) * max(1.0, float(self.config.contract_multiplier))

    def _margin_required(self, price: float, volume: int) -> float:
        return self._contract_value(price, volume) * self.config.margin_rate

    def _commission(self, price: float, volume: int) -> float:
        return self._contract_value(price, volume) * self.config.commission_rate

    def _apply_slippage(self, price: float, direction: Direction) -> float:
        """Apply directional slippage: buys pay up, sells receive down."""
        if self.config.slip_rate <= 0:
            return price
        if direction == Direction.LONG:
            return price * (1 + self.config.slip_rate)
        if direction == Direction.SHORT:
            return price * (1 - self.config.slip_rate)
        return price

    def _match_pending(self, bars):
        for oid, signal in list(self._pending_signals.items()):
            order = self.orders[oid]
            bar = bars.get(order.symbol)
            if bar is None:
                continue
            self._execute_order(order, signal, bar)
            if not order.is_active():
                self._pending_signals.pop(oid, None)

    def _execute_order(self, order, signal, bar=None):
        if not order.is_active() or bar is None:
            return
        opening = float(bar['open'])
        if order.order_type == OrderType.LIMIT:
            if order.direction == Direction.LONG:
                if bar['low'] > order.price:
                    return
                price = min(opening, order.price)
            else:
                if bar['high'] < order.price:
                    return
                price = max(opening, order.price)
        elif order.order_type == OrderType.STOP:
            trigger = signal.stop_price or order.price
            if order.direction == Direction.LONG:
                if bar['high'] < trigger:
                    return
                opening = max(opening, trigger)
            else:
                if bar['low'] > trigger:
                    return
                opening = min(opening, trigger)
            price = self._apply_slippage(opening, order.direction)
        else:
            price = self._apply_slippage(opening, order.direction)
        volume = min(order.volume - order.traded_volume, max(0, int(bar.get('volume',0))))
        if volume <= 0:
            return
        self._fill_order_id = order.order_id
        if order.offset == OffsetFlag.OPEN:
            cost = self._margin_required(price,1) + self._commission(price,1)
            volume = min(volume, max(0,int(self.available_capital / cost)))
            if not volume:
                order.status=OrderStatus.REJECTED
                order.error_msg='Insufficient margin'
            else:
                self._open_position(order.symbol,price,volume,order.direction)
        else:
            side = Direction.SHORT if order.direction == Direction.LONG else Direction.LONG
            _, pos = self._position_side(order.symbol,side)
            if pos is None or abs(pos.volume) < volume:
                order.status=OrderStatus.REJECTED
                order.error_msg='Insufficient position to close'
                volume=0
            else:
                self._close_position(order.symbol,price,volume,side)
        if volume:
            order.traded_volume += volume
            order.status = OrderStatus.FILLED if order.traded_volume == order.volume else OrderStatus.PARTFILLED
        order.update_time=self._current_date
        if self.strategy:
            self.strategy.on_order(order)
        self._fill_order_id=''

    def _position_side(self, symbol, direction):
        for key,pos in self.positions.items():
            if pos.symbol == symbol and pos.direction == direction and not pos.is_empty:
                return key,pos
        return None,None

    def _open_position(self, symbol, price, volume, direction):
        margin=self._margin_required(price,volume)
        commission=self._commission(price,volume)
        self.available_capital -= margin + commission
        key,pos=self._position_side(symbol,direction)
        if pos is None:
            key = symbol if symbol not in self.positions or self.positions[symbol].is_empty else f'{symbol}_{direction.value}'
            pos=Position(symbol,direction,0)
            self.positions[key]=pos
        old=abs(pos.volume)
        pos.cost=pos.price=(pos.price*old+price*volume)/(old+volume)
        pos.volume=(old+volume)*(1 if direction==Direction.LONG else -1)
        self.position_margins[key]=self.position_margins.get(key,0)+margin
        trade=self._create_trade(symbol,price,volume,direction,commission)
        trade.offset=OffsetFlag.OPEN
        self._emit_trade(trade)

    def _close_position(self, symbol, price, volume, direction):
        key,pos=self._position_side(symbol,direction)
        if pos is None or volume <= 0 or volume > abs(pos.volume):
            raise BacktestError('Insufficient position to close')
        commission=self._commission(price,volume)
        held=self.position_margins.get(key,0)
        released=held*volume/abs(pos.volume)
        gross=(price-pos.price)*volume*self.config.contract_multiplier*(1 if direction==Direction.LONG else -1)
        self.available_capital += released+gross-commission
        self.position_margins[key]=max(0,held-released)
        pos.volume += -volume if direction==Direction.LONG else volume
        if pos.is_empty:
            pos.price=pos.cost=pos.pnl=0
        close_side=Direction.SHORT if direction==Direction.LONG else Direction.LONG
        trade=self._create_trade(symbol,price,volume,close_side,commission)
        trade.offset=OffsetFlag.CLOSE
        trade.pnl=gross-commission
        self._emit_trade(trade)

    def _emit_trade(self, trade):
        self.trades.append(trade)
        if self.strategy:
            self.strategy.trades.append(trade)
            self.strategy.current_capital += trade.pnl if trade.offset != OffsetFlag.OPEN else -trade.commission
            self.strategy.on_trade(trade)

    def _create_trade(self, symbol, price, volume, direction, commission):
        """创建成交记录"""
        self.trade_id_counter += 1
        return Trade(
            trade_id=f"TRADE_{self.trade_id_counter}",
            order_id=self._fill_order_id,
            symbol=symbol,
            direction=direction,
            price=price,
            volume=volume,
            commission=commission,
            trade_time=self._current_date
        )

    def _update_positions(self, bars):
        """更新持仓盯市"""
        for symbol, pos in self.positions.items():
            if pos.is_empty:
                continue

            try:
                if pos.symbol in bars:
                    current_price = bars[pos.symbol]['close']
                    multiplier = max(1.0, float(self.config.contract_multiplier))
                    if pos.is_long:
                        pnl = (current_price - pos.price) * abs(pos.volume) * multiplier
                    else:
                        pnl = (pos.price - current_price) * abs(pos.volume) * multiplier

                    pos.pnl = pnl
            except Exception as e:
                logger.error(f"更新持仓盯市失败: {symbol} - {e}")

    def _record_equity(self, date, bars):
        """记录权益曲线"""
        try:
            total_value = self.available_capital
            total_margin = 0.0
            total_unrealized_pnl = 0.0

            for symbol, pos in self.positions.items():
                if not pos.is_empty:
                    margin = self.position_margins.get(symbol, 0.0)
                    total_margin += margin
                    total_unrealized_pnl += pos.pnl
                    total_value += margin + pos.pnl

            self.equity_curve[date] = {
                'date': date,
                'capital': total_value,
                'position_value': total_margin + total_unrealized_pnl,
                'cash': self.available_capital,
                'margin': total_margin,
                'unrealized_pnl': total_unrealized_pnl,
            }
        except Exception as e:
            logger.error(f"记录权益曲线失败: {e}")

    def _calculate_result(self, dates):
        """计算回测指标"""
        try:
            if not self.equity_curve:
                return

            equity_df = pd.DataFrame(list(self.equity_curve.values()))
            equity_df.set_index('date', inplace=True)

            equity_df['returns'] = equity_df['capital'].pct_change()

            self.result.total_return = (equity_df['capital'].iloc[-1] / self.config.initial_capital) - 1

            days = len(dates)
            years = days / 252
            self.result.annual_return = (1 + self.result.total_return) ** (1 / years) - 1 if years > 0 else 0

            daily_returns = equity_df['returns'].dropna()
            if len(daily_returns) > 0 and daily_returns.std() > 0:
                self.result.sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

            cummax = equity_df['capital'].cummax()
            drawdown = (equity_df['capital'] - cummax) / cummax
            self.result.max_drawdown_pct = abs(drawdown.min())
            self.result.max_drawdown = self.config.initial_capital * self.result.max_drawdown_pct

            self.result.trades = self.trades

            pnl_list = [t['pnl'] for t in completed_trades(self.trades)]

            self.result.total_trades = len(pnl_list)
            self.result.winning_trades = sum(1 for pnl in pnl_list if pnl > 0)
            self.result.losing_trades = sum(1 for pnl in pnl_list if pnl < 0)
            self.result.win_rate = self.result.winning_trades / self.result.total_trades if self.result.total_trades > 0 else 0

            self.result.equity_curve = self.equity_curve
            self.result.daily_returns = daily_returns.tolist()

        except Exception as e:
            logger.error(f"计算回测指标失败: {e}\n{traceback.format_exc()}")
