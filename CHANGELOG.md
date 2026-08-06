# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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