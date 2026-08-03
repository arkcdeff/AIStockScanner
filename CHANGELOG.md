# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Planned
- Auto-run IndicatorEngine after NSE import (Sprint 11)
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
