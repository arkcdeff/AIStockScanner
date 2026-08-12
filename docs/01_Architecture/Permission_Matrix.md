# Permission Matrix

This document defines the canonical permission codes, role assignments, and API route protections for the AI Stock Scanner authorization framework (Sprint 16 Task 16.6).

## Permission Codes (28 total)

| Code | Description |
|------|-------------|
| `users.read` | List and view users |
| `users.write` | Create, update, manage users |
| `roles.read` | List and view roles |
| `roles.write` | Create, update, manage roles |
| `stocks.read` | Read access to stock data, symbols, and reference data |
| `stocks.write` | Create/update stock records, import jobs |
| `market_data.write` | Write/import market data, run EOD pipeline imports |
| `scanners.read` | List scanner definitions, view scan results |
| `scanners.run` | Execute scanners, run custom scans |
| `analytics.read` | View analytics, portfolio performance, risk metrics |
| `ai.read` | Read AI explanations, market summaries |
| `dashboard.read` | View dashboard HTML page and widgets |
| `portfolio.read` | View portfolios, holdings, positions |
| `portfolio.write` | Create/update portfolios, add/remove holdings |
| `watchlists.read` | View watchlists and watchlist stocks |
| `watchlists.write` | Create/update watchlists, add/remove stocks |
| `alerts.read` | View alert rules and alert events |
| `alerts.write` | Create/update alert rules, run alert engine |
| `notifications.read` | View notification channels, preferences, deliveries |
| `notifications.write` | Configure notifications, send test messages |
| `pipeline.run` | Execute EOD pipeline runs (file/date) |
| `daily_summary.run` | Trigger daily summary generation |
| `scheduler.run` | Trigger scheduler delivery runs |
| `settings.read` | View user preferences, system settings |
| `settings.write` | Update application settings |
| `backtests.read` | View backtest strategies, runs and results |
| `backtests.run` | Execute backtest runs |
| `backtests.write` | Create and manage backtest strategies and runs |

**Note**: The canonical permission catalog in `app/services/builtin_auth.py`
contains 28 codes. The table above matches the catalog exactly. Indicator and
market-data read access reuse `stocks.read` (there is no separate
`indicators.read` / `market_data.read` code).

## Role Definitions

| Role | Description |
|------|-------------|
| `super_admin` | Platform superuser; bypasses all checks; holds `is_superuser` flag or `super_admin` role |
| `admin` | System administrator; all permissions; manages users, roles, permissions |
| `analyst` | Power user; read/write access to market data, scanners, analytics, AI, portfolio, watchlists, alerts |
| `viewer` | Read-only user; dashboard, stocks, scanners, analytics, AI, portfolio, watchlists, alerts, notifications |

## Role × Permission Matrix

| Permission | super_admin | admin | analyst | viewer |
|------------|-------------|-------|---------|--------|
| `users.read` | ✅ | ✅ | ❌ | ❌ |
| `users.write` | ✅ | ✅ | ❌ | ❌ |
| `roles.read` | ✅ | ✅ | ❌ | ❌ |
| `roles.write` | ✅ | ✅ | ❌ | ❌ |
| `stocks.read` | ✅ | ✅ | ✅ | ✅ |
| `stocks.write` | ✅ | ✅ | ❌ | ❌ |
| `market_data.write` | ✅ | ✅ | ✅ | ❌ |
| `scanners.read` | ✅ | ✅ | ✅ | ✅ |
| `scanners.run` | ✅ | ✅ | ✅ | ❌ |
| `analytics.read` | ✅ | ✅ | ✅ | ✅ |
| `ai.read` | ✅ | ✅ | ✅ | ✅ |
| `dashboard.read` | ✅ | ✅ | ✅ | ✅ |
| `portfolio.read` | ✅ | ✅ | ✅ | ✅ |
| `portfolio.write` | ✅ | ✅ | ✅ | ❌ |
| `watchlists.read` | ✅ | ✅ | ✅ | ✅ |
| `watchlists.write` | ✅ | ✅ | ✅ | ❌ |
| `alerts.read` | ✅ | ✅ | ✅ | ✅ |
| `alerts.write` | ✅ | ✅ | ✅ | ❌ |
| `notifications.read` | ✅ | ✅ | ✅ | ✅ |
| `notifications.write` | ✅ | ✅ | ❌ | ❌ |
| `pipeline.run` | ✅ | ✅ | ❌ | ❌ |
| `daily_summary.run` | ✅ | ✅ | ❌ | ❌ |
| `scheduler.run` | ✅ | ✅ | ❌ | ❌ |
| `settings.read` | ✅ | ✅ | ❌ | ❌ |
| `settings.write` | ✅ | ✅ | ❌ | ❌ |
| `backtests.read` | ✅ | ✅ | ✅ | ✅ |
| `backtests.run` | ✅ | ✅ | ✅ | ❌ |
| `backtests.write` | ✅ | ✅ | ❌ | ❌ |

> **Note**: `super_admin` and users with `is_superuser=True` bypass all permission checks entirely.

## API Route Protections

| Module | Route | Method | Permission(s) | Role(s) |
|--------|-------|--------|---------------|---------|
| **Analytics** | `/api/v1/analytics/*` | GET | `analytics.read` | viewer+ |
| **AI** | `/api/v1/ai/*` | GET | `ai.read` | viewer+ |
| **Stocks** | `/api/v1/stocks` | GET | `stocks.read` | viewer+ |
| | `/api/v1/stocks` | POST | `stocks.write` | analyst+ |
| | `/api/v1/stocks/{symbol}` | GET | `stocks.read` | viewer+ |
| **Market Data** | `/api/v1/market-data` | GET | `stocks.read` | viewer+ |
| | `/api/v1/market-data/import` | POST | `market_data.write` | analyst+ |
| **Indicators** | `/api/v1/indicators/*` | GET | `stocks.read` | viewer+ |
| | `/api/v1/indicators/calculate` | POST | `market_data.write` | analyst+ |
| **Scanners** | `/api/v1/scanners` | GET | `scanners.read` | viewer+ |
| | `/api/v1/scanners` | POST | `scanners.run` | analyst+ |
| | `/api/v1/scanners/results` | GET | `scanners.read` | viewer+ |
| **Pipeline** | `/api/v1/pipeline/status` | GET | `stocks.read` | viewer+ |
| | `/api/v1/pipeline/runs` | GET | `stocks.read` | viewer+ |
| | `/api/v1/pipeline/run*` | POST | `pipeline.run` | admin |
| **Watchlists** | `/api/v1/watchlists` | GET | `watchlists.read` | viewer+ |
| | `/api/v1/watchlists` | POST | `watchlists.write` | analyst+ |
| | `/api/v1/watchlists/{id}` | PUT/DELETE | `watchlists.write` | analyst+ |
| | `/api/v1/watchlists/{id}/stocks` | POST/DELETE | `watchlists.write` | analyst+ |
| **Portfolio** | `/api/v1/portfolio/*` | GET | `portfolio.read` | viewer+ |
| | `/api/v1/portfolio/*` | POST/PUT/DELETE | `portfolio.write` | analyst+ |
| **Alerts** | `/api/v1/alerts/rules` | GET | `alerts.read` | viewer+ |
| | `/api/v1/alerts/rules` | POST | `alerts.write` | analyst+ |
| | `/api/v1/alerts/run` | POST | `alerts.write` | analyst+ |
| | `/api/v1/alerts/events*` | GET | `alerts.read` | viewer+ |
| **Notifications** | `/api/v1/notifications/channels` | GET | `notifications.read` | viewer+ |
| | `/api/v1/notifications/daily-summary/run` | POST | `daily_summary.run` | admin |
| | `/api/v1/notifications/scheduler/run-delivery` | POST | `scheduler.run` | admin |
| | `/api/v1/notifications/*` | POST (other) | `notifications.write` | admin |
| **Backtests** | `/api/v1/backtests/strategies` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/strategies` | POST | `backtests.write` | admin+ |
| | `/api/v1/backtests/strategies/{id}` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/strategies/{id}` | PUT | `backtests.write` | admin+ |
| | `/api/v1/backtests/strategies/{id}` | DELETE | `backtests.write` | admin+ |
| | `/api/v1/backtests/runs` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/runs` | POST | `backtests.write` | admin+ |
| | `/api/v1/backtests/runs/{id}` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/runs/{id}/execute` | POST | `backtests.run` | analyst+ |
| | `/api/v1/backtests/runs/{id}/summary` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/runs/{id}/trades` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/runs/{id}/equity` | GET | `backtests.read` | viewer+ |
| | `/api/v1/backtests/runs/{id}/metrics` | GET | `backtests.read` | viewer+ |
| **Admin** | `/api/v1/admin/users` | GET | `users.read` | admin |
| | `/api/v1/admin/users` | POST | `users.write` | admin |
| | `/api/v1/admin/users/{id}` | PUT/POST/DELETE | `users.write` | admin |
| | `/api/v1/admin/roles` | GET | `roles.read` | admin |
| | `/api/v1/admin/roles` | POST | `roles.write` | admin |
| | `/api/v1/admin/permissions` | GET | `roles.read` | admin |

## HTML Page Protections (Browser/Cookie Auth)

| Page | Route | Permission | Redirect on Fail |
|------|-------|------------|------------------|
| Dashboard | `/dashboard` | `dashboard.read` | `/login?next=/dashboard` |
| Scanner Workspace | `/scanner-results` | `scanners.read` | `/login?next=/scanner-results` |
| Stock Detail | `/stocks/{symbol}` | `stocks.read` | `/login?next=/stocks/{symbol}` |
| Stock Analytics Tab | `/stocks/{symbol}?tab=analytics` | `analytics.read` | `/login?next=...` |
| Stock AI Tab | `/stocks/{symbol}?tab=ai` | `ai.read` | `/login?next=...` |
| Scanner Results Tab | `/stocks/{symbol}?tab=scanner` | `scanners.read` | `/login?next=...` |
| Watchlists | `/watchlists` | `watchlists.read` | `/login?next=/watchlists` |
| Create/Edit Watchlist | `/watchlists/new`, `/watchlists/{id}/edit` | `watchlists.write` | `/login?next=...` |
| Portfolio | `/portfolio` | `portfolio.read` | `/login?next=/portfolio` |
| Portfolio Holdings | `/portfolio/holdings` | `portfolio.write` | `/login?next=...` |
| Alerts | `/alerts` | `alerts.read` | `/login?next=/alerts` |
| Create/Edit Alert Rule | `/alerts/rules/new`, `/alerts/rules/{id}/edit` | `alerts.write` | `/login?next=...` |
| Notifications | `/notifications` | `notifications.read` | `/login?next=/notifications` |
| Notification Settings | `/notifications/settings` | `notifications.read` | `/login?next=...` |
| Daily Summary Run | `/notifications/daily-summary/run` | `daily_summary.run` | `/login?next=...` |
| Admin Users | `/admin/users` | `require_browser_admin` (admin only) | 403 page |
| Admin Roles | `/admin/roles` | `require_browser_admin` | 403 page |
| Admin Permissions | `/admin/permissions` | `require_browser_admin` | 403 page |

## Audit Events

| Event | Trigger | Metadata |
|-------|---------|----------|
| `access_denied` | Unauthenticated request to protected route | `path`, `required` (permissions) |
| `permission_denied` | Authenticated user lacks required permission/role | `required`, `granted` (user's permissions/roles) |
| `admin_override` | Admin/superuser accesses another user's owned resource | `owner_id`, `resource_id`, `entity_type` |
| `session_revoked` | User revokes own session | `session_id` |
| `builtin_auth_seeded` | Startup seeding of roles/permissions | `roles`, `permissions` |

## Implementation References

- **Core helpers**: `app/auth/authorization.py`
- **Permission catalog**: `app/services/builtin_auth.py`
- **Role seeding**: `app/services/role_service.py`
- **API route deps**: `app/api/routes/*`
- **Browser deps**: `app/api/browser_auth.py`, `app/api/routes/*_pages.py`
- **Test helpers**: `tests/auth_helpers.py`
- **Authorization tests**: `tests/test_authorization.py`