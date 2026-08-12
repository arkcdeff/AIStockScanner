# Current Task

Last updated: Sprint 17 Task 17.7 (Final Verification, Cleanup, Documentation & v0.13.0 Release) — COMPLETE & VERIFIED.

## In progress / status

- Task 17.1 — COMPLETE & verified (persistence/domain foundation).
- Task 17.2 — VERIFIED (historical NSE fetch + replay; manual local-file fallback confirmed).
- Task 17.3 — COMPLETE (simulation engine; two cost-math bugs fixed in `backtest_simulator.py`;
  23 tests in `tests/test_backtest_simulator.py`).
- Task 17.4 — COMPLETE & VERIFIED (performance metrics engine fully implemented, CLI-wired,
  40 tests in `tests/test_backtest_metrics.py`; benchmark TODO resolved; risk-free = 0).
- Task 17.5 — COMPLETE & VERIFIED (backtesting REST API: strategies, runs, synchronous
  execution, results; 13 routes / 9 paths; 16 tests in `tests/test_backtest_api.py`). Simulator
  settlement fix (entry commission no longer double-counted; 4 SETTLEMENT tests added;
  27 simulator tests). `backtests.read/run/write` permission codes added (25 → 28).
  `backtest_metrics.py` hardened to read committed trades/equity directly from the database.
- Task 17.6 — COMPLETE & VERIFIED (browser backtesting pages: workspace, strategies, runs,
  run detail; HTMX partials for metrics/trades/history/strategy-form; Lightweight Charts equity/drawdown;
  trade table; run-status locking; read/write/run permissions; CSRF on writes; run ownership;
  Indian-formatted currency; 29 tests in `tests/test_backtest_ui.py`. No migration.)
- Task 17.7 — **COMPLETE & VERIFIED (v0.13.0 release).** All release gates passed
  (alembic head `d7b4cc79fd45` clean; ruff clean; `git diff --check` clean; 570 passed ×3).
  Version bumped to `v0.13.0` in `app/main.py`; CHANGELOG v0.13.0 added and the inaccurate
  v0.12.1 version-bump claim corrected; Permission Matrix reconciled to the 28-code catalog;
  annotated tag `v0.13.0`; `sprint-17-backtesting` merged into `main` (fast-forward); pushed.
- CLI regression — **FIXED** (create_admin Typer/legacy dual mode; main() guards added).

## Verification status

- alembic current/heads/check: clean (`d7b4cc79fd45 (head)`, no new upgrade operations)
- ruff: clean
- pytest ×3: 570 passed (541 baseline + 29 UI), only unrelated deprecation warnings
- git diff --check: clean (CURRENT_TASK.md trailing-whitespace corruption fixed)
- OpenAPI: 241 paths / 278 routes; backtests REST 9 paths + UI 17 paths
- Security: no tracked secrets, `.env` gitignored, `SecretStr` fields, autoescape + CSRF verified
- CLI smoke: nse_history / backtest_replay / backtest / import_nse / create_admin all print help

## Next action

**Sprint 17 — COMPLETE. Do NOT start Sprint 18 until the project owner hands it off.**

Sprint 18 — Market Intelligence — not yet started. Do NOT implement it now.

## Key files

- `app/api/routes/backtests.py` — Task 17.5 backtesting REST API (strategies, runs, execute, results;
  run ownership + admin override; sanitized failures; soft delete).
- `app/services/backtest_simulator.py` — settlement fix: both close sites credit
  `net_pnl + entry_value + entry_commission` (`_cash_returned_on_close`).
- `app/services/backtest_metrics.py` — reads committed trades/equity directly from the database.
- `app/services/builtin_auth.py`, `docs/01_Architecture/Permission_Matrix.md` — `backtests.read/run/write`
  permission codes (28 total) + role/route matrices.
- `tests/test_backtest_api.py` — Task 17.5 API test suite (16 tests).
- `tests/test_backtest_simulator.py` — Task 17.5A settlement regression tests (`# SETTLEMENT`).
- `docs/03_APIs/Backtest_API.md` — API reference (endpoints, permissions, lifecycle, errors).
- `docs/05_Development/Sprint_17.md`, `docs/05_Development/PROJECT_STATE.md` — updated for 17.7 / v0.13.0.
- `app/api/routes/backtest_pages.py` — Task 17.6 browser page router.
- `app/templates/backtests/*.html` + components — Task 17.6 Jinja2 templates.
- `app/static/js/backtests.js` + `app/static/css/backtests.css` — Task 17.6 frontend assets.
- `tests/test_backtest_ui.py` — Task 17.6 UI test suite (29 tests).
