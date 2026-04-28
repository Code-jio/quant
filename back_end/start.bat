@echo off
cd /d %~dp0
.venv313\Scripts\python -m uvicorn src.api:create_app --factory --host 0.0.0.0 --port 8000 --reload
