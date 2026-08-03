# Project State

Snapshot of the AIStockScanner project as of Sprint 10 (stabilization).

## Stack
- Python 3.13 (uv-managed), FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic
- Jinja2 + HTMX frontend (dashboard, scanner-results workspace, stock detail)

## Data
- 7 models: Sector, Stock, DailyPrice, ImportJob, ScanDefinition, ScanResult, IndicatorResult
- 18 indicator series per stock/date stored in `indicator_results`
- 36 active scanners registered in `app/services/scanner_engine.py`; definitions seeded via
  migrations `fda0edd539bd` (25) and `bdc596b3589a` (11)
- `scan_results` is unique on `(scan_definition_id, stock_id, scan_date)`
  (`uq_scan_result_scan_stock_date`) — persistence is conflict-safe/idempotent

## Migrations
- Alembic current == head == `bdc596b3589a` (5 revisions)

## API surface
- `/api/v1`: health, stocks, market-data, indicators, scanners, dashboard summary endpoints
- `/dashboard`: HTML dashboard + `/dashboard/search` HTMX partial
- `/scanner-results`: HTML workspace + `/table`, `/export/{csv,excel,pdf}`
- `/stocks/{symbol}`: HTML stock detail page + HTMX partials
  (`/overview`, `/indicators`, `/scanner-results`) + `/api/v1/stocks/{symbol}/chart-data`

## Testing
- Real PostgreSQL `test` database; `tests/conftest.py` truncates + seeds 36 scan definitions
  idempotently per test
- Suite covers stock detail, scanner persistence/idempotency, dashboard, indicators, workspace
  exports, NSE parsing, market data, volume/delivery scanners
- Target: 3 consecutive green pytest runs + clean ruff

## Known gaps (not part of Sprint 10)
- IndicatorEngine is not auto-run after NSE import (planned for Sprint 11)
- `app/scanner/` contains dead duplicate scanner code (kept intentionally)
- AI explanations, watchlist, alerts, and scheduled automation are not implemented
- `scanner_engine.run_all` re-fetches indicator rows per scanner (perf follow-up)
