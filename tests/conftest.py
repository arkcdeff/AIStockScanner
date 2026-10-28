import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _read_env_file_var(var_name: str) -> str | None:
    """Safely extract a variable from project .env file if present without exposing it."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return None
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == var_name:
                    return v.strip().strip("'\"")
    except Exception:
        return None


def _sanitize_url_for_display(url: str) -> str:
    """Mask password from database URL for safe error messages / logs."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return "postgresql://***@localhost/test"


_FORBIDDEN_TEST_DB_NAMES = {"ai_stock_scanner", "postgres", "production", "prod", "master"}

def _normalize_and_validate_test_url(
    raw_url: str, *, fallback_db_name: str = "test", label: str = "test database"
) -> str:
    """Rewrite the main development DB to the test target and enforce the safety gate.

    This helper is deliberately shared by async and sync URLs so an explicitly
    configured TEST_DATABASE_URL_SYNC cannot bypass the test-database policy.
    Only database-name metadata is included in errors; credentials are never logged.
    """
    parsed = urlparse(raw_url)
    db_name = parsed.path.lstrip("/").split("?", 1)[0]
    if db_name.lower() in {"ai_stock_scanner", ""}:
        db_name = fallback_db_name
        parsed = parsed._replace(path=f"/{db_name}")

    normalized_name = db_name.lower()
    if normalized_name in _FORBIDDEN_TEST_DB_NAMES or "test" not in normalized_name:
        raise RuntimeError(
            f"SAFETY ERROR: Refusing to use {label} database '{normalized_name}'. "
            "The database name must contain 'test' and cannot be a production or "
            "main development database."
        )
    return urlunparse(parsed._replace(path=f"/{db_name}"))


def _configure_test_database_urls() -> tuple[str, str]:
    """Resolve and validate async and sync test URLs without exposing credentials.

    Resolution order:
    1. TEST_DATABASE_URL_ASYNC / TEST_DATABASE_URL_SYNC from the environment
    2. DATABASE_URL_ASYNC / DATABASE_URL_SYNC from the environment or .env
       (the main development DB name is rewritten to the test DB)
    3. A safe local test-database default for async, with sync derived from it.

    Both URLs must pass the same database-name guard and resolve to the same
    database name. This prevents an explicit sync URL from targeting another DB.
    """
    raw_async = (
        os.environ.get("TEST_DATABASE_URL_ASYNC")
        or os.environ.get("DATABASE_URL_ASYNC")
        or _read_env_file_var("TEST_DATABASE_URL_ASYNC")
        or _read_env_file_var("DATABASE_URL_ASYNC")
        or "postgresql+asyncpg://postgres:postgres@localhost:5432/test"
    )
    raw_async = _normalize_and_validate_test_url(
        raw_async, fallback_db_name="test", label="async test"
    )
    parsed_async = urlparse(raw_async)
    async_db_name = parsed_async.path.lstrip("/").split("?", 1)[0]

    raw_sync = (
        os.environ.get("TEST_DATABASE_URL_SYNC")
        or os.environ.get("DATABASE_URL_SYNC")
        or _read_env_file_var("TEST_DATABASE_URL_SYNC")
        or _read_env_file_var("DATABASE_URL_SYNC")
    )
    if raw_sync:
        raw_sync = _normalize_and_validate_test_url(
            raw_sync, fallback_db_name=async_db_name, label="sync test"
        )
    else:
        sync_scheme = parsed_async.scheme.replace("+asyncpg", "+psycopg")
        if "+psycopg" not in sync_scheme:
            sync_scheme = "postgresql+psycopg"
        raw_sync = urlunparse(
            parsed_async._replace(scheme=sync_scheme, path=f"/{async_db_name}")
        )

    sync_db_name = urlparse(raw_sync).path.lstrip("/").split("?", 1)[0]
    if sync_db_name.lower() != async_db_name.lower():
        raise RuntimeError(
            "SAFETY ERROR: Async and sync test database names do not match "
            f"('{async_db_name}' versus '{sync_db_name}'). Refusing to initialize tests."
        )
    return raw_async, raw_sync


_TEST_DB_ASYNC, _TEST_DB_SYNC = _configure_test_database_urls()

os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL_ASYNC"] = _TEST_DB_ASYNC
os.environ["DATABASE_URL_SYNC"] = _TEST_DB_SYNC
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-signing-key-" + ("x" * 48))


_TABLES_TO_TRUNCATE = (
    "audit_logs, user_preferences, user_sessions, role_permissions, user_roles, "
    "permissions, roles, users, notification_deliveries, notification_preferences, "
    "notification_channels, daily_summary_runs, alert_events, alert_rules, "
    "watchlist_stocks, watchlists, holdings, portfolios, ai_explanations, "
    "scan_results, scan_definitions, indicator_results, import_jobs, "
    "eod_pipeline_runs, technical_analysis, daily_prices, stocks, sectors, "
    "backtest_metrics, backtest_trades, backtest_equity_points, backtest_runs, "
    "backtest_strategies, "
    "market_indices, market_index_prices, institutional_flows, "
    "market_intelligence_snapshots, econ_events, "
    "trading_positions, trading_executions, trading_orders, trading_accounts, "
    "index_memberships, security_aliases, corporate_actions, data_quality_issues, "
    "derivative_daily_prices, derivative_lot_size_histories, "
    "derivative_contract_metadata_histories, derivative_contract_sync_runs, "
    "option_chain_snapshots, option_chain_quotes, derivative_contracts, "
    "derivative_participant_positions, fii_derivative_statistics, "
    "financial_statements, corporate_announcements, shareholding_patterns, "
    "news_entity_links, news_events, bulk_block_deals"
)

_SCANNER_DEFINITIONS = [
    ("ema_bullish_crossover", "EMA Bullish Crossover", "EMA 20 crosses above EMA 50", "Trend"),
    ("ema_bearish_crossover", "EMA Bearish Crossover", "EMA 20 crosses below EMA 50", "Trend"),
    ("golden_cross", "Golden Cross", "EMA 50 crosses above EMA 200", "Trend"),
    ("death_cross", "Death Cross", "EMA 50 crosses below EMA 200", "Trend"),
    ("price_above_ema20", "Price Above EMA 20", "Close price above EMA 20", "Trend"),
    ("price_below_ema20", "Price Below EMA 20", "Close price below EMA 20", "Trend"),
    ("supertrend_bullish", "SuperTrend Bullish", "SuperTrend direction is bullish", "Trend"),
    ("supertrend_bearish", "SuperTrend Bearish", "SuperTrend direction is bearish", "Trend"),
    ("rsi_oversold", "RSI Oversold", "RSI 14 below 30", "Momentum"),
    ("rsi_overbought", "RSI Overbought", "RSI 14 above 70", "Momentum"),
    ("macd_bullish", "MACD Bullish Crossover", "MACD line crosses above signal line", "Momentum"),
    ("macd_bearish", "MACD Bearish Crossover", "MACD line crosses below signal line", "Momentum"),
    ("stochastic_oversold", "Stochastic Oversold", "Stochastic %K below 20", "Momentum"),
    ("stochastic_overbought", "Stochastic Overbought", "Stochastic %K above 80", "Momentum"),
    ("macd_above_zero", "MACD Above Zero", "MACD line above zero", "Momentum"),
    ("macd_below_zero", "MACD Below Zero", "MACD line below zero", "Momentum"),
    ("bollinger_band_squeeze", "Bollinger Band Squeeze", "Bollinger bandwidth below 5%", "Volatility"),
    ("price_above_upper_bollinger", "Price Above Upper Bollinger", "Close price above upper Bollinger Band", "Volatility"),
    ("price_below_lower_bollinger", "Price Below Lower Bollinger", "Close price below lower Bollinger Band", "Volatility"),
    ("atr_high_volatility", "ATR High Volatility", "ATR percentage above 3%", "Volatility"),
    ("obv_rising", "OBV Rising", "OBV increasing from previous day", "Volume"),
    ("vwap_bullish", "VWAP Bullish", "Close price above VWAP", "Volume"),
    ("vwap_bearish", "VWAP Bearish", "Close price below VWAP", "Volume"),
    ("strong_trend_adx", "Strong Trend ADX", "ADX above 25", "Trend Strength"),
    ("weak_trend_adx", "Weak Trend ADX", "ADX below 20", "Trend Strength"),
    ("volume_2x_average", "Volume 2x Average", "Latest volume >= 2x 20-session average", "Volume"),
    ("volume_5x_average", "Volume 5x Average", "Latest volume >= 5x 20-session average", "Volume"),
    ("highest_volume_20d", "Highest Volume 20D", "Latest volume is highest in last 20 sessions", "Volume"),
    ("volume_breakout", "Volume Breakout", "Latest volume > previous 20-session max", "Volume"),
    ("delivery_above_60", "Delivery Above 60", "Latest delivery >= 60%", "Delivery"),
    ("delivery_above_70", "Delivery Above 70", "Latest delivery >= 70%", "Delivery"),
    ("delivery_above_average", "Delivery Above Average", "Latest delivery > 20-session average", "Delivery"),
    ("delivery_breakout", "Delivery Breakout", "Latest delivery > previous 20-session max", "Delivery"),
    ("high_volume_high_delivery", "High Volume High Delivery", "Volume >= 2x avg AND delivery >= 60%", "Combined"),
    ("price_breakout_high_delivery", "Price Breakout High Delivery", "Close > 20d high AND volume >= avg AND delivery >= 60%", "Combined"),
    ("accumulation", "Accumulation", "Close up, volume > avg, delivery > avg", "Combined"),
]


# Shared sync engine for fast per-test truncation (created once per session)
_sync_truncate_engine = None


def _get_truncate_engine():
    """Get or create shared sync engine for fast truncation."""
    global _sync_truncate_engine
    if _sync_truncate_engine is None:
        _sync_truncate_engine = create_engine(_TEST_DB_SYNC, poolclass=NullPool)
    return _sync_truncate_engine


def _cleanup_truncate_engine():
    """Dispose shared sync engine."""
    global _sync_truncate_engine
    if _sync_truncate_engine is not None:
        _sync_truncate_engine.dispose()
        _sync_truncate_engine = None


@pytest.fixture(autouse=True)
async def _clean_test_db():
    """Fast per-test cleanup: truncate all tables using async engine."""
    from app.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {_TABLES_TO_TRUNCATE} RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture(autouse=True)
async def _seed_scan_definitions(_clean_test_db):
    """Per-test seed of scan definitions after truncate."""

    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.models.scan_definition import ScanDefinition

    factory = get_session_factory()
    async with factory() as session:
        existing = set((await session.execute(select(ScanDefinition.code))).scalars())
        for code, name, description, category in _SCANNER_DEFINITIONS:
            if code in existing:
                continue
            scan_def = ScanDefinition(
                code=code,
                name=name,
                description=description,
                category=category,
                parameters={},
                is_active=True,
            )
            session.add(scan_def)
        await session.commit()


@pytest.fixture(autouse=True)
async def _dispose_engine() -> None:
    """Dispose the async engine after each test to prevent connection leaks."""
    from app.db.session import dispose_engine

    yield
    await dispose_engine()


@pytest.fixture
async def db_session():
    """Provides an isolated database session with transaction rollback.

    Tests that need strict per-test DB isolation should use this fixture.
    Creates a savepoint, yields the session, then rolls back to savepoint.
    """

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Create a savepoint for isolation
        await session.begin_nested()
        try:
            yield session
        finally:
            # Rollback to savepoint to undo test changes
            await session.rollback()


@pytest.fixture
async def mi_fixture():
    """Create a MarketIndex fixture for tests."""
    from app.db.session import get_session_factory
    from app.models.market_index import MarketIndex

    factory = get_session_factory()
    async with factory() as session:
        mi = MarketIndex(code="NIFTY", country="INDIA", name="Nifty 50")
        session.add(mi)
        await session.commit()
        await session.refresh(mi)
        yield mi