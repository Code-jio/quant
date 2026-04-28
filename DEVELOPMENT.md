# Development

## Runtime

- Python: 3.13.5 in this workspace (`.python-version`)
- Backend: FastAPI + vn.py/vnpy_ctp
- Frontend: Vue 3 + Vite + Element Plus + ECharts

## Backend

```bash
cd back_end
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check src tests
python -m mypy
```

Useful operational endpoints:

- `GET /health` - process, session, gateway, and WebSocket health snapshot.
- `GET /metrics` - Prometheus-style runtime metrics.
- `GET /data/quality?symbol=IF9999&timeframe=1d` - historical bar metadata, gap report, and OHLCV quality checks.
- `GET /audit/events` - protected trading/auth audit trail.

## Frontend

```bash
cd front_end
npm install
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
```

`VITE_API_BASE_URL`, `VITE_WS_BASE_URL`, and `VITE_WS_HOST` are centralized by
`front_end/src/config/network.js`.
