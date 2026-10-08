@echo off
cd /d %~dp0
.venv\Scripts\python -m uvicorn src.api:create_app --factory --host 127.0.0.1 --port 8000
