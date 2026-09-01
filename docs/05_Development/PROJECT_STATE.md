# Project State

### Current productization state (2026-09-01) — Stage 2.6.5 Phase F Browser Truth & Product Polish Repair

- **Current task**: Stage 2.6.5 Phase F Browser Truth & Product Polish Repair COMPLETE & CERTIFIED
- **Checkpoint**: `checkpoint/sprint19-20-20260822`
- **Version**: `0.14.0`
- **Alembic**: `478d723971bc` (single current head, no new migrations)
- **Verification**: 31 focused tests in `test_stock_intelligence.py` passed; 17 tests in `test_stock_detail.py` passed; full test suite 1,413 / 1,413 tests passing (100% PASS, 0 failures, 0 errors); Ruff clean; import OK; Alembic check clean; git diff clean; 11 pilot stocks verified live in database and browser.
- **Priority**: Deterministic research context aggregation, multi-domain factor attribution (Technicals, Fundamentals, Valuation, News & Policy, Corporate Events, Delivery, Sector, Market Context), strict missing != zero, completeness vs confidence separation, PIT safety, and responsive UI.
- **Scope Implemented**:
  - `app/schemas/stock_intelligence.py`: Typed schemas for Stock Intelligence, overall states, evidence items, and factors.
  - `app/services/stock_intelligence_service.py`: Point-in-time safe evidence synthesis engine across 8 domains.
  - `app/templates/components/stock_intelligence.html`: Responsive Bootstrap 5 UI component embedded into Stock360.
  - `tests/test_stock_intelligence.py`: Comprehensive test suite with 31 tests.
- **Gate**: do not start Phase G until explicitly authorized.

---

AIStockScanner after Sprint 19 Task 19.1J (Real Historical Data Expansion + Coverage Certification Engine) COMPLETE & VERIFIED (1283 tests). Sprint 18 COMPLETE (released v0.14.0). Sprint 19 IN PROGRESS. Stage 2.6.5 Phase F Complete & Certified.


## Stack

- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + Bootstrap 5 + HTMX frontend
- One APScheduler 3.11 instance for optional EOD, delivery, and daily-summary jobs
- AI Explanation Engine with async providers (Ollama, DeepSeek, OpenAI, mock)

## Data

- 58 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis, AIExplanation, Watchlist, WatchlistStock, Portfolio,
  Holding, AlertRule, AlertEvent, NotificationChannel, NotificationPreference,
  NotificationDelivery, DailySummaryRun, User, Role, Permission, UserRole, RolePermission,
  UserSession, UserPreference, AuditLog, BacktestStrategy, BacktestRun, BacktestTrade,
  BacktestEquityPoint, BacktestMetric, MarketIndex, MarketIndexPrice, InstitutionalFlow,
  MarketIntelligenceSnapshot, TradingAccount, TradingOrder, TradingExecution, TradingPosition,
  SecurityAlias, CorporateAction, IndexMembership, DataQualityIssue, DerivativeContract,
  DerivativeLotSizeHistory, DerivativeDailyPrice, DerivativeParticipantPosition, FiiDerivativeStatistic,
  FinancialStatement, CorporateAnnouncement, ShareholdingPattern, NewsEvent, NewsEntityLink,
  Industry, ComparableCompany

## Migrations

- Alembic current: `478d723971bc (head)`
- Alembic heads: `478d723971bc (head)`
- Linear history: 27 revisions
- Sprint 17 Task 17.1 migration: backtesting foundation tables (strategies, runs, trades, equity_points, metrics)
- Sprint 18 Task 18.1 migration `25e6f159a646`: market intelligence foundation tables
- Sprint 18 Task 18.8 migration `5f7b2c9d4e3a`: econ_events table
- Sprint 19 Task 19.1 migration `95a0be65d4a7`: trading foundation tables
- Sprint 19 Task 19.1C migration `ac9c6a6767be`: historical data core tables (security_aliases, corporate_actions, index_memberships, data_quality_issues, stocks.lifecycle_status)
- Sprint 19 Task 19.1D-1 migration `b71d9e2a3456`: derivative contract master tables (derivative_contracts, derivative_lot_size_histories)
- Sprint 19 Task 19.1D-2 migration `c82e3f4a5678`: derivative daily prices table (derivative_daily_prices)
- Sprint 19 Task 19.1D-7 migration `e91f4a5678ab`: derivative participant tables (derivative_participant_positions, fii_derivative_statistics)
- Sprint 19 Task 19.1E migration `f1a2b3c4d5e6`: fundamental corporate tables (financial_statements, corporate_announcements, shareholding_patterns)
- Sprint 19 Task 19.1F migration `g7h8i9j0k1l2`: news policy tables (news_events, news_entity_links)
- Stage 2.6.2 migration `478d723971bc`: add Industry and ComparableCompany tables and Stock.industry_id foreign key (head)

## Known Limitations & Architecture Notes

- **Dual Stock Industry Architecture**: `Stock` possesses both a legacy string column `Stock.industry` (String(120), used for raw exchange/master industry names and initial fallback) and a normalized foreign key `Stock.industry_id` (ForeignKey to `industries.id`, added in Stage 2.6.2 migration `478d723971bc`). Services access industry using `industry_val = stock.industry or (stock.industry_ref.name if stock.industry_ref else None)`. Normalization of all legacy strings into `industries` table is intentionally preserved without destructive schema redesign.

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
- **1100 total tests passing**,
  three consecutive full-suite passes
- 34 Task 19.1A tests in `tests/test_market_data_19a.py` covering format detection, parser +
  delivery merge, importer audit fields, upload archival, coverage, readiness, status, backfill
  API validation, CSRF/security, page actions, pipeline scanner-skip, and scheduler retry/catch-up
- 34 Task 19.1 tests in `tests/test_trading_foundation.py` (order lifecycle, live rejection,
  idempotency, Decimal precision, time-aware timestamps, backtest separation)
- 31 Sprint 18 Task 18.5 tests: 13 in `tests/test_institutional_intelligence.py` covering
  FII/FPI and DII exact labels/unit/dataset, Decimal net recomputation, 1/3/5/20-session rollups,
  FII 65%/DII 35% weighting, missing-not-zero, one bulk query, ordered provider/DB fallback,
  partial and raised-provider behavior, malformed-future isolation, provenance, and future invariance;
  18 in `tests/test_fx_crude_intelligence.py` covering exact India VIX, USD/INR spot orientation,
  and Europe Brent physical-spot identities, returns/trend/regime/scoring, provider exception and
  overlap behavior, exact/stale/unavailable fallback, no HTTP, date-only cutoffs, and mandatory
  future invariance for all three factors
- 67 Sprint 18 Task 18.4 tests: 25 in `tests/test_gift_nifty_provider.py` covering the canonical
  NSE IX `FUTIDX`/NIFTY 50/USD identity, no-HTTP unavailable/manual CSV contract, aware observation
  and availability timestamps, deterministic knowability, provenance, and proxy/invalid rejection;
  42 in `tests/test_gift_nifty_intelligence.py` covering current split sessions, completed NIFTY
  reference selection, one bounded bulk query, formulas/direction boundaries, score/confidence/
  completeness bounds, ordered provider/DB fallback, explicit contexts, date-only limitations,
  determinism, and mandatory future-invariance no-look-ahead
- 43 Sprint 18 Task 18.3 tests: 24 in `tests/test_global_index_provider.py` covering the exact
  seven-index provider catalog, injected/mock HTTP, normalization/provenance, provider failures,
  idempotent catalog/range/date ingestion, ordered fallback, boundary previous close, stale DB,
  missing data, and range validation; 19 in `tests/test_global_market_intelligence.py` covering
  native/DST-safe session cutoffs, one bulk history load, deterministic bounded scoring,
  breadth, missing-weight renormalization, completeness, freshness/agreement confidence,
  short history, no-look-ahead, cross-timezone behavior, weekends, and provenance/breakdown
- 27 Sprint 18 Task 18.2 tests: 7 in `tests/test_indian_index.py` (catalog, freshness/status,
  SENSEX=BSE-not-NSE, NSE provider SENSEX unavailable, NSE provider supports only NSE indices),
  20 in `tests/test_index_price_action.py` (basic price, gap, returns, SMAs, trend, momentum,
  52-week, daily range, 20-session avg move, price-action score bounded −100..+100, breakdown,
  missing-values-remain-None, and the mandatory no-look-ahead regression test)
- DB isolation verified: engine sessions closed properly; focused tests pass in both orders;
  index seeding idempotent; `provider_symbol` stays unique (DB constraint unchanged)
- No-look-ahead: repository `get_date_range_by_index_code`/`get_date_range` bound rows by the
  requested `end_date`, never the wall clock; `IndexPriceAction._fetch_history` uses a
  400-calendar-day lookback bounded by the requested date
- SENSEX handling: SENSEX is catalogued exchange=BSE; `NSEIndexProvider` returns honest
  `unavailable` for SENSEX (no fabricated NSE data, no scraping bypass)
- Task 18.3 global provider is separate from NSE, uses only injected/mocked HTTP in tests, and
  normalizes the seven required US/Asian indices into the existing `MarketIndexPrice` model
- Global intelligence uses an aware evaluation instant, native exchange close policies via
  `zoneinfo`, one SQL-bounded bulk history query, and the existing `IndexPriceAction` formulas
- Task 18.4 uses actual GIFT Nifty futures only. Its dedicated provider performs zero HTTP and is
  unavailable by default because no documented stable unauthenticated NSE IX automation contract
  was established; explicit local CSV input must validate NSE IX/FUTIDX/NIFTY_50/USD identity
- GIFT intelligence uses current audited NSE IX sessions (06:30–15:40 and 16:35–02:45 next day
  IST), the latest completed knowable NIFTY cash close, one bounded bulk query, conservative
  date-only stored fallback, explicit provenance/limitations, and deterministic no-look-ahead
- Task 18.5 providers likewise perform zero HTTP and are unavailable without explicitly configured,
  exact-identity local data. The services validate payloads again at their boundary and follow
  primary → secondary → exact DB → stale DB → unavailable with explicit attempts and freshness
- Institutional intelligence recomputes `net = buy - sell`, calculates daily/3/5/20-session FII/DII
  values and combined daily/5/20 values, and exposes independent bounded scores, directions,
  breakdown, completeness, and confidence. India VIX, USD/INR spot, and Europe Brent physical spot
  expose exact identities, return horizons, trend/regime context, and separate bounded scores
- Task 18.5 is historical/replay safe at an aware evaluation instant, uses conservative date-only
  cutoffs, ignores data unavailable at T, de-duplicates provider/DB overlaps, and performs one bounded
  DB bulk load per service. It does not create the Task 18.9 composite/regime engine
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
- Existing `MarketIndexPrice.observed_at`/`fetched_at` fields are dates, so stored Task 18.3/18.4
  provenance remains date-granular. Task 18.4 accepts aware timestamped provider/manual observations
  but explicitly does not claim timestamp replay for stored fallback
- Task 18.5 stored market-factor provenance is also date-granular. Existing `InstitutionalFlow`
  buy/sell/net columns are legacy Float; intelligence normalizes them via `Decimal(str(value))`
- The Stooq-compatible global provider contract is fixture-verified with injected transport;
  live upstream availability was intentionally not asserted by automated tests

## Current Sprint

Sprint 19: Trading Foundation & Paper Trading — IN PROGRESS
- Task 19.1A — NSE Market Data Ingestion + Historical Backfill + EOD Automation —
  **COMPLETE & VERIFIED**. Root cause of the owner complaint was the **UI layer** (hardcoded
  `nse_download_blocked`, no CSRF/size guards, no download/backfill/coverage UX). Unified UI,
  REST API, CLI, and scheduler onto one code path via `MarketDataService`
  (`app/services/market_data_service.py`): upload archival (`data/raw/nse/YYYY/MM` + `.sha256`
  sidecar), date/latest download+import, range backfill of missing weekdays (≤ `MAX_RANGE_DAYS`),
  per-day coverage, scanner readiness (20/50/100/200-session), data status. Parser
  `detect_format` (legacy/UDiFF/CM-UDiFF/delivery) + `parse_bhavcopy_detailed` with same-day
  delivery merge; importer mode constants + full audit `details`. New REST endpoints
  `POST /backfill`, `GET /coverage`, `GET /readiness`, `GET /status`; rewritten
  `market_data_pages.py` with CSRF/size-guarded writes + flash alerts + HTMX partials;
  `market_data.html` reworked + `market_coverage_table.html` (new) +
  `market_imports_table.html` (enhanced). EOD: pipeline `run_scanners` gate; scheduler bounded
  import-stage retry (max 3, every 15 min) + startup catch-up (auto, 7-day window); new config
  `eod_scheduler_{retry_enabled,max_retries,retry_minutes,run_scanners,auto_catchup,
  catchup_max_days}`. CLI `range`/`missing`/`status`. 34 tests in `tests/test_market_data_19a.py`
  (zero live network); full suite **1100 passed ×3**; ruff + alembic (head `95a0be65d4a7`, no
  migration) + import + diff gates clean. Real owner files `20260727_NSE.csv` /
  `20260728_NSE.csv` parse with 0 errors (legacy; EQ/BE/BZ/SM/ST imported; GB/bond skipped;
  no delivery columns). BSE files out of scope. Guide: `docs/MARKET_DATA_GUIDE.md`.
- Task 19.1 — Trading Foundation (PAPER ONLY) — **COMPLETE & VERIFIED** (no live broker, no execution
  engine, no risk, no signal-to-order, no API/UI). Four SQLAlchemy 2.0 models (`TradingAccount`,
  `TradingOrder`, `TradingExecution`, `TradingPosition`) with enums as `str` tuples + `CheckConstraint`,
  Decimal money, integer quantity, aware timestamps; live execution mode hard-rejected at both
  account- and order-creation; `TradingOrder` lifecycle NEW→PARTIALLY_FILLED→FILLED/REJECTED/CANCELLED/
  CANCEL_PENDING/REPLACED with explicit `ORDER_STATUS_TRANSITIONS`; idempotent order creation on
  `(account_id, client_order_id)`; repositories (`app/repositories/trading.py`), schemas
  (`app/schemas/trading.py`, 3-layer + validators), `TradingAccountService`/`TradingOrderService`
  (`app/services/trading_foundation.py`); one Alembic migration `95a0be65d4a7` (single head);
  34 focused tests; full suite **1066 passed ×3**; ruff + alembic + import + diff gates clean.
  Positions are persistence-only (no avg-price recompute or P&L — deferred to Task 19.4+).
- Task 18.1 — Market Intelligence Foundation — COMPLETE & VERIFIED (models, repositories, schemas,
  migration 25e6f159a646, provider interfaces, freshness/status conventions, configuration,
  72 foundation tests; full suite 642 passed x3; ruff + alembic gates clean). Do NOT fetch every
  external source yet.
- Task 18.2 — Indian Index Data & Price Action — **COMPLETE & VERIFIED** (async deterministic
  `IndexPriceAction` engine: OHLC, previous close, daily change %, gap points/%, gap classification,
  1/5/20/60-session returns, SMA20/50/100/200, above/below SMA, distance from SMA, trend
  classification, momentum, 52-week high/low, distance from 52w high/low, position in 52w range,
  daily range %, 20-session average daily move, price_action_score −100..+100, score breakdown;
  missing values remain None; repository no-look-ahead fix; SENSEX/BSE honesty; 27 tests;
  full suite 669 passed ×3; ruff + alembic gates clean).
- Task 18.3 — Global & Asian Markets — **COMPLETE & VERIFIED** (canonical seven-market extension
  in the existing catalog/table; separate Stooq-compatible provider; date/range/multi-index
  ingestion with ordered provider/DB fallback and no date×index HTTP N+1; native aware/DST-safe
  session cutoffs; one-query deterministic US/Asian/global scoring, breadth, completeness,
  confidence, provenance/freshness, and explicit no-look-ahead; 43 focused tests; 712 tests ×3;
  Ruff, Alembic, app import, and diff gates clean; migration created: NONE).
- Task 18.4 — GIFT Nifty Intelligence — **COMPLETE & VERIFIED** (canonical actual NSE IX
  `GIFT_NIFTY` futures identity; truthful no-HTTP unavailable/manual-import provider; current split
  NSE IX sessions; latest completed knowable NIFTY cash reference; deterministic gap, five-level
  direction, bounded score/confidence/completeness, ordered fallback, provenance/limitations, one
  bounded bulk query, and mandatory future-invariance no-look-ahead; 67 focused tests; 779 tests ×3;
  Ruff, Alembic, app import, diff, HTTP/proxy/secrets/placeholders gates clean; migration: NONE).
- Task 18.5 — FII/DII + India VIX + USD/INR + Brent Intelligence — **COMPLETE & VERIFIED**
  (canonical FII/FPI and DII provisional cash flow with recomputed net and 1/3/5/20-session context;
  exact NSE India VIX, USD/INR spot in INR per USD, and Europe Brent physical spot; independent
  bounded scores, directions/regime/trend, confidence/completeness, provenance/freshness; zero-HTTP
  validated manual providers; primary→secondary→exact DB→stale DB→unavailable; aware no-look-ahead,
  overlap de-duplication, one bulk query per service; 31 Task 18.5 tests; exact collection 810;
  full suite 810 ×3; Ruff, Alembic, import, and diff gates clean; migration: NONE).
- Task 18.6 — Market Breadth Engine — **COMPLETE & VERIFIED** (deterministic breadth from advances/
  declines, A/D ratio, percent advancing, new high/low balance, SMA20/50/200 participation, momentum,
  and divergence; central `breadth_config.py` constants; canonical state/direction thresholds at
  +60/+25/−25/−60; bounded breadth score −100..+100, five directions, completeness/confidence; one
  bulk query, no N+1; 11 breadth tests; exact collection 821; full suite ×3; gates clean; migration: NONE).
- Task 18.7 — Sector Strength & Rotation — **COMPLETE & VERIFIED** (sector 1D/5D/20D return, relative
  performance vs NIFTY, trend, rank, leaders/laggards, participation, rotation state; deterministic
  bounded sector score and direction; 9 sector tests; exact collection 830; full suite ×3; migration: NONE).
- Task 18.8 — Macro & Event Intelligence — **COMPLETE & VERIFIED** (declarative 34-event catalog
  (18 core + 16 sector) with category/importance/frequency/direction/has_forecast/timezone/unit and
  declarative surprise_full_scale; focused `EconEvent` model + migration `5f7b2c9d4e3a`;
  zero-HTTP unavailable-by-default validated manual provider; deterministic engine with
  upcoming/known/scheduled-not-knowable/missing lifecycle, forecast-then-previous surprise with
  inverted semantics, monetary/inflation/growth/employment/other domains, renormalized
  macro_score −100..+100 at +60/+25/−25/−60, event-risk dampen that never reverses direction,
  completeness/confidence with contradiction handling, provenance/limitations, one bulk query, and
  mandatory no-look-ahead; 24 macro tests; exact collection 854; full suite 854 ×3; Ruff, Alembic
  (head `5f7b2c9d4e3a`), import, and diff gates clean; migration created: YES, one focused table).
- Task 18.9 — Market Regime & Composite Intelligence — **COMPLETE & VERIFIED** (composite
  orchestrator `CompositeMarketIntelligence` consuming all factor engines 18.2–18.8 at one
  evaluation boundary; pure `compute_composite_factor_result()` emitting regime/composite_score/
  confidence_score/bullish_factors/bearish_factors/risk_factors/missing_factors plus
  contradictions, stale_factors, agreement, and provenance; centralized configurable weights;
  weighted completeness + confidence blend with low-confidence reliability dampener; five regime
  bands at +60/+25/−25/−60; high-volatility flag, deterministic contradiction pairs, and risk
  rules; idempotent `evaluate(persistence=True)` snapshot upsert one-row-per-market_date;
  mandatory no-look-ahead bounded by evaluation market_date; 38 pure composite tests covering all
  nine mandatory scenarios + 8 DB-backed integration tests (persistence/upsert idempotency and
  future-invariance); Task 18.1 foundation suite (72) preserved; migration: NONE).
- Sprint 17: Backtesting — COMPLETE (released as v0.13.0)
  - Task 17.1 — Backtesting database + domain foundation — COMPLETE
  - Task 17.2 — Historical NSE fetch + historical replay — VERIFIED
  - Task 17.3 — Trade simulation — COMPLETE (cost bugs fixed; 23 tests)
  - Task 17.4 — Performance metrics — COMPLETE & VERIFIED (40 tests; CLI-wired)
  - Task 17.5 — REST APIs — COMPLETE & VERIFIED (16 API tests; settlement fix; 28 permission codes)
  - Task 17.6 — UI/charts — COMPLETE & VERIFIED (29 tests, no migration)
  - Task 17.7 — Release — COMPLETE & VERIFIED (v0.13.0; tag + merge + push)
  - CLI regression (create_admin / main() guards) — FIXED

- Task 18.10 — AI Market Explanation — **COMPLETE & VERIFIED** (deterministic explanation layer
  from `CompositeMarketIntelligenceResult`: provider abstraction `app/services/ai_explanation_provider.py`,
  prompt builder, `AIExplanationResult` contract, `sanitize_provider_error` (sk-***, password=***),
  `validate_ai_output` anti-hallucination (regime/score/confidence override rejected),
  `AIMarketExplanationService` in `app/services/ai_market_explanation.py` with mandatory deterministic
  fallback `MarketExplanation` (always available, zero cost, zero HTTP), `market_date` bounded
  evaluation for historical reproducibility, provider fallback order primary→deterministic; 61 tests
  covering all 28 mandatory categories per 18.10M; exact collection 89; full suite 89 ×3;
  Ruff, Alembic, import, diff gates clean; migration: NONE).
## Next Task

Sprint 18 — Market Intelligence — COMPLETE & RELEASED as **v0.14.0**.
Tasks 18.1–18.12 all COMPLETE & VERIFIED.
Sprint 19 — Trading Foundation & Paper Trading — IN PROGRESS.
Task 19.1 — Trading Foundation (PAPER ONLY) — **COMPLETE & VERIFIED**.
Task 19.1A — NSE Market Data Ingestion + Historical Backfill + EOD Automation —
**COMPLETE & VERIFIED**.
Next: Sprint 19 Task 19.2 — Broker Abstraction (paper-only; define `BrokerAdapter` interface +
paper broker mock + fill/order lifecycle wiring; still no live, no execution-engine internals).
Version remains 0.14.0 (Sprint 19 target release = v0.15.0; do NOT bump until the sprint completes).
