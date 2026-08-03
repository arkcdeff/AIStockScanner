# Project State

Snapshot of the AIStockScanner project as of Sprint 11 (EOD pipeline & scheduler).

## Stack
- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + HTMX frontend (dashboard, scanner-results workspace, stock detail)
- APScheduler (3.11) for optional scheduled EOD pipeline runs

## Data
- 8 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult,
  EODPipelineRun
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`; definitions seeded via
  migrations `fda0edd539bd` (25) and `bdc596b3589a` (11)
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
  (`uq_scan_result_scan_stock_date`) — persistence is conflict-safe/idempotent
- `eod_pipeline_runs` tracks every automated/manual pipeline run with per-stage metrics

## Migrations
- Alembic current == head == `d1e2f3a4b5c6` (6 revisions)

## EOD pipeline
- `app/services/eod_pipeline.py`: import → indicators → all active scanners, one fresh session
  per stage, single-active-run guard, credential-redacted errors, abandoned-run recovery
- Manual triggers: `/api/v1/pipeline/run-file`, `/run-date`, `/run-latest`
- Observability: `/api/v1/pipeline/runs`, `/runs/{id}`, `/status`
- Scheduler (`app/scheduler/eod_scheduler.py`): APScheduler daily job, weekdays 18:30
  Asia/Kolkata, disabled by default (`EOD_SCHEDULER_ENABLED=false`)

## API surface
- `/api/v1`: health, stocks, market-data, indicators, scanners, pipeline, dashboard summary endpoints
- `/dashboard`: HTML dashboard + `/dashboard/search` HTMX partial + pipeline status card
- `/scanner-results`: HTML workspace + `/table`, `/export/{csv,excel,pdf}`
- `/stocks/{symbol}`: HTML stock detail page + HTMX partials
  (`/overview`, `/indicators`, `/scanner-results`) + `/api/v1/stocks/{symbol}/chart-data`

## Testing
- Real PostgreSQL `test` database; `tests/conftest.py` truncates + seeds 36 scan definitions
  idempotently per test
- 75 tests covering stock detail, scanner persistence/idempotency, dashboard, indicators,
  workspace exports, NSE parsing, market data, volume/delivery scanners, and the EOD pipeline
  (full run, per-stage failures, overlap, recovery, scheduler, dashboard rendering)
- Target: 3 consecutive green pytest runs + clean ruff

## Known gaps (not part of Sprint 11)
- `app/scanner/` contains dead duplicate scanner code (kept intentionally)
- AI explanations, watchlist, alerts, and real-time automation are not implemented
- `scanner_engine.run_all` re-fetches indicator rows per scanner (perf follow-up)
