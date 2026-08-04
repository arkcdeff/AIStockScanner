# Project State

Snapshot of the AIStockScanner project as of Sprint 12 (technical analytics engine).

## Stack
- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + HTMX frontend (dashboard, scanner-results workspace, stock detail)
- APScheduler (3.11) for optional scheduled EOD pipeline runs

## Data
- 9 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun, TechnicalAnalysis
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`; definitions seeded via
  migrations `fda0edd539bd` (25) and `bdc596b3589a` (11)
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
  (`uq_scan_result_scan_stock_date`) — persistence is conflict-safe/idempotent
- `eod_pipeline_runs` tracks every automated/manual pipeline run with per-stage metrics
- `technical_analysis` stores per-stock/date composite scores, pivots, ratings, flags

## Migrations
- Alembic current == head == `e1f2a3b4c5d6` (7 revisions)

## EOD pipeline
- `app/services/eod_pipeline.py`: import → indicators → all active scanners → analytics,
  one fresh session per stage, single-active-run guard, credential-redacted errors,
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

## API surface
- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, analytics,
  dashboard summary endpoints
- `/dashboard`: HTML dashboard + `/dashboard/search` HTMX partial + pipeline status +
  top-rated card
- `/scanner-results`: HTML workspace + `/table`, `/export/{csv,excel,pdf}`
- `/stocks/{symbol}`: HTML stock detail page + HTMX partials
  (`/overview`, `/indicators`, `/analytics`, `/scanner-results`) +
  `/api/v1/stocks/{symbol}/chart-data`

## Testing
- Real PostgreSQL `test` database; `tests/conftest.py` truncates + seeds 36 scan definitions
  idempotently per test
- 95 tests covering stock detail, scanner persistence/idempotency, dashboard, indicators,
  workspace exports, NSE parsing, market data, volume/delivery scanners, EOD pipeline, and
  the analytics engine (scoring, pivots, flags, APIs, pipeline integration, UI rendering)
- Target: 3 consecutive green pytest runs + clean ruff

## Known gaps (not part of Sprint 12)
- `app/scanner/` contains dead duplicate scanner code (kept intentionally)
- AI explanations, watchlist, alerts, and real-time automation are not implemented
- `scanner_engine.run_all` re-fetches indicator rows per scanner (perf follow-up)
