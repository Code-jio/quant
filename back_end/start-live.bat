@echo off
cd /d %~dp0
if not exist ".venv-live\Scripts\python.exe" (
  echo Missing .venv-live. Follow the live runtime setup in README.md.
  exit /b 1
)
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "QUANT_ALLOW_SYNTHETIC_DATA=0"
.venv-live\Scripts\python.exe server.py
