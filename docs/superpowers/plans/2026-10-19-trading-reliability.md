# Trading reliability implementation plan

> **Execution note:** Execute locally in the existing checkout. Use isolated fixtures; preserve the user's live session. Steps use checkbox syntax for tracking.

**Goal:** Address the ten findings from the current-code audit without placing broker orders or turning unknown facts into successful checks.

**Architecture:** Independent broker snapshot clocks; broker settlement and cash-flow fields for daily loss; explicit broker reconciliation with audited resolution; shared request sequencing and windowed volume alerts. Keep the single-account execution lock and durable execution journal authoritative.

**Tech Stack:** Python/FastAPI/vn.py CTP, SQLite, Vue 3, Vitest and Playwright.

## Tasks and acceptance

- [x] Snapshot freshness: `trading/types.py`, `vnpy_gateway.py`, `ctp_observer.py`. Track account and position freshness independently. A failed account query or account age >= 60 seconds must invalidate readiness even while positions refresh. Add regression tests with a controlled monotonic clock.
- [x] Daily loss: capture `PreBalance`, `Deposit`, `Withdraw` from the account callback; calculate `(pre_balance + deposit - withdraw - balance) / pre_balance`. Require known finite fields in native live mode; retain fixture baseline semantics. Test withdrawals, deposits, intraday start, invalid/missing fields and trading-day rollover.
- [x] Search race: give the manual selector request generations and dispose invalidation. Test out-of-order responses and clearing during a request in the real component/browser.
- [x] Volume alerts: compare completed fixed-duration delta-volume windows against previous windows; reset at trading-day changes, counter resets and connection gaps. Unit-test a large spike after a large cumulative baseline and ordinary flow.
- [x] Contract lifecycle: preserve raw instrument lifecycle fields before vn.py drops them; distinguish known trading status from unknown. Reject explicit inactive/expired instruments in the native validator. Test metadata propagation, invalid dates and unavailable fields.
- [x] Reconciliation: add read-only native order/trade query capture with request identity, completion/error/timeout state. Expose broker snapshot separately from local readiness; resolve unknown execution records only with fresh complete evidence and an explicit audited operator decision. Do not automatically resubmit or resume. Test incomplete/error/stale query results and wrong-account/generation decisions.
- [x] Margin reservation: use conservative reservation while account/order ordering is unknown; avoid double counting only when an account snapshot is confirmed after all relevant broker order updates. Report the estimation method. Test both callback orderings, partial fills and pending local submissions.
- [x] Environment wording and catalog: separate API library selection from counter verification. Add pageable domestic catalog browsing with product/month context, total count and stable filtering. Test pagination and existing domestic exclusions; verify the browser.
- [x] Conditional orders: persist local condition state and trailing extrema; restore pending/triggering records paused for explicit review. Persist triggering state before dispatch; keep unknown outcomes suspended. Test restart and crash windows without native dispatch.
- [x] Documentation and delivery: update README and remediation backlog; record actual validation and remaining live acceptance requirements. Run backend Ruff, mypy, full pytest; frontend quality and full Playwright suite. Review diff for secrets/debug residue, then commit and push under the user's existing authorization.

## Verification commands

Backend (from `back_end`, test environment): `.venv-live/Scripts/python.exe -m pytest`, `-m ruff check main.py server.py src tests`, `-m mypy`.

Frontend (Node 24 on PATH): `npm run quality`, `npm run e2e`. Playwright must use the committed isolated test backend and must not reuse a real account.

## Deployment boundary

Passing isolated tests does not prove live broker execution, counter identity, exchange eligibility or continuous cross-day recovery. Do not restart the live backend or ask for account credentials as part of implementation. Report whether changes are loaded separately from source completion.

## Final validation

315 backend tests, 38 frontend unit tests and 28 browser tests passed. Ruff, mypy (49 files), ESLint, vue-tsc, build and bundle budgets passed. See `docs/evidence/reliability-20261019/README.md`. Live service loading and broker acceptance remain separate.
