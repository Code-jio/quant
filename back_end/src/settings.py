"""Runtime settings helpers.

Keep production-sensitive defaults out of source code. Local/demo defaults can
still be supplied through environment variables or ignored config files.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit


FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
PRODUCTION_ENV_VALUES = {"prod", "production", "live"}

# These five counters are compliance alert thresholds only.  They may be
# adjusted while the service is running; every other risk field is a hard
# server-side limit and requires an operator-owned config change + restart.
LIVE_MUTABLE_RISK_FIELDS = frozenset(
    {
        "order_count_alert_threshold",
        "cancel_count_alert_threshold",
        "duplicate_open_alert_threshold",
        "duplicate_close_alert_threshold",
        "duplicate_cancel_alert_threshold",
    }
)

PRODUCTION_RISK_REQUIRED_FIELDS = frozenset(
    {
        "enabled",
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
        "duplicate_cancel_window_seconds",
        "default_contract_multiplier",
        "contract_multipliers",
        "allow_market_orders",
        "allowed_symbols",
        "blocked_symbols",
        *LIVE_MUTABLE_RISK_FIELDS,
    }
)

PRODUCTION_RISK_POSITIVE_INTEGER_FIELDS = frozenset(
    {
        "max_order_volume",
        "max_position_volume",
        "max_active_orders",
        "max_orders_per_minute",
        *LIVE_MUTABLE_RISK_FIELDS,
    }
)
PRODUCTION_RISK_POSITIVE_NUMBER_FIELDS = frozenset(
    {
        "max_order_value",
        "max_position_value",
        "max_market_data_age_seconds",
        "duplicate_signal_window_seconds",
        "duplicate_cancel_window_seconds",
        "default_contract_multiplier",
    }
)
PRODUCTION_RISK_UNIT_INTERVAL_FIELDS = frozenset(
    {
        "max_daily_loss_ratio",
        "max_price_deviation",
    }
)
PRODUCTION_RISK_BOOLEAN_FIELDS = frozenset({"enabled", "allow_market_orders"})


def env_text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def is_production_env() -> bool:
    return env_text("QUANT_ENV", "development").lower() in PRODUCTION_ENV_VALUES


def synthetic_data_enabled() -> bool:
    """Whether code paths may auto-generate demo/synthetic market data."""
    if "QUANT_ALLOW_SYNTHETIC_DATA" in os.environ:
        return env_bool("QUANT_ALLOW_SYNTHETIC_DATA", default=True)
    return not is_production_env()


def websocket_query_token_enabled() -> bool:
    """Allow websocket ?token= only for explicit legacy/local compatibility."""
    return env_bool("QUANT_ALLOW_WS_QUERY_TOKEN", default=False)


def secure_session_cookie_enabled() -> bool:
    """Set Secure on auth cookies in production unless explicitly overridden."""
    if "QUANT_SESSION_COOKIE_SECURE" in os.environ:
        return env_bool("QUANT_SESSION_COOKIE_SECURE", default=True)
    return is_production_env()


def runtime_risk_defaults() -> Dict[str, object]:
    """Conservative live-trading risk defaults, overridable by env/config."""
    return {
        "enabled": True,
        "max_order_volume": env_int("QUANT_RISK_MAX_ORDER_VOLUME", 100),
        "max_position_volume": env_int("QUANT_RISK_MAX_POSITION_VOLUME", 1000),
        "max_active_orders": env_int("QUANT_RISK_MAX_ACTIVE_ORDERS", 50),
        "max_orders_per_minute": env_int("QUANT_RISK_MAX_ORDERS_PER_MINUTE", 30),
        "max_daily_loss_ratio": env_float("QUANT_RISK_MAX_DAILY_LOSS_RATIO", 0.03),
        "max_order_value": env_float("QUANT_RISK_MAX_ORDER_VALUE", 1_000_000),
        "max_position_value": env_float("QUANT_RISK_MAX_POSITION_VALUE", 3_000_000),
        "max_price_deviation": env_float("QUANT_RISK_MAX_PRICE_DEVIATION", 0.01),
        "max_market_data_age_seconds": env_float("QUANT_RISK_MAX_MARKET_DATA_AGE_SECONDS", 10),
        "duplicate_signal_window_seconds": env_float("QUANT_RISK_DUPLICATE_SIGNAL_WINDOW_SECONDS", 5),
        "order_count_alert_threshold": env_int("QUANT_RISK_ORDER_COUNT_ALERT_THRESHOLD", 500),
        "cancel_count_alert_threshold": env_int("QUANT_RISK_CANCEL_COUNT_ALERT_THRESHOLD", 300),
        "duplicate_open_alert_threshold": env_int("QUANT_RISK_DUPLICATE_OPEN_ALERT_THRESHOLD", 1),
        "duplicate_close_alert_threshold": env_int("QUANT_RISK_DUPLICATE_CLOSE_ALERT_THRESHOLD", 1),
        "duplicate_cancel_alert_threshold": env_int("QUANT_RISK_DUPLICATE_CANCEL_ALERT_THRESHOLD", 1),
        "duplicate_cancel_window_seconds": env_float("QUANT_RISK_DUPLICATE_CANCEL_WINDOW_SECONDS", 5),
        "default_contract_multiplier": env_float("QUANT_RISK_DEFAULT_CONTRACT_MULTIPLIER", 10),
        "contract_multipliers": {},
        "allow_market_orders": env_bool("QUANT_RISK_ALLOW_MARKET_ORDERS", default=False),
        "allowed_symbols": [],
        "blocked_symbols": [],
    }


def _production_live_config_path() -> Path:
    configured = env_text("QUANT_LIVE_CONFIG_PATH")
    if not configured:
        raise RuntimeError(
            "QUANT_LIVE_CONFIG_PATH is required in production; copy "
            "config/config.production.example.json to an ignored local file"
        )
    return Path(configured).expanduser().resolve()


def _read_json_object(path: Path) -> Dict[str, Any]:
    try:
        # Python's stdlib decoder otherwise accepts the non-standard constants
        # NaN/Infinity.  A production risk limit must always be a real, finite
        # JSON number, so reject those spellings before any coercion can occur.
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"invalid JSON numeric constant: {constant}")
            ),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"production live config does not exist: {path}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"production live config is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("production live config root must be a JSON object")
    return payload


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_production_live_risk_config(raw_risk: Dict[str, Any]) -> None:
    """Validate raw JSON before ``RiskConfig`` applies compatibility coercions.

    ``RiskConfig.from_mapping`` deliberately tolerates old local configuration
    formats.  Production must not turn a malformed limit into a different,
    apparently valid limit, therefore validation happens on the JSON values.
    """
    for name in PRODUCTION_RISK_BOOLEAN_FIELDS:
        if type(raw_risk.get(name)) is not bool:
            raise RuntimeError(f"production live risk config {name} must be a JSON boolean")

    for name in PRODUCTION_RISK_POSITIVE_INTEGER_FIELDS:
        value = raw_risk.get(name)
        if type(value) is not int or value <= 0:
            raise RuntimeError(
                f"production live risk config {name} must be a positive JSON integer"
            )

    for name in PRODUCTION_RISK_POSITIVE_NUMBER_FIELDS:
        value = raw_risk.get(name)
        if not _is_finite_number(value) or value <= 0:
            raise RuntimeError(
                f"production live risk config {name} must be a positive finite JSON number"
            )

    for name in PRODUCTION_RISK_UNIT_INTERVAL_FIELDS:
        value = raw_risk.get(name)
        if not _is_finite_number(value) or not 0 < value <= 1:
            raise RuntimeError(
                f"production live risk config {name} must be a finite number in (0, 1]"
            )

    normalized_symbols: Dict[str, set[str]] = {}
    for name in ("allowed_symbols", "blocked_symbols"):
        values = raw_risk.get(name)
        if not isinstance(values, list) or any(
            not isinstance(symbol, str) or not symbol.strip() for symbol in values
        ):
            raise RuntimeError(
                f"production live risk config {name} must be a list of non-empty strings"
            )
        normalized_symbols[name] = {symbol.strip() for symbol in values}
    overlap = normalized_symbols["allowed_symbols"] & normalized_symbols["blocked_symbols"]
    if overlap:
        raise RuntimeError(
            "production live risk config allowed_symbols and blocked_symbols must not overlap"
        )

    multipliers = raw_risk.get("contract_multipliers")
    if not isinstance(multipliers, dict):
        raise RuntimeError("production live risk config contract_multipliers must be an object")
    for symbol, multiplier in multipliers.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise RuntimeError(
                "production live risk config contract_multipliers keys must be non-empty strings"
            )
        if not _is_finite_number(multiplier) or multiplier <= 0:
            raise RuntimeError(
                "production live risk config contract_multipliers values must be positive finite numbers"
            )


def server_live_risk_config() -> Dict[str, object]:
    """Return the server-authoritative live risk configuration.

    Development and tests keep the existing environment-based defaults.
    Production must use an explicit ignored local JSON file and fails closed
    when any hard limit or compliance threshold is missing.
    """
    if not is_production_env():
        return runtime_risk_defaults()

    payload = _read_json_object(_production_live_config_path())
    if str(payload.get("mode") or "").strip().lower() != "live":
        raise RuntimeError("production live config must set mode to 'live'")

    trading = payload.get("trading")
    if not isinstance(trading, dict):
        raise RuntimeError("production live config must contain a trading object")
    environment = str(
        trading.get("vnpy_environment") or trading.get("environment") or ""
    ).strip()
    if environment != "实盘":
        raise RuntimeError(
            "production live config must set vnpy_environment exactly to '实盘'"
        )

    raw_risk = payload.get("risk")
    if not isinstance(raw_risk, dict):
        raise RuntimeError("production live config must contain a risk object")
    missing = sorted(PRODUCTION_RISK_REQUIRED_FIELDS - set(raw_risk))
    if missing:
        raise RuntimeError(
            "production live risk config is incomplete; missing: " + ", ".join(missing)
        )
    if raw_risk.get("enabled") is not True:
        raise RuntimeError("production live risk config must keep enabled=true")
    _validate_production_live_risk_config(raw_risk)

    # Import lazily so the light-weight settings module remains usable by data
    # utilities without importing the full trading stack.
    from .trading.risk import RiskConfig

    parsed = RiskConfig.from_mapping(raw_risk)
    if not parsed.allowed_symbols:
        raise RuntimeError("production live risk config requires a non-empty allowed_symbols list")

    result: Dict[str, object] = {}
    for name, value in vars(parsed).items():
        if isinstance(value, set):
            result[name] = sorted(value)
        elif isinstance(value, dict):
            result[name] = dict(value)
        else:
            result[name] = value
    return result


def server_live_ctp_environment() -> str:
    """Return the operator-owned CTP environment for a production login."""
    if not is_production_env():
        return ""
    payload = _read_json_object(_production_live_config_path())
    trading = payload.get("trading")
    if not isinstance(trading, dict):
        raise RuntimeError("production live config must contain a trading object")
    environment = str(
        trading.get("vnpy_environment") or trading.get("environment") or ""
    ).strip()
    if environment != "实盘":
        raise RuntimeError(
            "production live config must set vnpy_environment exactly to '实盘'"
        )
    return environment


def production_runtime_paths() -> Dict[str, Path]:
    """Resolve required durable production paths without creating data files."""
    if not is_production_env():
        return {}
    required = {
        "audit": "QUANT_AUDIT_LOG_DIR",
        "risk_state": "QUANT_LIVE_RISK_STATE_PATH",
        "session_db": "QUANT_SESSION_DB",
    }
    resolved: Dict[str, Path] = {}
    missing: list[str] = []
    for label, env_name in required.items():
        raw = env_text(env_name)
        if not raw:
            missing.append(env_name)
            continue
        resolved[label] = Path(raw).expanduser().resolve()
    if missing:
        raise RuntimeError("production runtime path variables are required: " + ", ".join(missing))
    return resolved


def validate_production_runtime() -> Dict[str, object]:
    """Fail closed before serving requests when production safeguards are absent."""
    risk = server_live_risk_config()
    if not is_production_env():
        return risk
    if synthetic_data_enabled():
        raise RuntimeError("QUANT_ALLOW_SYNTHETIC_DATA must be false in production")
    if not secure_session_cookie_enabled():
        raise RuntimeError("secure session cookies are required in production")
    if not env_bool("QUANT_RATE_LIMIT_ENABLED", default=True):
        raise RuntimeError("QUANT_RATE_LIMIT_ENABLED must be true in production")
    if websocket_query_token_enabled():
        raise RuntimeError("QUANT_ALLOW_WS_QUERY_TOKEN must be false in production")
    cors_raw = env_text("QUANT_CORS_ORIGINS")
    cors_origins = [origin.strip() for origin in cors_raw.split(",") if origin.strip()]
    if not cors_origins:
        raise RuntimeError("QUANT_CORS_ORIGINS is required in production")
    if "*" in cors_origins:
        raise RuntimeError("QUANT_CORS_ORIGINS must not contain a wildcard in production")
    for origin in cors_origins:
        try:
            parsed_origin = urlsplit(origin)
            parsed_origin.port  # Validate the port while parsing the origin.
        except ValueError as exc:
            raise RuntimeError("production CORS origin is invalid") from exc
        local_http = parsed_origin.scheme == "http" and parsed_origin.hostname in {
            "localhost", "127.0.0.1", "::1",
        }
        if not (
            parsed_origin.hostname
            and "*" not in origin
            and parsed_origin.username is None
            and parsed_origin.password is None
            and parsed_origin.path == ""
            and not parsed_origin.query
            and not parsed_origin.fragment
            and (parsed_origin.scheme == "https" or local_http)
        ):
            raise RuntimeError(
                "production CORS origins must use HTTPS except localhost/127.0.0.1"
            )

    paths = production_runtime_paths()
    directory_targets = {
        paths["audit"],
        paths["risk_state"].parent,
        paths["session_db"].parent,
    }
    for directory in directory_targets:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory, prefix=".quant-write-probe-") as probe:
                probe.write(b"ok")
                probe.flush()
                os.fsync(probe.fileno())
        except OSError as exc:
            raise RuntimeError(f"production runtime directory is not writable: {directory}: {exc}") from exc
    return risk


def ctp_defaults() -> Dict[str, str]:
    """Safe CTP defaults; secret and production values must come from env/config."""
    return {
        "broker_id": env_text("QUANT_CTP_BROKER_ID"),
        "td_server": env_text("QUANT_CTP_TD_SERVER"),
        "md_server": env_text("QUANT_CTP_MD_SERVER"),
        "app_id": env_text("QUANT_CTP_APP_ID"),
        "auth_code": env_text("QUANT_CTP_AUTH_CODE"),
        "vnpy_environment": env_text("QUANT_CTP_ENVIRONMENT", "测试") or "测试",
    }


def _parse_server_presets(raw: str) -> List[Dict[str, str]]:
    """Parse `label=value,label2=value2` server preset strings."""
    presets: List[Dict[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            label, value = item.split("=", 1)
            label = label.strip() or value.strip()
            value = value.strip()
        else:
            label = item
            value = item
        if value:
            presets.append({"label": label, "value": value})
    return presets


def warn_production_risk_defaults() -> list[str]:
    """Return a list of risk parameters still at their built-in defaults.

    Call once at startup so operators see which values were not explicitly set.
    """
    warnings: list[str] = []
    if not is_production_env():
        return warnings
    checks = [
        ("QUANT_RISK_MAX_ORDER_VOLUME", "max_order_volume"),
        ("QUANT_RISK_MAX_POSITION_VOLUME", "max_position_volume"),
        ("QUANT_RISK_MAX_ACTIVE_ORDERS", "max_active_orders"),
        ("QUANT_RISK_MAX_ORDERS_PER_MINUTE", "max_orders_per_minute"),
        ("QUANT_RISK_MAX_DAILY_LOSS_RATIO", "max_daily_loss_ratio"),
        ("QUANT_RISK_MAX_MARKET_DATA_AGE_SECONDS", "max_market_data_age_seconds"),
        ("QUANT_RISK_MAX_PRICE_DEVIATION", "max_price_deviation"),
        ("QUANT_RISK_ALLOW_MARKET_ORDERS", "allow_market_orders"),
        ("QUANT_RISK_ORDER_COUNT_ALERT_THRESHOLD", "order_count_alert_threshold"),
        ("QUANT_RISK_CANCEL_COUNT_ALERT_THRESHOLD", "cancel_count_alert_threshold"),
        ("QUANT_RISK_DUPLICATE_OPEN_ALERT_THRESHOLD", "duplicate_open_alert_threshold"),
        ("QUANT_RISK_DUPLICATE_CLOSE_ALERT_THRESHOLD", "duplicate_close_alert_threshold"),
        ("QUANT_RISK_DUPLICATE_CANCEL_ALERT_THRESHOLD", "duplicate_cancel_alert_threshold"),
        ("QUANT_RISK_DUPLICATE_CANCEL_WINDOW_SECONDS", "duplicate_cancel_window_seconds"),
    ]
    for env_name, _param_name in checks:
        if env_name not in os.environ:
            warnings.append(f"PRODUCTION env但 {env_name} 未设置，使用内置默认值")
    return warnings


def ctp_server_presets(kind: str) -> List[Dict[str, str]]:
    env_name = "QUANT_CTP_TD_PRESETS" if kind.lower() == "td" else "QUANT_CTP_MD_PRESETS"
    return _parse_server_presets(os.getenv(env_name, ""))
