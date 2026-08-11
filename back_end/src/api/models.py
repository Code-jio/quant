"""
Pydantic request/response models for the REST API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    td_connected:      bool = False
    md_connected:      bool = False
    reconnecting:      bool = False
    reconnect_count:   int = 0
    last_disconnect_reason: str = ""


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
    td_connected:     bool = False
    md_connected:     bool = False
    contracts_ready:  bool = False
    reconciliation_ready: bool = False
    trading_day:      str = ""
    order_entry_ready: bool = False
    gateway_status:   str
    gateway_name:     str
    reconnecting:     bool = False
    reconnect_count:  int = 0
    last_disconnect_reason: str = ""
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
    force_close: bool = False


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
    auto_arm: bool = True
    bar_timeout_seconds: float = 90.0
    no_fill_timeout_seconds: float = 10.0
    simulate_fill_enabled: bool = False
    trading: Dict[str, Any] = Field(default_factory=dict)
    strategy: Dict[str, Any] = Field(default_factory=dict)
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class TrialRunSimulationPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_order_id: str = Field(min_length=1)


class TrialRunSimulateFillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1)


class TrialRunStatusResponse(BaseModel):
    state: str
    run_id: str = ""
    outcome: str = "running"
    success_basis: str = ""
    current_track: str = "real"
    current_order_id: str = ""
    entry_order_id: str = ""
    close_order_id: str = ""
    order_chain: List[Dict[str, Any]] = Field(default_factory=list)
    broker_position_volume: int = 0
    simulated_position_volume: int = 0
    broker_active_order_ids: List[str] = Field(default_factory=list)
    reconcile_ok: bool = False
    simulation_state: str = "not_started"
    simulation_prepare_allowed: bool = False
    failure_code: str = ""
    rate_limit_remaining: int = 0
    rate_limit_retry_after_seconds: float = 0.0
    hold_deadline_at: str = ""
    runtime_environment: str = ""
    simulation_environment_allowed: bool = False
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
    auto_arm: bool = True
    tick_count: int = 0
    bar_count: int = 0
    warmup_bars: int = 0
    readiness_bars: int = 0
    hold_bars: int = 0
    bars_since_entry: int = 0
    bar_timeout_seconds: float = 90.0
    no_fill_timeout_seconds: float = 10.0
    no_bar_wait_seconds: float = 0.0
    unfilled_wait_seconds: float = 0.0
    market_warning: str = ""
    execution_issue: str = ""
    execution_warning: str = ""
    simulate_fill_enabled: bool = False
    simulate_fill_allowed: bool = False
    last_fill_source: str = ""
    last_bar_time: str = ""
    last_market_price: float = 0.0
    last_market_timestamp: str = ""
    market_data_age_seconds: float = 0.0
    market_issue: str = ""
    subscribed_symbols: List[str] = Field(default_factory=list)
    first_tick_bar_enabled: bool = False
    first_tick_bar_emitted: bool = False
    first_tick_bar_skip_reason: str = ""
    price_tick: float = 0.0
    aggressive_ticks: int = 0
    last_order_price: float = 0.0
    last_order_pricing_source: str = ""
    chase_enabled: bool = False
    chase_interval_seconds: float = 0.0
    chase_attempts: int = 0
    chase_max_attempts: int = 0
    chase_step_ticks: int = 0
    chase_pending_cancel_order_id: str = ""
    chase_resubmit_ready: bool = False
    chase_state: str = ""
    rate_retry_after_seconds: float = 0.0
    last_chase_reason: str = ""
    last_chase_order_id: str = ""
    last_chase_price: float = 0.0
    position_volume: int = 0
    last_reject_reason: str = ""
    risk: Dict[str, Any] = Field(default_factory=dict)
    validation_errors: List[str] = Field(default_factory=list)
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class TrialRunSimulateFillResponse(BaseModel):
    success: bool
    message: str
    order: Dict[str, Any] = Field(default_factory=dict)
    trade: Dict[str, Any] = Field(default_factory=dict)
    status: TrialRunStatusResponse


class TrialRunActionResponse(BaseModel):
    success: bool
    action: str
    message: str
    status: TrialRunStatusResponse
