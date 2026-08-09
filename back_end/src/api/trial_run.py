"""Trial-run API routes and configuration guardrails."""

from __future__ import annotations

import copy
import functools
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request

from ..trading import TradingStatus
from ..trading.symbols import is_supported_symbol, symbol_key, symbols_match
from ..trading.execution_adapter import TrialRunSimulationLedger, SimulationLedgerError
from ..trading.trial_run_execution import TrialRunExecutionState
from .models import (
    TrialRunActionResponse,
    TrialRunConfigResponse,
    TrialRunSimulationPrepareRequest,
    TrialRunSimulateFillRequest,
    TrialRunSimulateFillResponse,
    TrialRunStatusResponse,
)

TRIAL_STRATEGY_ID = "verify_trial"

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _BACKEND_ROOT / "config"
_LOCAL_CONFIG = _CONFIG_DIR / "config.local.json"
_EXAMPLE_CONFIG = _CONFIG_DIR / "config.example.json"
_NO_TICK_DIAGNOSTIC_SECONDS = 15.0


class _TrialRunState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.state = "idle"
        self.allowed_symbol = ""
        self.last_errors: List[str] = []
        self.authorized = False
        self.prepared_at = 0.0
        self.execution: Optional[TrialRunExecutionState] = None
        self._prepare_in_progress = False

    def update(
        self,
        *,
        state: Optional[str] = None,
        allowed_symbol: Optional[str] = None,
        errors: Optional[List[str]] = None,
        authorized: Optional[bool] = None,
        mark_prepared: bool = False,
    ) -> None:
        with self._lock:
            if state is not None:
                self.state = state
            if allowed_symbol is not None:
                self.allowed_symbol = allowed_symbol
            if errors is not None:
                self.last_errors = list(errors)
            if authorized is not None:
                self.authorized = authorized
            if mark_prepared:
                self.prepared_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self.state = "idle"
            self.allowed_symbol = ""
            self.last_errors = []
            self.authorized = False
            self.prepared_at = 0.0
            self.execution = None

    def try_begin_prepare(self) -> bool:
        with self._lock:
            if self._prepare_in_progress:
                return False
            self._prepare_in_progress = True
            return True

    def end_prepare(self) -> None:
        with self._lock:
            self._prepare_in_progress = False

    def start_execution(self, symbol: str, volume: int = 1) -> TrialRunExecutionState:
        execution = TrialRunExecutionState(symbol=symbol, volume=volume)
        with self._lock:
            self.execution = execution
        return execution

    def clear_execution(self) -> None:
        with self._lock:
            self.execution = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "allowed_symbol": self.allowed_symbol,
                "errors": list(self.last_errors),
                "authorized": self.authorized,
                "prepared_at": self.prepared_at,
                "execution": self.execution.serialize() if self.execution else {},
            }


trial_run_state = _TrialRunState()


def _exclusive_trial_prepare(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not trial_run_state.try_begin_prepare():
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "trial_run_prepare_in_progress",
                    "message": "已有试运行准备流程正在执行",
                },
            )
        try:
            return func(*args, **kwargs)
        finally:
            trial_run_state.end_prepare()

    return wrapped


def _configured_path(*, allow_example: bool = True) -> Optional[Path]:
    override = os.getenv("QUANT_TRIAL_CONFIG", "").strip()
    if override:
        return Path(override)
    if _LOCAL_CONFIG.exists():
        return _LOCAL_CONFIG
    if allow_example:
        return _EXAMPLE_CONFIG
    return None


def _load_config(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须是对象")
    return data


def _without_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_secret_fields(item)
            for key, item in value.items()
            if str(key).lower() not in {"password", "auth_code", "authcode", "token", "secret", "key"}
        }
    if isinstance(value, list):
        return [_without_secret_fields(item) for item in value]
    return value


def _safe_subset(source: Dict[str, Any], allowed_keys: set[str]) -> Dict[str, Any]:
    return {key: source[key] for key in allowed_keys if key in source}


def _dict_section(source: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _mask_account_id(account_id: Any) -> str:
    value = str(account_id or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}****{value[-2:]}"


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip()


def _normalize_symbol_key(symbol: Any) -> str:
    return symbol_key(symbol)


def _symbols_match(left: Any, right: Any) -> bool:
    return symbols_match(left, right)


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _is_production_environment(config: Dict[str, Any]) -> bool:
    trial_run = _dict_section(config, "trial_run")
    trading = _dict_section(config, "trading")
    production_markers = {"prod", "production", "real", "live", "实盘", "生产"}
    candidates = [
        trial_run.get("vnpy_environment"),
        trial_run.get("environment"),
        trading.get("vnpy_environment"),
        trading.get("environment"),
    ]

    def _environment_texts(value: Any) -> List[str]:
        text = str(value or "").strip().lower()
        texts = [text]
        try:
            restored = text.encode("gbk").decode("utf-8").strip().lower()
        except UnicodeError:
            restored = ""
        if restored and restored not in texts:
            texts.append(restored)
        return texts

    return any(
        marker in text
        for value in candidates
        for text in _environment_texts(value)
        for marker in production_markers
    )


def _environment_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    production_markers = ("prod", "production", "real", "live", "实盘", "生产")
    if any(marker in text for marker in production_markers):
        return "production"
    if any(marker in text for marker in ("sim", "simulation", "仿真", "模拟")):
        return "simulation"
    if any(marker in text for marker in ("test", "testing", "测试")):
        return "test"
    return ""


def _simulation_environment_allowed(config: Dict[str, Any], runtime: Dict[str, Any]) -> bool:
    trial_run = _dict_section(config, "trial_run")
    trading = _dict_section(config, "trading")
    configured_values = [
        value
        for value in (
            trial_run.get("vnpy_environment"),
            trial_run.get("environment"),
            trading.get("vnpy_environment"),
            trading.get("environment"),
        )
        if str(value or "").strip()
    ]
    configured_kinds = {_environment_kind(value) for value in configured_values}
    runtime_kind = _environment_kind(
        runtime.get("environment") if isinstance(runtime, dict) else ""
    )
    return (
        len(configured_kinds) == 1
        and configured_kinds <= {"test", "simulation"}
        and runtime_kind in configured_kinds
    )


def _simulate_fill_enabled(config: Dict[str, Any], runtime: Dict[str, Any]) -> bool:
    trial_run = _dict_section(config, "trial_run")
    return _bool_value(trial_run.get("simulate_fill_enabled"), False) and _simulation_environment_allowed(
        config,
        runtime,
    )


def _validate_trial_config(config: Dict[str, Any]) -> tuple[str, List[str]]:
    errors: List[str] = []
    trial_run = _dict_section(config, "trial_run")
    strategy = _dict_section(config, "strategy")
    risk = _dict_section(config, "risk")

    trial_symbol = _clean_symbol(trial_run.get("allowed_symbol"))
    strategy_symbol = _clean_symbol(strategy.get("symbol"))
    allowed_symbols = risk.get("allowed_symbols")
    if not isinstance(allowed_symbols, list):
        allowed_symbols = []
    clean_allowed_symbols = [_clean_symbol(item) for item in allowed_symbols if _clean_symbol(item)]

    if trial_run.get("enabled") is not True:
        errors.append("trial_run.enabled 必须为 true")
    if not trial_symbol:
        errors.append("trial_run.allowed_symbol 不能为空")
    if not strategy_symbol:
        errors.append("strategy.symbol 不能为空")
    if len(clean_allowed_symbols) != 1:
        errors.append("risk.allowed_symbols 必须且只能包含一个合约")
    risk_symbol = clean_allowed_symbols[0] if len(clean_allowed_symbols) == 1 else ""
    for field_name, field_symbol in (
        ("trial_run.allowed_symbol", trial_symbol),
        ("strategy.symbol", strategy_symbol),
        ("risk.allowed_symbols[0]", risk_symbol),
    ):
        if field_symbol and not is_supported_symbol(field_symbol):
            errors.append(f"{field_name} 不是已知且交易所一致的期货合约")
    if trial_symbol and strategy_symbol and risk_symbol:
        if len({_normalize_symbol_key(trial_symbol), _normalize_symbol_key(strategy_symbol), _normalize_symbol_key(risk_symbol)}) != 1:
            errors.append("trial_run.allowed_symbol、strategy.symbol、risk.allowed_symbols[0] 必须一致")
    if strategy.get("name") != "verify":
        errors.append("strategy.name 必须为 verify")
    if _int_value(strategy.get("volume")) != 1:
        errors.append("strategy.volume 必须为 1")
    chase_max_attempts = _int_value(strategy.get("chase_max_attempts"), 5)
    if chase_max_attempts < 0 or chase_max_attempts > 5:
        errors.append("strategy.chase_max_attempts 必须在 0 到 5 之间")
    if _bool_value(trial_run.get("auto_arm"), True):
        if _int_value(strategy.get("warmup_bars"), 1) != 1:
            errors.append("strategy.warmup_bars must be 1 when trial_run.auto_arm is true")
        if _int_value(strategy.get("readiness_bars"), 1) != 1:
            errors.append("strategy.readiness_bars must be 1 when trial_run.auto_arm is true")
    if _int_value(risk.get("max_order_volume")) != 1:
        errors.append("risk.max_order_volume 必须为 1")
    if _int_value(risk.get("max_position_volume")) != 1:
        errors.append("risk.max_position_volume 必须为 1")
    if _int_value(risk.get("max_orders_per_minute"), 999999) > 5:
        errors.append("risk.max_orders_per_minute 必须小于等于 5")
    if _int_value(risk.get("max_active_orders"), 999999) > 2:
        errors.append("risk.max_active_orders 必须小于等于 2")
    max_age = _float_value(risk.get("max_market_data_age_seconds"))
    if max_age <= 0:
        errors.append("risk.max_market_data_age_seconds 必须大于 0")

    return trial_symbol, errors


def _read_trial_config(*, allow_example: bool = True) -> tuple[Path, Dict[str, Any], str, List[str]]:
    path = _configured_path(allow_example=allow_example)
    if path is None:
        raise FileNotFoundError("未找到试运行配置")
    config = _load_config(path)
    allowed_symbol, errors = _validate_trial_config(config)
    return path, config, allowed_symbol, errors


def _safe_config_response(path: Path, config: Dict[str, Any], allowed_symbol: str, errors: List[str]) -> TrialRunConfigResponse:
    trial_run = _dict_section(config, "trial_run")
    trading = _dict_section(config, "trading")
    strategy = _dict_section(config, "strategy")
    risk = _dict_section(config, "risk")
    raw_account_id = str(trial_run.get("account_id") or trading.get("username") or "").strip()
    environment = str(
        trial_run.get("vnpy_environment")
        or trading.get("vnpy_environment")
        or trading.get("environment")
        or "测试"
    )
    auto_arm = _bool_value(trial_run.get("auto_arm"), True)
    bar_timeout_seconds = max(1.0, _float_value(trial_run.get("bar_timeout_seconds"), 90.0))
    no_fill_timeout_seconds = max(1.0, _float_value(trial_run.get("no_fill_timeout_seconds"), 10.0))
    simulate_fill_enabled = _bool_value(trial_run.get("simulate_fill_enabled"), False)
    safe_strategy = _safe_subset(
        strategy,
        {
            "name",
            "symbol",
            "volume",
            "warmup_bars",
            "readiness_bars",
            "hold_bars",
            "contract_multiplier",
            "max_errors",
            "order_type",
            "price_tick",
            "aggressive_ticks",
            "chase_enabled",
            "chase_interval_seconds",
            "chase_max_attempts",
            "chase_step_ticks",
            "chase_fallback_to_market",
        },
    )
    safe_risk = _safe_subset(
        risk,
        {
            "enabled",
            "allowed_symbols",
            "blocked_symbols",
            "max_order_volume",
            "max_position_volume",
            "max_active_orders",
            "max_orders_per_minute",
            "max_daily_loss_ratio",
            "max_order_value",
            "max_position_value",
            "max_price_deviation",
            "max_market_data_age_seconds",
            "duplicate_signal_window_seconds",
            "default_contract_multiplier",
            "contract_multipliers",
            "allow_market_orders",
        },
    )
    gateway = str(trial_run.get("gateway") or trading.get("gateway") or "vnpy")
    safe_trading = _safe_subset(
        trading,
        {"gateway", "broker_id", "td_server", "md_server", "app_id", "vnpy_environment", "environment", "fronts"},
    )
    safe_trading.update({"gateway": gateway, "vnpy_environment": environment, "environment": environment})
    safe_trial_run = {
        "enabled": trial_run.get("enabled") is True,
        "allowed_symbol": allowed_symbol,
        "manual_open_enabled": bool(trial_run.get("manual_open_enabled", False)),
        "auto_arm": auto_arm,
        "no_fill_timeout_seconds": no_fill_timeout_seconds,
        "simulate_fill_enabled": simulate_fill_enabled,
        "bar_timeout_seconds": bar_timeout_seconds,
    }
    safe_config = {
        "trial_run": safe_trial_run,
        "trading": safe_trading,
        "strategy": safe_strategy,
        "risk": safe_risk,
    }
    return TrialRunConfigResponse(
        enabled=trial_run.get("enabled") is True,
        ready=not errors,
        valid=not errors,
        config_source=path.name,
        config_path=path.name,
        account_id="",
        masked_account_id=_mask_account_id(raw_account_id),
        gateway=gateway,
        environment=environment,
        allowed_symbol=allowed_symbol,
        manual_open_enabled=bool(trial_run.get("manual_open_enabled", False)),
        auto_arm=auto_arm,
        bar_timeout_seconds=bar_timeout_seconds,
        no_fill_timeout_seconds=no_fill_timeout_seconds,
        simulate_fill_enabled=simulate_fill_enabled,
        trading=safe_trading,
        strategy=safe_strategy,
        risk=safe_risk,
        validation_errors=errors,
        config=safe_config,
        errors=errors,
    )


def _gateway_connected(engine: Any) -> bool:
    if engine is None:
        return False
    return getattr(engine.gateway, "status", None) in (TradingStatus.CONNECTED, TradingStatus.TRADING)


def _strategy_snapshot(strategy: Any) -> Dict[str, Any]:
    snapshot = getattr(strategy, "snapshot", None)
    if callable(snapshot):
        value = snapshot()
        return value if isinstance(value, dict) else {"value": value}
    return {}


def _timestamp_to_text(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _enum_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _order_to_safe_dict(order: Any) -> Dict[str, Any]:
    return {
        "order_id": str(getattr(order, "order_id", "") or ""),
        "symbol": str(getattr(order, "symbol", "") or ""),
        "direction": _enum_value(getattr(order, "direction", "")),
        "order_type": _enum_value(getattr(order, "order_type", "")),
        "price": float(getattr(order, "price", 0.0) or 0.0),
        "volume": _int_value(getattr(order, "volume", 0)),
        "traded_volume": _int_value(getattr(order, "traded_volume", 0)),
        "status": _enum_value(getattr(order, "status", "")),
        "offset": _enum_value(getattr(order, "offset", "")),
        "create_time": _timestamp_to_text(getattr(order, "create_time", "")),
        "update_time": _timestamp_to_text(getattr(order, "update_time", "")),
        "error_msg": str(getattr(order, "error_msg", "") or ""),
    }


def _trade_to_safe_dict(trade: Any) -> Dict[str, Any]:
    return {
        "trade_id": str(getattr(trade, "trade_id", "") or ""),
        "order_id": str(getattr(trade, "order_id", "") or ""),
        "symbol": str(getattr(trade, "symbol", "") or ""),
        "direction": _enum_value(getattr(trade, "direction", "")),
        "price": float(getattr(trade, "price", 0.0) or 0.0),
        "volume": _int_value(getattr(trade, "volume", 0)),
        "commission": float(getattr(trade, "commission", 0.0) or 0.0),
        "pnl": float(getattr(trade, "pnl", 0.0) or 0.0),
        "trade_time": _timestamp_to_text(getattr(trade, "trade_time", "")),
    }


def _market_data_age_seconds(value: Any) -> float:
    if not value:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return max(0.0, datetime.now().timestamp() - float(value))
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
            return max(0.0, (now - value).total_seconds())
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _flatten_symbol_values(value: Any) -> List[str]:
    symbols: List[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            symbols.extend(_flatten_symbol_values(item))
    elif value:
        symbols.append(str(value))
    return symbols


def _gateway_subscribed_symbols(engine: Any) -> List[str]:
    if engine is None:
        return []
    gateway = getattr(engine, "gateway", None)
    if gateway is None:
        return []
    symbols = []
    symbols.extend(_flatten_symbol_values(getattr(gateway, "subscribed_symbols", [])))
    symbols.extend(_flatten_symbol_values(getattr(gateway, "_subscribed_symbols", [])))
    return list(dict.fromkeys(symbols))


def _gateway_cached_tick_symbols(engine: Any) -> List[str]:
    if engine is None:
        return []
    symbols: List[str] = []
    order_manager = getattr(engine, "order_manager", None)
    market_data = getattr(order_manager, "market_data", {}) if order_manager is not None else {}
    if isinstance(market_data, dict):
        symbols.extend(str(key) for key in market_data.keys())
    gateway = getattr(engine, "gateway", None)
    for attr in ("latest_ticks", "latest_tick_snapshots"):
        values = getattr(gateway, attr, {}) if gateway is not None else {}
        if isinstance(values, dict):
            symbols.extend(str(key) for key in values.keys())
    return list(dict.fromkeys(symbols))


def _has_matching_symbol(symbols: List[str], symbol: str) -> bool:
    return any(_symbols_match(item, symbol) for item in symbols)


def _active_broker_orders(gateway: Any, symbol: str) -> List[Any]:
    try:
        queried = gateway.query_orders()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_orders_unavailable", "message": str(exc)},
        ) from exc
    orders = list(queried or [])
    cached = getattr(gateway, "orders", {})
    if isinstance(cached, dict):
        known = {str(getattr(item, "order_id", "") or "") for item in orders}
        orders.extend(
            item
            for item in cached.values()
            if str(getattr(item, "order_id", "") or "") not in known
        )
    active: List[Any] = []
    for order in orders:
        if not _symbols_match(getattr(order, "symbol", ""), symbol):
            continue
        is_active = getattr(order, "is_active", None)
        if callable(is_active):
            if is_active():
                active.append(order)
            continue
        status = str(_enum_value(getattr(order, "status", "")) or "").lower()
        if status in {"submitting", "submitted", "partfilled"}:
            active.append(order)
    return active


def _broker_position_volume(gateway: Any, symbol: str) -> int:
    try:
        queried = gateway.query_positions()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_positions_unavailable", "message": str(exc)},
        ) from exc
    positions = list(queried or [])
    cached = getattr(gateway, "positions", {})
    if isinstance(cached, dict):
        known = {
            (str(getattr(item, "symbol", "") or ""), str(_enum_value(getattr(item, "direction", "")) or ""))
            for item in positions
        }
        positions.extend(
            item
            for item in cached.values()
            if (
                str(getattr(item, "symbol", "") or ""),
                str(_enum_value(getattr(item, "direction", "")) or ""),
            )
            not in known
        )
    return sum(
        abs(_int_value(getattr(position, "volume", 0)))
        for position in positions
        if _symbols_match(getattr(position, "symbol", ""), symbol)
    )


def _require_trial_preflight(trading_state: Any, engine: Any, symbol: str) -> None:
    conflicts = [
        entry.strategy_id
        for entry in trading_state.all_entries()
        if entry.strategy_id != TRIAL_STRATEGY_ID
    ]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "trial_run_engine_busy",
                "message": f"主交易引擎已有运行策略: {', '.join(conflicts)}",
            },
        )

    gateway = getattr(engine, "gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_gateway_unavailable", "message": "交易网关不可用"},
        )
    risk_manager = getattr(engine, "risk_manager", None)
    if risk_manager is not None:
        rate_snapshot = risk_manager.order_rate_snapshot(required_capacity=2)
        if int(rate_snapshot.get("remaining", 0)) < 2:
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "rate_capacity_not_ready",
                    "message": "提交入场委托后必须保留平仓容量",
                    "remaining": int(rate_snapshot.get("remaining", 0)),
                    "retry_after_seconds": float(rate_snapshot.get("retry_after_seconds", 0.0)),
                },
            )
    try:
        reconciliation = gateway.refresh_reconciliation(timeout_seconds=8.0)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_snapshot_failed", "message": str(exc)},
        ) from exc
    if not isinstance(reconciliation, dict) or not (
        reconciliation.get("ok") is True and reconciliation.get("fresh") is True
    ):
        failure_code = (
            str(reconciliation.get("failure_code") or "broker_snapshot_unavailable")
            if isinstance(reconciliation, dict)
            else "broker_snapshot_unavailable"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": failure_code,
                "message": "未取得完整的券商账户、持仓和委托快照",
            },
        )
    try:
        account = gateway.query_account()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_account_unavailable", "message": str(exc)},
        ) from exc
    if account is None:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "broker_account_unavailable", "message": "券商账户查询无结果"},
        )

    position_volume = _broker_position_volume(gateway, symbol)
    if position_volume:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "broker_position_not_flat",
                "message": f"目标合约真实持仓未归零: {position_volume}",
            },
        )
    active_orders = _active_broker_orders(gateway, symbol)
    if active_orders:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "broker_active_order_exists",
                "message": "目标合约仍有活动委托",
                "order_ids": [str(getattr(order, "order_id", "") or "") for order in active_orders],
            },
        )


def _order_age_seconds(order: Any) -> float:
    created_at = getattr(order, "create_time", None)
    if not created_at:
        return 0.0
    try:
        if isinstance(created_at, (int, float)):
            return max(0.0, time.time() - float(created_at))
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if isinstance(created_at, datetime):
            now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
            return max(0.0, (now - created_at).total_seconds())
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _active_order_wait_seconds(engine: Any, symbol: str) -> float:
    if engine is None or not symbol:
        return 0.0
    execution = getattr(engine, "trial_run_execution", None)
    current_order_id = str(getattr(execution, "current_order_id", "") or "")
    if not current_order_id:
        return 0.0
    gateway = getattr(engine, "gateway", None)
    orders = getattr(gateway, "orders", {}) if gateway is not None else {}
    if not isinstance(orders, dict):
        return 0.0

    active_statuses = {
        "submitting",
        "submitted",
        "accepted",
        "broker_accepted",
        "broker-accepted",
        "broker accepted",
        "partfilled",
        "partialfilled",
        "partial_filled",
    }
    order = orders.get(current_order_id)
    if order is None:
        return 0.0
    order_symbol = str(getattr(order, "symbol", "") or "")
    if not order_symbol or not _symbols_match(order_symbol, symbol):
        return 0.0
    status = str(_enum_value(getattr(order, "status", "")) or "").strip().lower()
    if status not in active_statuses:
        return 0.0
    volume = _int_value(getattr(order, "volume", 0))
    traded_volume = _int_value(getattr(order, "traded_volume", 0))
    if volume > 0 and traded_volume >= volume:
        return 0.0
    return round(_order_age_seconds(order), 1)


def _has_fresh_reconciliation(
    gateway: Any,
    max_age_seconds: float = 15.0,
    monotonic_clock: Optional[Callable[[], float]] = None,
) -> bool:
    snapshot = getattr(gateway, "last_reconciliation", {}) if gateway is not None else {}
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("ok") is not True or snapshot.get("fresh") is not True:
        return False
    refreshed = _float_value(snapshot.get("refreshed_monotonic"), 0.0)
    now = monotonic_clock() if callable(monotonic_clock) else time.monotonic()
    age = now - refreshed
    return refreshed > 0 and 0 <= age <= max_age_seconds


def _execution_order(execution: Dict[str, Any], order_id: str) -> Dict[str, Any]:
    return next(
        (
            item
            for item in execution.get("order_chain", [])
            if str(item.get("order_id") or "") == str(order_id or "")
        ),
        {},
    )


def _simulation_prepare_allowed(
    engine: Any,
    strategy: Any,
    execution: Dict[str, Any],
    source_order_id: str,
    timeout_seconds: float,
) -> bool:
    if not engine or not strategy or not source_order_id:
        return False
    if getattr(engine, "simulation_adapter", None) is not None:
        return False
    if str(getattr(engine, "_simulation_source_order_id", "") or ""):
        return False
    if str(execution.get("current_order_id") or "") != str(source_order_id):
        return False
    if execution.get("real_trade_ids"):
        return False
    if execution.get("real_submission_proof") is not True:
        return False
    order = _execution_order(execution, source_order_id)
    if not order or order.get("track") != "real":
        return False
    if str(order.get("status") or "").lower() not in {
        "submitting",
        "submitted",
        "accepted",
        "broker_accepted",
    }:
        return False
    broker_gateway = getattr(engine, "gateway", None)
    broker_orders = getattr(broker_gateway, "orders", {}) if broker_gateway is not None else {}
    broker_order = broker_orders.get(source_order_id) if isinstance(broker_orders, dict) else None
    if broker_order is None:
        return False
    if not _symbols_match(getattr(broker_order, "symbol", ""), execution.get("symbol", "")):
        return False
    if _int_value(getattr(broker_order, "volume", 0)) != 1:
        return False
    if _int_value(getattr(broker_order, "traded_volume", 0)) != 0:
        return False
    broker_status = str(
        _enum_value(getattr(broker_order, "status", "")) or ""
    ).strip().lower()
    broker_active_statuses = {
        "submitting",
        "submitted",
        "accepted",
        "broker_accepted",
    }
    chase_cancel_pending = str(
        getattr(strategy, "_chase_pending_cancel_order_id", "") or ""
    ) == source_order_id
    if broker_status not in broker_active_statuses and not (
        broker_status in {"cancelled", "canceled"} and chase_cancel_pending
    ):
        return False
    metadata = getattr(strategy, "_order_ownership", {}).get(source_order_id, {})
    submitted = metadata.get("submitted_monotonic")
    if submitted is None:
        return False
    age = max(0.0, float(getattr(engine, "_monotonic", time.monotonic)()) - float(submitted))
    return age >= float(timeout_seconds)


def _status_response(trading_state: Any) -> TrialRunStatusResponse:
    state = trial_run_state.snapshot()
    execution = _dict_section(state, "execution")
    entry = trading_state.get(TRIAL_STRATEGY_ID)
    engine = trading_state.primary_engine()
    strategy = getattr(entry, "strategy", None) if entry else None
    snapshot = _strategy_snapshot(strategy) if strategy else {}
    running = bool(entry and getattr(entry, "status", "") == "running")
    connected = _gateway_connected(engine)
    gateway_status = "stopped"
    risk = {}
    last_reject_reason = ""
    if engine is not None:
        gateway_status = engine.gateway.status.value if hasattr(engine.gateway.status, "value") else str(engine.gateway.status)
        risk = engine.risk_manager.status()
        last_reject_reason = getattr(engine, "last_reject_reason", "")

    config_valid = False
    config_errors: List[str] = []
    config: Dict[str, Any] = {}
    try:
        _, config, allowed_symbol, config_errors = _read_trial_config(allow_example=True)
        config_valid = not config_errors
    except Exception as exc:
        allowed_symbol = state["allowed_symbol"]
        config_errors = [str(exc)]
    trial_config = _dict_section(config, "trial_run")
    auto_arm = _bool_value(trial_config.get("auto_arm"), True)
    bar_timeout_seconds = max(1.0, _float_value(trial_config.get("bar_timeout_seconds"), 90.0))
    no_fill_timeout_seconds = max(1.0, _float_value(trial_config.get("no_fill_timeout_seconds"), 2.0))
    simulate_fill_enabled = _bool_value(trial_config.get("simulate_fill_enabled"), False)
    runtime_config = trading_state.main_config_snapshot()
    simulation_environment_allowed = bool(
        config_valid and _simulation_environment_allowed(config, runtime_config)
    )
    simulate_fill_enabled = bool(
        simulate_fill_enabled and simulation_environment_allowed
    )
    engine_execution = getattr(engine, "trial_run_execution", None) if engine is not None else None
    source_order_id = str(getattr(engine, "_simulation_source_order_id", "") or "") if engine is not None else ""
    simulation_requested = bool(getattr(engine, "_simulation_requested", False)) if engine is not None else False
    simulation_adapter = getattr(engine, "simulation_adapter", None) if engine is not None else None
    simulation_state = str(
        getattr(engine_execution, "simulation_state", "")
        or ("cancel_pending" if simulation_requested else "not_started")
    )
    if simulation_adapter is not None:
        simulation_state = str(getattr(engine_execution, "simulation_state", "ready") or "ready")
    prepare_source = source_order_id or str(execution.get("current_order_id") or "")
    simulation_prepare_allowed = bool(
        simulate_fill_enabled
        and _simulation_prepare_allowed(
            engine,
            strategy,
            execution,
            prepare_source,
            no_fill_timeout_seconds,
        )
    )
    simulation_current_order_id = str(
        getattr(getattr(simulation_adapter, "ledger", None), "current_order_id", "") or ""
    )
    simulate_fill_allowed = bool(
        simulate_fill_enabled
        and simulation_adapter is not None
        and simulation_current_order_id
        and str(execution.get("current_order_id") or "")
        == simulation_current_order_id
        and str(execution.get("final_outcome") or "running") == "running"
    )
    snapshot_state = str(snapshot.get("state") or "")
    base_authorized = bool(snapshot.get("authorized", state["authorized"]))
    auto_authorized = auto_arm and entry is not None and snapshot_state not in {"error", "completed"}
    authorized = bool(base_authorized or auto_authorized)
    started = bool(snapshot.get("started", base_authorized))
    market_ready = bool(snapshot.get("market_ready", snapshot.get("ready_to_arm", False)))

    response_state = str(snapshot.get("state") or state["state"] or ("connected" if connected else "disconnected"))
    if risk.get("emergency_stop"):
        response_state = "emergency_stopped"
    symbol = str(snapshot.get("symbol") or state["allowed_symbol"] or allowed_symbol or "")
    bar_count = _int_value(snapshot.get("bar_count"))
    tick_count = _int_value(snapshot.get("tick_count"))
    no_bar_wait_seconds = 0.0
    market_warning = ""
    if entry is not None and not bool(snapshot.get("completed", False)) and bar_count <= 0:
        prepared_at = _float_value(state.get("prepared_at"), 0.0)
        if prepared_at > 0:
            no_bar_wait_seconds = max(0.0, round(time.time() - prepared_at, 1))
    market_data: Dict[str, Any] = {}
    if engine is not None and symbol:
        market_data_for_symbol = getattr(engine, "_market_data_for_symbol", None)
        if callable(market_data_for_symbol):
            try:
                value = market_data_for_symbol(symbol)
                if isinstance(value, dict):
                    market_data = value
            except Exception:
                market_data = {}
    last_market_price = _float_value(
        snapshot.get("last_market_price")
        or market_data.get("last_price")
        or market_data.get("last")
    )
    last_market_timestamp = (
        snapshot.get("last_market_timestamp")
        or market_data.get("timestamp")
        or ""
    )
    subscribed_symbols = _gateway_subscribed_symbols(engine)
    cached_tick_symbols = _gateway_cached_tick_symbols(engine)
    first_tick_bar_enabled = bool(getattr(engine, "_emit_first_tick_bar", False)) if engine is not None else False
    emitted_symbols: Any = getattr(engine, "_first_tick_bar_emitted_symbols", set()) if engine is not None else set()
    first_tick_bar_emitted = bar_count > 0 or _normalize_symbol_key(symbol) in set(emitted_symbols or [])
    first_tick_bar_skip_reason = str(getattr(engine, "_first_tick_bar_skip_reason", "") or "") if engine is not None else ""
    last_reject_reason_value = str(snapshot.get("last_reject_reason") or last_reject_reason or "")
    market_issue = ""
    if "Market data is stale" in last_reject_reason_value:
        market_issue = "stale_market_data"
    elif "Market data timestamp is unavailable" in last_reject_reason_value:
        market_issue = "market_data_timestamp_unavailable"
    elif last_reject_reason_value == "invalid_market_price":
        market_issue = "invalid_tick_price"
    elif entry is not None and not bool(snapshot.get("completed", False)) and bar_count <= 0:
        has_market_price = last_market_price > 0
        if tick_count <= 0 and cached_tick_symbols:
            if symbol and _has_matching_symbol(cached_tick_symbols, symbol) and has_market_price:
                market_issue = first_tick_bar_skip_reason or "first_tick_bar_not_emitted"
            else:
                market_issue = "symbol_mismatch"
        elif tick_count <= 0 and not has_market_price and no_bar_wait_seconds >= _NO_TICK_DIAGNOSTIC_SECONDS:
            market_issue = "no_tick_timeout"
        elif first_tick_bar_enabled and tick_count > 0 and not first_tick_bar_emitted:
            market_issue = first_tick_bar_skip_reason or "first_tick_bar_not_emitted"

    if market_issue == "no_tick_timeout":
        market_warning = (
            f"订阅 {symbol or allowed_symbol} 后 {int(no_bar_wait_seconds)} 秒仍未收到有效 tick；"
            "请确认合约处于交易时段、行情前置已登录且订阅成功。"
        )
    elif market_issue == "stale_market_data":
        market_warning = (
            f"收到 {symbol or allowed_symbol} 行情但时间戳已超过风控新鲜度限制；"
            "系统已忽略该 tick，等待新的实时行情后再自动验证开仓。"
        )
    elif market_issue == "market_data_timestamp_unavailable":
        market_warning = (
            f"收到 {symbol or allowed_symbol} 行情但缺少有效时间戳；"
            "系统已忽略该 tick，等待带时间戳的新行情。"
        )
    elif market_issue == "symbol_mismatch":
        market_warning = (
            f"已收到行情缓存 {', '.join(cached_tick_symbols) or '--'}，"
            f"但未匹配目标合约 {symbol or allowed_symbol}。"
        )
    elif market_issue == "first_tick_bar_not_emitted":
        market_warning = "已发现目标合约 tick，但首 tick 验证 Bar 未生成；请检查首 tick Bar 诊断字段。"
    elif market_issue == "invalid_tick_price":
        market_warning = "已收到目标合约 tick，但价格无效，不能生成验证 Bar。"
    elif entry is not None and not bool(snapshot.get("completed", False)) and bar_count <= 0 and no_bar_wait_seconds >= bar_timeout_seconds:
        market_warning = (
            f"已等待 {int(no_bar_wait_seconds)} 秒仍未形成首根 Bar。"
            f"请检查 {symbol or allowed_symbol} 合约是否可交易、是否处于交易时段、行情前置是否推送 tick。"
        )
    position_volume = 0
    if strategy is not None and symbol:
        try:
            position_volume = int(getattr(strategy.get_position(symbol), "volume", 0) or 0)
        except Exception:
            position_volume = 0
    gateway = getattr(engine, "gateway", None) if engine is not None else None
    broker_position_volume = 0
    broker_active_order_ids: List[str] = []
    if gateway is not None:
        cached_positions = getattr(gateway, "positions", {})
        if isinstance(cached_positions, dict):
            broker_position_volume = sum(
                abs(_int_value(getattr(item, "volume", 0)))
                for item in cached_positions.values()
                if _symbols_match(getattr(item, "symbol", ""), symbol or allowed_symbol)
            )
        cached_orders = getattr(gateway, "orders", {})
        if isinstance(cached_orders, dict):
            broker_active_order_ids = [
                str(getattr(item, "order_id", "") or "")
                for item in cached_orders.values()
                if _symbols_match(getattr(item, "symbol", ""), symbol or allowed_symbol)
                and (
                    callable(getattr(item, "is_active", None))
                    and item.is_active()
                )
            ]
    completed = bool(snapshot.get("completed", False))
    unfilled_wait_seconds = float(snapshot.get("unfilled_wait_seconds") or 0.0)
    execution_issue = str(snapshot.get("execution_issue") or "")
    execution_warning = str(snapshot.get("execution_warning") or "")
    if not completed:
        active_order_wait_seconds = _active_order_wait_seconds(engine, symbol or allowed_symbol)
        if active_order_wait_seconds > unfilled_wait_seconds:
            unfilled_wait_seconds = active_order_wait_seconds
        if unfilled_wait_seconds >= no_fill_timeout_seconds:
            execution_issue = "waiting_counterparty"
            execution_warning = (
                f"订单已报入但 {int(unfilled_wait_seconds)} 秒未成交，测试环境可能没有对手盘，"
                "可等待券商撮合或使用模拟成交回报。"
            )

    return TrialRunStatusResponse(
        state=response_state,
        run_id=str(execution.get("run_id") or ""),
        outcome=str(execution.get("final_outcome") or "running"),
        success_basis=str(execution.get("success_basis") or ""),
        current_track=str(execution.get("current_track") or "real"),
        current_order_id=str(execution.get("current_order_id") or ""),
        entry_order_id=str(execution.get("entry_order_id") or ""),
        close_order_id=str(execution.get("close_order_id") or ""),
        order_chain=list(execution.get("order_chain") or []),
        broker_position_volume=broker_position_volume,
        simulated_position_volume=_int_value(execution.get("simulated_position_volume")),
        broker_active_order_ids=broker_active_order_ids,
        reconcile_ok=bool(
            connected
            and _has_fresh_reconciliation(
                gateway,
                monotonic_clock=getattr(engine, "_monotonic", None) if engine is not None else None,
            )
            and broker_position_volume == 0
            and not broker_active_order_ids
        ),
        rate_limit_remaining=_int_value(risk.get("rate_limit_remaining", 0)),
        rate_limit_retry_after_seconds=float(risk.get("rate_limit_retry_after_seconds", 0.0) or 0.0),
        simulation_state=simulation_state,
        simulation_prepare_allowed=simulation_prepare_allowed,
        failure_code=str(execution.get("failure_code") or ""),
        runtime_environment=str(runtime_config.get("environment") or ""),
        simulation_environment_allowed=simulation_environment_allowed,
        connected=connected,
        gateway_status=gateway_status,
        strategy_id=TRIAL_STRATEGY_ID if entry else "",
        strategy_name="verify",
        symbol=symbol,
        allowed_symbol=state["allowed_symbol"] or allowed_symbol,
        config_valid=config_valid,
        gateway_connected=connected,
        prepared=entry is not None,
        authorized=authorized,
        started=started,
        market_ready=market_ready,
        ready_to_arm=bool(snapshot.get("ready_to_arm", False)),
        completed=completed,
        running=running,
        auto_arm=auto_arm,
        tick_count=tick_count,
        bar_count=bar_count,
        warmup_bars=_int_value(snapshot.get("warmup_bars")),
        readiness_bars=_int_value(snapshot.get("readiness_bars")),
        hold_bars=_int_value(snapshot.get("hold_bars")),
        bars_since_entry=_int_value(snapshot.get("bars_since_entry")),
        bar_timeout_seconds=bar_timeout_seconds,
        no_fill_timeout_seconds=no_fill_timeout_seconds,
        no_bar_wait_seconds=no_bar_wait_seconds,
        unfilled_wait_seconds=unfilled_wait_seconds,
        market_warning=market_warning,
        execution_issue=execution_issue,
        execution_warning=execution_warning,
        simulate_fill_enabled=simulate_fill_enabled,
        simulate_fill_allowed=simulate_fill_allowed,
        last_fill_source=str(snapshot.get("last_fill_source") or getattr(strategy, "_last_fill_source", "") or ""),
        last_bar_time=str(snapshot.get("last_bar_time") or ""),
        last_market_price=last_market_price,
        last_market_timestamp=_timestamp_to_text(last_market_timestamp),
        market_data_age_seconds=_market_data_age_seconds(last_market_timestamp),
        market_issue=market_issue,
        subscribed_symbols=subscribed_symbols,
        first_tick_bar_enabled=first_tick_bar_enabled,
        first_tick_bar_emitted=first_tick_bar_emitted,
        first_tick_bar_skip_reason=first_tick_bar_skip_reason,
        price_tick=float(snapshot.get("price_tick") or 0.0),
        aggressive_ticks=_int_value(snapshot.get("aggressive_ticks")),
        last_order_price=float(snapshot.get("last_order_price") or 0.0),
        last_order_pricing_source=str(snapshot.get("last_order_pricing_source") or ""),
        chase_enabled=bool(snapshot.get("chase_enabled", False)),
        chase_interval_seconds=float(snapshot.get("chase_interval_seconds") or 0.0),
        chase_attempts=_int_value(snapshot.get("chase_attempts")),
        chase_max_attempts=_int_value(snapshot.get("chase_max_attempts")),
        chase_step_ticks=_int_value(snapshot.get("chase_step_ticks")),
        chase_pending_cancel_order_id=str(snapshot.get("chase_pending_cancel_order_id") or ""),
        chase_resubmit_ready=bool(snapshot.get("chase_resubmit_ready", False)),
        chase_state=str(snapshot.get("chase_state") or ""),
        rate_retry_after_seconds=float(snapshot.get("rate_retry_after_seconds") or 0.0),
        last_chase_reason=str(snapshot.get("last_chase_reason") or ""),
        last_chase_order_id=str(snapshot.get("last_chase_order_id") or ""),
        last_chase_price=float(snapshot.get("last_chase_price") or 0.0),
        position_volume=position_volume,
        last_reject_reason=last_reject_reason_value,
        risk=risk,
        validation_errors=state["errors"] or config_errors,
        snapshot=snapshot,
        errors=state["errors"] or config_errors,
    )


def _action_response(
    trading_state: Any,
    action: str,
    message: str,
    *,
    success: bool = True,
) -> TrialRunActionResponse:
    return TrialRunActionResponse(
        success=success,
        action=action,
        message=message,
        status=_status_response(trading_state),
    )


def _cleanup_failed_trial_start(engine: Any, strategy: Any) -> bool:
    begin_shutdown = getattr(engine, "begin_trial_run_shutdown", None)
    if callable(begin_shutdown) and begin_shutdown(strategy) is False:
        return False
    try:
        engine.order_manager.stop()
    except Exception:
        pass
    unbind = getattr(engine, "unbind_trial_run_execution", None)
    if callable(unbind) and unbind() is False:
        return False
    clear_strategy = getattr(engine, "clear_strategy", None)
    if callable(clear_strategy):
        clear_strategy(strategy)
    elif getattr(engine, "strategy", None) is strategy:
        engine.strategy = None
    finish_shutdown = getattr(engine, "finish_trial_run_shutdown", None)
    if callable(finish_shutdown):
        finish_shutdown()
    return True


def _stop_trial_strategy(trading_state: Any, *, reset: bool = False) -> None:
    entry = trading_state.get(TRIAL_STRATEGY_ID)
    if entry is None:
        if reset:
            trial_run_state.reset()
        return

    strategy = entry.strategy
    engine = entry.engine
    begin_shutdown = getattr(engine, "begin_trial_run_shutdown", None)
    if not callable(begin_shutdown) or begin_shutdown(strategy) is False:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "trial_submission_in_flight",
                "message": "试运行订单正在提交登记，请稍后重试",
            },
        )
    try:
        engine.order_manager.stop()
    except Exception:
        pass

    execution = getattr(engine, "trial_run_execution", None)
    current_order_id = str(getattr(execution, "current_order_id", "") or "")
    if current_order_id:
        already_requested = str(
            getattr(strategy, "_chase_pending_cancel_order_id", "") or ""
        ) == current_order_id
        request_cancel = getattr(engine, "request_trial_run_shutdown_cancel", None)
        requested = bool(
            callable(request_cancel)
            and request_cancel(current_order_id, already_requested=already_requested)
        )
        trial_run_state.update(state="cancel_pending", authorized=False)
        if not requested:
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "trial_order_cancel_failed",
                    "message": f"试运行委托撤单请求失败: {current_order_id}",
                    "order_id": current_order_id,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "trial_order_cancel_pending",
                "message": f"已请求撤销试运行委托，等待券商确认: {current_order_id}",
                "order_id": current_order_id,
            },
        )

    gateway = getattr(engine, "gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "broker_gateway_unavailable",
                "message": "交易网关不可用，不能完成停止前对账",
            },
        )
    try:
        reconciliation = gateway.refresh_reconciliation(timeout_seconds=8.0)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"failure_code": "trial_reconciliation_required", "message": str(exc)},
        ) from exc
    if not isinstance(reconciliation, dict) or not (
        reconciliation.get("ok") is True and reconciliation.get("fresh") is True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "trial_reconciliation_required",
                "message": "停止试运行前必须取得新鲜、完整的券商委托与持仓快照",
            },
        )

    symbol = str(getattr(execution, "symbol", "") or getattr(strategy, "symbol", "") or "")
    active_orders = _active_broker_orders(gateway, symbol)
    position_volume = _broker_position_volume(gateway, symbol)
    if active_orders or position_volume:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "flatten_required",
                "message": "真实委托或持仓尚未归零，不能停止并清除试运行证据",
                "active_order_ids": [
                    str(getattr(order, "order_id", "") or "") for order in active_orders
                ],
                "position_volume": position_volume,
            },
        )

    resolve_untracked = getattr(engine, "resolve_untracked_trial_orders_after_reconciliation", None)
    if callable(resolve_untracked):
        resolve_untracked()
    unbind = getattr(engine, "unbind_trial_run_execution", None)
    if callable(unbind) and unbind() is False:
        raise HTTPException(
            status_code=409,
            detail={
                "failure_code": "trial_order_state_unresolved",
                "message": "试运行订单状态尚未完全收敛，不能解绑执行链",
            },
        )

    clear_simulation = getattr(engine, "clear_simulation_context", None)
    if callable(clear_simulation):
        clear_simulation()

    try:
        strategy.on_stop()
    except Exception:
        pass
    if _gateway_connected(engine):
        engine.status = TradingStatus.CONNECTED
    clear_strategy = getattr(engine, "clear_strategy", None)
    if callable(clear_strategy):
        clear_strategy(strategy)
    else:
        engine.strategy = None
    trading_state.unregister(TRIAL_STRATEGY_ID)
    finish_shutdown = getattr(engine, "finish_trial_run_shutdown", None)
    if callable(finish_shutdown):
        finish_shutdown()
    if reset:
        trial_run_state.reset()
    else:
        trial_run_state.update(state="stopped", authorized=False)


def trial_run_manual_open_enabled() -> bool:
    """Return False when an active trial-run config should block manual opens."""
    try:
        path = _configured_path(allow_example=False)
        if path is None:
            return True
        config = _load_config(path)
        trial_run = _dict_section(config, "trial_run")
        if trial_run.get("enabled") is not True:
            return True
        _allowed_symbol, errors = _validate_trial_config(config)
        if errors:
            return False
        return bool(trial_run.get("manual_open_enabled", False))
    except Exception:
        return False


def register_trial_run_routes(
    app: FastAPI,
    *,
    trading_state: Any,
    subscribe_market_ticks: Callable[[Any, List[str]], None],
    record_audit: Callable[..., None],
) -> None:
    @app.get(
        "/trial-run/config",
        response_model=TrialRunConfigResponse,
        summary="读取本地试运行配置",
        tags=["试运行"],
    )
    def get_trial_run_config():
        try:
            path, config, allowed_symbol, errors = _read_trial_config(allow_example=True)
        except Exception as exc:
            return TrialRunConfigResponse(valid=False, config_path="", config={}, errors=[str(exc)])
        return _safe_config_response(path, config, allowed_symbol, errors)

    @app.get(
        "/trial-run/status",
        response_model=TrialRunStatusResponse,
        summary="试运行状态",
        tags=["试运行"],
    )
    def get_trial_run_status():
        return _status_response(trading_state)

    @app.post(
        "/trial-run/prepare",
        response_model=TrialRunActionResponse,
        summary="准备 VerifyStrategy 试运行",
        tags=["试运行"],
    )
    @_exclusive_trial_prepare
    def prepare_trial_run(request: Request):
        try:
            path, config, allowed_symbol, errors = _read_trial_config(allow_example=True)
        except FileNotFoundError as exc:
            trial_run_state.update(state="error", errors=[str(exc)], authorized=False)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            trial_run_state.update(state="error", errors=[str(exc)], authorized=False)
            raise HTTPException(status_code=400, detail=f"试运行配置读取失败: {exc}") from exc
        if errors:
            trial_run_state.update(state="error", allowed_symbol=allowed_symbol, errors=errors, authorized=False)
            raise HTTPException(status_code=400, detail={"errors": errors, "config_path": path.name})

        runtime_config = trading_state.main_config_snapshot()
        if not _simulation_environment_allowed(config, runtime_config):
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "trial_run_environment_not_allowed",
                    "message": "试运行仅允许配置与登录环境一致的测试或仿真柜台",
                },
            )

        engine = trading_state.primary_engine()
        if engine is None or not _gateway_connected(engine):
            raise HTTPException(status_code=503, detail="交易主引擎未连接")

        risk_config = copy.deepcopy(config.get("risk", {}))
        engine.risk_manager.configure({"risk": risk_config})
        _require_trial_preflight(trading_state, engine, allowed_symbol)
        _stop_trial_strategy(trading_state)
        if getattr(engine, "trial_run_execution", None) is not None or bool(
            getattr(engine, "has_untracked_trial_orders", lambda: False)()
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "trial_order_state_unresolved",
                    "message": "上一轮试运行执行域尚未完成撤单与对账，不能开始新一轮",
                },
            )

        from ..strategy import create_strategy

        strategy_config = copy.deepcopy(config.get("strategy", {}))
        trial_config = _dict_section(config, "trial_run")
        strategy_config["auto_arm"] = _bool_value(trial_config.get("auto_arm"), True)
        if strategy_config["auto_arm"]:
            strategy_config["warmup_bars"] = 1
            strategy_config["readiness_bars"] = 1
        strategy = create_strategy("verify", strategy_config)
        engine.set_strategy(strategy)
        trading_config = copy.deepcopy(config.get("trading", {}))
        start_config = {**trading_config, "risk": risk_config}
        if strategy_config["auto_arm"]:
            start_config["emit_first_tick_bar"] = True
        execution = trial_run_state.start_execution(
            allowed_symbol,
            volume=_int_value(strategy_config.get("volume"), 1),
        )
        bind = getattr(engine, "bind_trial_run_execution", None)
        if not callable(bind):
            trial_run_state.clear_execution()
            clear_strategy = getattr(engine, "clear_strategy", None)
            if callable(clear_strategy):
                clear_strategy(strategy)
            raise HTTPException(status_code=500, detail="trial-run execution binding unavailable")
        bind(execution)
        trading_state.register(TRIAL_STRATEGY_ID, strategy, engine, _without_secret_fields(config))
        try:
            started = engine.start(start_config)
        except Exception:
            if _cleanup_failed_trial_start(engine, strategy):
                trading_state.unregister(TRIAL_STRATEGY_ID)
                trial_run_state.clear_execution()
            else:
                trial_run_state.update(
                    state="error",
                    allowed_symbol=allowed_symbol,
                    errors=["试运行启动异常且订单状态尚未收敛"],
                    authorized=False,
                )
            raise
        if not started:
            if _cleanup_failed_trial_start(engine, strategy):
                trading_state.unregister(TRIAL_STRATEGY_ID)
                trial_run_state.clear_execution()
            trial_run_state.update(state="error", allowed_symbol=allowed_symbol, errors=["试运行策略启动失败"], authorized=False)
            raise HTTPException(status_code=500, detail="试运行策略启动失败")

        subscribe_market_ticks(engine, [allowed_symbol])
        trial_run_state.update(
            state="prepared",
            allowed_symbol=allowed_symbol,
            errors=[],
            authorized=bool(strategy_config["auto_arm"]),
            mark_prepared=True,
        )
        record_audit(
            "trial_run",
            "prepare",
            "success",
            request=request,
            resource=TRIAL_STRATEGY_ID,
            detail={"allowed_symbol": allowed_symbol},
        )
        return _action_response(trading_state, "prepare", "试运行策略已准备")

    def _start_trial_run(request: Request, *, action: str) -> TrialRunActionResponse:
        entry = trading_state.get(TRIAL_STRATEGY_ID)
        if entry is None:
            raise HTTPException(status_code=409, detail="试运行策略尚未准备")
        start = getattr(entry.strategy, "start_verification", None)
        if not callable(start):
            start = getattr(entry.strategy, "authorize_trading", None)
        if not callable(start) or start() is not True:
            trial_run_state.update(state="prepared", authorized=False)
            record_audit(
                "trial_run",
                action,
                "rejected",
                request=request,
                resource=TRIAL_STRATEGY_ID,
                detail={"reason": "start_verification returned false"},
            )
            raise HTTPException(status_code=409, detail="VerifyStrategy 尚未行情就绪，不能开始验证交易")
        trial_run_state.update(state="started", authorized=True, errors=[])
        record_audit("trial_run", action, "success", request=request, resource=TRIAL_STRATEGY_ID)
        return _action_response(trading_state, action, "验证交易已开始")

    @app.post(
        "/trial-run/start",
        response_model=TrialRunActionResponse,
        summary="开始试运行验证交易",
        tags=["试运行"],
    )
    @_exclusive_trial_prepare
    def start_trial_run(request: Request):
        return _start_trial_run(request, action="start")

    @app.post(
        "/trial-run/arm",
        response_model=TrialRunActionResponse,
        summary="兼容旧版授权试运行交易",
        tags=["试运行"],
    )
    @_exclusive_trial_prepare
    def arm_trial_run(request: Request):
        return _start_trial_run(request, action="arm")

    @app.post(
        "/trial-run/simulation/prepare",
        response_model=TrialRunActionResponse,
        summary="Prepare the isolated simulation ledger",
        tags=["trial-run"],
    )
    @_exclusive_trial_prepare
    def prepare_trial_run_simulation(
        body: TrialRunSimulationPrepareRequest,
        request: Request,
    ):
        try:
            _, config, _, errors = _read_trial_config(allow_example=True)
        except Exception as exc:
            raise HTTPException(status_code=409, detail={"failure_code": "trial_run_config_invalid", "message": str(exc)}) from exc
        runtime_config = trading_state.main_config_snapshot()
        if errors or not _simulate_fill_enabled(config, runtime_config):
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "simulation_environment_not_allowed",
                    "message": "模拟账本仅允许在配置与运行时均为测试或仿真环境时启用",
                },
            )
        engine = trading_state.primary_engine()
        entry = trading_state.get(TRIAL_STRATEGY_ID)
        strategy = getattr(entry, "strategy", None) if entry else None
        execution = getattr(engine, "trial_run_execution", None) if engine is not None else None
        source_order_id = str(body.source_order_id or "")
        if engine is None or strategy is None or execution is None:
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "trial_run_not_ready", "message": "试运行执行域尚未准备"},
            )

        existing_source = str(getattr(engine, "_simulation_source_order_id", "") or "")
        if getattr(engine, "simulation_adapter", None) is not None:
            if source_order_id != existing_source:
                raise HTTPException(
                    status_code=409,
                    detail={"failure_code": "order_not_current", "message": "模拟账本已绑定其他源订单"},
                )
            return _action_response(trading_state, "simulation_prepare", "模拟账本已准备")

        source = next(
            (order for order in execution.order_chain if order.order_id == source_order_id),
            None,
        )
        if source is None:
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "order_not_owned_by_trial_run", "message": "源订单不属于当前试运行"},
            )

        if existing_source and source_order_id != existing_source:
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "order_not_current", "message": "源订单不是当前模拟迁移订单"},
            )

        if not existing_source:
            allowed = _simulation_prepare_allowed(
                engine,
                strategy,
                execution.serialize(),
                source_order_id,
                max(1.0, _float_value(_dict_section(config, "trial_run").get("no_fill_timeout_seconds"), 2.0)),
            )
            if not allowed:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "failure_code": "simulation_prepare_not_allowed",
                        "message": "真实订单尚未获得成交等待资格或已不再是当前订单",
                    },
                )
            try:
                already_chasing = engine.begin_simulation_request(source_order_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "failure_code": "simulation_prepare_not_allowed",
                        "message": str(exc),
                    },
                ) from exc
            if not already_chasing:
                if not engine.cancel_order(source_order_id):
                    latest_source = next(
                        (
                            order
                            for order in execution.order_chain
                            if order.order_id == source_order_id
                        ),
                        None,
                    )
                    if latest_source is None or str(latest_source.status).lower() not in {
                        "cancelled",
                        "canceled",
                    }:
                        engine.rollback_simulation_request(source_order_id)
                        raise HTTPException(
                            status_code=409,
                            detail={"failure_code": "cancel_request_failed", "message": "券商撤单请求未发出"},
                        )
            trial_run_state.update(state="simulation_cancel_pending")
            record_audit(
                "trial_run",
                "simulation_prepare",
                "cancel_pending",
                request=request,
                resource=TRIAL_STRATEGY_ID,
                detail={"source_order_id": source_order_id},
            )
            return _action_response(trading_state, "simulation_prepare", "等待券商撤单确认")

        source_status = str(source.status).lower()
        if source_status == "filled" or execution.real_trade_ids:
            engine.rollback_simulation_request(source_order_id)
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "real_fill_prevents_simulation",
                    "message": "真实委托已成交，继续按真实成交链路完成试运行",
                },
            )
        if source_status in {"rejected", "failed"}:
            engine.rollback_simulation_request(source_order_id)
            try:
                execution.mark_failed(
                    "source_order_not_cancelled",
                    f"source_status={source_status}",
                )
            except Exception:
                pass
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "source_order_not_cancelled",
                    "message": "源委托未取得券商撤单确认，不能迁移到模拟账本",
                },
            )
        if source_status not in {"cancelled", "canceled"}:
            return _action_response(trading_state, "simulation_prepare", "等待券商撤单确认", success=False)
        gateway = getattr(engine, "gateway", None)
        try:
            reconciliation = gateway.refresh_reconciliation(timeout_seconds=8.0)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "broker_snapshot_failed", "message": str(exc)},
            ) from exc
        if not isinstance(reconciliation, dict) or reconciliation.get("ok") is not True or reconciliation.get("fresh") is not True:
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "broker_snapshot_unavailable", "message": "等待新鲜券商对账快照"},
            )
        active_orders = _active_broker_orders(gateway, execution.symbol)
        position_volume = _broker_position_volume(gateway, execution.symbol)
        if active_orders or position_volume:
            raise HTTPException(
                status_code=409,
                detail={
                    "failure_code": "broker_state_not_flat",
                    "message": "撤单确认后目标合约仍有活动委托或持仓",
                },
            )
        execution.evaluate(
            broker_position_volume=position_volume,
            broker_active_order_ids=[getattr(item, "order_id", "") for item in active_orders],
            reconcile_ok=True,
        )
        ledger = TrialRunSimulationLedger()
        try:
            broker_order = next(
                (
                    item
                    for item in getattr(gateway, "orders", {}).values()
                    if getattr(item, "order_id", "") == source_order_id
                ),
                None,
            )
            if broker_order is None:
                broker_order = next(
                    (
                        item
                        for item in (gateway.query_orders() or [])
                        if getattr(item, "order_id", "") == source_order_id
                    ),
                    None,
                )
            if broker_order is None:
                raise SimulationLedgerError("order_not_owned_by_trial_run")
            broker_status = str(
                _enum_value(getattr(broker_order, "status", "")) or ""
            ).strip().lower()
            if broker_status not in {"cancelled", "canceled"}:
                raise SimulationLedgerError("source_order_not_cancelled")
            if _int_value(getattr(broker_order, "traded_volume", 0)) != 0:
                raise SimulationLedgerError("real_fill_prevents_simulation")
            ledger.create_entry_from_order(
                copy.deepcopy(broker_order)
            )
        except (StopIteration, ValueError, SimulationLedgerError) as exc:
            failure_code = getattr(exc, "failure_code", "simulation_entry_create_failed")
            raise HTTPException(
                status_code=409,
                detail={"failure_code": failure_code, "message": str(exc)},
            ) from exc
        try:
            engine.activate_simulation(ledger, source_order_id=source_order_id)
        except Exception as exc:
            failure_code = str(exc) or "simulation_activation_failed"
            raise HTTPException(
                status_code=409,
                detail={"failure_code": failure_code, "message": str(exc)},
            ) from exc
        trial_run_state.update(state="simulation_ready", errors=[])
        record_audit(
            "trial_run",
            "simulation_prepare",
            "success",
            request=request,
            resource=TRIAL_STRATEGY_ID,
            detail={"source_order_id": source_order_id, "current_order_id": ledger.current_order_id},
        )
        return _action_response(trading_state, "simulation_prepare", "隔离模拟账本已准备")

    @app.post(
        "/trial-run/simulate-fill",
        response_model=TrialRunSimulateFillResponse,
        summary="Simulate a trial-run order fill",
        tags=["trial-run"],
    )
    @_exclusive_trial_prepare
    def simulate_trial_run_fill(body: TrialRunSimulateFillRequest, request: Request):
        engine = trading_state.primary_engine()
        entry = trading_state.get(TRIAL_STRATEGY_ID)
        execution = getattr(engine, "trial_run_execution", None) if engine is not None else None
        adapter = getattr(engine, "simulation_adapter", None) if engine is not None else None
        try:
            _, config, _, errors = _read_trial_config(allow_example=True)
        except Exception as exc:
            errors = [str(exc)]
            config = {}
        if errors or not _simulate_fill_enabled(config, trading_state.main_config_snapshot()):
            failure_code = "simulation_environment_not_allowed"
            raise HTTPException(status_code=409, detail={"failure_code": failure_code, "message": "模拟成交未启用"})
        if execution is None or entry is None or adapter is None or not getattr(adapter, "is_simulation", False):
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "simulation_not_ready", "message": "隔离模拟账本尚未准备"},
            )
        if str(body.order_id or "") != str(execution.current_order_id or ""):
            raise HTTPException(
                status_code=409,
                detail={"failure_code": "order_not_current", "message": "只能成交当前模拟订单"},
            )
        try:
            result = adapter.fill(body.order_id)
        except (SimulationLedgerError, RuntimeError) as exc:
            failure_code = getattr(exc, "failure_code", "simulation_evidence_conflict")
            raise HTTPException(
                status_code=409,
                detail={"failure_code": failure_code, "message": str(exc)},
            ) from exc
        record_audit(
            "trial_run",
            "simulate_fill",
            "success",
            request=request,
            resource=TRIAL_STRATEGY_ID,
            detail={"order_id": body.order_id, "trade_id": result.trade.trade_id},
        )
        return TrialRunSimulateFillResponse(
            success=True,
            message="模拟成交已记录",
            order=copy.deepcopy(result.order.__dict__),
            trade=copy.deepcopy(result.trade.__dict__),
            status=_status_response(trading_state),
        )

    @app.post(
        "/trial-run/stop",
        response_model=TrialRunActionResponse,
        summary="停止试运行策略",
        tags=["试运行"],
    )
    @_exclusive_trial_prepare
    def stop_trial_run(request: Request):
        _stop_trial_strategy(trading_state)
        record_audit("trial_run", "stop", "success", request=request, resource=TRIAL_STRATEGY_ID)
        return _action_response(trading_state, "stop", "试运行策略已停止")

    @app.post(
        "/trial-run/reset",
        response_model=TrialRunActionResponse,
        summary="重置试运行状态",
        tags=["试运行"],
    )
    @_exclusive_trial_prepare
    def reset_trial_run(request: Request):
        _stop_trial_strategy(trading_state, reset=True)
        record_audit("trial_run", "reset", "success", request=request, resource=TRIAL_STRATEGY_ID)
        return _action_response(trading_state, "reset", "试运行状态已重置")
