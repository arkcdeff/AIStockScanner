# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.14.0] - 2026-08-17

### Added

- **Sprint 18 — Market Intelligence System (Tasks 18.1–18.12)**
  - Task 18.1: Market Intelligence Foundation — models, repositories, schemas,
    migration `25e6f159a646`, provider interfaces, freshness/status conventions,
    configuration, 72 foundation tests
  - Task 18.2: Indian Index Data & Price Action — async deterministic
    `IndexPriceAction` engine (OHLC, gaps, returns, SMAs, trend, momentum,
    52-week, price_action_score −100..+100), no-look-ahead repository, SENSEX
    honesty (BSE index), 27 tests
  - Task 18.3: Global & Asian Markets — 7 US/Asian indices via Stooq-compatible
    `GlobalIndexProvider`, native aware/DST-safe session cutoffs, one-query
    deterministic US/Asian/global scoring, 43 tests, zero live HTTP
  - Task 18.4: GIFT Nifty Intelligence — canonical NSE IX `GIFT_NIFTY` futures
    identity, truthful no-HTTP unavailable/manual-import provider, current split
    NSE IX sessions, deterministic gap/direction/score/confidence, 67 tests,
    zero live HTTP
  - Task 18.5: FII/DII + India VIX + USD/INR + Brent — canonical FII/FPI/DII
    flows in INR_CRORE, exact India VIX, USD/INR spot (INR_PER_1_USD), Europe
    Brent physical spot, independent bounded scores, zero-HTTP providers,
    31 tests, no migration
  - Task 18.6: Market Breadth Engine — advances/declines, A/D ratio, new
    high/low balance, SMA participation, momentum/divergence, breadth score
    −100..+100, canonical thresholds at +60/+25/−25/−60, 11 tests
  - Task 18.7: Sector Strength & Rotation — 1D/5D/20D sector returns, relative
    vs NIFTY, trend, rank, leaders/laggards, rotation state, deterministic
    bounded sector score, 9 tests
  - Task 18.8: Macro & Event Intelligence — declarative 34-event catalog (18
    core + 16 sector), `EconEvent` model + migration `5f7b2c9d4e3a`,
    zero-HTTP validated manual provider, deterministic engine with lifecycle
    classification, surprise normalization, importance-weighted domains,
    macro_score −100..+100 at +60/+25/−25/−60, event-risk dampener, 24 tests
  - Task 18.9: Market Regime & Composite Intelligence — orchestrator consuming
    all 10 upstream factor engines at same evaluation boundary, pure
    `compute_composite_factor_result()` core, weighted composite −100..+100,
    five regime bands at +60/+25/−25/−60, confidence blend with reliability
    dampener, deterministic contradiction/risk rules, idempotent snapshot
    upsert, 46 tests (38 pure + 8 DB-backed)
  - Task 18.10: AI Market Explanation — provider abstraction, deterministic
    fallback `MarketExplanation`, compact JSON prompt builder, anti-hallucination
    validation, provider fallback order, 61 tests covering 28 mandatory
    categories, zero external HTTP
  - Task 18.11: REST APIs & Market Intelligence Dashboard — 14 endpoints under
    `/api/v1/market-intelligence/` with Bearer auth + permissions, browser
    `/market-intelligence` dashboard + HTMX partials, CSRF on POST `/refresh`,
    Pydantic schemas validate all responses, missing values render "Unavailable",
    36 focused tests, 998 total tests
  - Task 18.12: Scheduler + Notifications + v0.14.0 Release —
    *Scheduler*: 3 market intelligence jobs (pre-market 08:30 IST, post-market
    16:15 IST, daily snapshot 17:00 IST) on existing APScheduler, timezone-aware
    cron triggers, max_instances=1, coalesce, idempotent evaluation with
    persistence, failure isolation, last-good-data preservation
    *Notifications*: deterministic regime-change, score-transition (≥15 pts),
    extreme VIX (≤−60), and high-risk macro event triggers (upcoming Task 18.8
    macro event with top-tier `high` importance; dedup by real event identity +
    date, no AI, no direction prediction); reuses Sprint 15
    notification infrastructure (channels, preferences, retry, dedup, cooldown),
    AI explanation optional after deterministic trigger, historical API never
    emits live alerts, 28 new Task 18.12 tests

### Changed

- Version bumped to `v0.14.0` (`app/main.py`)
- Alembic head: `5f7b2c9d4e3a` (single head, no drift)
- Test suite: 1026 tests (998 → 1026 with 28 new Task 18.12 tests)
- All Sprint 18 work intentionally uncommitted until release authorization

### Security

- Timezone safety: all `utcnow()` replaced with `datetime.now(UTC)` in reachable
  Sprint 18 paths
- No-look-ahead guarantees preserved across all factor engines, composite,
  scheduler, and historical APIs
- AI explanation never computes scores/regime/confidence; only explains
  deterministic output
- Notification triggers are deterministic; AI only adds prose after trigger
- Sanitized provider errors, no secrets in logs
- CSRF on all browser state-changing actions

## [v0.13.0] - 2026-08-12

### Added

- **Sprint 17 Tasks 17.5–17.6 (backtesting REST API + browser UI/charts)**
  - REST API `/api/v1/backtests` (13 routes / 9 paths): strategy CRUD + soft
    deactivate, run create/list/detail, synchronous `execute`, and
    summary/trades/equity/metrics result endpoints
  - Browser backtesting UI under `/backtests` (workspace, strategies, runs,
    run detail) with HTMX partials and Lightweight Charts equity/drawdown
  - `backtests.read/run/write` permission codes (catalog 25 → 28)
  - Simulator settlement fix: both close sites credit
    `net_pnl + entry_value + entry_commission`, preserving
    `final_capital = initial_capital + gross_pnl - costs`
  - API reference: `docs/03_APIs/Backtest_API.md`
  - Test suites: `tests/test_backtest_api.py` (16), `tests/test_backtest_ui.py` (29)

### Changed

- Version bumped to `v0.13.0` (`app/main.py`) — first version where the
  `app/main.py` version field matches the release (the v0.12.1 release did not
  bump the field; corrected here)
- Permission Matrix reconciled to the canonical 28-code catalog
  (`docs/01_Architecture/Permission_Matrix.md`); phantom codes
  (`market_data.read`, `indicators.read/write`, `scanners.write`,
  `analytics.write`, `ai.write`, `pipeline.read`, `daily_summary.read`)
  removed from the tables and route dependencies updated to match code
- PROJECT_STATE.md updated for Sprint 17 completion (33 models)
- Test suite: 570 total (541 baseline + 29 UI)

### Documentation

- `docs/05_Development/Sprint_17.md` updated for Tasks 17.5–17.6
- `docs/05_Development/PROJECT_STATE.md` updated for v0.13.0
- `docs/CURRENT_TASK.md` updated for Sprint 17 completion

## [v0.12.0] - 2026-08-06

### Added

- **Authorization Framework** (`app/auth/authorization.py`)
  - Reusable Bearer helpers: `require_authenticated`, `require_permission`,
    `require_any_permission`, `require_all_permissions`, `require_role`,
    `require_admin`, `require_superuser`, `require_owner_or_admin`
  - Reusable browser helpers: `require_browser_permission`, `require_browser_role`,
    `enforce_browser_permission`
  - Pure check functions: `is_superuser`, `user_has_role`, `user_has_permission`
  - Audit event recorder: `record_authorization_event` (best-effort, cross-loop safe)
  - Superuser/super_admin bypass; ownership/admin-override with audit

- **Permission catalog** (`app/services/builtin_auth.py`)
  - 23 permission codes across 7 domains
  - 4 roles: `super_admin`, `admin`, `analyst`, `viewer`
  - Role-permission matrix documented in `docs/01_Architecture/Permission_Matrix.md`

- **Auth models & migrations**
  - `User`, `Role`, `Permission`, `UserRole`, `RolePermission`, `UserSession`,
    `UserPreference`, `AuditLog`
  - Alembic migration head: `3f8c1a2b4d6e`

- **Auth REST APIs** (`/api/v1/auth`)
  - `POST /login` (Bearer + HttpOnly cookies), `POST /refresh`, `POST /logout`,
    `POST /logout-all`
  - `GET /me`, `GET/PUT /preferences`, `GET/PUT /profile`
  - `GET /sessions`, `DELETE /sessions/{id}`, `POST /logout-all`
  - `POST /change-password`
  - HttpOnly cookies, CSRF protection, refresh token rotation, session revocation

- **Browser auth UI** (`/login`, `/logout`, `/profile`, `/settings`, `/sessions`,
  `/password/change`)
  - `/login` GET/POST with HTMX, CSRF, `next` redirect
  - Friendly 403 page (no stack traces), unauthenticated redirect to `/login?next=...`
  - Session management (list, revoke, revoke-all)

- **Admin management**
  - API: `/api/v1/admin/users` (list, create, get, update, activate/deactivate,
    verify/unverify, reset-password, role assign/remove, delete)
  - API: `/api/v1/admin/roles` (list, create, get, edit, delete, permission assign/remove)
  - API: `/api/v1/admin/permissions` (list, create, edit, delete)
  - UI: `/admin/users`, `/admin/roles`, `/admin/permissions` with HTMX partials
  - Requires `super_admin` role or `is_superuser` flag

- **Route protections**
  - 12 API modules protected: analytics, ai, stocks, market_data, indicators,
    scanners, pipeline, watchlists, portfolio, alerts, notifications, admin
  - 9 HTML page modules protected: dashboard, scanner_workspace, stock_detail,
    watchlists, portfolio, alerts, notifications, admin
  - Role/permission requirements documented in Permission Matrix

- **Audit events**
  - `access_denied` (unauthenticated access)
  - `permission_denied` (authenticated but insufficient permissions)
  - `admin_override` (admin accessing another user's owned resource)
  - `session_revoked`, `builtin_auth_seeded`
  - Best-effort commit (cross-loop safe)

- **Test suite & helpers**
  - `tests/test_authorization.py`: 15 comprehensive tests (Bearer 401/403/200,
    browser 302/403/200, ownership+admin_override, audit assertions)
  - `tests/auth_helpers.py`: `auth_client`, role-specific clients, `create_test_user`,
    `get_bearer_token`, cookie auth via `extract_cookies`
  - All existing tests updated to authenticate (no failing tests)

### Changed

- **Version** bumped to `v0.12.0` (`app/main.py`)
- **PROJECT_STATE.md** updated for Sprint 16
- **Sprint_16.md** created with full task documentation
- **Permission Matrix** created at `docs/01_Architecture/Permission_Matrix.md`
- Updated `tests/test_pipeline.py`, `tests/test_daily_summary.py` for `_client` context manager
- Updated `tests/test_alerts.py`, `tests/test_watchlists.py`, `tests/test_dashboard.py`
  for browser authentication

### Fixed

- Cross-loop asyncpg crash in sync TestClient by making audit commit best-effort
- Browser cookie transmission via `extract_cookies` synthesis
- Seeding after per-test TRUNCATE (removed `_seeded` flag)
- Pipeline `_client` context manager (switched to `admin_client`)

### Documentation

- `docs/01_Architecture/Permission_Matrix.md` (full matrix, route protections, audit events)
- `docs/05_Development/Sprint_16.md` (complete task documentation)
- `docs/05_Development/PROJECT_STATE.md` updated for Sprint 16
- `CHANGELOG.md` created

## [v0.12.1] - 2026-08-08

### Added

- **Sprint 16.8 — Application UI & navigation**
  - HTML pages for Market Data, Indicators, Scanner Library, Scanner Run, Pipeline, Scheduler, Analytics, AI, and Stocks
  - Sidebar and navbar updated to link all new pages (replaced disabled/placeholder links)
  - Wired page routers into `app/main.py`
  - 16 UI smoke tests covering pages and HTMX partials

- **Sprint 17 Task 17.1 — Backtesting database + domain foundation**
  - Models: `BacktestStrategy`, `BacktestRun`, `BacktestTrade`, `BacktestEquityPoint`, `BacktestMetric`
  - Strategy types: scanner, rule_based, composite; run statuses: pending, running, completed, failed, cancelled
  - Trades support long/short sides, entry/exit signals, stop loss, target, and PnL fields
  - Equity points track daily portfolio value; metrics store per-run performance summary
  - Constraints: positive capital/quantity/prices, non-negative cost rates, valid enum values, valid date ranges
  - Unique constraints: strategy code, equity (run+date), metric (run)
  - Repositories: create/get, get-by-code, paginated list, status update, eager get-with-details, bulk inserts, metric upsert
  - Schemas: validated create/update/read schemas
  - Alembic migration `d7b4cc79fd45` applied to dev and test databases
  - 29 model tests covering constraints, relationships, repositories, and cascades

### Changed

- `app/main.py` version field left at `0.12.0` for this release (corrected to
  `0.13.0` in the v0.13.0 release)
- `Stock` model extended with `backtest_trades` relationship
- Test suite: 432 total tests (403 + 29 new)
- PROJECT_STATE.md updated for Sprint 16.8 and Task 17.1
- Sprint_17.md created with Task 17.1 documentation

### Documentation

- `docs/05_Development/Sprint_17.md` (Task 17.1 documentation)
- `docs/05_Development/PROJECT_STATE.md` updated

## [v0.11.0] - 2026-08-05

### Added

- **Notification system** (`app/notifications/`)
  - Models: `NotificationChannel`, `NotificationPreference`, `NotificationDelivery`,
    `DailySummaryRun`
  - Provider abstraction: deterministic mock, Telegram, SMTP email, in-app
  - Templates with HTML escaping and XSS protection

- **Notification service & retry engine**
  - Centralized eligibility, quiet hours, rendering, delivery persistence
  - Duplicate prevention, retry with capped attempts, stale-send recovery
  - Transient vs permanent failure distinction, max attempts enforcement
  - Provider error sanitization before persistence/logging

- **Notification REST APIs** (`/api/v1/notifications`)
  - Channel CRUD, preference CRUD, delivery history/detail, retry/read
  - Provider health/test, daily-summary run/history, scheduler run-delivery

- **Notification UI** (`/notifications`, `/notifications/settings`, `/notifications/history`)
  - Bootstrap 5 + HTMX, targeted partial refreshes
  - Dashboard & stock-detail notification sections

- **Daily summary & scheduler**
  - Deterministic, read-only, idempotent daily summaries
  - Optional cached AI market text
  - Delivery and weekday daily-summary jobs on APScheduler
  - Disabled by default, coalesced, non-overlapping, fresh sessions

- **Migration** `22d886d53759` for notification foundation tables

## [v0.10.0] - 2026-07-XX

### Added

- Alert rules engine (8 rule types)
- Portfolio & holding management
- Watchlist management
- AI Explanation Engine (mock, Ollama, DeepSeek, OpenAI)
- Analytics engine (trend, momentum, volume, delivery, volatility, overall scores)
- EOD pipeline (import, indicators, scanners, analytics, alerts)
- Scanner engine (36 scanners across 8 categories)

## [v0.9.0] - 2026-06-XX

### Added

- Core data models (Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, etc.)
- NSE Bhavcopy import with validation
- Indicator engine (EMA, RSI, MACD, SuperTrend, Bollinger, ATR, OBV, VWAP, Stochastic, ADX)
- Scanner engine (36 scanners)
- Basic FastAPI app with HTML/HTMX frontend

---

**Note**: This changelog started at v0.9.0. Earlier versions were not formally tracked.