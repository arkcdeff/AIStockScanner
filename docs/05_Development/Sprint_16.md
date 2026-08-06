# Sprint 16: Authorization Framework

## Release

- Version: `v0.12.0`
- Date: 2026-08-06
- Status: Complete
- Migration head: `3f8c1a2b4d6e`
- Test baseline: 373 tests

## Objective

Deliver a comprehensive, reusable authorization framework that protects all API modules and HTML
routes with both Bearer (API) and cookie (browser) authentication. Introduce RBAC with 23
permission codes across 4 roles, ownership/admin-override, audit events, and a friendly 403 page.
All existing unauthenticated tests updated to authenticate; no failing tests left behind.

## Completed tasks

### 16.1 - Authentication database foundation

- Added auth models: `User`, `Role`, `Permission`, `UserRole`, `RolePermission`, `UserSession`,
  `UserPreference`, `AuditLog` with migrations.
- Seeded built-in roles (`super_admin`, `admin`, `analyst`, `viewer`) and 23 permissions.
- Session management with revocation, IP/user-agent, and refresh token hashing.
- Superuser flag and super_admin role for platform-level bypass.

### 16.2 - Authentication REST APIs

- `/api/v1/auth/login` (Bearer + HttpOnly cookies), `/refresh`, `/logout`, `/logout-all`
- `/me`, `/preferences`, `/profile`, `/sessions`, `/sessions/{id}`, `/password/change`
- Security: CSRF on browser forms, HttpOnly cookies, refresh token rotation, session revocation.
- Password policy enforcement (12+ chars, upper/lower/digit/special).

### 16.3 - Browser authentication UI

- `/login` (GET/POST with HTMX, CSRF, redirect `next`), `/logout`
- `/profile` (view/update), `/settings` (preferences), `/sessions` (list/revoke/revoke-all)
- `/password/change` with current password verification.
- Friendly error pages: 403 (Forbidden), LoginRequired redirect to `/login?next=...`

### 16.4 - Admin management

- Admin API: `/api/v1/admin/users` (list, create, get, update, activate/deactivate, verify,
  unverify, reset-password, role assign/remove, delete) with per-route `users.read`/`users.write`
- `/api/v1/admin/roles` (list, create, get, edit, delete, permission assign/remove)
- `/api/v1/admin/permissions` (list, create, edit, delete)
- Admin UI: `/admin/users`, `/admin/roles`, `/admin/permissions` with HTMX partials
- Admin access requires `super_admin` role or `is_superuser` flag (not `admin` role).

### 16.5 - Browser auth UI for admin (completed in 16.4)

- Admin UI pages protected by `require_browser_admin` (superuser/super_admin only).
- Friendly 403 page for non-admin browser access.

### 16.6 - Authorization framework

- **Reusable helpers** in `app/auth/authorization.py`:
  - Bearer: `require_authenticated`, `require_permission`, `require_any_permission`,
    `require_all_permissions`, `require_role`, `require_admin`, `require_superuser`,
    `require_owner_or_admin(resolver, id_param)`
  - Browser: `require_browser_permission`, `require_browser_role`, `enforce_browser_permission`
  - Pure checks: `is_superuser`, `user_has_role`, `user_has_permission`
  - Audit: `record_authorization_event` (best-effort, cross-loop safe)
- **Permission catalog** (23 codes): `stocks.read`, `stocks.write`, `market_data.read`,
  `market_data.write`, `indicators.read`, `indicators.write`, `scanners.read`,
  `scanners.run`, `scanners.write`, `analytics.read`, `analytics.write`, `ai.read`,
  `ai.write`, `portfolio.read`, `portfolio.write`, `watchlists.read`, `watchlists.write`,
  `alerts.read`, `alerts.write`, `notifications.read`, `notifications.write`,
  `settings.read`, `dashboard.read`, `pipeline.run`, `daily_summary.run`, `scheduler.run`
- **Role matrix**: super_admin=all; admin=all; analyst=stocks.read/write, market_data.write,
  scanners.read/run, analytics.read, ai.read, dashboard.read, portfolio.read/write,
  watchlists.read/write, alerts.read/write, notifications.read; viewer=dashboard.read,
  stocks.read, scanners.read, analytics.read, ai.read, portfolio.read, watchlists.read,
  alerts.read, notifications.read
- **Route protections**:
  - API modules (12): analytics, ai, stocks, market_data, indicators, scanners, pipeline,
    watchlists, portfolio, alerts, notifications, admin
  - HTML pages (9): dashboard, scanner_workspace, stock_detail, watchlists, portfolio,
    alerts, notifications, admin
  - Superuser/super_admin bypass; owner-or-admin with `admin_override` audit.
- **Audit events**: `access_denied`, `permission_denied`, `admin_override`, `session_revoked`,
  `builtin_auth_seeded` (best-effort commit, cross-loop safe).
- **Tests** (`tests/test_authorization.py`): 15 comprehensive tests covering Bearer 401/403/200,
  browser 302/403/200, ownership+admin_override via mini-app, audit assertions.
- **Test helpers**: `auth_client`, `viewer_client`, `analyst_client`, `admin_client`,
  `superuser_client`, `create_test_user`, `get_bearer_token` (cookie auth via `extract_cookies`).

### 16.7 - Release preparation

- Updated version to `v0.12.0`
- Updated `PROJECT_STATE.md`, created `Sprint_16.md`, updated `CHANGELOG.md`
- Verified: `alembic current=head`, `ruff check .` clean, `pytest -q` ×3, `uvicorn` smoke
- Audited for TODOs, debug code, temp files, secrets, dead routes

## Final guarantees

- All API modules and HTML routes protected by reusable authorization helpers.
- Both Bearer (API) and cookie (browser) flows work and are tested.
- 23 permission codes across 4 roles with documented matrix.
- Ownership + admin-override with `admin_override` audit events.
- Friendly 403 page (no stack traces); unauthenticated browser redirects to `/login`.
- All existing tests updated to authenticate; 373 tests pass, Ruff clean.
- Alembic current = head (`3f8c1a2b4d6e`), three consecutive full-suite passes.

## Out of scope

- Redis, Celery, or distributed scheduler
- Browser push, OpenID Connect, SSO, MFA
- Application-wide CSRF system (HTMX forms follow existing conventions)
- Per-resource ownership for Portfolio/Watchlist/AlertRule (permission-only per scope decision)
- Redis, Celery, or distributed scheduler

## Release version

`v0.12.0`