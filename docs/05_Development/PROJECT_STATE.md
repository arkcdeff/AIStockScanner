# Project State

Snapshot of the AIStockScanner project as of Sprint 14 (Portfolio, Watchlists & Alert Rules).

## Stack
- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + HTMX frontend (dashboard, scanner-results workspace, stock detail, watchlists,
  portfolio, alerts)
- APScheduler (3.11) for optional scheduled EOD pipeline runs
- AI Explanation Engine with async httpx providers (Ollama / DeepSeek / OpenAI / mock)

## Data
- 16 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis, AIExplanation, Watchlist, WatchlistStock, Portfolio,
  Holding, AlertRule, AlertEvent
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`; definitions seeded via
  migrations `fda0edd539bd` (25) and `bdc596b3589a` (11)
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
  (`uq_scan_result_scan_stock_date`) — persistence is conflict-safe/idempotent
- `eod_pipeline_runs` tracks every automated/manual pipeline run with per-stage metrics
  (incl. `alerts_triggered`)
- `technical_analysis` stores per-stock/date composite scores, pivots, ratings, flags
- `ai_explanations` stores per-stock/date/provider AI explanations, unique on
  `(stock_id, trade_date, provider)` (`uq_ai_explanation_stock_date_provider`)
- `watchlist_stocks` unique on `(watchlist_id, stock_id)`; `holdings` unique on
  `(portfolio_id, stock_id)`

## Migrations
- Alembic current == head == `fd96a19d8b61` (9 revisions)

## EOD pipeline
- `app/services/eod_pipeline.py`: import → indicators → all active scanners → analytics →
  alerts, one fresh session per stage, single-active-run guard, credential-redacted errors,
  abandoned-run recovery
- Manual triggers: `/api/v1/pipeline/run-file`, `/run-date`, `/run-latest`
- Observability: `/api/v1/pipeline/runs`, `/runs/{id}`, `/status`
- Scheduler (`app/scheduler/eod_scheduler.py`): APScheduler daily job, weekdays 18:30
  Asia/Kolkata, disabled by default (`EOD_SCHEDULER_ENABLED=false`)

## Analytics engine
- `app/services/analytics_engine.py`: trend/momentum/volume/delivery/volatility/overall
  scores (0-100), support/resistance, classic + Fibonacci pivots, relative volume,
  delivery strength, ratings, trending/breakout flags
- APIs: `/api/v1/analytics/{symbol}`, `/top-rated`, `/trending`, `/breakouts`
- Surfaced on dashboard (Top Rated card) and stock detail (analytics card + HTMX partial)

## AI Explanation Engine
- `app/ai/`: provider abstraction (`BaseAIProvider` + factory), prompt builder (Pydantic DTOs
  only), response parser with deterministic fallback, TTL + DB caching
- Providers: `mock` (default, no network), `ollama`, `deepseek`, `openai` — selected via
  `AI_PROVIDER` env var
- AI explains existing deterministic analytics only — never computes indicators/scores/scanners
- `ai_explanations` persistence + cache invalidation via prompt hash (trade date, indicators,
  analytics, scanner results, pipeline rerun)
- APIs: `/api/v1/ai/stock/{symbol}`, `/api/v1/ai/top-rated`, `/api/v1/ai/market-summary`
- Stock detail AI card + HTMX partial (`/stocks/{symbol}/ai`); dashboard AI sections
  (Today's AI Insights, Top Opportunities, Highest Confidence, Recent AI Explanations)

## Watchlists
- `WatchlistService` (`app/services/watchlist.py`): CRUD, default-singleton handling,
  add/remove/update stocks, latest-price enrichment, stock search
- APIs: `/api/v1/watchlists/...` (list/create/get/update/delete, `/search`,
  `/{id}/stocks`)
- Page + HTMX partials at `/watchlists`; stock detail "Add to Watchlist" panel;
  dashboard watchlist summary card

## Portfolio
- `PortfolioService` (`app/services/portfolio.py`): portfolio/holding CRUD, valuations
  (invested, current value, P&L, ROI %, today's P&L, allocation), per-portfolio and overall
  summaries
- APIs: `/api/v1/portfolios/...` (list/create/get/update/delete, `/summary`,
  `/{id}/holdings`)
- Page + HTMX partials at `/portfolio`; stock detail portfolio position panel;
  dashboard portfolio value card

## Alert rules
- `AlertService` (`app/services/alert.py`): rule CRUD + deterministic engine
  (`run_for_trade_date`) evaluating `overall_score`, `rsi`, `ema_cross`, `delivery`,
  `volume`, `breakout`, `support_broken`, `resistance_broken`; cooldown via
  `last_triggered`; persisted `AlertEvent` rows (in-app only, no notifications)
- APIs: `/api/v1/alerts/rules` CRUD, `/run`, `/events`, `/events/pending`,
  `/events/symbol/{symbol}`, `PATCH /events/{id}`
- Page + HTMX partials at `/alerts`; stock detail matching-alerts panel; dashboard alert
  summary card; `alerts_triggered` metric in the EOD pipeline

## API surface
- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, analytics, ai,
  watchlists, portfolios, alerts, dashboard summary endpoints
- `/dashboard`: HTML dashboard + `/dashboard/search` HTMX partial + pipeline status +
  top-rated card + AI insights sections + portfolio/watchlist/alert summary cards
- `/scanner-results`: HTML workspace + `/table`, `/export/{csv,excel,pdf}`
- `/stocks/{symbol}`: HTML stock detail page + HTMX partials
  (`/overview`, `/indicators`, `/analytics`, `/scanner-results`, `/ai`) +
  `/api/v1/stocks/{symbol}/chart-data`
- `/watchlists`, `/portfolio`, `/alerts`: HTML pages with HTMX partials

## Testing
- Real PostgreSQL `test` database; `tests/conftest.py` truncates (incl. `alert_events`,
  `alert_rules`, `watchlist_stocks`, `watchlists`, `holdings`, `portfolios`, `ai_explanations`)
  and seeds 36 scan definitions idempotently per test
- 170 tests covering stock detail, scanner persistence/idempotency, dashboard, indicators,
  workspace exports, NSE parsing, market data, volume/delivery scanners, EOD pipeline,
  the analytics engine, the AI explanation engine, watchlists, portfolios, and the alert
  engine (rule evaluation, cooldown, APIs, UI rendering) — no real AI calls in tests
- Target: 3 consecutive green pytest runs + clean ruff

## Known gaps (not part of Sprint 14)
- `app/scanner/` contains dead duplicate scanner code (kept intentionally)
- Alert events are in-app only — notification delivery (email/push/webhook) not implemented
- No authentication / per-user data — the schema is single-user by design
- `scanner_engine.run_all` re-fetches indicator rows per scanner (perf follow-up)
- AI collection endpoints (top-rated, market-summary) are memory-cached only, not persisted
