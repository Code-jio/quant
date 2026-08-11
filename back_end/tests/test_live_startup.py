"""Static safety contract for the live API startup command."""

from pathlib import Path


def test_live_startup_binds_locally_by_default_and_allows_explicit_overrides():
    script = (Path(__file__).resolve().parents[1] / "start.bat").read_text(
        encoding="utf-8"
    )
    normalized = script.lower()

    assert "setlocal" in normalized
    assert "endlocal" in normalized
    assert 'cd /d "%~dp0"' in normalized
    assert 'if not defined quant_api_host set "quant_api_host=127.0.0.1"' in normalized
    assert 'if not defined quant_api_port set "quant_api_port=8000"' in normalized
    assert ".venv313\\scripts\\python -m uvicorn src.api:create_app --factory" in normalized
    assert '--host "%quant_api_host%"' in normalized
    assert '--port "%quant_api_port%"' in normalized
    assert "--reload" not in normalized
