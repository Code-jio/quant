@echo off
setlocal
cd /d "%~dp0"
if not defined QUANT_API_HOST set "QUANT_API_HOST=127.0.0.1"
if not defined QUANT_API_PORT set "QUANT_API_PORT=8000"
.venv313\Scripts\python -m uvicorn src.api:create_app --factory --host "%QUANT_API_HOST%" --port "%QUANT_API_PORT%"
endlocal
