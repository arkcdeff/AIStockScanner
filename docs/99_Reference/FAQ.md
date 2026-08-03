# FAQ

## General

**Q: What is AI Stock Scanner?**
A: AI Stock Scanner is a FastAPI-based application for analyzing NSE equity data. It downloads bhavcopy files, calculates technical indicators, and runs scanners to identify stock patterns.

**Q: What data sources does it use?**
A: It uses NSE bhavcopy files (CSV/ZIP) downloaded from the NSE website. These contain daily OHLC data, volume, and delivery information.

**Q: What technical indicators are supported?**
A: 15 indicators: EMA (20/50/100/200), SMA, RSI, MACD, Stochastic, ATR, Bollinger Bands, ADX, SuperTrend, VWAP, OBV.

**Q: How many scanners are available?**
A: 36 scanners across trend, momentum, volatility, volume, delivery, and combined categories.

## Technical

**Q: Why PostgreSQL 18?**
A: PostgreSQL 18 provides excellent ACID compliance, performance, and support for complex queries. It's well-suited for time-series market data.

**Q: Why FastAPI?**
A: FastAPI provides native async support, automatic OpenAPI documentation, and high performance. It's ideal for building async APIs with SQLAlchemy.

**Q: Why pandas-ta?**
A: pandas-ta provides 100+ technical indicators with a simple pandas-native API. It's actively maintained and pure Python (no C bindings required).

**Q: Why asyncpg?**
A: asyncpg is the fastest async PostgreSQL driver. It uses the native async protocol and has very low overhead compared to synchronous drivers.

**Q: How are scanners registered?**
A: Scanners use a decorator pattern: `@ScannerRegistry.register("code")`. The registry maintains a dictionary of scanner functions.

**Q: What is the 20-day aggregate computation?**
A: Before running scanners, the engine computes avg_volume, max_volume, avg_delivery, max_delivery, and max_high over the last 20 trading sessions.

## Troubleshooting

**Q: Tests fail on Windows with event loop errors**
A: This is a known Windows-specific issue. Ensure `asyncio_default_fixture_loop_scope = "function"` is set in pyproject.toml and use AsyncClient instead of TestClient.

**Q: Port 8000 is already in use**
A: Find the process with `netstat -ano | findstr :8000` and kill it with `taskkill /PID <pid> /F`.

**Q: Database connection refused**
A: Verify PostgreSQL is running with `net start postgresql-18` and check credentials in .env.

**Q: Migration fails with "table already exists"**
A: The database may have stale schema. Drop and recreate the test database, or use `alembic stamp head` to mark migrations as applied.

**Q: How do I add a new scanner?**
A: Create a function with the signature `(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict]` and decorate it with `@ScannerRegistry.register("code")`.
