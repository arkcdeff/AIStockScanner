from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.scan_definition import ScanDefinition
from app.models.stock import Stock
from app.repositories.stock import StockRepository
from app.services.scanner_engine import ScannerRegistry
from app.db.session import get_session_factory


@pytest.mark.asyncio
async def test_volume_2x_average_exact_boundary() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = StockRepository(session)
        stock = Stock(
            symbol="TESTBOUND",
            company_name="Test Boundary",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        await repo.add(stock)
        await session.flush()

        from app.models.daily_price import DailyPrice
        from app.models.indicator_result import IndicatorResult

        base_date = datetime.now(UTC).date()
        for i in range(25):
            trade_date = base_date - __import__("datetime").timedelta(days=i)
            dp = DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=100,
                high=105,
                low=95,
                close=100,
                volume=1000,
                delivery_percentage=50,
            )
            session.add(dp)
            ir = IndicatorResult(
                stock_id=stock.id,
                trade_date=trade_date,
            )
            session.add(ir)
        await session.commit()

        from app.services.scanner_engine import IndicatorRow
        from app.services.scanner_engine import volume_2x_average

        current = IndicatorRow(
            stock_id=stock.id,
            symbol="TESTBOUND",
            trade_date=base_date,
            close=100,
            volume=2000,
            avg_volume_20d=1000.0,
            max_volume_20d=1000,
            avg_delivery_20d=50.0,
            max_delivery_20d=50.0,
            max_high_20d=105.0,
            ema_20=None, ema_50=None, ema_100=None, ema_200=None,
            sma=None, rsi_14=None, macd=None, macd_signal=None, macd_hist=None,
            stochastic_k=None, stochastic_d=None, atr=None,
            bollinger_upper=None, bollinger_middle=None, bollinger_lower=None,
            adx=None, supertrend=None, supertrend_direction=None,
            vwap=None, obv=None,
        )
        matched, score, metrics = volume_2x_average(current, None)
        assert matched is True
        assert score == 1.0
        assert metrics["volume_multiple"] == 2.0


@pytest.mark.asyncio
async def test_delivery_above_60_exact_boundary() -> None:
    from app.services.scanner_engine import delivery_above_60

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.delivery_percentage = 60.0

    matched, score, metrics = delivery_above_60(current, None)
    assert matched is True
    assert score == 1.0
    assert metrics["delivery_percent"] == 60.0


@pytest.mark.asyncio
async def test_delivery_above_60_below_boundary() -> None:
    from app.services.scanner_engine import delivery_above_60

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.delivery_percentage = 59.9

    matched, score, metrics = delivery_above_60(current, None)
    assert matched is False
    assert score == 0.0


@pytest.mark.asyncio
async def test_insufficient_history_no_match() -> None:
    from app.services.scanner_engine import volume_2x_average

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.volume = 2000
    current.avg_volume_20d = None
    current.max_volume_20d = None
    current.avg_delivery_20d = None
    current.max_delivery_20d = None
    current.max_high_20d = None

    matched, score, metrics = volume_2x_average(current, None)
    assert matched is False
    assert score == 0.0


@pytest.mark.asyncio
async def test_null_delivery_handled() -> None:
    from app.services.scanner_engine import delivery_above_60

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.delivery_percentage = None

    matched, score, metrics = delivery_above_60(current, None)
    assert matched is False
    assert score == 0.0


@pytest.mark.asyncio
async def test_duplicate_execution_persists_results() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response1 = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
        assert response1.status_code == 200
        body1 = response1.json()

        response2 = await client.post("/api/v1/scanners/run?scan_code=delivery_above_60")
        assert response2.status_code == 200
        body2 = response2.json()

        assert body1["matched_stocks"] == body2["matched_stocks"]


@pytest.mark.asyncio
async def test_high_volume_high_delivery_combined_match() -> None:
    from app.services.scanner_engine import high_volume_high_delivery

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.volume = 2000
    current.avg_volume_20d = 1000.0
    current.delivery_percentage = 65.0
    current.avg_delivery_20d = None
    current.max_delivery_20d = None

    matched, score, metrics = high_volume_high_delivery(current, None)
    assert matched is True
    assert score == 1.0
    assert "Volume" in metrics["reason"]
    assert "Delivery" in metrics["reason"]


@pytest.mark.asyncio
async def test_high_volume_high_delivery_volume_fails() -> None:
    from app.services.scanner_engine import high_volume_high_delivery

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 100
    current.volume = 1000
    current.avg_volume_20d = 1000.0
    current.delivery_percentage = 65.0
    current.avg_delivery_20d = None
    current.max_delivery_20d = None

    matched, score, metrics = high_volume_high_delivery(current, None)
    assert matched is False


@pytest.mark.asyncio
async def test_price_breakout_high_delivery_match() -> None:
    from app.services.scanner_engine import price_breakout_high_delivery

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 110
    current.high = 108
    current.max_high_20d = 105.0
    current.volume = 1000
    current.avg_volume_20d = 900.0
    current.delivery_percentage = 65.0

    matched, score, metrics = price_breakout_high_delivery(current, None)
    assert matched is True
    assert metrics["breakout_level"] == 105.0


@pytest.mark.asyncio
async def test_accumulation_match() -> None:
    from app.services.scanner_engine import accumulation

    current = type("Row", (), {})()
    current.symbol = "TEST"
    current.close = 105
    current.volume = 1200
    current.avg_volume_20d = 1000.0
    current.delivery_percentage = 55.0
    current.avg_delivery_20d = 50.0

    previous = type("Row", (), {})()
    previous.close = 100

    matched, score, metrics = accumulation(current, previous)
    assert matched is True
    assert metrics["prev_close"] == 100.0
