"""
回测引擎模块
"""

from __future__ import annotations

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

        self._current_date = None
        self._error_count = 0
        self._max_errors = 100

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

    def run(self) -> BacktestResult:
        """运行回测"""
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
                return self.result

            common_dates = None
            for df in all_data.values():
                if common_dates is None:
                    common_dates = set(df.index)
                else:
                    common_dates = common_dates.intersection(set(df.index))

            if not common_dates:
                logger.warning("没有共同的交易日期")
                return self.result

            sorted_dates = sorted(list(common_dates))

            for date in sorted_dates:
                try:
                    self._current_date = date

                    for symbol, df in all_data.items():
                        self.strategy.data[symbol] = df.loc[:date]

                    self.strategy.current_date = date

                    bar = {}
                    for symbol, df in all_data.items():
                        if date in df.index:
                            bar[symbol] = df.loc[date]

                    for symbol, series in bar.items():
                        self.strategy.on_bar(series)

                    self._process_signals()
                    self._update_positions(bar)
                    self._record_equity(date, bar)

                except Exception as e:
                    self._error_count += 1
                    logger.error(f"处理日期 {date} 时发生错误: {e}")
                    if self._error_count >= self._max_errors:
                        logger.error(f"错误次数过多 ({self._error_count}), 停止回测")
                        break
                    continue

            self.strategy.on_stop()
            self._calculate_result(sorted_dates)

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
                self._execute_order(order, signal)
            except Exception as e:
                logger.error(f"处理信号失败: {e}")
                continue

    def _create_order(self, signal):
        """创建订单"""
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

    def _execute_order(self, order, signal):
        """执行订单"""
        if order.status != OrderStatus.SUBMITTED:
            return

        try:
            exec_price = order.price
            if order.order_type == OrderType.MARKET:
                exec_price = self._apply_slippage(order.price, order.direction)

            pos = self.positions.get(order.symbol)
            remaining_volume = order.volume

            if order.direction == Direction.LONG:
                if pos and pos.is_short:
                    needed_close = min(remaining_volume, abs(pos.volume))
                    self._close_position(order.symbol, exec_price, needed_close, Direction.SHORT)
                    remaining_volume -= needed_close

                if getattr(order.offset, "value", order.offset) == "open" and remaining_volume > 0:
                    per_lot_cost = self._margin_required(exec_price, 1) + self._commission(exec_price, 1)
                    can_open = int((self.available_capital * 0.95) / per_lot_cost) if per_lot_cost > 0 else 0
                    open_volume = min(remaining_volume, can_open)

                    if open_volume > 0:
                        self._open_position(order.symbol, exec_price, open_volume, Direction.LONG)

            elif order.direction == Direction.SHORT:
                if pos and pos.is_long:
                    needed_close = min(remaining_volume, pos.volume)
                    self._close_position(order.symbol, exec_price, needed_close, Direction.LONG)
                    remaining_volume -= needed_close

                if getattr(order.offset, "value", order.offset) == "open" and remaining_volume > 0:
                    per_lot_cost = self._margin_required(exec_price, 1) + self._commission(exec_price, 1)
                    can_open = int((self.available_capital * 0.95) / per_lot_cost) if per_lot_cost > 0 else 0
                    open_volume = min(remaining_volume, can_open)

                    if open_volume > 0:
                        self._open_position(order.symbol, exec_price, open_volume, Direction.SHORT)

        except Exception as e:
            logger.error(f"执行订单失败: {e}")
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)

    def _open_position(self, symbol, price, volume, direction):
        """开仓"""
        margin = self._margin_required(price, volume)
        commission = self._commission(price, volume)

        self.available_capital -= margin + commission
        self.position_margins[symbol] = self.position_margins.get(symbol, 0.0) + margin

        trade = self._create_trade(symbol, price, volume, direction, commission)
        self.trades.append(trade)

        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol, direction=Direction.NET, volume=0)

        pos = self.positions[symbol]
        old_volume = abs(pos.volume)
        new_volume = old_volume + volume
        avg_price = (
            ((pos.price or 0) * old_volume + price * volume) / new_volume
            if new_volume > 0 else price
        )
        if direction == Direction.LONG:
            if pos.is_short:
                pos.volume += volume
                if pos.volume > 0:
                    pos.direction = Direction.LONG
                else:
                    pos.direction = Direction.SHORT
            else:
                pos.direction = Direction.LONG
                pos.volume += volume
        else:
            if pos.is_long:
                pos.volume -= volume
                if pos.volume < 0:
                    pos.direction = Direction.SHORT
                else:
                    pos.direction = Direction.LONG
            else:
                pos.direction = Direction.SHORT
                pos.volume -= volume

        pos.price = avg_price
        pos.cost = avg_price
        if self.strategy:
            self.strategy.trades.append(trade)

        logger.debug(f"开仓: {symbol} {direction.value} {volume}@{price}")

    def _close_position(self, symbol, price, volume, direction):
        """平仓"""
        if volume <= 0:
            return

        commission = self._commission(price, volume)

        pos = self.positions.get(symbol)
        entry_price = pos.price if pos else price
        pos_volume_before = abs(pos.volume) if pos else 0
        held_margin = self.position_margins.get(symbol, 0.0)
        margin_released = (
            held_margin * min(1.0, volume / pos_volume_before)
            if pos_volume_before > 0 else 0.0
        )

        gross_pnl = 0.0
        multiplier = max(1.0, float(self.config.contract_multiplier))
        if direction == Direction.LONG:
            gross_pnl = (price - entry_price) * volume * multiplier
        else:
            gross_pnl = (entry_price - price) * volume * multiplier

        self.available_capital += margin_released + gross_pnl - commission
        self.position_margins[symbol] = max(0.0, held_margin - margin_released)

        close_direction = Direction.SHORT if direction == Direction.LONG else Direction.LONG
        trade = self._create_trade(symbol, price, volume, close_direction, commission)
        trade.pnl = gross_pnl - commission

        self.trades.append(trade)

        if pos:
            if direction == Direction.LONG:
                pos.volume -= volume
            else:
                pos.volume += volume

            if pos.volume == 0:
                pos.direction = Direction.NET
                pos.price = 0
                pos.cost = 0
                pos.pnl = 0

        if self.strategy:
            self.strategy.trades.append(trade)

        logger.debug(f"平仓: {symbol} {close_direction.value} {volume}@{price}")

    def _create_trade(self, symbol, price, volume, direction, commission):
        """创建成交记录"""
        self.trade_id_counter += 1
        return Trade(
            trade_id=f"TRADE_{self.trade_id_counter}",
            order_id="",
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
                if symbol in bars:
                    current_price = bars[symbol]['close']
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
                if not pos.is_empty and symbol in bars:
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

            pnl_list = []
            in_position = False
            entry_price = 0
            entry_direction = None

            for trade in self.trades:
                if trade.direction == Direction.LONG and not in_position:
                    entry_price = trade.price
                    entry_direction = Direction.LONG
                    in_position = True
                elif trade.direction == Direction.SHORT and not in_position:
                    entry_price = trade.price
                    entry_direction = Direction.SHORT
                    in_position = True
                elif in_position:
                    if entry_direction == Direction.LONG and trade.direction == Direction.SHORT:
                        pnl = trade.price - entry_price
                        pnl_list.append(pnl)
                        in_position = False
                    elif entry_direction == Direction.SHORT and trade.direction == Direction.LONG:
                        pnl = entry_price - trade.price
                        pnl_list.append(pnl)
                        in_position = False

            self.result.total_trades = len(pnl_list)
            self.result.winning_trades = sum(1 for pnl in pnl_list if pnl > 0)
            self.result.losing_trades = sum(1 for pnl in pnl_list if pnl < 0)
            self.result.win_rate = self.result.winning_trades / self.result.total_trades if self.result.total_trades > 0 else 0

            self.result.equity_curve = self.equity_curve
            self.result.daily_returns = daily_returns.tolist()

        except Exception as e:
            logger.error(f"计算回测指标失败: {e}\n{traceback.format_exc()}")
