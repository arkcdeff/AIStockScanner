# Project State

Snapshot of AIStockScanner after Sprint 16 (Authorization Framework), prepared for release v0.12.0.

## Stack

- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + Bootstrap 5 + HTMX frontend
- One APScheduler 3.11 instance for optional EOD, delivery, and daily-summary jobs
- AI Explanation Engine with async providers (Ollama, DeepSeek, OpenAI, mock)

## Data

- 21 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis, AIExplanation, Watchlist, WatchlistStock, Portfolio,
  Holding, AlertRule, AlertEvent, NotificationChannel, NotificationPreference,
  NotificationDelivery, DailySummaryRun, User, Role, Permission, UserRole, RolePermission,
  UserSession, UserPreference, AuditLog
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

## Migrations

- Alembic current: `3f8c1a2b4d6e (head)`
- Alembic heads: `3f8c1a2b4d6e (head)`
- Linear history: 15 revisions
- Sprint 16 migrations: auth tables + RBAC + audit_logs

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
- 23 permission codes across 4 roles: super_admin, admin, analyst, viewer
- API modules protected: analytics, ai, stocks, market_data, indicators, scanners, pipeline,
  watchlists, portfolio, alerts, notifications, admin
- HTML pages protected: dashboard, scanner-workspace, stock_detail, watchlists, portfolio,
  alerts, notifications, admin
- Superuser/super_admin bypass; owner-or-admin with audit on `admin_override`
- Audit events: `access_denied`, `permission_denied`, `admin_override`, `session_revoked`,
  `builtin_auth_seeded`
- Browser auth: `require_browser_permission`, `require_browser_role`, `enforce_browser_permission`
- Friendly 403 page (no stack traces), unauthenticated redirect to `/login?next=...`

## API and page surface

- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, analytics, ai,
  watchlists, portfolios, alerts, notifications, admin
- `/dashboard`: pipeline, analytics, AI, portfolio, watchlist, alert, and notification summaries
- `/scanner-results`: server-rendered workspace with CSV/Excel/PDF export
- `/stocks/{symbol}`: overview, indicators, analytics, scanners, AI, watchlist, portfolio, alerts,
  and related notifications
- `/watchlists`, `/portfolio`, `/alerts`, `/notifications`: HTML/HTMX workspaces
- `/admin/users`, `/admin/roles`, `/admin/permissions`: admin management
- `/login`, `/profile`, `/settings`, `/sessions`, `/password/change`: auth UI
- `/api/v1/auth`: login, refresh, logout, me, preferences, profile, sessions, change-password

## Testing

- Real PostgreSQL test persistence with database cleanup between tests
- Scan definitions are seeded idempotently
- 373+ total tests
- 119 focused Sprint 16 tests cover auth models, providers, authorization helpers, APIs, UI,
  ownership/admin_override, audit events, and browser cookie auth
- External AI, Telegram, and SMTP calls are mocked or replaced by deterministic providers
- Release gate: Alembic current equals head, Ruff clean, and three consecutive full-suite passes

## Known gaps

- No Redis, Celery, or distributed scheduler
- Notification jobs and outbound sending remain disabled until explicitly configured
- `app/scanner/` contains intentionally retained duplicate/dead scanner code
- `scanner_engine.run_all` re-fetches indicator rows per scanner (performance follow-up)
- AI collection endpoints are memory-cached only; only per-stock explanations are persisted
- CSRF system follows existing project conventions (HTMX forms)
