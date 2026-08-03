import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.sector import Sector
from app.models.stock import Stock


@pytest.mark.asyncio
async def test_stock_detail_page_existing_symbol() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "RELIANCE" in response.text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_page_unknown_symbol() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stocks/UNKNOWN123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_chart_data_sorted_candles() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/stocks/RELIANCE/chart-data?period=1y&include_indicators=true"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "RELIANCE"
        candles = data["candles"]
        if candles:
            times = [c["time"] for c in candles]
            assert times == sorted(times)
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_chart_data_period_filter() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/stocks/RELIANCE/chart-data?period=3m&include_indicators=false"
            )
        assert response.status_code == 200
        data = response.json()
        assert "candles" in data
        assert "volume" in data
        assert data["ema20"] == []
        assert data["ema50"] == []
        assert data["ema200"] == []
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_chart_data_empty_history() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stocks/UNKNOWN123/chart-data")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_overview_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE/overview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_indicators_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE/indicators")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_scanner_results_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE/scanner-results")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_page_without_price_data() -> None:
    stock_id = await _create_test_stock(no_prices=True)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "RELIANCE" in response.text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_overview_partial_without_price_data() -> None:
    stock_id = await _create_test_stock(no_prices=True)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/stocks/RELIANCE/overview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


async def _create_test_stock(no_prices: bool = False) -> int:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        sector = Sector(name="Test Sector", description="Test")
        session.add(sector)
        await session.flush()

        stock = Stock(
            symbol="RELIANCE",
            company_name="Reliance Industries Ltd",
            exchange="NSE",
            series="EQ",
            isin="INE002A01018",
            sector_id=sector.id,
            industry="Oil & Gas",
        )
        session.add(stock)
        await session.flush()

        if no_prices:
            await session.commit()
            return stock.id

        from datetime import date, timedelta

        base_date = date.today() - timedelta(days=30)
        for i in range(30):
            trade_date = base_date + timedelta(days=i)
            price = DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=2500.0 + i,
                high=2550.0 + i,
                low=2490.0 + i,
                close=2540.0 + i,
                previous_close=2530.0 + i - 1,
                volume=1000000 + i * 10000,
                delivery_quantity=200000 + i * 5000,
                delivery_percentage=20.0 + i * 0.5,
            )
            session.add(price)

        indicator = IndicatorResult(
            stock_id=stock.id,
            trade_date=base_date + timedelta(days=29),
            ema_20=2520.0,
            ema_50=2510.0,
            ema_100=2500.0,
            ema_200=2490.0,
            sma=2515.0,
            rsi_14=55.5,
            macd=10.5,
            macd_signal=9.8,
            macd_hist=0.7,
            atr=25.0,
            adx=28.0,
            supertrend=2530.0,
            supertrend_direction=1,
            vwap=2535.0,
            obv=15000000.0,
            bollinger_upper=2600.0,
            bollinger_middle=2515.0,
            bollinger_lower=2430.0,
            stochastic_k=65.0,
            stochastic_d=60.0,
        )
        session.add(indicator)

        scan_def = ScanDefinition(
            code="test_scanner",
            name="Test Scanner",
            description="Test",
            category="Test",
            parameters={},
            is_active=True,
        )
        session.add(scan_def)
        await session.flush()

        scan_result = ScanResult(
            scan_definition_id=scan_def.id,
            stock_id=stock.id,
            scan_date=base_date + timedelta(days=29),
            score=0.85,
            metrics={"reason": "Test reason"},
        )
        session.add(scan_result)

        await session.commit()
        return stock.id


async def _cleanup_test_stock(stock_id: int) -> None:
    from sqlalchemy import delete

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(ScanResult).where(ScanResult.stock_id == stock_id))
        await session.execute(delete(IndicatorResult).where(IndicatorResult.stock_id == stock_id))
        await session.execute(delete(DailyPrice).where(DailyPrice.stock_id == stock_id))
        stock = await session.get(Stock, stock_id)
        if stock:
            await session.delete(stock)
        await session.commit()
