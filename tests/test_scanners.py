from datetime import UTC, datetime

import pytest

from app.models.scan_definition import ScanDefinition
from app.services.scanner_engine import ScannerRegistry
from tests.auth_helpers import analyst_client


def test_scanner_registry_has_36_scanners() -> None:
    scanners = ScannerRegistry.all()
    assert len(scanners) == 36


@pytest.mark.asyncio
async def test_api_list_scanners() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/scanners")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 36
        codes = [item["code"] for item in body]
        expected = [
            "ema_bullish_crossover",
            "ema_bearish_crossover",
            "golden_cross",
            "death_cross",
            "price_above_ema20",
            "price_below_ema20",
            "supertrend_bullish",
            "supertrend_bearish",
            "rsi_oversold",
            "rsi_overbought",
            "macd_bullish",
            "macd_bearish",
            "stochastic_oversold",
            "stochastic_overbought",
            "macd_above_zero",
            "macd_below_zero",
            "bollinger_band_squeeze",
            "price_above_upper_bollinger",
            "price_below_lower_bollinger",
            "atr_high_volatility",
            "obv_rising",
            "vwap_bullish",
            "vwap_bearish",
            "strong_trend_adx",
            "weak_trend_adx",
            "volume_2x_average",
            "volume_5x_average",
            "highest_volume_20d",
            "volume_breakout",
            "delivery_above_60",
            "delivery_above_70",
            "delivery_above_average",
            "delivery_breakout",
            "high_volume_high_delivery",
            "price_breakout_high_delivery",
            "accumulation",
        ]
        for code in expected:
            assert code in codes


@pytest.mark.asyncio
async def test_api_run_scanner_not_found() -> None:
    async with analyst_client() as client:
        response = await client.post("/api/v1/scanners/run?scan_code=nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_run_all_scanners() -> None:
    async with analyst_client() as client:
        response = await client.post("/api/v1/scanners/run-all")
        assert response.status_code == 200
        body = response.json()
        assert "total_scanners" in body
        assert "total_matched" in body
        assert "execution_time_ms" in body
        assert "results" in body


@pytest.mark.asyncio
async def test_api_list_scan_results() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/scanners/results")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


async def _seed_matching_stock(symbol: str = "PERSIST1") -> int:

    from app.db.session import get_session_factory
    from app.models.daily_price import DailyPrice
    from app.models.indicator_result import IndicatorResult
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(
            symbol=symbol,
            company_name="Persist Test",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stock)
        await session.flush()

        trade_date = datetime.now(UTC).date()
        session.add(
            DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=100,
                high=110,
                low=95,
                close=105,
                previous_close=100,
                volume=1000,
                delivery_percentage=65.0,
            )
        )
        session.add(
            IndicatorResult(
                stock_id=stock.id,
                trade_date=trade_date,
                ema_20=100.0,
                ema_50=99.0,
            )
        )
        await session.commit()
        return stock.id


async def _count_scan_results(scan_code: str) -> int:
    from sqlalchemy import func, select

    from app.db.session import get_session_factory
    from app.models.scan_result import ScanResult

    factory = get_session_factory()
    async with factory() as session:
        count = await session.scalar(
            select(func.count(ScanResult.id))
            .join(ScanDefinition, ScanResult.scan_definition_id == ScanDefinition.id)
            .where(ScanDefinition.code == scan_code)
        )
        return int(count or 0)


@pytest.mark.asyncio
async def test_api_run_scanner_persists_results() -> None:
    stock_id = await _seed_matching_stock()
    try:
        async with analyst_client() as client:
            response = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
        assert response.status_code == 200
        assert await _count_scan_results("delivery_above_60") >= 1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_api_run_all_persists_results() -> None:
    stock_id = await _seed_matching_stock()
    try:
        async with analyst_client() as client:
            response = await client.post("/api/v1/scanners/run-all")
        assert response.status_code == 200
        assert await _count_scan_results("delivery_above_60") >= 1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_api_run_scanner_idempotent() -> None:
    stock_id = await _seed_matching_stock()
    try:
        async with analyst_client() as client:
            response1 = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
            assert response1.status_code == 200
            count1 = await _count_scan_results("delivery_above_60")
            response2 = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
            assert response2.status_code == 200
            count2 = await _count_scan_results("delivery_above_60")
        assert count1 >= 1
        assert count2 == count1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_api_run_no_match_creates_no_rows() -> None:
    async with analyst_client() as client:
        response = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
    assert response.status_code == 200
    assert await _count_scan_results("delivery_above_60") == 0


async def _cleanup_stock(stock_id: int) -> None:
    from sqlalchemy import delete

    from app.db.session import get_session_factory
    from app.models.daily_price import DailyPrice
    from app.models.indicator_result import IndicatorResult
    from app.models.scan_result import ScanResult
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(ScanResult).where(ScanResult.stock_id == stock_id))
        await session.execute(delete(IndicatorResult).where(IndicatorResult.stock_id == stock_id))
        await session.execute(delete(DailyPrice).where(DailyPrice.stock_id == stock_id))
        stock = await session.get(Stock, stock_id)
        if stock:
            await session.delete(stock)
        await session.commit()