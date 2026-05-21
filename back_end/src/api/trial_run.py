"""Trial-run API routes and configuration guardrails."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request

from ..trading import TradingStatus
from .models import TrialRunActionResponse, TrialRunConfigResponse, TrialRunStatusResponse

TRIAL_STRATEGY_ID = "verify_trial"

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _BACKEND_ROOT / "config"
_LOCAL_CONFIG = _CONFIG_DIR / "config.local.json"
_EXAMPLE_CONFIG = _CONFIG_DIR / "config.example.json"


class _TrialRunState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.state = "idle"
        self.allowed_symbol = ""
        self.last_errors: List[str] = []
        self.authorized = False

    def update(
        self,
        *,
        state: Optional[str] = None,
        allowed_symbol: Optional[str] = None,
        errors: Optional[List[str]] = None,
        authorized: Optional[bool] = None,
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

    def reset(self) -> None:
        self.update(state="idle", allowed_symbol="", errors=[], authorized=False)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "allowed_symbol": self.allowed_symbol,
                "errors": list(self.last_errors),
                "authorized": self.authorized,
            }


trial_run_state = _TrialRunState()


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


def _mask_account_id(account_id: Any) -> str:
    value = str(account_id or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}****{value[-2:]}"


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip()


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


def _validate_trial_config(config: Dict[str, Any]) -> tuple[str, List[str]]:
    errors: List[str] = []
    trial_run = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
    strategy = config.get("strategy") if isinstance(config.get("strategy"), dict) else {}
    risk = config.get("risk") if isinstance(config.get("risk"), dict) else {}

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
    if trial_symbol and strategy_symbol and risk_symbol:
        if len({trial_symbol, strategy_symbol, risk_symbol}) != 1:
            errors.append("trial_run.allowed_symbol、strategy.symbol、risk.allowed_symbols[0] 必须一致")
    if strategy.get("name") != "verify":
        errors.append("strategy.name 必须为 verify")
    if _int_value(strategy.get("volume")) != 1:
        errors.append("strategy.volume 必须为 1")
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
    trial_run = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
    trading = config.get("trading") if isinstance(config.get("trading"), dict) else {}
    strategy = config.get("strategy") if isinstance(config.get("strategy"), dict) else {}
    risk = config.get("risk") if isinstance(config.get("risk"), dict) else {}
    raw_account_id = str(trial_run.get("account_id") or trading.get("username") or "").strip()
    environment = str(
        trial_run.get("vnpy_environment")
        or trading.get("vnpy_environment")
        or trading.get("environment")
        or "测试"
    )
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
        {"gateway", "broker_id", "td_server", "md_server", "app_id", "auth_code", "vnpy_environment", "environment", "fronts"},
    )
    safe_trading.update({"gateway": gateway, "vnpy_environment": environment, "environment": environment})
    safe_trial_run = {
        "enabled": trial_run.get("enabled") is True,
        "allowed_symbol": allowed_symbol,
        "manual_open_enabled": bool(trial_run.get("manual_open_enabled", False)),
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


def _status_response(trading_state: Any) -> TrialRunStatusResponse:
    state = trial_run_state.snapshot()
    entry = trading_state.get(TRIAL_STRATEGY_ID)
    engine = trading_state.primary_engine()
    strategy = getattr(entry, "strategy", None) if entry else None
    snapshot = _strategy_snapshot(strategy) if strategy else {}
    authorized = bool(snapshot.get("authorized", state["authorized"]))
    started = bool(snapshot.get("started", authorized))
    market_ready = bool(snapshot.get("market_ready", snapshot.get("ready_to_arm", False)))
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
    try:
        _, _, allowed_symbol, config_errors = _read_trial_config(allow_example=True)
        config_valid = not config_errors
    except Exception as exc:
        allowed_symbol = state["allowed_symbol"]
        config_errors = [str(exc)]

    response_state = str(snapshot.get("state") or state["state"] or ("connected" if connected else "disconnected"))
    if risk.get("emergency_stop"):
        response_state = "emergency_stopped"
    symbol = str(snapshot.get("symbol") or state["allowed_symbol"] or allowed_symbol or "")
    position_volume = 0
    if strategy is not None and symbol:
        try:
            position_volume = int(getattr(strategy.get_position(symbol), "volume", 0) or 0)
        except Exception:
            position_volume = 0

    return TrialRunStatusResponse(
        state=response_state,
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
        completed=bool(snapshot.get("completed", False)),
        running=running,
        bar_count=_int_value(snapshot.get("bar_count")),
        warmup_bars=_int_value(snapshot.get("warmup_bars")),
        readiness_bars=_int_value(snapshot.get("readiness_bars")),
        hold_bars=_int_value(snapshot.get("hold_bars")),
        bars_since_entry=_int_value(snapshot.get("bars_since_entry")),
        position_volume=position_volume,
        last_reject_reason=last_reject_reason,
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


def _stop_trial_strategy(trading_state: Any, *, reset: bool = False) -> None:
    entry = trading_state.get(TRIAL_STRATEGY_ID)
    if entry is None:
        if reset:
            trial_run_state.reset()
        return

    strategy = entry.strategy
    revoke = getattr(strategy, "revoke_authorization", None)
    if callable(revoke):
        revoke()
    try:
        strategy.on_stop()
    except Exception:
        pass
    try:
        entry.engine.order_manager.stop()
    except Exception:
        pass
    if _gateway_connected(entry.engine):
        entry.engine.status = TradingStatus.CONNECTED
    entry.engine.strategy = None
    trading_state.unregister(TRIAL_STRATEGY_ID)
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
        trial_run = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
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

        engine = trading_state.primary_engine()
        if engine is None or not _gateway_connected(engine):
            raise HTTPException(status_code=503, detail="交易主引擎未连接")

        _stop_trial_strategy(trading_state)

        from ..strategy import create_strategy

        strategy_config = copy.deepcopy(config.get("strategy", {}))
        strategy = create_strategy("verify", strategy_config)
        engine.set_strategy(strategy)
        trading_config = copy.deepcopy(config.get("trading", {}))
        risk_config = copy.deepcopy(config.get("risk", {}))
        start_config = {**trading_config, "risk": risk_config}
        if not engine.start(start_config):
            trial_run_state.update(state="error", allowed_symbol=allowed_symbol, errors=["试运行策略启动失败"], authorized=False)
            raise HTTPException(status_code=500, detail="试运行策略启动失败")

        trading_state.register(TRIAL_STRATEGY_ID, strategy, engine, _without_secret_fields(config))
        subscribe_market_ticks(engine, [allowed_symbol])
        trial_run_state.update(state="prepared", allowed_symbol=allowed_symbol, errors=[], authorized=False)
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
    def start_trial_run(request: Request):
        return _start_trial_run(request, action="start")

    @app.post(
        "/trial-run/arm",
        response_model=TrialRunActionResponse,
        summary="兼容旧版授权试运行交易",
        tags=["试运行"],
    )
    def arm_trial_run(request: Request):
        return _start_trial_run(request, action="arm")

    @app.post(
        "/trial-run/stop",
        response_model=TrialRunActionResponse,
        summary="停止试运行策略",
        tags=["试运行"],
    )
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
    def reset_trial_run(request: Request):
        _stop_trial_strategy(trading_state, reset=True)
        record_audit("trial_run", "reset", "success", request=request, resource=TRIAL_STRATEGY_ID)
        return _action_response(trading_state, "reset", "试运行状态已重置")
