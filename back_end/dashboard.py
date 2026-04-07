"""
量化交易系统 - 可视化仪表盘
运行: python dashboard.py
访问: http://localhost:8050
"""

import os
import sys
import json
import threading
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, List

import numpy as np
import pandas as pd

# 默认回测日期（相对今天）
_TODAY = datetime.now()
_BT_START = (_TODAY - timedelta(days=600)).strftime("%Y-%m-%d")
_BT_END = (_TODAY - timedelta(days=10)).strftime("%Y-%m-%d")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx, no_update
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data import DataManager
from src.strategy import create_strategy, Direction, Signal, OrderType
from src.backtest import BacktestEngine, BacktestConfig, BacktestResult
from src.trading import TradingEngine, SimulatedGateway, TradingStatus, AccountInfo, MarketData
from src.analysis import Analyzer

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 主题颜色
# ══════════════════════════════════════════════════════════════════════════════
C = {
    'bg':      '#0d1117',
    'card':    '#161b22',
    'border':  '#30363d',
    'text':    '#e6edf3',
    'muted':   '#8b949e',
    'green':   '#3fb950',
    'red':     '#f85149',
    'blue':    '#58a6ff',
    'yellow':  '#d29922',
    'purple':  '#bc8cff',
    'orange':  '#f0883e',
    'cyan':    '#39d0d8',
}

def _input_style(width='100%'):
    return {
        'width': width, 'boxSizing': 'border-box',
        'backgroundColor': '#0d1117', 'color': C['text'],
        'border': f"1px solid {C['border']}",
        'borderRadius': '6px', 'padding': '6px 10px',
        'fontSize': '13px', 'outline': 'none',
    }

def _btn(label, btn_id, color=C['blue'], outline=False, **kwargs):
    style = {
        'backgroundColor': 'transparent' if outline else color,
        'color': color if outline else '#0d1117',
        'border': f"1px solid {color}",
        'borderRadius': '6px', 'padding': '6px 16px',
        'cursor': 'pointer', 'fontSize': '13px', 'fontWeight': '600',
        'marginRight': '8px',
    }
    return html.Button(label, id=btn_id, style=style, **kwargs)

def _card(children, title=None, style=None):
    body = []
    if title:
        body.append(html.Div(title, style={
            'fontSize': '12px', 'color': C['muted'],
            'fontWeight': '600', 'marginBottom': '10px',
            'textTransform': 'uppercase', 'letterSpacing': '0.5px',
        }))
    body.extend(children if isinstance(children, list) else [children])
    base = {
        'backgroundColor': C['card'], 'border': f"1px solid {C['border']}",
        'borderRadius': '8px', 'padding': '16px',
    }
    if style:
        base.update(style)
    return html.Div(body, style=base)

def _label(text):
    return html.Label(text, style={
        'fontSize': '12px', 'color': C['muted'],
        'marginBottom': '4px', 'display': 'block',
    })

def _metric_card(label, value_id, color=C['text'], suffix=''):
    return html.Div([
        html.Div(label, style={'fontSize': '11px', 'color': C['muted'], 'marginBottom': '4px'}),
        html.Div([
            html.Span(id=value_id, style={'fontSize': '22px', 'fontWeight': '700', 'color': color}),
            html.Span(suffix, style={'fontSize': '12px', 'color': C['muted'], 'marginLeft': '4px'}) if suffix else None,
        ], style={'display': 'flex', 'alignItems': 'baseline'}),
    ], style={
        'backgroundColor': C['card'], 'border': f"1px solid {C['border']}",
        'borderRadius': '8px', 'padding': '14px 18px', 'flex': '1',
    })

def _table_style():
    return {
        'style_table': {'overflowX': 'auto'},
        'style_header': {
            'backgroundColor': '#1c2128', 'color': C['muted'],
            'fontWeight': '600', 'fontSize': '12px',
            'border': f"1px solid {C['border']}",
        },
        'style_cell': {
            'backgroundColor': C['card'], 'color': C['text'],
            'fontSize': '12px', 'padding': '8px 12px',
            'border': f"1px solid {C['border']}",
            'textAlign': 'left',
        },
        'style_data_conditional': [
            {'if': {'row_index': 'odd'}, 'backgroundColor': '#0d1117'},
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 全局应用状态
# ══════════════════════════════════════════════════════════════════════════════
class AppState:
    """线程安全的全局状态单例"""
    _lock = threading.Lock()

    def __init__(self):
        self.gateway = None
        self.engine: Optional[TradingEngine] = None
        self.backtest_result: Optional[BacktestResult] = None
        self.backtest_engine: Optional[BacktestEngine] = None
        self.ticks: Dict[str, MarketData] = {}
        self.tick_history: Dict[str, deque] = {}
        self.log_buffer: deque = deque(maxlen=300)
        self.subscribed: List[str] = []
        self._connected = False

    @property
    def connected(self) -> bool:
        if self.gateway is None:
            return False
        return self.gateway.status == TradingStatus.CONNECTED

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log_buffer.append(f"[{ts}] {msg}")

    def get_logs(self) -> str:
        with self._lock:
            return "\n".join(self.log_buffer)

    def get_account(self) -> Optional[AccountInfo]:
        if self.gateway:
            return self.gateway.account
        return None

    def get_positions(self) -> dict:
        if self.gateway:
            return self.gateway.positions
        return {}

    def get_orders(self) -> dict:
        if self.gateway:
            return self.gateway.orders
        return {}


STATE = AppState()


# ══════════════════════════════════════════════════════════════════════════════
# 布局工具函数
# ══════════════════════════════════════════════════════════════════════════════
def _empty_figure(msg="暂无数据"):
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=C['card'], plot_bgcolor=C['card'],
        font_color=C['muted'],
        annotations=[{
            'text': msg, 'xref': 'paper', 'yref': 'paper',
            'x': 0.5, 'y': 0.5, 'showarrow': False,
            'font': {'size': 14, 'color': C['muted']},
        }],
        margin={'l': 20, 'r': 20, 't': 30, 'b': 20},
        xaxis={'visible': False}, yaxis={'visible': False},
    )
    return fig


def _chart_layout(fig, title='', height=300):
    fig.update_layout(
        paper_bgcolor=C['card'], plot_bgcolor=C['bg'],
        font=dict(color=C['text'], size=12),
        title=dict(text=title, font=dict(size=13, color=C['muted']), x=0.01),
        margin=dict(l=50, r=20, t=40, b=40),
        height=height,
        legend=dict(bgcolor='rgba(0,0,0,0)', font=dict(size=11)),
        xaxis=dict(gridcolor=C['border'], showgrid=True),
        yaxis=dict(gridcolor=C['border'], showgrid=True),
        hovermode='x unified',
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: 登录
# ══════════════════════════════════════════════════════════════════════════════
# 经纪商连接预设（生产环境）
# AppID: client_TraderMaster_v1.0.0  AuthCode: 20260324LHJYMHBG
# 交易端口: 42205  行情端口: 42213  BrokerID: 2071
_BROKER_APP_ID   = 'client_TraderMaster_v1.0.0'
_BROKER_AUTH     = '20260324LHJYMHBG'
_BROKER_ID       = '2071'

BROKER_PRESETS = {
    'telecom_1': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://114.94.128.1:42205',
        'md_server': 'tcp://114.94.128.1:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
    'unicom_1': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://140.206.34.161:42205',
        'md_server': 'tcp://140.206.34.161:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
    'telecom_2': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://114.94.128.5:42205',
        'md_server': 'tcp://114.94.128.5:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
    'unicom_2': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://140.206.34.165:42205',
        'md_server': 'tcp://140.206.34.165:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
    'telecom_3': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://114.94.128.6:42205',
        'md_server': 'tcp://114.94.128.6:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
    'unicom_3': {
        'broker_id': _BROKER_ID,
        'td_server': 'tcp://140.206.34.166:42205',
        'md_server': 'tcp://140.206.34.166:42213',
        'app_id':    _BROKER_APP_ID,
        'auth_code': _BROKER_AUTH,
    },
}


def _tab_login():
    def _field(fid, label, placeholder='', password=False, default=''):
        return html.Div([
            _label(label),
            dcc.Input(
                id=fid, type='password' if password else 'text',
                placeholder=placeholder, value=default,
                style={**_input_style(), 'marginBottom': '10px'},
                debounce=True,
            ),
        ])

    return html.Div([
        html.Div([
            # 左列：连接表单
            html.Div([
                _card([
                    # 快速切换服务器线路
                    html.Div([
                        html.Span("切换线路：", style={'fontSize': '12px', 'color': C['muted'], 'marginRight': '8px'}),
                        _btn("电信1", "btn-preset-7x24",     color=C['cyan'],   outline=True),
                        _btn("联通1", "btn-preset-trading",  color=C['blue'],   outline=True),
                        _btn("电信2", "btn-preset-tel2",     color=C['cyan'],   outline=True),
                        _btn("联通2", "btn-preset-uni2",     color=C['blue'],   outline=True),
                        _btn("电信3", "btn-preset-tel3",     color=C['cyan'],   outline=True),
                        _btn("联通3", "btn-preset-uni3",     color=C['blue'],   outline=True),
                    ], style={'marginBottom': '16px', 'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'gap': '6px'}),

                    html.Div([
                        html.Div([
                            _field("inp-broker-id", "经纪商 ID (BrokerID)", "2071", default=_BROKER_ID),
                        ], style={'flex': '1', 'minWidth': '120px'}),
                        html.Div([
                            _field("inp-username", "账号 (UserID)", "请输入账号"),
                        ], style={'flex': '1', 'minWidth': '120px'}),
                        html.Div([
                            _field("inp-password", "密码", "请输入密码", password=True),
                        ], style={'flex': '1', 'minWidth': '120px'}),
                    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),

                    html.Div([
                        html.Div([
                            _field("inp-td-server", "交易前置地址 (端口 42205)", "tcp://host:42205",
                                   default="tcp://114.94.128.1:42205"),
                        ], style={'flex': '2'}),
                        html.Div([
                            _field("inp-md-server", "行情前置地址 (端口 42213)", "tcp://host:42213",
                                   default="tcp://114.94.128.1:42213"),
                        ], style={'flex': '2'}),
                    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),

                    html.Div([
                        html.Div([
                            _field("inp-app-id", "AppID", _BROKER_APP_ID,
                                   default=_BROKER_APP_ID),
                        ], style={'flex': '1'}),
                        html.Div([
                            _field("inp-auth-code", "AuthCode", _BROKER_AUTH,
                                   default=_BROKER_AUTH),
                        ], style={'flex': '1'}),
                    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),

                    html.Div([
                        _btn("连接", "btn-connect", color=C['green']),
                        _btn("断开", "btn-disconnect", color=C['red'], outline=True),
                    ], style={'marginTop': '8px'}),
                ], title="CTP 连接配置"),

                # 状态卡片
                _card([
                    html.Div([
                        html.Div(id="login-status-dot", style={
                            'width': '12px', 'height': '12px', 'borderRadius': '50%',
                            'backgroundColor': C['red'], 'marginRight': '10px', 'flexShrink': '0',
                        }),
                        html.Span(id="login-status-text", children="未连接",
                                  style={'fontSize': '14px', 'color': C['muted']}),
                    ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '12px'}),

                    html.Div([
                        html.Div([
                            html.Div("账号", style={'fontSize': '11px', 'color': C['muted']}),
                            html.Div(id="login-info-user", children="—", style={'fontSize': '13px', 'color': C['text']}),
                        ], style={'flex': '1'}),
                        html.Div([
                            html.Div("交易日", style={'fontSize': '11px', 'color': C['muted']}),
                            html.Div(id="login-info-day", children="—", style={'fontSize': '13px', 'color': C['text']}),
                        ], style={'flex': '1'}),
                        html.Div([
                            html.Div("网关", style={'fontSize': '11px', 'color': C['muted']}),
                            html.Div(id="login-info-gw", children="—", style={'fontSize': '13px', 'color': C['text']}),
                        ], style={'flex': '1'}),
                    ], style={'display': 'flex'}),
                ], title="连接状态"),
            ], style={'flex': '1', 'minWidth': '320px'}),

            # 右列：日志
            html.Div([
                _card([
                    html.Pre(
                        id="login-log",
                        children="等待连接...",
                        style={
                            'backgroundColor': '#0d1117', 'color': '#8b949e',
                            'fontSize': '12px', 'padding': '12px',
                            'borderRadius': '6px', 'height': '420px',
                            'overflowY': 'auto', 'fontFamily': 'monospace',
                            'margin': '0', 'whiteSpace': 'pre-wrap',
                        },
                    ),
                ], title="连接日志"),
            ], style={'flex': '1', 'minWidth': '320px'}),
        ], style={'display': 'flex', 'gap': '16px', 'flexWrap': 'wrap'}),
    ], style={'padding': '16px'})


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: 行情
# ══════════════════════════════════════════════════════════════════════════════
def _tab_market():
    ts = _table_style()
    return html.Div([
        # 订阅栏
        _card([
            html.Div([
                html.Div([
                    _label("订阅合约（逗号分隔）"),
                    dcc.Input(
                        id="inp-subscribe", type="text",
                        placeholder="如: IF2409,rb2501,cu2410",
                        style={**_input_style(), 'width': '360px'},
                    ),
                ]),
                html.Div([
                    html.Br(),
                    _btn("订阅", "btn-subscribe", color=C['blue']),
                ]),
            ], style={'display': 'flex', 'gap': '16px', 'alignItems': 'flex-end'}),
        ], style={'marginBottom': '16px'}),

        html.Div([
            # 行情表格
            html.Div([
                _card([
                    dash_table.DataTable(
                        id="table-ticks",
                        columns=[
                            {'name': '合约', 'id': 'symbol'},
                            {'name': '最新价', 'id': 'last'},
                            {'name': '涨跌', 'id': 'change'},
                            {'name': '涨跌幅', 'id': 'pct'},
                            {'name': '买一', 'id': 'bid'},
                            {'name': '卖一', 'id': 'ask'},
                            {'name': '成交量', 'id': 'volume'},
                            {'name': '更新时间', 'id': 'time'},
                        ],
                        data=[],
                        row_selectable='single',
                        selected_rows=[],
                        **ts,
                        style_cell={**ts['style_cell'], 'minWidth': '80px'},
                        style_data_conditional=[
                            *ts['style_data_conditional'],
                            {'if': {'filter_query': '{change} > 0', 'column_id': 'change'},
                             'color': C['green']},
                            {'if': {'filter_query': '{change} < 0', 'column_id': 'change'},
                             'color': C['red']},
                        ],
                    ),
                ], title="实时行情"),
            ], style={'flex': '1'}),

            # Tick 走势图
            html.Div([
                _card([
                    dcc.Graph(
                        id="chart-tick",
                        figure=_empty_figure("选择合约查看走势"),
                        config={'displayModeBar': False},
                    ),
                ], title="Tick 走势"),
            ], style={'flex': '1'}),
        ], style={'display': 'flex', 'gap': '16px', 'flexWrap': 'wrap'}),
    ], style={'padding': '16px'})


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: 回测
# ══════════════════════════════════════════════════════════════════════════════
def _tab_backtest():
    ts = _table_style()
    return html.Div([
        html.Div([
            # 左侧参数面板
            html.Div([
                _card([
                    html.Div([
                        html.Div([
                            _label("策略"),
                            dcc.Dropdown(
                                id="bt-strategy",
                                options=[
                                    {'label': '双均线 MA Cross', 'value': 'ma_cross'},
                                    {'label': 'RSI 均值回归', 'value': 'rsi'},
                                    {'label': '通道突破 Breakout', 'value': 'breakout'},
                                ],
                                value='ma_cross',
                                clearable=False,
                                style={'backgroundColor': C['bg'], 'color': C['text'], 'fontSize': '13px'},
                            ),
                        ], style={'marginBottom': '10px'}),

                        html.Div([
                            _label("合约"),
                            dcc.Input(id="bt-symbol", value="IF9999", style=_input_style()),
                        ], style={'marginBottom': '10px'}),

                        html.Div([
                            html.Div([
                                _label("快线周期"),
                                dcc.Input(id="bt-fast", type="number", value=10, min=2, style=_input_style()),
                            ], style={'flex': '1'}),
                            html.Div([
                                _label("慢线周期"),
                                dcc.Input(id="bt-slow", type="number", value=30, min=3, style=_input_style()),
                            ], style={'flex': '1'}),
                        ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '10px'}),

                        html.Div([
                            _label("初始资金"),
                            dcc.Input(id="bt-capital", type="number", value=1000000, style=_input_style()),
                        ], style={'marginBottom': '10px'}),

                        html.Div([
                        html.Div([
                            _label("开始日期"),
                            dcc.Input(id="bt-start", value=_BT_START, style=_input_style()),
                        ], style={'flex': '1'}),
                        html.Div([
                            _label("结束日期"),
                            dcc.Input(id="bt-end", value=_BT_END, style=_input_style()),
                        ], style={'flex': '1'}),
                        ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '16px'}),

                        _btn("运行回测", "btn-run-backtest", color=C['green']),
                    ]),
                ], title="回测参数"),

                # 指标卡片
                html.Div([
                    html.Div([
                        html.Div("总收益率", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-return", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['blue']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                    html.Div([
                        html.Div("年化收益", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-annual", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['green']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                    html.Div([
                        html.Div("最大回撤", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-dd", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['red']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                ], style={'display': 'flex', 'gap': '10px', 'marginTop': '12px'}),

                html.Div([
                    html.Div([
                        html.Div("夏普比率", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-sharpe", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['yellow']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                    html.Div([
                        html.Div("胜率", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-winrate", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['purple']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                    html.Div([
                        html.Div("总交易次数", style={'fontSize': '11px', 'color': C['muted']}),
                        html.Div(id="bt-metric-trades", children="—",
                                 style={'fontSize': '20px', 'fontWeight': '700', 'color': C['orange']}),
                    ], style={'flex': '1', 'padding': '12px', 'backgroundColor': C['card'],
                              'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
                ], style={'display': 'flex', 'gap': '10px', 'marginTop': '10px'}),
            ], style={'width': '280px', 'flexShrink': '0'}),

            # 右侧图表
            html.Div([
                _card([
                    dcc.Graph(id="bt-equity-chart",
                              figure=_empty_figure("运行回测后显示权益曲线"),
                              config={'displayModeBar': True},
                              style={'height': '320px'}),
                ], title="权益曲线 & 回撤", style={'marginBottom': '16px'}),

                _card([
                    dash_table.DataTable(
                        id="bt-trades-table",
                        columns=[
                            {'name': '时间', 'id': 'time'},
                            {'name': '合约', 'id': 'symbol'},
                            {'name': '方向', 'id': 'direction'},
                            {'name': '成交价', 'id': 'price'},
                            {'name': '手数', 'id': 'volume'},
                            {'name': '盈亏', 'id': 'pnl'},
                            {'name': '手续费', 'id': 'commission'},
                        ],
                        data=[],
                        page_size=10,
                        **ts,
                    ),
                ], title="成交明细"),
            ], style={'flex': '1', 'minWidth': '0'}),
        ], style={'display': 'flex', 'gap': '16px', 'alignItems': 'flex-start'}),
    ], style={'padding': '16px'})


# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: 交易
# ══════════════════════════════════════════════════════════════════════════════
def _tab_trading():
    ts = _table_style()
    return html.Div([
        # 账户资金
        html.Div([
            html.Div([
                html.Div("账户余额", style={'fontSize': '11px', 'color': C['muted']}),
                html.Div(id="td-balance", children="—",
                         style={'fontSize': '22px', 'fontWeight': '700', 'color': C['text']}),
            ], style={'flex': '1', 'padding': '14px 18px', 'backgroundColor': C['card'],
                      'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
            html.Div([
                html.Div("可用资金", style={'fontSize': '11px', 'color': C['muted']}),
                html.Div(id="td-available", children="—",
                         style={'fontSize': '22px', 'fontWeight': '700', 'color': C['green']}),
            ], style={'flex': '1', 'padding': '14px 18px', 'backgroundColor': C['card'],
                      'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
            html.Div([
                html.Div("占用保证金", style={'fontSize': '11px', 'color': C['muted']}),
                html.Div(id="td-margin", children="—",
                         style={'fontSize': '22px', 'fontWeight': '700', 'color': C['yellow']}),
            ], style={'flex': '1', 'padding': '14px 18px', 'backgroundColor': C['card'],
                      'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
            html.Div([
                html.Div("持仓盈亏", style={'fontSize': '11px', 'color': C['muted']}),
                html.Div(id="td-pnl", children="—",
                         style={'fontSize': '22px', 'fontWeight': '700', 'color': C['blue']}),
            ], style={'flex': '1', 'padding': '14px 18px', 'backgroundColor': C['card'],
                      'borderRadius': '8px', 'border': f"1px solid {C['border']}"}),
        ], style={'display': 'flex', 'gap': '12px', 'marginBottom': '16px', 'flexWrap': 'wrap'}),

        html.Div([
            # 持仓 + 下单面板
            html.Div([
                _card([
                    dash_table.DataTable(
                        id="td-positions-table",
                        columns=[
                            {'name': '合约', 'id': 'symbol'},
                            {'name': '方向', 'id': 'direction'},
                            {'name': '数量', 'id': 'volume'},
                            {'name': '均价', 'id': 'price'},
                            {'name': '浮盈', 'id': 'pnl'},
                        ],
                        data=[],
                        **ts,
                    ),
                ], title="持仓", style={'marginBottom': '16px'}),

                # 下单面板
                _card([
                    html.Div([
                        html.Div([
                            _label("合约代码"),
                            dcc.Input(id="td-order-symbol", type="text", placeholder="如 IF2409",
                                      style=_input_style()),
                        ], style={'flex': '2'}),
                        html.Div([
                            _label("方向"),
                            dcc.Dropdown(
                                id="td-order-direction",
                                options=[
                                    {'label': '买多', 'value': 'long'},
                                    {'label': '卖空', 'value': 'short'},
                                ],
                                value='long', clearable=False,
                                style={'backgroundColor': C['bg'], 'color': C['text'], 'fontSize': '13px'},
                            ),
                        ], style={'flex': '1'}),
                    ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '10px'}),

                    html.Div([
                        html.Div([
                            _label("价格"),
                            dcc.Input(id="td-order-price", type="number", placeholder="0.0",
                                      style=_input_style()),
                        ], style={'flex': '1'}),
                        html.Div([
                            _label("数量（手）"),
                            dcc.Input(id="td-order-volume", type="number", value=1, min=1,
                                      style=_input_style()),
                        ], style={'flex': '1'}),
                        html.Div([
                            _label("类型"),
                            dcc.Dropdown(
                                id="td-order-type",
                                options=[
                                    {'label': '限价', 'value': 'limit'},
                                    {'label': '市价', 'value': 'market'},
                                ],
                                value='limit', clearable=False,
                                style={'backgroundColor': C['bg'], 'color': C['text'], 'fontSize': '13px'},
                            ),
                        ], style={'flex': '1'}),
                    ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '14px'}),

                    html.Div([
                        _btn("下 单", "btn-send-order", color=C['green']),
                        _btn("撤单（选中）", "btn-cancel-order", color=C['red'], outline=True),
                    ]),
                    html.Div(id="td-order-msg", style={'marginTop': '8px', 'fontSize': '12px', 'color': C['yellow']}),
                ], title="手动下单"),
            ], style={'width': '320px', 'flexShrink': '0'}),

            # 委托列表
            html.Div([
                _card([
                    dash_table.DataTable(
                        id="td-orders-table",
                        columns=[
                            {'name': '委托号', 'id': 'order_id'},
                            {'name': '合约', 'id': 'symbol'},
                            {'name': '方向', 'id': 'direction'},
                            {'name': '价格', 'id': 'price'},
                            {'name': '数量', 'id': 'volume'},
                            {'name': '已成', 'id': 'traded'},
                            {'name': '状态', 'id': 'status'},
                        ],
                        data=[],
                        row_selectable='single',
                        selected_rows=[],
                        page_size=15,
                        **ts,
                        style_data_conditional=[
                            *ts['style_data_conditional'],
                            {'if': {'filter_query': '{status} = "filled"', 'column_id': 'status'}, 'color': C['green']},
                            {'if': {'filter_query': '{status} = "cancelled"', 'column_id': 'status'}, 'color': C['muted']},
                            {'if': {'filter_query': '{status} = "rejected"', 'column_id': 'status'}, 'color': C['red']},
                        ],
                    ),
                ], title="委托记录"),
            ], style={'flex': '1', 'minWidth': '0'}),
        ], style={'display': 'flex', 'gap': '16px', 'alignItems': 'flex-start'}),
    ], style={'padding': '16px'})


# ══════════════════════════════════════════════════════════════════════════════
# Tab 5: 分析
# ══════════════════════════════════════════════════════════════════════════════
def _tab_analysis():
    return html.Div([
        html.Div([
            # 主图：权益曲线 + 回撤
            html.Div([
                _card([
                    dcc.Graph(
                        id="ana-equity-chart",
                        figure=_empty_figure("请先运行回测"),
                        config={'displayModeBar': True},
                        style={'height': '300px'},
                    ),
                ], title="权益曲线 & 基准对比", style={'marginBottom': '16px'}),
                _card([
                    dcc.Graph(
                        id="ana-drawdown-chart",
                        figure=_empty_figure("请先运行回测"),
                        config={'displayModeBar': False},
                        style={'height': '200px'},
                    ),
                ], title="回撤分析"),
            ], style={'flex': '2', 'minWidth': '0'}),

            # 右侧：风险指标 + 月度收益
            html.Div([
                _card([
                    html.Div(id="ana-risk-panel", children=html.Div(
                        "运行回测后显示指标",
                        style={'color': C['muted'], 'fontSize': '13px'}
                    )),
                ], title="风险与绩效指标", style={'marginBottom': '16px'}),

                _card([
                    dcc.Graph(
                        id="ana-monthly-chart",
                        figure=_empty_figure("月度收益热力图"),
                        config={'displayModeBar': False},
                        style={'height': '220px'},
                    ),
                ], title="月度收益"),
            ], style={'width': '340px', 'flexShrink': '0'}),
        ], style={'display': 'flex', 'gap': '16px', 'flexWrap': 'wrap'}),
    ], style={'padding': '16px'})


# ══════════════════════════════════════════════════════════════════════════════
# 主布局
# ══════════════════════════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    title="量化交易系统",
    suppress_callback_exceptions=True,
)

app.layout = html.Div([
    # 顶部导航栏
    html.Div([
        html.Div([
            html.Span("📈", style={'fontSize': '18px', 'marginRight': '8px'}),
            html.Span("量化交易系统", style={
                'fontSize': '15px', 'fontWeight': '700', 'color': C['text'],
                'marginRight': '32px',
            }),
        ], style={'display': 'flex', 'alignItems': 'center'}),

        dcc.Tabs(
            id="main-tabs",
            value="tab-login",
            children=[
                dcc.Tab(label="登 录", value="tab-login"),
                dcc.Tab(label="行 情", value="tab-market"),
                dcc.Tab(label="回 测", value="tab-backtest"),
                dcc.Tab(label="交 易", value="tab-trading"),
                dcc.Tab(label="分 析", value="tab-analysis"),
            ],
            style={'flex': '1'},
            colors={'border': C['border'], 'primary': C['blue'], 'background': C['card']},
        ),
    ], style={
        'display': 'flex', 'alignItems': 'center',
        'backgroundColor': C['card'],
        'borderBottom': f"1px solid {C['border']}",
        'padding': '0 20px', 'height': '48px',
    }),

    # 标签页内容区域
    html.Div(id="tab-content", style={
        'backgroundColor': C['bg'], 'minHeight': 'calc(100vh - 48px)',
        'color': C['text'],
    }),

    # 定时器
    dcc.Interval(id="interval-fast", interval=1000, n_intervals=0),   # 1s 行情/交易
    dcc.Interval(id="interval-slow", interval=5000, n_intervals=0),   # 5s 状态
], style={
    'backgroundColor': C['bg'],
    'fontFamily': "'Segoe UI', system-ui, sans-serif",
    'color': C['text'],
    'margin': '0', 'padding': '0',
})


# ══════════════════════════════════════════════════════════════════════════════
# 路由回调
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(Output("tab-content", "children"), Input("main-tabs", "value"))
def render_tab(tab):
    if tab == "tab-login":    return _tab_login()
    if tab == "tab-market":   return _tab_market()
    if tab == "tab-backtest": return _tab_backtest()
    if tab == "tab-trading":  return _tab_trading()
    if tab == "tab-analysis": return _tab_analysis()
    return html.Div("未知页面")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1 回调：登录
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(
    Output("inp-broker-id", "value"),
    Output("inp-td-server", "value"),
    Output("inp-md-server", "value"),
    Output("inp-app-id", "value"),
    Output("inp-auth-code", "value"),
    Input("btn-preset-7x24",    "n_clicks"),
    Input("btn-preset-trading", "n_clicks"),
    Input("btn-preset-tel2",    "n_clicks"),
    Input("btn-preset-uni2",    "n_clicks"),
    Input("btn-preset-tel3",    "n_clicks"),
    Input("btn-preset-uni3",    "n_clicks"),
    prevent_initial_call=True,
)
def fill_preset(n1, n2, n3, n4, n5, n6):
    preset_map = {
        "btn-preset-7x24":    BROKER_PRESETS['telecom_1'],
        "btn-preset-trading": BROKER_PRESETS['unicom_1'],
        "btn-preset-tel2":    BROKER_PRESETS['telecom_2'],
        "btn-preset-uni2":    BROKER_PRESETS['unicom_2'],
        "btn-preset-tel3":    BROKER_PRESETS['telecom_3'],
        "btn-preset-uni3":    BROKER_PRESETS['unicom_3'],
    }
    preset = preset_map.get(ctx.triggered_id, BROKER_PRESETS['telecom_1'])
    return (
        preset['broker_id'],
        preset['td_server'],
        preset['md_server'],
        preset['app_id'],
        preset['auth_code'],
    )


@app.callback(
    Output("login-status-dot", "style"),
    Output("login-status-text", "children"),
    Output("login-info-user", "children"),
    Output("login-info-gw", "children"),
    Output("login-log", "children"),
    Input("btn-connect", "n_clicks"),
    Input("btn-disconnect", "n_clicks"),
    Input("interval-slow", "n_intervals"),
    State("inp-broker-id", "value"),
    State("inp-td-server", "value"),
    State("inp-md-server", "value"),
    State("inp-username", "value"),
    State("inp-password", "value"),
    State("inp-app-id", "value"),
    State("inp-auth-code", "value"),
    prevent_initial_call=True,
)
def handle_connect(n_connect, n_disconnect, n_interval,
                   broker_id, td_server, md_server,
                   username, password, app_id, auth_code):
    trig = ctx.triggered_id
    dot_base = {'width': '12px', 'height': '12px', 'borderRadius': '50%',
                'marginRight': '10px', 'flexShrink': '0'}

    if trig == "btn-connect" and n_connect:
        if not username or not password:
            STATE.log("错误：请填写账号和密码")
            return (
                {**dot_base, 'backgroundColor': C['red']},
                "参数不完整",
                "—", "—",
                STATE.get_logs(),
            )
        try:
            from src.trading.ctp_native_gateway import CTPNativeGateway
            gw = CTPNativeGateway()

            def _on_account(acc):
                STATE.log(f"账户更新: 余额={acc.balance:.2f} 可用={acc.available:.2f}")

            gw.on_account_callback = _on_account
            gw.on_error_callback = lambda e, ctx_: STATE.log(f"错误: {e}")

            STATE.log(f"正在连接 {td_server}...")
            cfg = {
                'broker_id': broker_id or '9999',
                'td_server': td_server or '',
                'md_server': md_server or '',
                'username': username,
                'password': password,
                'app_id': app_id or '',
                'auth_code': auth_code or '',
            }

            def _connect_bg():
                ok = gw.connect(cfg)
                if ok:
                    STATE.gateway = gw
                    STATE.log("✓ 连接成功，交易就绪")
                else:
                    STATE.log("✗ 连接失败，请检查账号/网络")

            threading.Thread(target=_connect_bg, daemon=True).start()

            return (
                {**dot_base, 'backgroundColor': C['yellow']},
                "连接中...",
                username, "CTP",
                STATE.get_logs(),
            )
        except Exception as e:
            STATE.log(f"连接异常: {e}")
            return (
                {**dot_base, 'backgroundColor': C['red']},
                f"连接异常",
                "—", "—",
                STATE.get_logs(),
            )

    if trig == "btn-disconnect" and n_disconnect:
        if STATE.gateway:
            STATE.gateway.disconnect()
            STATE.gateway = None
            STATE.log("已断开连接")
        return (
            {**dot_base, 'backgroundColor': C['red']},
            "未连接",
            "—", "—",
            STATE.get_logs(),
        )

    # interval 刷新状态
    if STATE.gateway:
        s = STATE.gateway.status
        if s == TradingStatus.CONNECTED:
            color = C['green']
            text = "已连接 · 就绪"
            user = STATE.gateway.username
        elif s == TradingStatus.CONNECTING:
            color = C['yellow']
            text = "连接中..."
            user = STATE.gateway.username
        elif s == TradingStatus.ERROR:
            color = C['red']
            text = "连接错误"
            user = STATE.gateway.username
        else:
            color = C['muted']
            text = "已断开"
            user = "—"
        return ({**dot_base, 'backgroundColor': color}, text, user, "CTP", STATE.get_logs())

    return (
        {**dot_base, 'backgroundColor': C['red']},
        "未连接",
        "—", "—",
        STATE.get_logs(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2 回调：行情
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(
    Output("table-ticks", "data"),
    Output("chart-tick", "figure"),
    Input("btn-subscribe", "n_clicks"),
    Input("interval-fast", "n_intervals"),
    State("inp-subscribe", "value"),
    State("table-ticks", "selected_rows"),
    State("table-ticks", "data"),
    prevent_initial_call=True,
)
def update_market(n_sub, n_interval, symbols_str, selected_rows, current_data):
    trig = ctx.triggered_id

    if trig == "btn-subscribe" and n_sub and symbols_str:
        symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]
        if STATE.gateway and STATE.connected:
            STATE.gateway.subscribe_market_data(symbols)
            STATE.subscribed = list(set(STATE.subscribed + symbols))
            STATE.log(f"已订阅: {symbols}")
        else:
            # 无 CTP 连接时模拟注册，后续手动 tick 更新
            STATE.subscribed = list(set(STATE.subscribed + symbols))

    # 构建行情表格
    rows = []
    for sym in STATE.subscribed:
        tick = STATE.ticks.get(sym) or (STATE.gateway.latest_ticks.get(sym) if STATE.gateway else None)
        if tick:
            rows.append({
                'symbol': sym,
                'last': f"{tick.last_price:.2f}",
                'change': round(tick.last_price - tick.bid_price_1, 2),
                'pct': "—",
                'bid': f"{tick.bid_price_1:.2f}",
                'ask': f"{tick.ask_price_1:.2f}",
                'volume': tick.volume,
                'time': tick.timestamp.strftime("%H:%M:%S"),
            })
        else:
            rows.append({
                'symbol': sym, 'last': '—', 'change': 0,
                'pct': '—', 'bid': '—', 'ask': '—',
                'volume': '—', 'time': '—',
            })

    # 更新 tick 历史
    if STATE.gateway:
        for sym, tick in STATE.gateway.latest_ticks.items():
            if sym not in STATE.tick_history:
                STATE.tick_history[sym] = deque(maxlen=300)
            h = STATE.tick_history[sym]
            if not h or h[-1].timestamp != tick.timestamp:
                h.append(tick)

    # Tick 走势图
    selected_sym = None
    if selected_rows and rows:
        idx = selected_rows[0]
        if idx < len(rows):
            selected_sym = rows[idx]['symbol']

    if selected_sym and selected_sym in STATE.tick_history and STATE.tick_history[selected_sym]:
        hist = list(STATE.tick_history[selected_sym])
        times = [t.timestamp for t in hist]
        prices = [t.last_price for t in hist]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=times, y=prices, mode='lines',
            line=dict(color=C['blue'], width=1.5),
            name=selected_sym,
            fill='tozeroy',
            fillcolor='rgba(88,166,255,0.08)',
        ))
        _chart_layout(fig, f"{selected_sym} Tick 走势", height=280)
    else:
        fig = _empty_figure("选择合约查看走势")

    return rows, fig


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3 回调：回测
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(
    Output("bt-equity-chart", "figure"),
    Output("bt-trades-table", "data"),
    Output("bt-metric-return", "children"),
    Output("bt-metric-annual", "children"),
    Output("bt-metric-dd", "children"),
    Output("bt-metric-sharpe", "children"),
    Output("bt-metric-winrate", "children"),
    Output("bt-metric-trades", "children"),
    Input("btn-run-backtest", "n_clicks"),
    State("bt-strategy", "value"),
    State("bt-symbol", "value"),
    State("bt-fast", "value"),
    State("bt-slow", "value"),
    State("bt-capital", "value"),
    State("bt-start", "value"),
    State("bt-end", "value"),
    prevent_initial_call=True,
)
def run_backtest(n, strategy_name, symbol, fast, slow, capital, start, end):
    empty = _empty_figure("运行回测后显示权益曲线")
    no_data = ("—", "—", "—", "—", "—", "—")

    if not n:
        return (empty, [], no_data[0], no_data[1], no_data[2],
                no_data[3], no_data[4], no_data[5])

    try:
        params = {
            'name': strategy_name,
            'symbol': symbol or 'IF9999',
            'fast_period': int(fast or 10),
            'slow_period': int(slow or 30),
            'rsi_period': 14, 'oversold': 30, 'overbought': 70,
            'lookback_period': int(slow or 20),
            'position_ratio': 0.8,
        }
        bt_cfg = BacktestConfig(
            start_date=start or '2023-01-01',
            end_date=end or '2024-12-31',
            initial_capital=float(capital or 1000000),
            commission_rate=0.0003, slip_rate=0.0001, margin_rate=0.12,
        )
        dm = DataManager()
        pure_sym = symbol.split('.')[-1] if symbol and '.' in symbol else (symbol or 'IF9999')
        dm.generate_sample_data(pure_sym, days=700)

        strategy = create_strategy(strategy_name, params)
        engine = BacktestEngine(bt_cfg)
        engine.set_data_manager(dm)
        engine.set_strategy(strategy)
        result = engine.run()

        STATE.backtest_result = result
        STATE.backtest_engine = engine

        # 权益曲线
        if engine.equity_curve:
            eq_df = pd.DataFrame(list(engine.equity_curve.values()))
            eq_df = eq_df.sort_values('date')
            cummax = eq_df['capital'].cummax()
            dd = (eq_df['capital'] - cummax) / cummax * 100

            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.7, 0.3],
                vertical_spacing=0.04,
            )
            fig.add_trace(go.Scatter(
                x=eq_df['date'], y=eq_df['capital'],
                name='权益', line=dict(color=C['blue'], width=2),
            ), row=1, col=1)
            # 基准（持有不动）
            init_cap = bt_cfg.initial_capital
            fig.add_trace(go.Scatter(
                x=[eq_df['date'].iloc[0], eq_df['date'].iloc[-1]],
                y=[init_cap, init_cap],
                name='基准', line=dict(color=C['muted'], width=1, dash='dot'),
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=eq_df['date'], y=dd,
                name='回撤%', fill='tozeroy',
                fillcolor='rgba(248,81,73,0.15)',
                line=dict(color=C['red'], width=1),
            ), row=2, col=1)

            fig.update_layout(
                paper_bgcolor=C['card'], plot_bgcolor=C['bg'],
                font=dict(color=C['text'], size=12),
                margin=dict(l=50, r=20, t=20, b=40),
                legend=dict(bgcolor='rgba(0,0,0,0)', font=dict(size=11)),
                hovermode='x unified',
                height=320,
            )
            fig.update_xaxes(gridcolor=C['border'])
            fig.update_yaxes(gridcolor=C['border'])
        else:
            fig = _empty_figure("无权益数据")

        # 成交明细
        trade_rows = []
        for t in result.trades[:200]:
            trade_rows.append({
                'time': t.trade_time.strftime("%Y-%m-%d") if hasattr(t.trade_time, 'strftime') else str(t.trade_time),
                'symbol': t.symbol,
                'direction': '多' if t.direction == Direction.LONG else '空',
                'price': f"{t.price:.2f}",
                'volume': t.volume,
                'pnl': f"{t.pnl:+.2f}" if t.pnl != 0 else '—',
                'commission': f"{t.commission:.2f}",
            })

        return (
            fig, trade_rows,
            f"{result.total_return:.2%}",
            f"{result.annual_return:.2%}",
            f"{result.max_drawdown_pct:.2%}",
            f"{result.sharpe_ratio:.2f}",
            f"{result.win_rate:.2%}",
            str(result.total_trades),
        )
    except Exception as e:
        logger.error(f"回测失败: {e}", exc_info=True)
        return (_empty_figure(f"回测失败: {str(e)[:50]}"),
                [], "错误", "—", "—", "—", "—", "—")


# ══════════════════════════════════════════════════════════════════════════════
# Tab 4 回调：交易
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(
    Output("td-balance", "children"),
    Output("td-available", "children"),
    Output("td-margin", "children"),
    Output("td-pnl", "children"),
    Output("td-positions-table", "data"),
    Output("td-orders-table", "data"),
    Input("interval-fast", "n_intervals"),
    Input("btn-send-order", "n_clicks"),
    Input("btn-cancel-order", "n_clicks"),
    State("td-order-symbol", "value"),
    State("td-order-direction", "value"),
    State("td-order-price", "value"),
    State("td-order-volume", "value"),
    State("td-order-type", "value"),
    State("td-orders-table", "selected_rows"),
    State("td-orders-table", "data"),
    prevent_initial_call=True,
)
def update_trading(n_interval, n_send, n_cancel,
                   sym, direction, price, volume, order_type,
                   sel_rows, order_data):
    trig = ctx.triggered_id

    # 下单
    if trig == "btn-send-order" and n_send:
        if not STATE.connected:
            STATE.log("未连接，无法下单")
        elif not sym:
            STATE.log("请填写合约代码")
        else:
            try:
                from src.strategy import Signal, Direction as Dir, OrderType as OT
                sig = Signal(
                    symbol=sym,
                    datetime=datetime.now(),
                    direction=Dir.LONG if direction == 'long' else Dir.SHORT,
                    price=float(price or 0),
                    volume=int(volume or 1),
                    order_type=OT.MARKET if order_type == 'market' else OT.LIMIT,
                )
                oid = STATE.gateway.send_order(sig)
                STATE.log(f"下单: {sym} {direction} {volume}@{price} → {oid}")
            except Exception as e:
                STATE.log(f"下单失败: {e}")

    # 撤单
    if trig == "btn-cancel-order" and n_cancel:
        if sel_rows and order_data:
            idx = sel_rows[0]
            if idx < len(order_data):
                oid = order_data[idx]['order_id']
                if STATE.gateway:
                    ok = STATE.gateway.cancel_order(oid)
                    STATE.log(f"撤单 {oid}: {'成功' if ok else '失败'}")

    # 账户
    acc = STATE.get_account()
    if acc and acc.balance > 0:
        bal = f"{acc.balance:,.2f}"
        avail = f"{acc.available:,.2f}"
        margin = f"{acc.margin:,.2f}"
        pnl_val = acc.position_pnl
        pnl = f"{pnl_val:+,.2f}"
    else:
        bal = avail = margin = pnl = "—"

    # 持仓
    pos_rows = []
    for key, pos in STATE.get_positions().items():
        pos_rows.append({
            'symbol': pos.symbol,
            'direction': '多' if pos.direction == Direction.LONG else '空',
            'volume': pos.volume,
            'price': f"{pos.price:.2f}" if pos.price else "—",
            'pnl': f"{pos.pnl:+.2f}" if pos.pnl else "—",
        })

    # 委托
    order_rows = []
    for key, order in STATE.get_orders().items():
        order_rows.append({
            'order_id': order.order_id,
            'symbol': order.symbol,
            'direction': '买' if order.direction == Direction.LONG else '卖',
            'price': f"{order.price:.2f}",
            'volume': order.volume,
            'traded': order.traded_volume,
            'status': order.status.value,
        })

    return bal, avail, margin, pnl, pos_rows, order_rows


@app.callback(
    Output("td-order-msg", "children"),
    Input("btn-send-order", "n_clicks"),
    Input("btn-cancel-order", "n_clicks"),
    prevent_initial_call=True,
)
def order_feedback(n_send, n_cancel):
    trig = ctx.triggered_id
    if trig == "btn-send-order" and n_send:
        if not STATE.connected:
            return "⚠ 请先连接 CTP 网关"
        return "✓ 委托已提交"
    if trig == "btn-cancel-order" and n_cancel:
        return "✓ 撤单已提交"
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Tab 5 回调：分析
# ══════════════════════════════════════════════════════════════════════════════
@app.callback(
    Output("ana-equity-chart", "figure"),
    Output("ana-drawdown-chart", "figure"),
    Output("ana-monthly-chart", "figure"),
    Output("ana-risk-panel", "children"),
    Input("main-tabs", "value"),
    Input("interval-slow", "n_intervals"),
)
def update_analysis(tab, n):
    if tab != "tab-analysis" and ctx.triggered_id == "main-tabs":
        return no_update, no_update, no_update, no_update

    result = STATE.backtest_result
    engine = STATE.backtest_engine

    if not result or not engine or not engine.equity_curve:
        empty = _empty_figure("请先运行回测")
        return empty, empty, empty, html.Div("运行回测后显示指标",
                                              style={'color': C['muted'], 'fontSize': '13px'})

    eq_df = pd.DataFrame(list(engine.equity_curve.values())).sort_values('date')
    eq_series = eq_df.set_index('date')['capital']
    returns = eq_series.pct_change().dropna()

    # 1. 权益曲线
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=eq_df['date'], y=eq_df['capital'],
        name='策略权益', line=dict(color=C['blue'], width=2),
        fill='tozeroy', fillcolor='rgba(88,166,255,0.06)',
    ))
    init = eq_df['capital'].iloc[0]
    fig_eq.add_trace(go.Scatter(
        x=[eq_df['date'].iloc[0], eq_df['date'].iloc[-1]],
        y=[init, init],
        name='基准', line=dict(color=C['muted'], width=1, dash='dot'),
    ))
    _chart_layout(fig_eq, '权益曲线 vs 基准', height=300)

    # 2. 回撤图
    cummax = eq_series.cummax()
    dd = (eq_series - cummax) / cummax * 100
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=dd.index, y=dd.values,
        fill='tozeroy', fillcolor='rgba(248,81,73,0.2)',
        line=dict(color=C['red'], width=1), name='回撤',
    ))
    _chart_layout(fig_dd, '回撤 (%)', height=200)

    # 3. 月度收益热力图
    monthly_fig = _empty_figure("月度数据不足")
    try:
        eq_df2 = eq_df.copy()
        eq_df2['date'] = pd.to_datetime(eq_df2['date'])
        eq_df2 = eq_df2.set_index('date')
        monthly = eq_df2['capital'].resample('ME').last().pct_change().dropna() * 100
        if len(monthly) >= 3:
            monthly.index = monthly.index.to_period('M')
            years = sorted(set(p.year for p in monthly.index))
            months_labels = ['1月','2月','3月','4月','5月','6月',
                             '7月','8月','9月','10月','11月','12月']
            z = []
            for yr in years:
                row = []
                for mo in range(1, 13):
                    p = pd.Period(f"{yr}-{mo:02d}", freq='M')
                    val = monthly.get(p, np.nan)
                    row.append(val)
                z.append(row)
            monthly_fig = go.Figure(go.Heatmap(
                z=z, x=months_labels, y=[str(y) for y in years],
                colorscale=[[0, C['red']], [0.5, C['bg']], [1, C['green']]],
                zmid=0,
                text=[[f"{v:.1f}%" if not np.isnan(v) else '' for v in row] for row in z],
                texttemplate='%{text}',
                showscale=True,
            ))
            monthly_fig.update_layout(
                paper_bgcolor=C['card'], plot_bgcolor=C['card'],
                font=dict(color=C['text'], size=11),
                margin=dict(l=40, r=20, t=20, b=30),
                height=220,
            )
    except Exception:
        pass

    # 4. 风险指标
    analyzer = Analyzer(initial_capital=engine.config.initial_capital)
    analyzer.set_data(list(engine.equity_curve.values()), result.trades)
    metrics = analyzer.analyze()

    risk = metrics.get('risk', {})
    perf = metrics.get('performance', {})

    def _row(label, value, color=C['text']):
        return html.Div([
            html.Span(label, style={'color': C['muted'], 'fontSize': '12px', 'flex': '1'}),
            html.Span(value, style={'color': color, 'fontSize': '13px', 'fontWeight': '600'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'padding': '5px 0', 'borderBottom': f"1px solid {C['border']}"})

    panel = html.Div([
        html.Div("收益指标", style={'fontSize': '11px', 'color': C['muted'],
                                    'marginBottom': '6px', 'textTransform': 'uppercase'}),
        _row("总收益率", f"{perf.get('total_return', 0):.2%}", C['blue']),
        _row("年化收益率", f"{perf.get('annual_return', 0):.2%}", C['green']),
        _row("胜率", f"{perf.get('win_rate', 0):.2%}", C['green']),
        _row("盈亏比", f"{perf.get('profit_loss_ratio', 0):.2f}", C['text']),
        _row("平均盈利", f"{perf.get('avg_win', 0):.2f}", C['green']),
        _row("平均亏损", f"{perf.get('avg_loss', 0):.2f}", C['red']),

        html.Div("风险指标", style={'fontSize': '11px', 'color': C['muted'],
                                    'marginTop': '12px', 'marginBottom': '6px',
                                    'textTransform': 'uppercase'}),
        _row("最大回撤", f"{risk.get('max_drawdown_pct', 0):.2%}", C['red']),
        _row("年化波动率", f"{risk.get('volatility', 0):.2%}", C['yellow']),
        _row("夏普比率", f"{risk.get('sharpe_ratio', 0):.2f}", C['yellow']),
        _row("索提诺比率", f"{risk.get('sortino_ratio', 0):.2f}", C['text']),
        _row("卡玛比率", f"{risk.get('calmar_ratio', 0):.2f}", C['text']),
        _row("VaR (95%)", f"{risk.get('var_95', 0):.2%}", C['orange']),
        _row("CVaR (95%)", f"{risk.get('cvar_95', 0):.2%}", C['orange']),
        _row("偏度", f"{risk.get('skewness', 0):.3f}", C['text']),
        _row("峰度", f"{risk.get('kurtosis', 0):.3f}", C['text']),
    ])

    return fig_eq, fig_dd, monthly_fig, panel


# ══════════════════════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  量化交易系统  http://localhost:8050")
    print("=" * 60)
    app.run(debug=False, host="0.0.0.0", port=8050)
