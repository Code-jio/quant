# Workspace instructions

This file is a thin project overlay on the machine's capability kernel.

- Verified offline stack: Windows x64, Python 3.12, Node 24; use the committed dependency locks.
- Canonical API factory: `back_end/src/api/__init__.py:create_app`. `api/app.py`, `state.py`, `deps.py`, `schemas.py` and `ws.py` are compatibility facades, not a second implementation.
- Tests must use fixtures/FakeGateway. Do not treat nontrading-hour connection failures as code defects or submit broker orders as part of tests.
- Session identity, account generation, execution ledger and all order entry points must preserve the single-account executor boundary.
- Simulated data requires explicit opt-in and provenance. Missing data, margin fields, contract metadata and unresolved orders must remain unknown or block opening risk.
- Keep secrets, CTP flow files and SQLite runtime files out of Git. Sample configuration: `back_end/config/config.example.json`; real configuration: ignored `config_production.json`.
- Quality gates: backend `ruff check main.py server.py src tests`, `mypy`, full `pytest`; frontend `npm run quality` and `npm run e2e`. Important UI changes require browser verification.
- Update README and the remediation backlog when behavior or operating limits change. Keep the original review report as a historical baseline.
