# Test Strategy

## Testing Philosophy
- Test-driven development where possible
- Async tests using pytest-asyncio
- Integration tests for API endpoints
- Unit tests for business logic

## Test Types

### Unit Tests
- NSE parser tests
- Scanner logic tests
- Indicator calculation tests
- Utility function tests

### Integration Tests
- API endpoint tests
- Database operation tests
- Import flow tests
- Scanner execution tests

### Test Structure
```
tests/
├── conftest.py                # Test fixtures and configuration
├── test_health.py             # Health endpoint tests
├── test_indicators.py         # Indicator calculation tests
├── test_market_data.py        # Market data API tests
├── test_nse_parser.py         # NSE parser unit tests
├── test_scanner_registry.py   # Scanner registry tests
├── test_scanners.py           # Scanner execution tests
└── test_volume_delivery_scanners.py  # Volume/delivery scanner tests
```

## Test Coverage
- Current: 31 tests passing
- Target: 80%+ code coverage

## Key Test Fixtures
- `db_session`: Async database session for tests
- `test_app`: FastAPI test application
- `async_client`: Async HTTP client for API testing

## Running Tests
```bash
# Run all tests
uv run pytest -q

# Run with coverage
uv run pytest --cov=app --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_scanners.py -v
```

## Windows-specific Considerations
- `asyncio_default_fixture_loop_scope = "function"` in pyproject.toml
- AsyncClient + ASGITransport instead of TestClient for async endpoints
- Engine disposal after each test to avoid event loop conflicts
