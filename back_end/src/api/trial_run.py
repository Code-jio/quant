"""Trial-run API routes and configuration guardrails."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime
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
_NO_TICK_DIAGNOSTIC_SECONDS = 15.0


class _TrialRunState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.state = "idle"
        self.allowed_symbol = ""
        self.last_errors: List[str] = []
        self.authorized = False
        self.prepared_at = 0.0

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

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "allowed_symbol": self.allowed_symbol,
                "errors": list(self.last_errors),
                "authorized": self.authorized,
                "prepared_at": self.prepared_at,
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


def _normalize_symbol_key(symbol: Any) -> str:
    raw = str(symbol or "").strip().lower()
    if "." not in raw:
        return raw
    parts = [part for part in raw.split(".") if part]
    for part in parts:
        if any(ch.isdigit() for ch in part):
            return part
    return parts[0] if parts else raw


def _symbols_match(left: Any, right: Any) -> bool:
    return _normalize_symbol_key(left) == _normalize_symbol_key(right)


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
        if len({_normalize_symbol_key(trial_symbol), _normalize_symbol_key(strategy_symbol), _normalize_symbol_key(risk_symbol)}) != 1:
            errors.append("trial_run.allowed_symbol、strategy.symbol、risk.allowed_symbols[0] 必须一致")
    if strategy.get("name") != "verify":
        errors.append("strategy.name 必须为 verify")
    if _int_value(strategy.get("volume")) != 1:
        errors.append("strategy.volume 必须为 1")
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
    auto_arm = _bool_value(trial_run.get("auto_arm"), True)
    bar_timeout_seconds = max(1.0, _float_value(trial_run.get("bar_timeout_seconds"), 90.0))
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
        "auto_arm": auto_arm,
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


def _status_response(trading_state: Any) -> TrialRunStatusResponse:
    state = trial_run_state.snapshot()
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
    trial_config = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
    auto_arm = _bool_value(trial_config.get("auto_arm"), True)
    bar_timeout_seconds = max(1.0, _float_value(trial_config.get("bar_timeout_seconds"), 90.0))
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
    emitted_symbols = getattr(engine, "_first_tick_bar_emitted_symbols", set()) if engine is not None else set()
    first_tick_bar_emitted = bar_count > 0 or _normalize_symbol_key(symbol) in set(emitted_symbols or [])
    first_tick_bar_skip_reason = str(getattr(engine, "_first_tick_bar_skip_reason", "") or "") if engine is not None else ""
    last_reject_reason_value = str(snapshot.get("last_reject_reason") or last_reject_reason or "")
    market_issue = ""
    if last_reject_reason_value == "invalid_market_price":
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
        auto_arm=auto_arm,
        tick_count=tick_count,
        bar_count=bar_count,
        warmup_bars=_int_value(snapshot.get("warmup_bars")),
        readiness_bars=_int_value(snapshot.get("readiness_bars")),
        hold_bars=_int_value(snapshot.get("hold_bars")),
        bars_since_entry=_int_value(snapshot.get("bars_since_entry")),
        bar_timeout_seconds=bar_timeout_seconds,
        no_bar_wait_seconds=no_bar_wait_seconds,
        market_warning=market_warning,
        last_bar_time=str(snapshot.get("last_bar_time") or ""),
        last_market_price=last_market_price,
        last_market_timestamp=_timestamp_to_text(last_market_timestamp),
        market_data_age_seconds=_market_data_age_seconds(last_market_timestamp),
        market_issue=market_issue,
        subscribed_symbols=subscribed_symbols,
        first_tick_bar_enabled=first_tick_bar_enabled,
        first_tick_bar_emitted=first_tick_bar_emitted,
        first_tick_bar_skip_reason=first_tick_bar_skip_reason,
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
        trial_config = config.get("trial_run") if isinstance(config.get("trial_run"), dict) else {}
        strategy_config["auto_arm"] = _bool_value(trial_config.get("auto_arm"), True)
        if strategy_config["auto_arm"]:
            strategy_config["warmup_bars"] = 1
            strategy_config["readiness_bars"] = 1
        strategy = create_strategy("verify", strategy_config)
        engine.set_strategy(strategy)
        trading_config = copy.deepcopy(config.get("trading", {}))
        risk_config = copy.deepcopy(config.get("risk", {}))
        start_config = {**trading_config, "risk": risk_config}
        if strategy_config["auto_arm"]:
            start_config["emit_first_tick_bar"] = True
        if not engine.start(start_config):
            trial_run_state.update(state="error", allowed_symbol=allowed_symbol, errors=["试运行策略启动失败"], authorized=False)
            raise HTTPException(status_code=500, detail="试运行策略启动失败")

        trading_state.register(TRIAL_STRATEGY_ID, strategy, engine, _without_secret_fields(config))
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
