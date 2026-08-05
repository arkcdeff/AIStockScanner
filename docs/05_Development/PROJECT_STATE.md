# Project State

Snapshot of AIStockScanner after Sprint 15 (Notification System), prepared for release v0.11.0.

## Stack

- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + Bootstrap 5 + HTMX frontend
- One APScheduler 3.11 instance for optional EOD, delivery, and daily-summary jobs
- AI Explanation Engine with async providers (Ollama, DeepSeek, OpenAI, mock)

## Data

- 20 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis, AIExplanation, Watchlist, WatchlistStock, Portfolio,
  Holding, AlertRule, AlertEvent, NotificationChannel, NotificationPreference,
  NotificationDelivery, DailySummaryRun
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
- `technical_analysis` stores composite scores, pivots, ratings, and trend/breakout flags
- `ai_explanations` is unique on `(stock_id, trade_date, provider)`
- `watchlist_stocks` is unique on `(watchlist_id, stock_id)`
- `holdings` is unique on `(portfolio_id, stock_id)`
- Notification channel names and channel/event preferences are unique
- `daily_summary_runs.summary_date` is unique, preventing duplicate summary execution per date

## Migrations

- Alembic current: `22d886d53759 (head)`
- Alembic heads: `22d886d53759 (head)`
- Linear history: 10 revisions
- Sprint 15 migration: `22d886d53759_add_notification_foundation_tables.py`

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

## API and page surface

- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, analytics, ai,
  watchlists, portfolios, alerts, notifications, dashboard summaries
- `/dashboard`: pipeline, analytics, AI, portfolio, watchlist, alert, and notification summaries
- `/scanner-results`: server-rendered workspace with CSV/Excel/PDF export
- `/stocks/{symbol}`: overview, indicators, analytics, scanners, AI, watchlist, portfolio, alerts,
  and related notifications
- `/watchlists`, `/portfolio`, `/alerts`, `/notifications`: HTML/HTMX workspaces

## Testing

- Real PostgreSQL test persistence with database cleanup between tests
- Scan definitions are seeded idempotently
- 279 total tests
- 109 focused Sprint 15 tests cover notification models, providers, renderer, service/retry,
  APIs, UI, dashboard/stock integration, summaries, scheduler, security, idempotency, and pipeline
  isolation
- External AI, Telegram, and SMTP calls are mocked or replaced by deterministic providers
- Release gate: Alembic current equals head, Ruff clean, and three consecutive full-suite passes

## Known gaps

- No authentication or per-user ownership; current schema is single-user
- No application-wide CSRF system; HTMX forms follow existing project conventions
- No browser push, Redis, Celery, or distributed scheduler
- Notification jobs and outbound sending remain disabled until explicitly configured
- `app/scanner/` contains intentionally retained duplicate/dead scanner code
- `scanner_engine.run_all` re-fetches indicator rows per scanner (performance follow-up)
- AI collection endpoints are memory-cached only; only per-stock explanations are persisted
