"""Static safety contract for the live API startup command."""

from pathlib import Path

import pytest

from main import run_live_trading


def test_live_startup_binds_locally_and_forces_production_safety_switches():
    script = (Path(__file__).resolve().parents[1] / "start.bat").read_text(
        encoding="utf-8"
    )
    normalized = script.lower()

    assert "setlocal" in normalized
    assert "endlocal" in normalized
    assert 'cd /d "%~dp0"' in normalized
    assert 'if not defined quant_api_host set "quant_api_host=127.0.0.1"' in normalized
    assert 'if not defined quant_api_port set "quant_api_port=8000"' in normalized
    assert 'set "quant_env=production"' in normalized
    assert 'set "quant_allow_synthetic_data=false"' in normalized
    assert 'set "quant_session_cookie_secure=true"' in normalized
    assert 'set "quant_rate_limit_enabled=true"' in normalized
    assert 'set "quant_allow_ws_query_token=false"' in normalized
    assert "if not defined quant_cors_origins" in normalized
    assert "exit /b 1" in normalized
    assert 'if not defined quant_env' not in normalized
    assert 'if not defined quant_allow_synthetic_data' not in normalized
    assert 'if not defined quant_session_cookie_secure' not in normalized
    assert 'if not defined quant_rate_limit_enabled' not in normalized
    assert 'if not defined quant_live_config_path set "quant_live_config_path=%~dp0config\\config_production.json"' in normalized
    assert 'if not defined quant_audit_log_dir set "quant_audit_log_dir=%~dp0logs\\compliance"' in normalized
    assert 'if not defined quant_live_risk_state_path set "quant_live_risk_state_path=%~dp0data\\historical\\live_risk_state.json"' in normalized
    assert 'if not defined quant_session_db set "quant_session_db=%~dp0data\\historical\\sessions.db"' in normalized
    assert ".venv313\\scripts\\python -m uvicorn src.api:create_app --factory" in normalized
    assert '--host "%quant_api_host%"' in normalized
    assert '--port "%quant_api_port%"' in normalized
    assert "--reload" not in normalized


def test_legacy_cli_live_entry_is_disabled():
    with pytest.raises(RuntimeError, match="CLI 实盘入口已禁用"):
        run_live_trading({})
