# Bug Fixes

## Fixed Issues

### Async API Test Failures on Windows
**Issue:** Tests using synchronous TestClient against async FastAPI app failed on Windows.
**Root Cause:** TestClient spins up its own event loop in a separate thread, causing asyncpg connection futures to attach to the wrong loop. During teardown, SQLAlchemy tried to terminate connections from a different loop.
**Fix:**
- Added `asyncio_default_fixture_loop_scope = "function"` to pyproject.toml
- Created async `_dispose_engine` fixture in conftest.py
- Replaced TestClient with AsyncClient + ASGITransport in test_indicators.py
**Status:** Fixed

### Event Loop Attachment Issues with asyncpg
**Issue:** asyncpg connections attached to wrong event loop on Windows.
**Root Cause:** Multiple event loops created during test execution.
**Fix:**
- Added `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` in conftest.py
- Proper engine disposal after each test
- Cached engine cleanup in fixtures
**Status:** Fixed

### Database Connection Pool Exhaustion
**Issue:** Tests failed with connection pool exhaustion after multiple runs.
**Root Cause:** Engine not properly disposed between test runs.
**Fix:** Added `engine.dispose()` in test fixture cleanup.
**Status:** Fixed

### Migration Conflicts on Test Database
**Issue:** Alembic migrations failed on test database due to existing tables.
**Root Cause:** Test database reused between sessions with stale schema.
**Fix:** Added database truncation in test fixture setup.
**Status:** Fixed

### `sector_name` KeyError on Stock Detail Without Price Data
**Issue:** `GET /stocks/{symbol}` raised `KeyError: 'sector_name'` when a stock existed but had no `DailyPrice` rows.
**Root Cause:** `StockDetailService.get_overview` returned early without a `sector_name` key when `latest_price` was `None`, but the route always accessed `overview["sector_name"]`.
**Fix:** Compute `sector_name` before the early return and include it in both return paths (`app/services/stock_detail.py`).
**Tests:** Added `test_stock_detail_page_without_price_data` and `test_stock_overview_partial_without_price_data`.
**Status:** Fixed

### Scanner Results Not Persisted
**Issue:** `POST /api/v1/scanners/run` and `/run-all` returned matches but never stored them in `scan_results`, so the workspace and dashboard stayed empty.
**Root Cause:** Routes called `run_scanner`/`run_all` but never invoked `ScannerService.persist_results`.
**Fix:** Routes now call `persist_results` after running. `persist_results` uses a PostgreSQL conflict-safe insert (`ON CONFLICT DO NOTHING` on `uq_scan_result_scan_stock_date`) and rolls back on failure (`app/services/scanner_engine.py`, `app/api/routes/scanners.py`).
**Tests:** `test_api_run_scanner_persists_results`, `test_api_run_all_persists_results`, `test_api_run_scanner_idempotent`, `test_api_run_no_match_creates_no_rows`.
**Status:** Fixed

### Flaky Scan-Definition Seeding (UniqueViolationError)
**Issue:** Conftest seeding of scan definitions intermittently failed with `UniqueViolationError` on `ix_scan_definitions_code`.
**Root Cause:** The seed fixture always inserted all 36 definitions without checking for existing rows.
**Fix:** Seed only codes that are not already present (`tests/conftest.py`). No broad exception hiding.
**Status:** Fixed

### Chart Marker N+1
**Issue:** `get_chart_data` issued a `session.get(ScanDefinition, ...)` lookup for every scanner marker.
**Root Cause:** Scan results were eager-loaded via `selectinload`, but the loop ignored the loaded relationship and re-fetched by id.
**Fix:** Use `sr.scan_definition` from the eager-loaded relationship (`app/repositories/stock_detail.py`).
**Status:** Fixed

### fpdf2 `dest="S"` Deprecation
**Issue:** `pdf.output(dest="S")` emitted a DeprecationWarning (removed in future fpdf2 releases).
**Fix:** Use `bytes(pdf.output())` which returns bytes directly (`app/services/scanner_workspace.py`).
**Status:** Fixed
