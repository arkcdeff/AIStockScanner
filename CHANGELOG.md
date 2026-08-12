# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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