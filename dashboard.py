"""
量化交易系统 - 可视化仪表盘
运行: python dashboard.py
访问: http://localhost:8050
"""

import os
import sys
import logging
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dash
from dash import dcc, html, Input, Output, State, dash_table
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data import DataManager
from src.strategy import create_strategy, Direction
from src.backtest import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# 颜色主题
# ─────────────────────────────────────────────────────────
C = {
    'bg':     '#0d1117',
    'card':   '#161b22',
    'border': '#30363d',
    'text':   '#e6edf3',
    'muted':  '#8b949e',
    'green':  '#3fb950',
    'red':    '#f85149',
    'blue':   '#58a6ff',
    'yellow': '#d29922',
    'purple': '#bc8cff',
    'orange': '#f0883e',
}

INPUT_STYLE = {
    'width': '100%', 'boxSizing': 'border-box',
    'backgroundColor': C['bg'], 'color': C['text'],
    'border': f"1px solid {C['border']}",
    'borderRadius': '6px', 'padding': '6px 10px',
    'marginBottom': '10px', 'fontSize': '13px',
    'outline': 'none',
}

LABEL_STYLE = {'fontSize': '12px', 'color': C['muted'], 'marginBottom': '4px', 'display': 'block'}


# ─────────────────────────────────────────────────────────
# 回测执行
# ─────────────────────────────────────────────────────────
def exec_backtest(strategy_name, symbol, fast, slow, capital, start, end):
    """执行回测，返回 (engine, result, bars_df)"""
    params = {
        'name': strategy_name,
        'symbol': symbol,
        'fast_period': int(fast),
        'slow_period': int(slow),
        'position_ratio': 0.8,
        'rsi_period': 14, 'oversold': 30, 'overbought': 70,
        'lookback_period': 20,
    }
    bt_cfg = BacktestConfig(
        start_date=start, end_date=end,
        initial_capital=float(capital),
        commission_rate=0.0003, slip_rate=0.0001, margin_rate=0.12,
    )
    dm = DataManager()
    pure = symbol.split('.')[-1] if '.' in symbol else symbol
    bars = dm.generate_sample_data(pure, days=600)

    strategy = create_strategy(strategy_name, params)
    engine = BacktestEngine(bt_cfg)
    engine.set_data_manager(dm)
    engine.set_strategy(strategy)
    result = engine.run()
    return engine, result, bars


def serialize(engine, result, bars, capital, symbol):
    """把回测结果序列化为 JSON 兼容的 dict"""
    # equity curve: {datetime -> {capital, cash, ...}}
    eq_dates, eq_vals, eq_cash = [], [], []
    for dt, rec in sorted(engine.equity_curve.items()):
        eq_dates.append(str(dt))
        eq_vals.append(float(rec['capital']))
        eq_cash.append(float(rec.get('cash', rec['capital'])))

    # trades
    trades = []
    for t in (result.trades or []):
        d = t.direction.value if hasattr(t.direction, 'value') else str(t.direction)
        trades.append({
            'trade_time': str(t.trade_time),
            'symbol': str(t.symbol),
            'direction': d,
            'direction_cn': '买入 ▲' if d.lower() == 'long' else '卖出 ▼',
            'price': round(float(t.price), 2),
            'volume': int(abs(t.volume)),
            'commission': round(float(t.commission), 2),
        })

    # bars OHLCV
    bars_data = None
    if bars is not None and not bars.empty:
        b = bars.copy()
        if not isinstance(b.index, pd.DatetimeIndex):
            if 'datetime' in b.columns:
                b = b.set_index('datetime')
            b.index = pd.to_datetime(b.index)
        bars_data = {
            'dates':  b.index.strftime('%Y-%m-%d').tolist(),
            'open':   [round(x, 2) for x in b['open'].tolist()],
            'high':   [round(x, 2) for x in b['high'].tolist()],
            'low':    [round(x, 2) for x in b['low'].tolist()],
            'close':  [round(x, 2) for x in b['close'].tolist()],
            'volume': [int(x) for x in b['volume'].tolist()],
        }

    m = result
    return {
        'equity': {'dates': eq_dates, 'values': eq_vals, 'cash': eq_cash},
        'trades': trades,
        'bars': bars_data,
        'metrics': {
            'total_return':    round(float(m.total_return), 6),
            'annual_return':   round(float(m.annual_return), 6),
            'sharpe_ratio':    round(float(m.sharpe_ratio), 4),
            'max_drawdown_pct': round(float(m.max_drawdown_pct), 6),
            'win_rate':        round(float(m.win_rate), 6),
            'total_trades':    int(m.total_trades),
            'winning_trades':  int(m.winning_trades),
            'losing_trades':   int(m.losing_trades),
        },
        'capital': float(capital),
        'symbol': str(symbol),
    }


# ─────────────────────────────────────────────────────────
# 图表生成
# ─────────────────────────────────────────────────────────
def _theme(fig):
    """统一应用深色主题"""
    fig.update_layout(
        paper_bgcolor=C['bg'], plot_bgcolor=C['card'],
        font=dict(color=C['text'], size=12,
                  family="'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif"),
        legend=dict(bgcolor='rgba(0,0,0,0)', bordercolor=C['border'], borderwidth=1,
                    orientation='h', yanchor='bottom', y=1.01, xanchor='left', x=0),
        margin=dict(l=10, r=20, t=40, b=10),
        hovermode='x unified',
    )
    fig.update_xaxes(gridcolor=C['border'], showgrid=True, zeroline=False,
                     showspikes=True, spikecolor=C['muted'], spikethickness=1)
    fig.update_yaxes(gridcolor=C['border'], showgrid=True, zeroline=False)


def chart_equity(data: dict) -> go.Figure:
    eq = data['equity']
    if not eq['dates']:
        return go.Figure()

    dates = pd.to_datetime(eq['dates'])
    vals = np.array(eq['values'])
    initial = data['capital']

    rolling_max = np.maximum.accumulate(vals)
    drawdown = np.where(rolling_max > 0, (vals - rolling_max) / rolling_max * 100, 0)

    fig = make_subplots(rows=2, cols=1, row_heights=[0.70, 0.30],
                        shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=('资产净值曲线', '回撤'))

    # 净值曲线
    fig.add_trace(go.Scatter(
        x=dates, y=vals, name='资产净值', mode='lines',
        line=dict(color=C['blue'], width=2),
        fill='tozeroy', fillcolor='rgba(88,166,255,0.07)',
        hovertemplate='%{x|%Y-%m-%d}<br>净值: ¥%{y:,.0f}<extra></extra>',
    ), row=1, col=1)

    # 初始资金基准线
    fig.add_shape(type='line', x0=dates[0], x1=dates[-1],
                  y0=initial, y1=initial,
                  line=dict(color=C['muted'], width=1, dash='dash'),
                  row=1, col=1)
    fig.add_annotation(x=dates[-1], y=initial, text='初始资金',
                        showarrow=False, font=dict(color=C['muted'], size=10),
                        xanchor='right', row=1, col=1)

    # 回撤曲线
    fig.add_trace(go.Scatter(
        x=dates, y=drawdown, name='回撤 %', mode='lines',
        line=dict(color=C['red'], width=1.5),
        fill='tozeroy', fillcolor='rgba(248,81,73,0.12)',
        hovertemplate='%{x|%Y-%m-%d}<br>回撤: %{y:.2f}%<extra></extra>',
    ), row=2, col=1)

    _theme(fig)
    fig.update_yaxes(title_text='净值 (元)', tickformat=',.0f', row=1, col=1)
    fig.update_yaxes(title_text='回撤 %', row=2, col=1)
    return fig


def chart_candle(data: dict) -> go.Figure:
    bars_raw = data.get('bars')
    if not bars_raw:
        return go.Figure()

    dates = pd.to_datetime(bars_raw['dates'])
    o = np.array(bars_raw['open'])
    h = np.array(bars_raw['high'])
    l = np.array(bars_raw['low'])
    c = np.array(bars_raw['close'])
    vol = np.array(bars_raw['volume'])
    close_s = pd.Series(c, index=dates)

    fig = make_subplots(rows=2, cols=1, row_heights=[0.75, 0.25],
                        shared_xaxes=True, vertical_spacing=0.03,
                        subplot_titles=('K线图 & 买卖点', '成交量'))

    # K线（A股配色：红涨绿跌）
    fig.add_trace(go.Candlestick(
        x=dates, open=o, high=h, low=l, close=c,
        name='K线',
        increasing=dict(line=dict(color=C['red'], width=1), fillcolor=C['red']),
        decreasing=dict(line=dict(color=C['green'], width=1), fillcolor=C['green']),
    ), row=1, col=1)

    # MA 均线
    for period, color, name in [
        (10, C['yellow'], 'MA10'),
        (20, C['purple'], 'MA20'),
        (60, C['orange'], 'MA60'),
    ]:
        ma = close_s.rolling(period).mean()
        fig.add_trace(go.Scatter(
            x=dates, y=ma.values, name=name, mode='lines',
            line=dict(color=color, width=1.2),
            hovertemplate=f'{name}: %{{y:.2f}}<extra></extra>',
        ), row=1, col=1)

    # 买卖点标记
    trades = data.get('trades', [])
    buy_x, buy_y, sell_x, sell_y = [], [], [], []
    for t in trades:
        td = pd.to_datetime(t['trade_time'])
        p = float(t['price'])
        if t['direction'].lower() == 'long':
            buy_x.append(td)
            buy_y.append(p * 0.997)
        else:
            sell_x.append(td)
            sell_y.append(p * 1.003)

    if buy_x:
        fig.add_trace(go.Scatter(
            x=buy_x, y=buy_y, mode='markers', name='买入',
            marker=dict(symbol='triangle-up', size=12, color=C['red'],
                        line=dict(width=1, color='white')),
            hovertemplate='买入: ¥%{y:.2f}<br>%{x|%Y-%m-%d}<extra></extra>',
        ), row=1, col=1)

    if sell_x:
        fig.add_trace(go.Scatter(
            x=sell_x, y=sell_y, mode='markers', name='卖出',
            marker=dict(symbol='triangle-down', size=12, color=C['green'],
                        line=dict(width=1, color='white')),
            hovertemplate='卖出: ¥%{y:.2f}<br>%{x|%Y-%m-%d}<extra></extra>',
        ), row=1, col=1)

    # 成交量柱
    bar_colors = [C['red'] if c[i] >= o[i] else C['green'] for i in range(len(c))]
    fig.add_trace(go.Bar(
        x=dates, y=vol, name='成交量',
        marker_color=bar_colors, opacity=0.65,
        hovertemplate='成交量: %{y:,}<extra></extra>',
    ), row=2, col=1)

    _theme(fig)
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(title_text='价格', row=1, col=1)
    fig.update_yaxes(title_text='手数', row=2, col=1)
    return fig


def chart_returns(data: dict):
    """返回 (日收益直方图, 月度热力图, 滚动夏普图)"""
    eq = data['equity']
    if not eq['dates']:
        empty = go.Figure()
        return empty, empty, empty

    vals = pd.Series(eq['values'], index=pd.to_datetime(eq['dates']))
    dr = vals.pct_change().dropna() * 100

    # ── 日收益率分布 ──
    fig_hist = go.Figure()
    pos_vals = dr[dr >= 0].values
    neg_vals = dr[dr < 0].values

    if len(pos_vals):
        fig_hist.add_trace(go.Histogram(
            x=pos_vals, nbinsx=25, name='盈利日', marker_color=C['red'], opacity=0.7,
        ))
    if len(neg_vals):
        fig_hist.add_trace(go.Histogram(
            x=neg_vals, nbinsx=25, name='亏损日', marker_color=C['green'], opacity=0.7,
        ))

    mean_r = dr.mean()
    fig_hist.add_vline(x=0, line_color=C['muted'], line_dash='dash', line_width=1)
    fig_hist.add_vline(x=mean_r, line_color=C['yellow'], line_dash='dot',
                       annotation_text=f'均值 {mean_r:.2f}%',
                       annotation_font_color=C['yellow'], annotation_font_size=11)
    _theme(fig_hist)
    fig_hist.update_layout(
        title=dict(text='日收益率分布', font=dict(size=13)),
        barmode='overlay',
        xaxis_title='日收益率 %', yaxis_title='频次',
    )

    # ── 月度收益热力图 ──
    try:
        monthly = dr.resample('ME').sum()
    except Exception:
        monthly = dr.resample('M').sum()

    df_m = pd.DataFrame({
        'year': monthly.index.year,
        'month': monthly.index.month,
        'ret': monthly.values,
    })
    month_names = ['1月', '2月', '3月', '4月', '5月', '6月',
                   '7月', '8月', '9月', '10月', '11月', '12月']

    if not df_m.empty and len(df_m) > 1:
        pivot = df_m.pivot(index='year', columns='month', values='ret')
        z = pivot.values
        text_z = [[f'{v:.1f}%' if not np.isnan(v) else '-' for v in row] for row in z]

        fig_heat = go.Figure(go.Heatmap(
            z=z,
            x=[month_names[int(m) - 1] for m in pivot.columns],
            y=[str(int(y)) for y in pivot.index],
            colorscale=[[0, C['red']], [0.5, '#1c2128'], [1, C['green']]],
            zmid=0,
            text=text_z, texttemplate='%{text}',
            colorbar=dict(title='收益%', tickfont=dict(color=C['text'])),
            hovertemplate='%{y}年 %{x}: %{text}<extra></extra>',
        ))
        _theme(fig_heat)
        fig_heat.update_layout(
            title=dict(text='月度收益热力图', font=dict(size=13)),
        )
    else:
        fig_heat = go.Figure()
        fig_heat.update_layout(
            paper_bgcolor=C['bg'],
            annotations=[dict(text='数据不足以生成热力图', showarrow=False,
                              font=dict(color=C['muted'], size=14), x=0.5, y=0.5)],
        )

    # ── 滚动夏普比率 ──
    window = min(60, max(20, len(dr) // 5))
    rolling_sharpe = (dr.rolling(window).mean() / dr.rolling(window).std()) * np.sqrt(252)

    fig_sharpe = go.Figure()
    fig_sharpe.add_trace(go.Scatter(
        x=rolling_sharpe.index, y=rolling_sharpe.values,
        mode='lines', name=f'滚动夏普({window}日)',
        line=dict(color=C['blue'], width=1.5),
        fill='tozeroy', fillcolor='rgba(88,166,255,0.07)',
        hovertemplate='%{x|%Y-%m-%d}<br>夏普: %{y:.2f}<extra></extra>',
    ))
    fig_sharpe.add_hline(y=0, line_color=C['muted'], line_dash='dash', line_width=1)
    fig_sharpe.add_hline(y=1, line_color=C['green'], line_dash='dot', line_width=1,
                          annotation_text='夏普=1', annotation_font_color=C['green'])
    _theme(fig_sharpe)
    fig_sharpe.update_layout(
        title=dict(text=f'滚动夏普比率（{window}日窗口）', font=dict(size=13)),
        xaxis_title='日期', yaxis_title='夏普比率',
    )

    return fig_hist, fig_heat, fig_sharpe


# ─────────────────────────────────────────────────────────
# KPI 卡片
# ─────────────────────────────────────────────────────────
def _kpi(title, value, color):
    return html.Div(
        style={
            'flex': 1, 'backgroundColor': C['card'],
            'border': f"1px solid {C['border']}",
            'borderRadius': '8px', 'padding': '14px 10px',
            'textAlign': 'center', 'minWidth': '100px',
        },
        children=[
            html.Div(title, style={'fontSize': '11px', 'color': C['muted'],
                                   'marginBottom': '6px', 'letterSpacing': '0.5px'}),
            html.Div(value, style={'fontSize': '20px', 'fontWeight': '700', 'color': color}),
        ]
    )


def build_kpis(metrics: dict):
    tr = metrics['total_return']
    ar = metrics['annual_return']
    sr = metrics['sharpe_ratio']
    dd = metrics['max_drawdown_pct']
    wr = metrics['win_rate']
    tt = metrics['total_trades']
    wt = metrics['winning_trades']
    lt = metrics['losing_trades']

    return [
        _kpi('总收益率',   f"{tr:.2%}",  C['green'] if tr >= 0 else C['red']),
        _kpi('年化收益率', f"{ar:.2%}",  C['green'] if ar >= 0 else C['red']),
        _kpi('夏普比率',   f"{sr:.2f}",  C['blue']  if sr >= 1 else C['yellow']),
        _kpi('最大回撤',   f"{dd:.2%}",  C['red']),
        _kpi('胜率',       f"{wr:.2%}",  C['green'] if wr >= 0.5 else C['yellow']),
        _kpi('总交易',     f"{tt} 笔",   C['muted']),
        _kpi('盈利',       f"{wt} 笔",   C['green']),
        _kpi('亏损',       f"{lt} 笔",   C['red']),
    ]


# ─────────────────────────────────────────────────────────
# Dash 应用布局
# ─────────────────────────────────────────────────────────
app = dash.Dash(__name__, title='量化交易仪表盘',
                suppress_callback_exceptions=True)

app.layout = html.Div(
    style={
        'backgroundColor': C['bg'], 'minHeight': '100vh',
        'fontFamily': "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif",
        'color': C['text'],
    },
    children=[
        dcc.Store(id='store'),

        # ── 顶部导航栏 ──────────────────────────────────
        html.Div(
            style={
                'backgroundColor': C['card'],
                'borderBottom': f"1px solid {C['border']}",
                'padding': '13px 24px',
                'display': 'flex', 'alignItems': 'center', 'gap': '12px',
            },
            children=[
                html.Div('📈', style={'fontSize': '18px'}),
                html.H2('量化交易仪表盘',
                        style={'margin': 0, 'fontSize': '17px',
                               'color': C['blue'], 'fontWeight': '600'}),
                html.Span(id='status-text',
                          style={'marginLeft': 'auto', 'color': C['muted'], 'fontSize': '12px'}),
            ]
        ),

        # ── 主体：左侧面板 + 右侧内容 ──────────────────
        html.Div(
            style={'display': 'flex', 'height': 'calc(100vh - 55px)'},
            children=[

                # 左侧控制面板
                html.Div(
                    style={
                        'width': '210px', 'flexShrink': 0,
                        'backgroundColor': C['card'],
                        'borderRight': f"1px solid {C['border']}",
                        'padding': '18px 14px', 'overflowY': 'auto',
                    },
                    children=[
                        html.P('回测参数', style={
                            'fontSize': '11px', 'color': C['muted'],
                            'textTransform': 'uppercase', 'letterSpacing': '1px',
                            'marginTop': 0, 'marginBottom': '14px',
                        }),

                        html.Label('策略', style=LABEL_STYLE),
                        dcc.Dropdown(
                            id='sel-strategy',
                            options=[
                                {'label': '双均线 (MA Cross)', 'value': 'ma_cross'},
                                {'label': 'RSI 均值回归', 'value': 'rsi'},
                                {'label': '价格突破', 'value': 'breakout'},
                            ],
                            value='ma_cross', clearable=False,
                            style={'marginBottom': '10px', 'fontSize': '13px',
                                   'backgroundColor': C['bg']},
                        ),

                        html.Label('合约代码', style=LABEL_STYLE),
                        dcc.Input(id='sel-symbol', value='rb2505', type='text',
                                  style=INPUT_STYLE, debounce=True),

                        html.Label('快线周期', style=LABEL_STYLE),
                        dcc.Slider(
                            id='sel-fast', min=3, max=30, step=1, value=10,
                            marks={3: '3', 10: '10', 20: '20', 30: '30'},
                            tooltip={'placement': 'bottom', 'always_visible': True},
                        ),
                        html.Div(style={'marginBottom': '10px'}),

                        html.Label('慢线周期', style=LABEL_STYLE),
                        dcc.Slider(
                            id='sel-slow', min=10, max=120, step=5, value=20,
                            marks={10: '10', 60: '60', 120: '120'},
                            tooltip={'placement': 'bottom', 'always_visible': True},
                        ),
                        html.Div(style={'marginBottom': '10px'}),

                        html.Label('初始资金 (元)', style=LABEL_STYLE),
                        dcc.Input(id='sel-capital', value='1000000', type='number',
                                  style=INPUT_STYLE),

                        html.Label('开始日期', style=LABEL_STYLE),
                        dcc.Input(id='sel-start', value='2023-01-01', type='text',
                                  placeholder='YYYY-MM-DD', style=INPUT_STYLE, debounce=True),

                        html.Label('结束日期', style=LABEL_STYLE),
                        dcc.Input(id='sel-end', value='2024-12-31', type='text',
                                  placeholder='YYYY-MM-DD', style=INPUT_STYLE, debounce=True),

                        html.Button(
                            '▶  运行回测',
                            id='btn-run', n_clicks=0,
                            style={
                                'width': '100%', 'padding': '10px 0',
                                'backgroundColor': C['blue'], 'color': 'white',
                                'border': 'none', 'borderRadius': '6px',
                                'fontSize': '13px', 'fontWeight': '600',
                                'cursor': 'pointer', 'marginTop': '6px',
                                'letterSpacing': '0.5px',
                            },
                        ),

                        # 运行中提示
                        dcc.Loading(
                            type='circle', color=C['blue'],
                            children=html.Div(id='loading-dummy',
                                              style={'height': '4px', 'marginTop': '8px'}),
                        ),
                    ]
                ),

                # 右侧主内容区
                html.Div(
                    style={'flex': 1, 'overflowY': 'auto', 'padding': '18px 20px'},
                    children=[
                        # KPI 卡片行
                        html.Div(id='kpi-row',
                                 style={'display': 'flex', 'gap': '10px', 'marginBottom': '16px',
                                        'flexWrap': 'wrap'}),

                        # Tab 区域
                        dcc.Tabs(
                            id='tabs', value='tab-equity',
                            colors={'border': C['border'], 'primary': C['blue'],
                                    'background': C['card']},
                            children=[
                                dcc.Tab(label='资产净值 / 回撤', value='tab-equity',
                                        style={'color': C['muted'], 'backgroundColor': C['card'],
                                               'border': f"1px solid {C['border']}",
                                               'padding': '8px 16px'},
                                        selected_style={'color': C['text'], 'backgroundColor': C['bg'],
                                                        'borderTop': f"2px solid {C['blue']}",
                                                        'padding': '8px 16px'}),
                                dcc.Tab(label='K线图 & 买卖点', value='tab-candle',
                                        style={'color': C['muted'], 'backgroundColor': C['card'],
                                               'border': f"1px solid {C['border']}",
                                               'padding': '8px 16px'},
                                        selected_style={'color': C['text'], 'backgroundColor': C['bg'],
                                                        'borderTop': f"2px solid {C['blue']}",
                                                        'padding': '8px 16px'}),
                                dcc.Tab(label='收益率分析', value='tab-returns',
                                        style={'color': C['muted'], 'backgroundColor': C['card'],
                                               'border': f"1px solid {C['border']}",
                                               'padding': '8px 16px'},
                                        selected_style={'color': C['text'], 'backgroundColor': C['bg'],
                                                        'borderTop': f"2px solid {C['blue']}",
                                                        'padding': '8px 16px'}),
                                dcc.Tab(label='交易记录', value='tab-trades',
                                        style={'color': C['muted'], 'backgroundColor': C['card'],
                                               'border': f"1px solid {C['border']}",
                                               'padding': '8px 16px'},
                                        selected_style={'color': C['text'], 'backgroundColor': C['bg'],
                                                        'borderTop': f"2px solid {C['blue']}",
                                                        'padding': '8px 16px'}),
                            ],
                        ),

                        html.Div(id='tab-content', style={'marginTop': '12px'}),
                    ]
                ),
            ]
        ),
    ]
)


# ─────────────────────────────────────────────────────────
# 回调：执行回测
# ─────────────────────────────────────────────────────────
@app.callback(
    Output('store', 'data'),
    Output('status-text', 'children'),
    Output('loading-dummy', 'children'),
    Input('btn-run', 'n_clicks'),
    State('sel-strategy', 'value'),
    State('sel-symbol', 'value'),
    State('sel-fast', 'value'),
    State('sel-slow', 'value'),
    State('sel-capital', 'value'),
    State('sel-start', 'value'),
    State('sel-end', 'value'),
    prevent_initial_call=False,
)
def on_run(_, strategy, symbol, fast, slow, capital, start, end):
    try:
        engine, result, bars = exec_backtest(
            strategy or 'ma_cross',
            symbol or 'rb2505',
            fast or 10,
            slow or 20,
            float(capital or 1_000_000),
            start or '2023-01-01',
            end or '2024-12-31',
        )
        store = serialize(engine, result, bars, float(capital or 1_000_000), symbol)
        m = store['metrics']
        status = (
            f"回测完成  {datetime.now().strftime('%H:%M:%S')}"
            f"  |  总收益 {m['total_return']:.2%}"
            f"  |  夏普 {m['sharpe_ratio']:.2f}"
            f"  |  共 {m['total_trades']} 笔"
        )
        return store, status, ''
    except Exception as e:
        logger.exception('回测失败')
        return None, f'❌ 错误: {e}', ''


# ─────────────────────────────────────────────────────────
# 回调：更新 KPI 卡片
# ─────────────────────────────────────────────────────────
@app.callback(Output('kpi-row', 'children'), Input('store', 'data'))
def update_kpi(data):
    if not data:
        return []
    return build_kpis(data['metrics'])


# ─────────────────────────────────────────────────────────
# 回调：切换 Tab 内容
# ─────────────────────────────────────────────────────────
@app.callback(
    Output('tab-content', 'children'),
    Input('tabs', 'value'),
    Input('store', 'data'),
)
def update_tab(tab, data):
    placeholder = html.Div(
        '请点击「运行回测」开始',
        style={'textAlign': 'center', 'color': C['muted'],
               'marginTop': '100px', 'fontSize': '15px'},
    )

    if not data:
        return placeholder

    GRAPH_CFG = {'displayModeBar': True, 'scrollZoom': True,
                 'modeBarButtonsToRemove': ['select2d', 'lasso2d']}
    H = 'calc(100vh - 240px)'

    if tab == 'tab-equity':
        return dcc.Graph(figure=chart_equity(data), style={'height': H}, config=GRAPH_CFG)

    elif tab == 'tab-candle':
        return dcc.Graph(figure=chart_candle(data), style={'height': H}, config=GRAPH_CFG)

    elif tab == 'tab-returns':
        fig_hist, fig_heat, fig_sharpe = chart_returns(data)
        return html.Div([
            # 上半：直方图 + 热力图
            html.Div([
                dcc.Graph(figure=fig_hist, style={'height': '320px'},
                          config={'displayModeBar': False}),
                dcc.Graph(figure=fig_heat, style={'height': '320px'},
                          config={'displayModeBar': False}),
            ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr',
                      'gap': '12px', 'marginBottom': '12px'}),
            # 下半：滚动夏普
            dcc.Graph(figure=fig_sharpe, style={'height': '260px'},
                      config={'displayModeBar': False}),
        ])

    elif tab == 'tab-trades':
        trades = data.get('trades', [])
        if not trades:
            return html.Div('暂无交易记录',
                            style={'color': C['muted'], 'textAlign': 'center', 'marginTop': '60px'})

        df = pd.DataFrame(trades)

        # 统计摘要
        total = len(df)
        buys = len(df[df['direction'].str.lower() == 'long'])
        sells = total - buys

        summary = html.Div([
            html.Span(f'共 {total} 笔交易', style={'marginRight': '20px', 'color': C['muted']}),
            html.Span(f'买入 {buys} 笔', style={'color': C['red'], 'marginRight': '20px'}),
            html.Span(f'卖出 {sells} 笔', style={'color': C['green']}),
        ], style={'marginBottom': '12px', 'fontSize': '13px'})

        columns = [
            {'name': '时间', 'id': 'trade_time'},
            {'name': '合约', 'id': 'symbol'},
            {'name': '方向', 'id': 'direction_cn'},
            {'name': '价格 (元)', 'id': 'price'},
            {'name': '手数', 'id': 'volume'},
            {'name': '手续费 (元)', 'id': 'commission'},
        ]

        table = dash_table.DataTable(
            data=df.to_dict('records'),
            columns=columns,
            page_size=20,
            sort_action='native',
            filter_action='native',
            style_table={'overflowX': 'auto'},
            style_header={
                'backgroundColor': C['border'], 'color': C['text'],
                'fontWeight': '600', 'fontSize': '12px',
                'border': f"1px solid {C['border']}",
                'textAlign': 'center',
            },
            style_cell={
                'backgroundColor': C['card'], 'color': C['text'],
                'fontSize': '13px', 'border': f"1px solid {C['border']}",
                'padding': '8px 14px', 'textAlign': 'center',
            },
            style_data_conditional=[
                {'if': {'filter_query': '{direction_cn} contains "买入"'},
                 'color': C['red'], 'fontWeight': '600'},
                {'if': {'filter_query': '{direction_cn} contains "卖出"'},
                 'color': C['green'], 'fontWeight': '600'},
                {'if': {'row_index': 'odd'},
                 'backgroundColor': '#0d1117'},
            ],
            style_filter={
                'backgroundColor': C['bg'], 'color': C['text'],
                'border': f"1px solid {C['border']}",
            },
        )
        return html.Div([summary, table])

    return placeholder


# ─────────────────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    print()
    print('=' * 52)
    print('  量化交易可视化仪表盘')
    print('  访问: http://localhost:8050')
    print('  按 Ctrl+C 停止')
    print('=' * 52)
    print()
    app.run(debug=False, host='0.0.0.0', port=8050)
