# Changelog

All notable changes to this project will be documented in this file.

## [0.8.0] - 2026-08-04

### Added
- Technical Analytics Engine (`app/services/analytics_engine.py`) computing deterministic
  composite scores: trend, momentum, volume, delivery, volatility, and overall (0-100)
- Support/resistance, classic + Fibonacci pivot points, relative volume, delivery strength,
  trend/momentum/volatility ratings, and trending/breakout flags
- `technical_analysis` table (unique per stock/date) + migration `e1f2a3b4c5d6`
- `analytics_calculated` metric added to `eod_pipeline_runs`
- Analytics APIs: `GET /api/v1/analytics/{symbol}`, `/top-rated`, `/trending`, `/breakouts`
- EOD pipeline now runs analytics after scanners (import → indicators → scanners → analytics)
- Dashboard "Top Rated Stocks" card + analytics metric in pipeline status
- Stock detail Technical Analytics card with HTMX refresh partial
- 20 new tests (scoring, pivots, flags, APIs, pipeline integration, UI rendering)

## [0.7.0] - 2026-08-03

### Added
- Automated EOD processing pipeline (`app/services/eod_pipeline.py`) orchestrating:
  import NSE EOD file → calculate indicators → run all active scanners → persist results
- `eod_pipeline_runs` table with metrics, stage tracking, indexes, and `import_job_id` link
- Manual execution APIs: `POST /api/v1/pipeline/run-file`, `run-date`, `run-latest`;
  `GET /api/v1/pipeline/runs`, `runs/{id}`, `status`
- APScheduler-based EOD scheduler (`app/scheduler/eod_scheduler.py`) — disabled by default
  (`EOD_SCHEDULER_ENABLED=false`), weekdays-only 18:30 Asia/Kolkata, single-instance, coalesced
- Overlapping-run prevention (409 on conflict), abandoned-run recovery on startup, credential
  redaction in error messages
- Pipeline Status card on the dashboard (latest run, trade date, metrics, failed stage)
- 19 new tests (pipeline service, API, scheduler, dashboard rendering)

### Fixed
- `IndicatorEngine` never imported `pandas_ta`, so `df.ta` was unavailable — computation now works
- Indicator calculation failed on `Decimal` columns from the database — numeric columns are cast
  to float before indicator math

## [Unreleased]

### Planned
- Watchlist management
- Price alerts and notifications
- Real-time scanner execution

## [0.6.1] - 2026-08-03

### Added
- Stock detail works for stocks without price history (regression tests)
- Scanner results are persisted by `run` / `run-all` (conflict-safe, idempotent)
- Regression tests: scanner persistence, idempotency, no-match, no-price stock
- HTMX refresh wiring for stock detail overview/indicators/scanner-matches partials

### Fixed
- `sector_name` KeyError when a stock has no `DailyPrice` rows
- Flaky conftest scan-definition seeding (`UniqueViolationError`)
- Chart marker N+1 (`session.get(ScanDefinition)` per marker removed)
- Dashboard scanner summary now uses a single grouped query instead of 36 counts
- Indicator status uses aggregate counts instead of loading full result sets
- fpdf2 `dest="S"` deprecation removed
- Sidebar/navbar no longer link to JSON endpoints

### Changed
- `IndicatorResult` added to `app/models/__init__.py` `__all__`
- Removed duplicate `python-multipart` dependency
- Scanner API docs document persistence and idempotency

### Migrations
- None (no schema change)

## [0.4.0] - 2026-08-03

### Added
- Dashboard sprint planning
- Priority task tracking: Dashboard, Charts, AI Explanation, Watchlist, Alerts

### Fixed
- Project completion updated to 50%

## [0.3.0] - 2026-08-01

### Added
- NSE EOD bhavcopy downloader and parser
- Market data APIs: download, import-file, prices, imports, summary
- Indicator engine with 15 technical indicators
- Scanner engine with 36 scanners
- Volume and delivery scanners (11 new)
- CLI for NSE import
- Comprehensive test suite (31 tests)

### Fixed
- Async API test failures on Windows
- Event loop attachment issues with asyncpg

### Migrations
- 0002_market_data_foundation
- c64742f7da53_add_indicator_results_table
- fda0edd539bd_seed_scan_definitions
- bdc596b3589a_seed_volume_and_delivery_scan_

## [0.2.0] - 2026-07-30

### Added
- FastAPI foundation with async PostgreSQL
- Stock CRUD APIs
- Scanner registry infrastructure
- Alembic migrations
- Docker compose for PostgreSQL
- Git workflow documentation

### Migration
- 0001_create_core_tables

## [0.1.0] - 2026-07-29

### Added
- Project initialization
- Base models and database session
- Health check endpoint
- Basic project structure
