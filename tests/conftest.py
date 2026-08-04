import asyncio
import os

import pytest

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL_ASYNC"] = "postgresql+asyncpg://postgres:ramki12345@localhost/test"
os.environ["DATABASE_URL_SYNC"] = "postgresql+psycopg://postgres:ramki12345@localhost/test"


@pytest.fixture(autouse=True)
def _clear_db_engine_cache() -> None:
    from app.db.session import get_engine

    get_engine.cache_clear()
    yield
    get_engine.cache_clear()


@pytest.fixture(autouse=True)
def _clean_test_db() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL_SYNC"])
    tables = (
        "alert_events, alert_rules, watchlist_stocks, watchlists, holdings, portfolios, "
        "ai_explanations, scan_results, scan_definitions, indicator_results, "
        "import_jobs, eod_pipeline_runs, technical_analysis, daily_prices, stocks, sectors"
    )
    with engine.connect() as conn:
        conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture(autouse=True)
async def _dispose_engine() -> None:
    from app.db.session import dispose_engine

    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _seed_scan_definitions() -> None:
    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.models.scan_definition import ScanDefinition

    scanners = [
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
        (
            "macd_bullish",
            "MACD Bullish Crossover",
            "MACD line crosses above signal line",
            "Momentum",
        ),
        (
            "macd_bearish",
            "MACD Bearish Crossover",
            "MACD line crosses below signal line",
            "Momentum",
        ),
        ("stochastic_oversold", "Stochastic Oversold", "Stochastic %K below 20", "Momentum"),
        ("stochastic_overbought", "Stochastic Overbought", "Stochastic %K above 80", "Momentum"),
        ("macd_above_zero", "MACD Above Zero", "MACD line above zero", "Momentum"),
        ("macd_below_zero", "MACD Below Zero", "MACD line below zero", "Momentum"),
        (
            "bollinger_band_squeeze",
            "Bollinger Band Squeeze",
            "Bollinger bandwidth below 5%",
            "Volatility",
        ),
        (
            "price_above_upper_bollinger",
            "Price Above Upper Bollinger",
            "Close price above upper Bollinger Band",
            "Volatility",
        ),
        (
            "price_below_lower_bollinger",
            "Price Below Lower Bollinger",
            "Close price below lower Bollinger Band",
            "Volatility",
        ),
        ("atr_high_volatility", "ATR High Volatility", "ATR percentage above 3%", "Volatility"),
        ("obv_rising", "OBV Rising", "OBV increasing from previous day", "Volume"),
        ("vwap_bullish", "VWAP Bullish", "Close price above VWAP", "Volume"),
        ("vwap_bearish", "VWAP Bearish", "Close price below VWAP", "Volume"),
        ("strong_trend_adx", "Strong Trend ADX", "ADX above 25", "Trend Strength"),
        ("weak_trend_adx", "Weak Trend ADX", "ADX below 20", "Trend Strength"),
        (
            "volume_2x_average",
            "Volume 2x Average",
            "Latest volume >= 2x 20-session average",
            "Volume",
        ),
        (
            "volume_5x_average",
            "Volume 5x Average",
            "Latest volume >= 5x 20-session average",
            "Volume",
        ),
        (
            "highest_volume_20d",
            "Highest Volume 20D",
            "Latest volume is highest in last 20 sessions",
            "Volume",
        ),
        ("volume_breakout", "Volume Breakout", "Latest volume > previous 20-session max", "Volume"),
        ("delivery_above_60", "Delivery Above 60", "Latest delivery >= 60%", "Delivery"),
        ("delivery_above_70", "Delivery Above 70", "Latest delivery >= 70%", "Delivery"),
        (
            "delivery_above_average",
            "Delivery Above Average",
            "Latest delivery > 20-session average",
            "Delivery",
        ),
        (
            "delivery_breakout",
            "Delivery Breakout",
            "Latest delivery > previous 20-session max",
            "Delivery",
        ),
        (
            "high_volume_high_delivery",
            "High Volume High Delivery",
            "Volume >= 2x avg AND delivery >= 60%",
            "Combined",
        ),
        (
            "price_breakout_high_delivery",
            "Price Breakout High Delivery",
            "Close > 20d high AND volume >= avg AND delivery >= 60%",
            "Combined",
        ),
        ("accumulation", "Accumulation", "Close up, volume > avg, delivery > avg", "Combined"),
    ]

    factory = get_session_factory()
    async with factory() as session:
        existing = set((await session.execute(select(ScanDefinition.code))).scalars())
        for code, name, description, category in scanners:
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
