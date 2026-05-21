"""
Pydantic request/response models for the REST API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username:   str
    password:   str
    broker_id:  str = ""
    td_server:  str = ""
    md_server:  str = ""
    app_id:     str = ""
    auth_code:  str = ""
    gateway_type: str = "vnpy"
    environment: str = "测试"
    auto_start_strategy: bool = False
    strategy_name: str = "ma_cross"
    strategy_params: Dict[str, Any] = Field(default_factory=dict)
    risk: Dict[str, Any] = Field(default_factory=dict)


class LoginResponse(BaseModel):
    success:        bool
    message:        str
    gateway_status: str
    account_id:     str = ""
    balance:        float = 0.0
    strategy_started: bool = False
    strategy_id:      str = ""


class AuthStatusResponse(BaseModel):
    logged_in:         bool
    gateway_connected: bool
    gateway_status:    str
    gateway_name:      str
    account_id:        str
    connect_log:       List[str]


class ActionRequest(BaseModel):
    action: str


class ActionResponse(BaseModel):
    success:     bool
    strategy_id: str
    action:      str
    message:     str


class SystemStatusResponse(BaseModel):
    timestamp:        str
    market_connected: bool
    gateway_status:   str
    gateway_name:     str
    cpu_percent:      float
    memory_percent:   float
    active_strategies: int
    account:          Optional[Dict[str, Any]] = None


class PositionInfo(BaseModel):
    symbol:     str
    direction:  str
    volume:     int
    frozen:     int
    cost_price: float
    pnl:        float


class StrategyInfo(BaseModel):
    strategy_id: str
    name:        str
    status:      str
    symbol:      Optional[str]
    pnl:         float
    positions:   List[PositionInfo]
    trade_count: int
    error_count: int


class SignalSchema(BaseModel):
    symbol:     str
    time:       str
    direction:  str
    price:      float
    volume:     int
    comment:    str
    order_type: str


class StrategyDetailResponse(BaseModel):
    strategy_id:    str
    name:           str
    status:         str
    symbol:         Optional[str]
    pnl:            float
    trade_count:    int
    error_count:    int
    positions:      List[PositionInfo]
    weight:         float
    params:         Dict[str, Any]
    recent_signals: List[SignalSchema]


class ParamsUpdateRequest(BaseModel):
    params:  Dict[str, Any]
    restart: bool = False


class WeightRequest(BaseModel):
    weights: Dict[str, float]


class BacktestRunRequest(BaseModel):
    strategy_name:   str            = "ma_cross"
    strategy_params: Dict[str, Any] = Field(default_factory=dict)
    start_date:      str            = "2023-01-01"
    end_date:        str            = "2024-12-31"
    initial_capital: float          = 1_000_000
    commission_rate: float          = 0.0003
    slip_rate:       float          = 0.0001
    margin_rate:     float          = 0.12
    contract_multiplier: float      = 10.0
    max_errors:      int            = 100
    sample_days:     int            = 700
    allow_synthetic_data: bool      = False


class ManualOrderRequest(BaseModel):
    symbol:     str
    direction:  str
    offset:     str = "open"
    price:      float = 0
    volume:     int = 1
    order_type: str = "market"


class ClosePositionRequest(BaseModel):
    volume:     int = 0
    price:      float = 0
    direction:  str = ""
    offset:     str = "close"
    order_type: str = ""


class EmergencyStopRequest(BaseModel):
    reason: str = ""
    cancel_orders: bool = True
    stop_strategies: bool = False


class RiskConfigRequest(BaseModel):
    risk: Dict[str, Any] = Field(default_factory=dict)


class TrialRunConfigResponse(BaseModel):
    enabled: bool = False
    ready: bool = False
    valid: bool
    config_source: str = ""
    config_path: str
    account_id: str = ""
    masked_account_id: str = ""
    gateway: str = "vnpy"
    environment: str = "测试"
    allowed_symbol: str = ""
    manual_open_enabled: bool = False
    trading: Dict[str, Any] = Field(default_factory=dict)
    strategy: Dict[str, Any] = Field(default_factory=dict)
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class TrialRunStatusResponse(BaseModel):
    state: str
    connected: bool = False
    gateway_status: str = "stopped"
    strategy_id: str = ""
    strategy_name: str = "verify"
    symbol: str = ""
    allowed_symbol: str = ""
    config_valid: bool = False
    gateway_connected: bool = False
    prepared: bool = False
    authorized: bool = False
    started: bool = False
    market_ready: bool = False
    ready_to_arm: bool = False
    completed: bool = False
    running: bool = False
    bar_count: int = 0
    warmup_bars: int = 0
    readiness_bars: int = 0
    hold_bars: int = 0
    bars_since_entry: int = 0
    position_volume: int = 0
    last_reject_reason: str = ""
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class TrialRunActionResponse(BaseModel):
    success: bool
    action: str
    message: str
    status: TrialRunStatusResponse
