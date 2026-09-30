@echo off
setlocal
cd /d "%~dp0"
if not defined QUANT_API_HOST set "QUANT_API_HOST=127.0.0.1"
if not defined QUANT_API_PORT set "QUANT_API_PORT=8000"
rem This is the production live launcher.  Never inherit a development or
rem fail-open security switch from the operator shell.
set "QUANT_ENV=production"
set "QUANT_ALLOW_SYNTHETIC_DATA=false"
set "QUANT_SESSION_COOKIE_SECURE=true"
set "QUANT_RATE_LIMIT_ENABLED=true"
set "QUANT_ALLOW_WS_QUERY_TOKEN=false"
if not defined QUANT_CORS_ORIGINS (
  echo ERROR: QUANT_CORS_ORIGINS must be set to the HTTPS frontend origin.
  exit /b 1
)
if not defined QUANT_LIVE_CONFIG_PATH set "QUANT_LIVE_CONFIG_PATH=%~dp0config\config_production.json"
if not defined QUANT_AUDIT_LOG_DIR set "QUANT_AUDIT_LOG_DIR=%~dp0logs\compliance"
if not defined QUANT_LIVE_RISK_STATE_PATH set "QUANT_LIVE_RISK_STATE_PATH=%~dp0data\historical\live_risk_state.json"
if not defined QUANT_SESSION_DB set "QUANT_SESSION_DB=%~dp0data\historical\sessions.db"
.venv313\Scripts\python -m uvicorn src.api:create_app --factory --host "%QUANT_API_HOST%" --port "%QUANT_API_PORT%"
endlocal
