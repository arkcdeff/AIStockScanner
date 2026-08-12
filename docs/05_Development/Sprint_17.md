# Sprint 17: Backtesting

## Release

- Version: work-in-progress on branch `sprint-17-backtesting`
- Migration head (Task 17.1): `d7b4cc79fd45`

## Objective

Add a deterministic, reproducible backtesting subsystem to the project. It will support historical
scanner-based and rule-based strategy testing, entry/exit rules, transaction costs, position
sizing, stop loss / target, trade journals, and performance metrics. Later tasks add the replay
and simulation engines, REST APIs, HTML UI, and charts; Task 17.1 only lays the persistence and
domain foundation.

## Completed tasks

### 17.1 - Backtesting database + domain foundation

Introduced the persistence and domain model for backtesting. No engine, API, or UI was
implemented in this task.

#### Domain model

Five tables were added, designed to be reusable for scanner, strategy, and (later) portfolio
backtests:

| Table | Purpose |
| --- | --- |
| `backtest_strategies` | Named, reusable strategy definitions (scanner / rule-based / composite) with a unique stable `code` and a validated `configuration_json`. |
| `backtest_runs` | A single execution of a strategy over a date range with capital, cost, and position-sizing settings, plus run status and timing. |
| `backtest_trades` | Individual simulated trades (entry/exit, side, signal, stop/target, PnL) linked to a run and a stock. |
| `backtest_equity_points` | Day-by-day portfolio equity curve (cash, invested value, portfolio value, drawdown). Unique per `(backtest_run_id, trade_date)`. |
| `backtest_metrics` | Performance summary per run (returns, win rate, profit factor, drawdown, Sharpe/Sortino). Unique per run. |

#### Relationships

- `BacktestStrategy` 1→many `BacktestRun` (cascade delete).
- `BacktestRun` 1→many `BacktestTrade`, 1→many `BacktestEquityPoint`, 1→one `BacktestMetric`
  (all cascade delete).
- `BacktestTrade` → `Stock`, `BacktestRun` → `BacktestStrategy`.
- Optional nullable user FK: `backtest_strategies.created_by_user_id`,
  `backtest_runs.requested_by_user_id`.

#### Indexes and constraints

- Indexes on strategy `code` (unique), `strategy_type`, `is_active`, `scanner_code`; run
  `strategy_id`, `status`, `start_date`, `end_date`; trade `backtest_run_id`, `stock_id`,
  `entry_date`, `exit_date`; and on equity/metric `backtest_run_id`.
- Check constraints enforce allowed enum values (`strategy_type`, run `status`, trade
  `position_side`, `status`), positive capital/quantity/prices, and non-negative cost rates.
- Unique constraints: equity `(backtest_run_id, trade_date)`, metric `backtest_run_id`,
  strategy `code`.

#### Repository layer

`app/repositories/backtest.py` provides `BacktestStrategyRepository`,
`BacktestRunRepository`, `BacktestTradeRepository`, `BacktestEquityPointRepository`, and
`BacktestMetricRepository` with create/get, get-by-code, list (with filtering/pagination),
run status update, eager "get with details", bulk inserts, and metric upsert.

#### Schemas

`app/schemas/backtest.py` exposes validated Pydantic schemas for strategies (create/update/read)
and runs (create/read), plus read schemas for trades, equity points, metrics, and a combined
run detail. Fields are validated for date ranges, positive capital, non-negative cost rates,
position sizing, and allowed enum values.

#### Migration

- Revision `d7b4cc79fd45` (`add_backtesting_foundation_tables`), down-revision
  `3f8c1a2b4d6e`. Applied to both the development and test databases.

#### Tests

- `tests/test_backtest_models.py` covers strategy create/unique-code/update, run
  relationships/status and constraint violations, trade creation/stock linkage/pricing and
  side constraints, equity uniqueness, metric uniqueness, repository pagination/status/bulk
  insert/metric upsert, and cascade deletes for run and strategy.

### 17.2 - Historical NSE data fetch + historical replay engine

Introduced official NSE historical data acquisition by date/date-range and a
historical scanner replay engine with strict no-look-ahead guarantees. No trade
simulator, performance metrics, API, or UI was implemented in this task.

#### A. Historical NSE data fetch

`app/services/nse_historical.py` provides `NSEHistoricalService` with:

- `fetch_date(trade_date)` — download + import a single date.
- `fetch_range(start_date, end_date)` — bounded (366 calendar days max)
  sequential fetch with configurable delay between NSE requests.
- `fetch_missing_dates(start_date, end_date)` — fetch only dates not already
  present in `daily_prices`.
- `check_date_available(trade_date)` — query whether EOD data already exists.

##### Formats supported

- Modern equity EOD: **CM-UDiFF Common Bhavcopy Final ZIP** (official NSE
  `https://www.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{day}{MONTH}{year}bhav.csv.zip`).
- Legacy CM bhavcopy/common bhavcopy formats are handled by the existing
  `app/data/nse_parser.py` alias-based column normalization when required for
  older historical dates (pre-08-Jul-2024).
- Raw files are stored under `data/raw/nse/YYYY/MM/`.
- No second market-data stack is introduced; the service reuses
  `NSEDownloader`, `NSEImportService`, the existing parser, `DailyPrice`,
  `ImportJob`, and the PostgreSQL duplicate-safe upsert.

##### Per-date statuses

`downloaded`, `already_present`, `imported`, `skipped_weekend`,
`skipped_holiday`, `unavailable`, `blocked_403`, `failed`.

##### NSE 403 handling

- NSE anti-bot controls are **not** bypassed.
- A 403 is surfaced clearly as `blocked_403` with the underlying message.
- Dates already present locally/database continue to work.
- Manual-file fallback remains available via the existing
  `python -m app.cli.import_nse file <path>` command.
- Historical replay works entirely from already-imported DB data even when
  fetch fails.

##### Range summary

`requested_dates`, `trading_dates_attempted`, `downloaded`, `already_present`,
`imported`, `skipped`, `blocked`, `failed`.

#### B. Historical replay engine

`app/services/backtest_replay.py` provides `BacktestReplayService` with:

- `replay_scanner(scanner_code, start_date, end_date, symbols=None)`
- `replay_all(start_date, end_date, symbols=None)`
- `ensure_historical_data(start_date, end_date, fetch_missing=False)`

##### Replay architecture

- Bulk-loads `DailyPrice` and `IndicatorResult` rows for the requested
  universe/date range (plus a 400-day lookback window for long indicators)
  grouped in memory — no date × stock N+1 query loops.
- For each replay date D, the engine uses **only** rows with
  `trade_date <= D`. Futures rows are never used.
- `ReplaySignal` objects (in-memory) carry `trade_date`, `stock_id`, `symbol`,
  `scanner_code`, `matched`, `score`, `reason`, `close`, `volume`,
  `delivery_percent`, `indicator_snapshot`, `metadata`.
- The existing 36 scanner rules are reused **unchanged** via
  `ScannerRegistry`; scanner formulas are not copied into a second engine.

##### Scanner reuse

Live/EOD scanner and replay engine call the same `scanner_func(current,
previous)` registered rules. The replay engine constructs `IndicatorRow`
contexts from historical `DailyPrice`/`IndicatorResult` rows (using the
correct historical `IndicatorResult` row for each date; calculating from past
candles only when those rows are absent — never persisted, cached per run).

##### 20-day / 52-week windows

20-day aggregates (`avg_volume_20d`, `max_volume_20d`, etc.) are computed from
price rows **strictly before** the replay date, preserving the live scanner
semantics where the current row is excluded from its own prior window.

##### No-look-ahead safeguards

- Only `DailyPrice` rows `<=` replay date are loaded for evaluation.
- Only `IndicatorResult` rows `<=` replay date are loaded.
- Inserting future price/indicator rows after the range does not change
  replay results (verified by mandatory tests).
- Insufficient-history handling: scanners requiring 20+ prior sessions do not
  match when the prior window is unavailable.

##### Performance strategy

- Bulk fetch price/indicator history per universe/date range.
- Group in memory; cache rolling windows; reuse scanner contexts.
- Chunking is supported via per-stock grouping (dates processed per batch);
  a `symbols` filter limits the universe.
- Conservative sequential requests to NSE with configurable delay
  (`request_delay`).

#### CLI (optional, added)

Historical data:

```bash
uv run python -m app.cli.nse_history --date 2025-07-15
uv run python -m app.cli.nse_history --from 2025-01-01 --to 2025-12-31
```

Replay:

```bash
uv run python -m app.cli.backtest_replay \
  --scanner volume_2x_average \
  --from 2025-01-01 \
  --to 2025-12-31
```

No REST APIs or UI were added in this task.

#### Migration

None. The existing `daily_prices`, `indicator_results`,
`scan_definitions`, and backtesting foundation schema fully support
historical data and replay; no new table is needed for Task 17.2.

#### Tests

- `tests/test_nse_historical.py` — mocked NSE HTTP: one valid modern
  bhavcopy imported into `DailyPrice`, already-existing date skipped,
  weekend skipped, 404/holiday handled, 403 surfaced, network error,
  range summary, date-range validation, missing-date detection, no real
  NSE requests.
- `tests/test_backtest_replay.py` — single scanner one date, single
  scanner date range, replay all, symbol filter, insufficient history,
  **no future-data leakage** (future price and future indicator),
  volume-2x excludes replay date, breakout excludes future prices,
  deterministic repeated replay, missing-date reporting,
  `ensure_historical_data` with `fetch_missing=false`.

### 17.4 - Backtest performance metrics engine

Completed `app/services/backtest_metrics.py` (`BacktestMetricCalculator`),
wired it into the backtest CLI after successful simulation, and added a
comprehensive test suite. No APIs, UI, charts, OpenAlgo, paper trading, or
real-time data were added.

#### Metrics implemented

- `total_return_percent` — `(final - initial) / initial * 100`.
- `annualized_return_percent` / `cagr` — same actual-calendar-duration
  convention (`(final / initial) ** (365.25 / days) - 1`, `days` between
  `start_date` and `end_date`) so the two never diverge.
- Trade stats (closed trades only): `total_trades`, `winning_trades`,
  `losing_trades`, `win_rate` (`winners / total * 100`), `gross_profit`
  / `gross_loss` (numeric sums, `0` when a side is empty), `profit_factor`
  (None when no trades or `gross_loss == 0`), `average_win` / `average_loss`
  (None when a side is empty), `expectancy`
  (`win_prob * avg_win - loss_prob * avg_loss`, missing side contributes 0).
- Breakeven trades (`net_pnl == 0`) count toward `total_trades` but are
  neither winners nor losers.
- `max_drawdown_percent` — running-peak drawdown from ordered equity points;
  each `BacktestEquityPoint.drawdown_percent` is persisted.
- `sharpe_ratio` / `sortino_ratio` — periodic returns from ordered equity
  points, annualized with `sqrt(252)`; risk-free rate is 0 (config.py defines
  no backtest risk-free setting). Both are None for insufficient data / zero
  volatility; Sortino is None when there is no downside deviation.
- `average_holding_days` — prefers `bars_held`; falls back to calendar days
  `(exit_date - entry_date).days` when `bars_held` is NULL; None when no
  usable values.
- `benchmark_return_percent` — first available close on/after `start_date` to
  last available close on/before `end_date` from existing `DailyPrice` rows
  only (no external fetch, no dates outside the run range); None when the
  symbol/stock/prices are missing or the first close is invalid.
- `alpha` — `total_return_percent - benchmark_return_percent` when a
  benchmark return exists, else None.

#### Conventions

- Only CLOSED `BacktestTrade` rows are used; open trades are ignored.
- Undefined metrics are persisted as NULL — never NaN, Infinity, or -Infinity.
- CAGR uses actual calendar duration with 365.25 days/year; duration `<= 0`,
  `initial_capital <= 0`, or `final_capital <= 0` yields None (no div-by-zero).

#### Persistence

- Reuses `BacktestMetricRepository.upsert` — exactly one `BacktestMetric`
  row per run; recompute updates the same row (verified idempotent).

#### Integration and failure policy

- The backtest CLI (`app/cli/backtest.py`) computes and displays a metrics
  table after a successful `simulate` (Initial Capital, Final Capital, Total
  Return, CAGR, Trades, Win Rate, Max Drawdown, Sharpe, Sortino, Profit
  Factor, Benchmark Return, Alpha). `compute_metrics` and
  `simulate_and_metrics` use the same table helper.
- If metric computation fails, a sanitized error
  (`BacktestMetricError` via `sanitized_metric_failure`) is surfaced that
  never leaks exception internals, and the process exits non-zero. Run status
  is not modified by metric failure.

#### Tests

- `tests/test_backtest_metrics.py` (40 tests) covers positive/negative/flat
  returns; multi-year/sub-year/zero-day/non-positive-capital CAGR; winners +
  losers, no-trades, no-winners, no-losers, breakeven, and open-trade
  exclusion trade stats; flat/single/multi-peak/recovery drawdown plus
  equity-point persistence; valid/zero-variance/insufficient Sharpe; downside/
  no-downside/insufficient Sortino; bars_held and calendar-day fallback
  holding days; valid/missing-symbol/missing-prices/range-selection/zero-close
  benchmark return; alpha present/absent; persistence create-and-recompute
  single-row; determinism; simulator-then-metrics integration (values match
  `run.final_capital`); and the sanitized failure policy.

### 17.5 - Backtesting REST APIs

Completed the backtesting REST API plus the simulator settlement fix and the
backtest permission codes. No UI, charts, OpenAlgo, paper trading, or
real-time data were added.

#### 17.5A - Simulator settlement fix

`app/services/backtest_simulator.py` double-counted the entry commission in
`final_capital`: the entry outlay (`entry_value + entry_commission`) was
deducted from cash on entry and `trade.net_pnl` also subtracted the entry
commission, so the credit on close understated the cash returned. Both
settlement sites (the `exit_result` close in the daily loop and the
`end_of_backtest` forced close) now credit
`_cash_returned_on_close(trade, pos)` = `net_pnl + entry_value +
entry_commission`, preserving
`final_capital = initial_capital + gross_pnl - costs`. No other simulator
behavior changed. Regression tests added to `tests/test_backtest_simulator.py`
(`# SETTLEMENT` section: `_flat_round_trip`/`_trend_round_trip` helpers and
4 tests) — 27 simulator tests total.

#### 17.5B - Permission codes

`app/services/builtin_auth.py::BUILT_IN_PERMISSION_DEFINITIONS` grew from 25 to
28 codes with `backtests.read/run/write`. `admin` gets all three, `analyst`
gets read+run, `viewer` gets read; `super_admin` remains unrestricted.
`docs/01_Architecture/Permission_Matrix.md` updated with the codes table, the
role matrix rows, and a route-protection table for all 13 backtest routes.

#### 17.5C-H - REST API

New `app/api/routes/backtests.py` (prefix `/backtests`, tag `Backtests`) with
13 routes across strategies, runs, execution, and results:

- Strategies: `POST/GET /strategies`, `GET/PUT/DELETE /strategies/{id}`.
  Create validates active `scanner_code` (ScanDefinition active) and rejects
  duplicate `code` with `409`. DELETE is a **soft deactivate**
  (`is_active = false`) so historical runs keep valid FKs.
- Runs: `POST/GET /runs`, `GET /runs/{id}`. Create validates date range,
  capital/cost/sizing bounds, no-future `end_date`, active strategy, and an
  active benchmark stock. `list_runs` hides other users' runs from non-admins.
- Execution: `POST /runs/{id}/execute` — synchronous, guarded by an in-process
  per-run `asyncio.Lock` and run-status checks (`409` when already
  running/completed). On success the run is persisted `completed` with
  `final_capital`/timing, and trades/equity/metric rows are written; the
  response carries the simulation summary and metrics dict. Failures are
  sanitized (`400` for `ValueError`, `500` otherwise;
  `detail = "Backtest execution failed: <ExceptionType>"`) and the run is
  persisted `failed`.
- Results: `GET /runs/{id}/summary`, `/trades` (paginated with
  status/position_side/profitable/symbol filters + stock symbol enrichment),
  `/equity` (chronological), `/metrics` (persisted row; `409` before
  completion, `404` if missing).

Ownership: runs are private to `requested_by_user_id`; a non-admin may only
access their own runs, admins/superusers may access any run (an `admin_override`
audit event is recorded). Denials record `access_denied`.

Supporting changes: `app/schemas/backtest.py` added request/response schemas
(`BacktestStrategyCreateRequest`, `BacktestRunCreateRequest`,
`BacktestStrategyList`, `BacktestRunList`, `BacktestRunDetailRead`,
`BacktestTradeList`, `BacktestEquityPointList`, `BacktestRunSummary`,
`BacktestExecutionResponse`, `BacktestDeleteResponse`); `BacktestTradeRead`
gained optional `symbol`. `app/repositories/backtest.py` extended
strategy/run/trade/equity repositories (search+count+update, owner filter,
paginated trade listing with filters, equity listing). `app/api/router.py`
includes the backtest router. `app/services/backtest_metrics.py` now reads
committed trades/equity points directly from the database so metrics reflect
committed state even in the same session where a run was just simulated (the
in-memory relationship collections can be stale after `commit()` with
`expire_on_commit=False`).

No migration — no schema change.

#### 17.5I - API tests

`tests/test_backtest_api.py` (16 tests): unauthenticated 401 for all endpoint
groups; viewer read-only (403 execute/write); analyst read+run, 403 write;
admin + superuser write; strategy create/duplicate-409/detail/update/
soft-delete/404 and invalid-scanner 422; strategy list pagination + filters;
run create + validation errors (dates, capital, commission, sizing, future
end_date, benchmark, inactive strategy); run list pagination + filters;
execute end-to-end with the settlement regression (`final_capital =
99996.000`, persisted completed run + metric with `total_trades = 1`);
running-state 409; sanitized failure (400, exact detail, `failed` persisted);
results before completion (summary zeros, metrics 409); results after
execution (summary trade_count, trades pagination + filters + symbol, equity
chronological, metrics nulls preserved); benchmark-symbol metrics; run
ownership restrictions + admin_override audit + superuser override.

### 17.6 - Backtesting HTML UI & charts

Completed the browser-facing backtesting UI built on the REST layer from Task 17.5
(no migration, no business logic duplication, no frontend recompute).

#### Files added

- `app/api/routes/backtest_pages.py` — page router registered in `app/main.py`
  under `/backtests`. Pages: workspace (`GET /backtests`), strategies
  (`GET /backtests/strategies`), runs (`GET /backtests/runs`), run detail
  (`GET /backtests/runs/{id}`). HTMX partials: `/runs/{id}/workspace`,
  `/runs/{id}/status`, `/runs/{id}/metrics`, `/runs/{id}/trades`,
  `/runs/history`, `/strategies/table`, `/strategies/{id}/form`. POST handlers:
  `/strategies/create`, `/strategies/{id}/update`, `/strategies/{id}/deactivate`,
  `/runs/create`, `/runs/{id}/execute`.
- `app/templates/backtests/` — workspace, strategies, runs, run_detail pages.
- `app/templates/backtests/components/` — run_form, strategies_table,
  runs_table, strategy_form, run_workspace, run_status, metrics_cards,
  benchmark_cards, equity_chart, trades_table partials.
- `app/static/js/backtests.js` + `app/static/css/backtests.css` —
  Lightweight Charts (CDN) equity/drawdown rendering, resize, HTMX
  `afterSwap` lifecycle, empty-state handling.
- `tests/test_backtest_ui.py` — 29 tests (navigation/auth, workspace,
  strategy CRUD (incl. XSS escaping + deactivation), run creation, execution
  end-to-end with patched replay, metrics cards, charts, trades table +
  empty-state, run history, pending/running/completed/failed detail states,
  HTMX partials, ownership denial, CSRF-missing rejection, read-only UX).

#### Permission model

- `backtests.read` gates every page and partial (route dependency).
- `backtests.write` gates strategy create/update/deactivate and run creation.
- `backtests.run` gates execution.
- Runs are private: a non-admin may only view runs they requested
  (`_can_access_run`); an admin override raises `ForbiddenError` for
  cross-user access. The workspace form shows a read-only hint and disables
  write controls for viewers.

#### Execution

- `POST /runs/{id}/execute` calls the shared `BacktestSimulator.simulate_run`
  and `BacktestMetricCalculator.compute_metrics` (same services as the REST
  API), guarded by an in-process per-run `asyncio.Lock` and run-status checks
  (completed/running locked). Failures are sanitized (no exception leak); the
  run remains accessible with a danger flash. `can_run`/`can_write` are
  pre-computed before the try/except to avoid detached-session lazy loads
  after `session.rollback()`.

#### Presentation

- Indian-formatted currency (\u20b9 + lakh grouping) via Jinja filters
  (`inr`, `num`, `pct`, `int`, `sign_class`, `status_badge`, `label`).
- `BacktestMetric` NULL (never NaN/inf) renders `—`/`N/A`.

#### Tests

`tests/test_backtest_ui.py` (29 tests): sidebar link read-gated; workspace
form + recent runs; viewer read-only hint; strategy create denied for viewer;
strategy create + duplicate-safe (admin); strategy edit/deactivate with XSS
escaping; run creation redirect (admin) and denial (viewer); run detail
ownership restriction; missing run 404; execute end-to-end (completed render,
sanitized failure, double-execution rejection, empty-state charts); metrics
cards + benchmark cards; trades table + empty state; run history status
badges + detail links; pending/running/completed/failed detail states; HTMX
partials; CSRF-missing rejection.

No migration.

## Backlog (future tasks)

- None for Sprint 17 (Sprint 18: Market Intelligence).

## Status (reconciliation)

- Task 17.2 — VERIFIED. Audit complete; no new NSE downloader/mirror added (manual
  local-file fallback preserved via `uv run python -m app.cli.import_nse file <path>`.
  Test suite green: historical + replay + parser + market_data (36 tests).
- Task 17.3 — COMPLETE. `app/services/backtest_simulator.py` implemented; two cost
  bugs fixed during reconciliation (exit-commission used slippage rate instead of
  `commission_rate`; cash settlement in `_close_position` double-counted commissions).
  `tests/test_backtest_simulator.py` (23 tests) covers entry modes, sizing, one
  position per stock, duplicate-signal skip, SL/target/max-holding/opposite-signal/
  end-of-backtest exits, cost math, equity curve, run state and re-simulation rejection.
- Task 17.4 — COMPLETE & VERIFIED. `BacktestMetricCalculator` fully implemented and
  wired into the CLI; `tests/test_backtest_metrics.py` (40 tests) green; migration head
  unchanged (`d7b4cc79fd45`); full suite 521 passed ×3; ruff clean. See the 17.4
  section above for conventions.
- Task 17.5 — **COMPLETE & VERIFIED.** Simulator settlement fix (entry commission no
  longer double-counted in `final_capital`; 27 simulator tests), backtests.read/run/write
  permissions (26 permission codes, role matrix + route table in Permission_Matrix.md),
  full `/api/v1/backtests` REST API (strategies, runs, synchronous execution, results;
  13 routes / 9 paths), `tests/test_backtest_api.py` (16 tests) green. `backtest_metrics.py`
  hardened to read committed trades/equity directly from the database instead of the
  (possibly stale after `commit()`) in-memory relationship collections. Migration head
  unchanged (`d7b4cc79fd45`); full suite 541 passed; ruff clean. See the 17.5 section
  above for details.
- Task 17.6 — **COMPLETE & VERIFIED.** Browser backtesting UI/charts; 29 tests in
  `tests/test_backtest_ui.py`; no migration. See the 17.6 section above.
- Task 17.7 — **COMPLETE & VERIFIED (v0.13.0 release).** Alembic head `d7b4cc79fd45`
  (clean, no pending operations); ruff clean; `git diff --check` clean; three consecutive
  full-suite passes (570 passed). Version bumped to `v0.13.0` in `app/main.py`;
  CHANGELOG v0.13.0 section added and the inaccurate v0.12.1 version-bump claim
  corrected; Permission Matrix reconciled to the canonical 28-code catalog (phantom
  codes removed, role/route matrices aligned with `app/services/builtin_auth.py` and
  the route dependency layer). Annotated tag `v0.13.0` created after all release gates;
  `sprint-17-backtesting` merged into `main` (fast-forward) and pushed.

## Known issues (Sprint 17)

- **Resolved in 17.5A:** the simulator entry-commission double count. Both settlement
  sites now credit `net_pnl + entry_value + entry_commission`, preserving
  `final_capital = initial_capital + gross_pnl - costs`.
- Backtest execution is synchronous inside the request (no Celery/Redis); very long
  runs block the request thread. A worker-based queue is a future improvement.

## CLI regression

- Create-admin: Typer conversion deleted the module-level `_create_admin` and the
  int-returning `main(argv)` imported by `tests/test_auth_api.py` (AttributeError).
  Fix: `app/cli/create_admin.py` restored as a dual-mode module (Typer app plus
  legacy-compatible `main(argv) -> int`). `main()` guards added to `import_nse`,
  `nse_history`, `backtest_replay`, and `backtest`; `app/cli/backtest.py`
  `async_session_factory` reference bug fixed.
