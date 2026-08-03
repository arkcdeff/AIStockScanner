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
