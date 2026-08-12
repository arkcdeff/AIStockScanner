# Project State

Snapshot of AIStockScanner after Sprint 17 (Backtesting) — released as v0.13.0.

## Stack

- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + Bootstrap 5 + HTMX frontend
- One APScheduler 3.11 instance for optional EOD, delivery, and daily-summary jobs
- AI Explanation Engine with async providers (Ollama, DeepSeek, OpenAI, mock)

## Data

- 33 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis, AIExplanation, Watchlist, WatchlistStock, Portfolio,
  Holding, AlertRule, AlertEvent, NotificationChannel, NotificationPreference,
  NotificationDelivery, DailySummaryRun, User, Role, Permission, UserRole, RolePermission,
  UserSession, UserPreference, AuditLog, BacktestStrategy, BacktestRun, BacktestTrade,
  BacktestEquityPoint, BacktestMetric
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
- `technical_analysis` stores composite scores, pivots, ratings, and trend/breakout flags
- `ai_explanations` is unique on `(stock_id, trade_date, provider)`
- `watchlist_stocks` is unique on `(watchlist_id, stock_id)`
- `holdings` is unique on `(portfolio_id, stock_id)`
- Notification channel names and channel/event preferences are unique
- `daily_summary_runs.summary_date` is unique, preventing duplicate summary execution per date
- Auth: `users` with `is_superuser`, `is_active`, `is_verified`; `user_sessions` with revocation;
  `audit_logs` with JSON metadata
- Auth: `roles`, `permissions`, `user_roles`, `role_permissions` for RBAC
- Backtesting: `backtest_strategies` (unique code), `backtest_runs`, `backtest_trades`,
  `backtest_equity_points` (unique run+date), `backtest_metrics` (unique run)

## Migrations

- Alembic current: `d7b4cc79fd45 (head)`
- Alembic heads: `d7b4cc79fd45 (head)`
- Linear history: 16 revisions
- Sprint 17 Task 17.1 migration: backtesting foundation tables (strategies, runs, trades, equity_points, metrics)

## EOD pipeline

- Flow: import -> indicators -> active scanners -> analytics -> alerts
- One fresh session per stage, single-active-run guard, abandoned-run recovery, and sanitized errors
- Manual triggers: `/api/v1/pipeline/run-file`, `/run-date`, `/run-latest`
- Observability: `/api/v1/pipeline/runs`, `/runs/{id}`, `/status`
- Optional weekday EOD job defaults to 18:30 Asia/Kolkata and is disabled by default
- After successful completion, alert delivery orchestration uses a separate fresh session
- Notification failures are persisted/logged separately and cannot change a completed pipeline
- Failed pipelines do not trigger notification delivery

## Analytics engine

- `app/services/analytics_engine.py` calculates deterministic trend, momentum, volume, delivery,
  volatility, and overall scores (0-100)
- Includes support/resistance, classic/Fibonacci pivots, relative volume, delivery strength,
  ratings, and trend/breakout flags
- APIs: `/api/v1/analytics/{symbol}`, `/top-rated`, `/trending`, `/breakouts`
- Surfaced on dashboard and stock detail

## AI Explanation Engine

- Provider abstraction, validated prompt DTOs, safe response parsing, deterministic fallback,
  in-memory TTL caching, and per-stock persistent caching
- Providers: mock (default/no network), Ollama, DeepSeek, OpenAI
- AI only explains deterministic analytics; it never computes indicators, scores, or scanners
- APIs: `/api/v1/ai/stock/{symbol}`, `/top-rated`, `/market-summary`
- Daily summaries read cached AI market text only and never force a provider call

## Watchlists

- CRUD, default-list handling, add/remove/update stocks, latest-price enrichment, stock search
- APIs: `/api/v1/watchlists/...`
- HTML/HTMX page: `/watchlists`
- Integrated into stock detail and dashboard

## Portfolio

- Portfolio/holding CRUD and current/invested value, P&L, ROI, daily P&L, and allocation summaries
- APIs: `/api/v1/portfolios/...`
- HTML/HTMX page: `/portfolio`
- Integrated into stock detail and dashboard

## Alert rules

- Deterministic engine for `overall_score`, `rsi`, `ema_cross`, `delivery`, `volume`, `breakout`,
  `support_broken`, and `resistance_broken`
- Rule scope supports one stock, a watchlist, or all active stocks
- Cooldown is enforced through `last_triggered`; matches persist as `AlertEvent`
- APIs: `/api/v1/alerts/...`; HTML/HTMX page: `/alerts`
- The EOD pipeline records `alerts_triggered`

## Notification system

- Models: channel, per-event preference, delivery state/attempts, and one summary run per date
- Providers: deterministic mock, Telegram, SMTP email, and in-app
- `NotificationService` centralizes eligibility, quiet hours, rendering, delivery persistence,
  duplicate prevention, retry, stale recovery, and mark-read behavior
- Retry engine distinguishes transient/permanent failures and enforces maximum attempts
- Provider errors are sanitized before persistence, logging, or display
- Daily summaries read existing pipeline, scanner, analytics, alert, watchlist, and portfolio data;
  missing sections are safe and cached AI text is optional
- Existing scheduler supports stable jobs `notification_delivery_interval` and
  `notification_daily_summary`; both are disabled by default, coalesced, non-overlapping, and use
  fresh sessions
- APIs: `/api/v1/notifications/...`
- Pages: `/notifications`, `/notifications/settings`, `/notifications/history`
- Dashboard: notification counts, recent rows, latest summary, delivery-scheduler state
- Stock detail: latest five related delivery rows without N+1 queries
- Secrets are masked from JSON/HTML and stored message content is escaped

## Authorization Framework (Sprint 16)

- Reusable helpers in `app/auth/authorization.py` for both API (Bearer) and browser (cookie) auth
- 28 permission codes across 4 roles: super_admin, admin, analyst, viewer
- API modules protected: analytics, ai, stocks, market_data, indicators, scanners, pipeline,
  watchlists, portfolio, alerts, notifications, backtests, admin
- HTML pages protected: dashboard, scanner-workspace, stock_detail, watchlists, portfolio,
  alerts, notifications, admin
- Superuser/super_admin bypass; owner-or-admin with audit on `admin_override`
- Audit events: `access_denied`, `permission_denied`, `admin_override`, `session_revoked`,
  `builtin_auth_seeded`
- Browser auth: `require_browser_permission`, `require_browser_role`, `enforce_browser_permission`
- Friendly 403 page (no stack traces), unauthenticated redirect to `/login?next=...`

## Backtesting (Sprint 17 Tasks 17.1–17.7)

- Task 17.7 — release: **COMPLETE & VERIFIED.** Version bumped to v0.13.0
  (`app/main.py`); CHANGELOG v0.13.0 section added; Permission Matrix reconciled to
  the 28-code catalog; release gates passed (alembic head `d7b4cc79fd45`, ruff clean,
  three consecutive full-suite passes, `git diff --check` clean); annotated tag
  `v0.13.0` created and merged `sprint-17-backtesting` → `main` (fast-forward).
- Task 17.6 — UI/charts: **COMPLETE & VERIFIED**.
  Browser pages (workspace, strategies, runs, run detail), HTMX partials, Lightweight Charts
  equity/drawdown, trade table, run-status locking, read/write/run permissions, CSRF on writes,
  ownership enforcement, Indian-formatted currency, 29 tests in `tests/test_backtest_ui.py`.
  No migration.
- Task 17.1 — persistence/domain foundation: COMPLETE and verified.
  Models `BacktestStrategy`, `BacktestRun`, `BacktestTrade`, `BacktestEquityPoint`,
  `BacktestMetric`; repositories, validated schemas, migration `d7b4cc79fd45` applied to dev + test DBs.
- Task 17.2 — historical NSE fetch + replay: VERIFIED.
  `app/services/nse_historical.py`, `app/services/backtest_replay.py`; replay is strict no-look-ahead
  (only `<= D` price/indicator rows), 403 surfaced as `blocked_403` (no NSE bypass), bulk in-memory
  loads, existing scanner rules reused. Manual local-file fallback preserved;
  legacy/pre-08-Jul-2024 bhavcopy handled by the alias-based parser.
- Task 17.3 — trade simulation engine: COMPLETE.
  `app/services/backtest_simulator.py` and `tests/test_backtest_simulator.py` (23 tests) covering
  entry modes, position sizing, one-position-per-stock, duplicate-signal skip, SL/target/max-holding/
  opposite-signal/end-of-backtest exits, transaction-cost math, equity curve, run state and rerun
  rejection. Two cost-math bugs fixed during reconciliation (exit-commission used `entry_slippage`
  instead of `commission_rate`; cash settlement double-counted commissions).
- Task 17.4 — performance metrics engine: COMPLETE & VERIFIED.
  `app/services/backtest_metrics.py` (`BacktestMetricCalculator`) implemented: total/annualized/CAGR
  returns (365.25-day convention), closed-trade stats, win rate, profit factor, expectancy, breakeven
  handling, running-peak drawdown (persisted per equity point and as `max_drawdown_percent`),
  Sharpe/Sortino (risk-free 0, sqrt(252) annualization, None when undefined), `bars_held`/calendar-day
  holding days, `DailyPrice`-only benchmark return (no external fetch), and alpha. Metrics are None
  (never NaN/Inf) when undefined, persisted idempotently via `BacktestMetricRepository.upsert`
  (one row per run), wired into the CLI metrics table after successful simulation, and surfaced
  through a sanitized failure policy. `tests/test_backtest_metrics.py` (40 tests).
- CLI regression fixed: `app/cli/create_admin.py` now exposes a Typer app AND a legacy-compatible
  module-level `_create_admin` + `main(argv) -> int`; `main()` guards added to import_nse/nse_history/
  backtest_replay/backtest; `app/cli/backtest.py` reference bug (`async_session_factory`) fixed.
- Task 17.5 — REST APIs + settlement fix + permissions: **COMPLETE & VERIFIED**.

  Simulator settlement fixed (both close sites credit `net_pnl + entry_value + entry_commission`;
  `final_capital = initial_capital + gross_pnl - costs`; 27 simulator tests incl. 4 new SETTLEMENT tests).
  Permission codes 25→28 (`backtests.read/run/write`; admin=all, analyst=read+run, viewer=read),
  Permission_Matrix.md role/route tables updated. New `app/api/routes/backtests.py`: 13 routes /
  9 paths under `/api/v1/backtests` (strategy CRUD + soft-deactivate, run create/list/detail,
  synchronous execute guarded by an in-process per-run lock, summary/trades/equity/metrics).
  Runs are private to their requester with admin/superuser override + `admin_override` audit;
  failures sanitized and the run persisted `failed`. Schemas/repositories extended
  (`BacktestStrategyCreateRequest`, `BacktestRunCreateRequest`, `BacktestRunSummary`,
  `BacktestExecutionResponse`, `BacktestDeleteResponse`, trade listing filters + symbol,
  strategy search/update, owner-filtered run listing). `backtest_metrics.py` reads committed
  trades/equity directly from the database (fixes stale in-memory collections after `commit()`).
  `tests/test_backtest_api.py` (16 tests). No migration. Full suite 570 passed; ruff clean.
  API docs: `docs/03_APIs/Backtest_API.md`.

## API and page surface

- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, analytics, ai,
  watchlists, portfolios, alerts, notifications, backtests, admin
- `/dashboard`: pipeline, analytics, AI, portfolio, watchlist, alert, and notification summaries
- `/scanner-results`: server-rendered workspace with CSV/Excel/PDF export
- `/stocks/{symbol}`: overview, indicators, analytics, scanners, AI, watchlist, portfolio, alerts,
  and related notifications
- `/watchlists`, `/portfolio`, `/alerts`, `/notifications`: HTML/HTMX workspaces
- `/admin/users`, `/admin/roles`, `/admin/permissions`: admin management
- `/login`, `/profile`, `/settings`, `/sessions`, `/password/change`: auth UI
- `/api/v1/auth`: login, refresh, logout, me, preferences, profile, sessions, change-password
- Sprint 16.8 pages: `/market-data`, `/indicators`, `/scanner-library`, `/scanner-run`,
  `/pipeline`, `/scheduler`, `/analytics`, `/ai`, `/stocks` (browse)

## Testing

- Real PostgreSQL test persistence with database cleanup between tests
- Scan definitions are seeded idempotently
- 570 total tests passing (541 baseline + 29 Task 17.6 UI),
  three consecutive full-suite passes
- 119 focused Sprint 16 tests cover auth models, providers, authorization helpers, APIs, UI,
  ownership/admin_override, audit events, and browser cookie auth
- 16 Sprint 16.8 UI tests cover market data, indicators, scanner library/run, pipeline,
  scheduler, analytics, AI, and stocks pages
- 29 Sprint 17 Task 17.1 tests cover backtest models, constraints, relationships, repositories,
  and cascade deletes
- 23 Sprint 17 Task 17.3 tests cover the simulation engine: entry modes, sizing, positions,
  exits, cost math, equity curve, run state and rerun rejection
- 40 Sprint 17 Task 17.4 tests cover the metrics engine: returns/CAGR, trade stats,
  drawdown, Sharpe/Sortino, holding days, benchmark/alpha, persistence idempotency,
  simulator integration, determinism, and the sanitized failure policy
- 4 Sprint 17 Task 17.5 settlement regression tests cover the simulator cash fix
  (entry commission not double-counted; zero-cost win/loss; exit commission charged once;
  multi-trade settlement identity)
- 16 Sprint 17 Task 17.5 API tests cover auth/roles, strategy CRUD + soft delete +
  validation, run create/list + validation, synchronous execute end-to-end (incl. settlement),
  running-state rejection, sanitized failures, results pre/post-completion, benchmark metrics,
  and run ownership + admin override
- 29 Sprint 17 Task 17.6 UI tests cover sidebar gating, workspace, strategy CRUD + XSS,
  run creation/execution, metrics/benchmark cards, charts, trades table + empty state,
  run history + detail states, HTMX partials, ownership, CSRF, and read-only UX
- External AI, Telegram, and SMTP calls are mocked or replaced by deterministic providers
- Release gate: Alembic current equals head, Ruff clean, and three consecutive full-suite passes

## Known gaps

- No Redis, Celery, or distributed scheduler
- Notification jobs and outbound sending remain disabled until explicitly configured
- `app/scanner/` contains intentionally retained duplicate/dead scanner code
- `scanner_engine.run_all` re-fetches indicator rows per scanner (performance follow-up)
- AI collection endpoints are memory-cached only; only per-stock explanations are persisted
- CSRF system follows existing project conventions (HTMX forms)
- The pre-existing simulator entry-commission double count was resolved in Task 17.5A;
  both settlement sites credit `net_pnl + entry_value + entry_commission`
- Backtest execution is synchronous inside the request (no Celery/Redis); long runs
  block the request thread — a worker-based queue is a future improvement

## Current Sprint

Sprint 17: Backtesting — COMPLETE
- Task 17.1 — Backtesting database + domain foundation — COMPLETE
- Task 17.2 — Historical NSE fetch + historical replay — VERIFIED
- Task 17.3 — Trade simulation — COMPLETE (cost bugs fixed; 23 tests)
- Task 17.4 — Performance metrics — COMPLETE & VERIFIED (40 tests; CLI-wired)
- Task 17.5 — REST APIs — COMPLETE & VERIFIED (16 API tests; settlement fix; 28 permission codes)
- Task 17.6 — UI/charts — COMPLETE & VERIFIED (29 tests, no migration)
- Task 17.7 — Release — COMPLETE & VERIFIED (v0.13.0; tag + merge + push)
- CLI regression (create_admin / main() guards) — FIXED

## Next Task

Sprint 18 — Market Intelligence (not yet started).