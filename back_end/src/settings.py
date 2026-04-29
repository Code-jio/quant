"""Runtime settings helpers.

Keep production-sensitive defaults out of source code. Local/demo defaults can
still be supplied through environment variables or ignored config files.
"""

from __future__ import annotations

import os
from typing import Dict, List


FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
PRODUCTION_ENV_VALUES = {"prod", "production", "live"}


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


def is_production_env() -> bool:
    return env_text("QUANT_ENV", "development").lower() in PRODUCTION_ENV_VALUES


def synthetic_data_enabled() -> bool:
    """Whether code paths may auto-generate demo/synthetic market data."""
    if "QUANT_ALLOW_SYNTHETIC_DATA" in os.environ:
        return env_bool("QUANT_ALLOW_SYNTHETIC_DATA", default=True)
    return not is_production_env()


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


def ctp_server_presets(kind: str) -> List[Dict[str, str]]:
    env_name = "QUANT_CTP_TD_PRESETS" if kind.lower() == "td" else "QUANT_CTP_MD_PRESETS"
    return _parse_server_presets(os.getenv(env_name, ""))
