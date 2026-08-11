# AI Handoff

Snapshot of AIStockScanner after Sprint 17 backtesting reconciliation (Task 17.1 through 17.3
verified, Task 17.4 quarantined as WIP). All work is uncommitted; do NOT commit/push/stash/reset/clean.

## Workspace / environment

- Branch: `sprint-17-backtesting`
- HEAD: `75640f4` (Sprint 16.8), tag `v0.12.1`
- Working directory: `AIStockScanner/` (run via `uv run`)
- Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic

## What is verified complete

- **Task 17.1 — Backtesting database + domain foundation: COMPLETE**
  - Models: `BacktestStrategy`, `BacktestRun`, `BacktestTrade`, `BacktestEquityPoint`, `BacktestMetric`
  - Repository layer: `app/repositories/backtest.py` (create/get, get-by-code, list, status update, eager `get_with_details`, bulk inserts, metric upsert)
  - Schemas: `app/schemas/backtest.py`; tests: `tests/test_backtest_models.py`
  - Migration `d7b4cc79fd45` applied to dev + test DBs
- **Task 17.2 — Historical NSE fetch + replay: VERIFIED** (audit + tests, no new downloader/mirror)
  - `app/services/nse_historical.py`, `app/services/backtest_replay.py`, parsers, `tests/test_nse_historical.py` + `tests/test_backtest_replay.py` + `tests/test_nse_parser.py` + `tests/test_market_data.py`
  - No-look-ahead guarantees (rows strictly `<= D`), 403 surfaced as `blocked_403`, no NSE bypass, manual local-file import preserved
- **Task 17.3 — Trade simulation: COMPLETE (with bug fixes)**
  - `app/services/backtest_simulator.py`: fixed `_close_position` exit-commission (was using `entry_slippage`; now uses `commission_rate`) and cash settlement (`cash += trade.net_pnl` in both normal-exit and end-of-backtest paths)
  - `tests/test_backtest_simulator.py`: 23 tests covering entry modes, sizing, one-position-per-stock, duplicate-signal skip, exit conditions (stop/target/max_holding/opposite/end_of_backtest), cost math, equity curve, run-state and rerun rejection
- **CLI regression (create_admin): FIXED**
  - Root cause: prior Typer conversion deleted the module-level `_create_admin` and the int-returning `main(argv)` that `tests/test_auth_api.py` imports
  - Fix in `app/cli/create_admin.py`: Typer app preserved AND legacy `main(argv) -> int` + async `_create_admin` restored
  - `tests/test_auth_api.py::test_create_admin_cli_reads_password_from_environment_without_printing_it` now passes
  - Added `main()` guards to `app/cli/import_nse.py`, `app/cli/nse_history.py`, `app/cli/backtest_replay.py`, `app/cli/backtest.py`; fixed an `app.db.session.async_session_factory()` reference bug in `app/cli/backtest.py`

## Quarantined as WIP (NOT verified / not yet tested)

- **Task 17.4 — Performance metrics: WIP, UNVERIFIED**
  - `app/services/backtest_metrics.py` (`BacktestMetricCalculator`) exists but has **no** tests and is **not wired** into the CLI/simulator. `_compute_benchmark_return` is a TODO stub returning `None`. Leave as-is; do not extend or ship as "complete".

## Manual local-file NSE fallback (verified present)

The project already provides a manual local-file import path through the existing NSE import
infrastructure (no new downloader/mirror added). Used with historical bhavcopy CSV/ZIP files:

```bash
uv run python -m app.cli.import_nse file <path-to-csv-or-zip>
```

- `file` subcommand: imports a local bhavcopy CSV/ZIP into `daily_prices` using the existing
  alias-based parser (`app/services/nse_import.py` / parser ALIASES map).
- Modern CM-UDiFF column names and legacy aliases are normalized; legacy pre-08-Jul-2024 formats
  are supported by the same parser.
- `date` and `latest` subcommands download from NSE; `file` is the manual/offline path used on
  403 or air-gapped networks.
- Replay and simulation read exclusively from already-imported `daily_prices`/`indicator_results`
  rows, so historical runs work entirely from local data.

## Verification (Step 8)

- `alembic current` = `alembic heads` = `d7b4cc79fd45 (head)`; `alembic check` = "No new upgrade operations detected."
- `ruff check .` → All checks passed!
- `pytest` three consecutive runs → **481 passed, 1 warning** each (warning is an unrelated Starlette/httpx deprecation in fastapi's TestClient).
- `git diff --check` → clear (only benign LF→CRLF EOL-normalization warnings, no whitespace/error lines).

## Uncommitted change inventory

Tracked & modified (` M`):
- `.gitignore`, `CHANGELOG.md`, `app/cli/__init__.py`, `app/cli/create_admin.py`,
  `app/cli/import_nse.py`, `app/cli/seed_auth.py`, `app/models/__init__.py`,
  `app/models/stock.py`, `docs/05_Development/PROJECT_STATE.md`, `tests/conftest.py`,
  `tests/test_nse_parser.py` (3 new parser format-proof tests added by me).

Untracked (`??`, prior session + my additions):
- `app/cli/backtest.py`, `app/cli/backtest_replay.py`, `app/cli/nse_history.py`,
  `app/models/backtest.py`, `app/repositories/backtest.py`, `app/schemas/backtest.py`,
  `app/services/backtest_metrics.py`, `app/services/backtest_replay.py`,
  `app/services/backtest_simulator.py`, `app/services/nse_historical.py`,
  `docs/05_Development/Sprint_17.md`,
  `migrations/versions/d7b4cc79fd45_add_backtesting_foundation_tables.py`,
  `tests/test_backtest_models.py`, `tests/test_backtest_replay.py`,
  `tests/test_backtest_simulator.py` (rewritten with 23 tests by me),
  `tests/test_nse_historical.py`.

## Recommended next task

**Task 17.4 completion (NOT yet started per WIP policy)**: add `tests/test_backtest_metrics.py`
and wire `BacktestMetricCalculator.compute_metrics` into the backtest CLI/API. Until then leave the
module quarantined — it computes metrics but is untested and uncalled.

## Notes / gotchas

- `BacktestRun.position_size_value` column is `NUMERIC(10,6)`; do not use values > 9999.99 in tests.
- Stored `Decimal` columns round to 4 dp (prices) / 6 dp (rates); assert with +/- 1e-3 tolerance.
- `signal_close` records `entry_date` as the *next* session (entry price uses the signal-date close).
- On Windows the async test event loop must use `WindowsSelectorEventLoopPolicy`
  (handled by `tests/conftest.py`); run pytest via `uv run pytest`, not bare `python -m pytest`.
- Do NOT commit/push/stash/reset/clean — leave the working tree intact for the next AI.
