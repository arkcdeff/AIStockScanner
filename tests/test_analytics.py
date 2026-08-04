from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.main import app
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.sector import Sector
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis
from app.services.analytics_engine import AnalyticsEngine, AnalyticsEngineError
from app.services.eod_pipeline import EODPipelineService


async def _seed_stock(
    *,
    close_base: float = 2500.0,
    delivery_pct: float = 55.0,
    bullish: bool = True,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        sector = Sector(name="Analytics Test", description="Test")
        session.add(sector)
        await session.flush()

        stock = Stock(
            symbol="ANALYTX",
            company_name="Analytics Test Ltd",
            exchange="NSE",
            series="EQ",
            isin="INEANALYT1",
            sector_id=sector.id,
            industry="Testing",
        )
        session.add(stock)
        await session.flush()

        base_date = date.today() - timedelta(days=35)
        for i in range(35):
            trade_date = base_date + timedelta(days=i)
            price = DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=close_base - 10 + i,
                high=close_base + 40 + i,
                low=close_base - 40 + i,
                close=close_base + i,
                previous_close=close_base + i - 1,
                volume=1000000 + i * 10000,
                delivery_quantity=200000 + i * 5000,
                delivery_percentage=delivery_pct + (i % 5),
            )
            session.add(price)

        latest_date = base_date + timedelta(days=34)
        ema_20 = close_base + 20
        ema_50 = close_base + 10 if bullish else close_base + 30
        ema_200 = close_base if bullish else close_base + 40
        indicator = IndicatorResult(
            stock_id=stock.id,
            trade_date=latest_date,
            ema_20=ema_20,
            ema_50=ema_50,
            ema_100=close_base + 5,
            ema_200=ema_200,
            sma=close_base + 15,
            rsi_14=58.0 if bullish else 42.0,
            macd=10.5 if bullish else -5.0,
            macd_signal=9.0 if bullish else 4.0,
            macd_hist=1.5,
            stochastic_k=62.0,
            stochastic_d=55.0,
            atr=25.0,
            adx=30.0 if bullish else 15.0,
            supertrend=close_base + 5,
            supertrend_direction=1 if bullish else 0,
            vwap=close_base + 12,
            obv=15000000.0,
        )
        session.add(indicator)
        await session.commit()
        return stock.id


async def _calculate(symbol: str = "ANALYTX") -> TechnicalAnalysis:
    factory = get_session_factory()
    async with factory() as session:
        engine = AnalyticsEngine(session)
        result = await engine.calculate_for_stock(symbol)
        await session.commit()
        return result


async def _count_analytics() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(TechnicalAnalysis)) or 0


async def _latest_close(symbol: str) -> float:
    factory = get_session_factory()
    async with factory() as session:
        stock = await session.scalar(select(Stock).where(Stock.symbol == symbol))
        price = await session.scalar(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )
        return float(price.close)


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_calculate_for_stock_creates_analysis() -> None:
    await _seed_stock()
    result = await _calculate()
    assert result is not None
    assert result.trend_score >= 0
    assert result.momentum_score >= 0
    assert result.volume_score >= 0
    assert result.delivery_score >= 0
    assert result.volatility_score >= 0
    assert 0 <= result.overall_score <= 100
    assert result.support is not None
    assert result.resistance is not None
    assert result.pivot is not None
    assert result.fib_pivot is not None


@pytest.mark.asyncio
async def test_scores_bounded_between_0_and_100() -> None:
    await _seed_stock()
    result = await _calculate()
    for score in (
        result.trend_score,
        result.momentum_score,
        result.volume_score,
        result.delivery_score,
        result.volatility_score,
        result.overall_score,
    ):
        assert 0 <= score <= 100


@pytest.mark.asyncio
async def test_bullish_stock_scores_high() -> None:
    await _seed_stock(bullish=True)
    result = await _calculate()
    assert result.trend_score >= 60
    assert result.trend_rating in {"Uptrend", "Strong Uptrend"}
    assert result.momentum_score >= 50


@pytest.mark.asyncio
async def test_support_below_close_resistance_above_close() -> None:
    await _seed_stock()
    result = await _calculate()
    price = await _latest_close("ANALYTX")
    assert result.support is not None
    assert result.resistance is not None
    assert float(result.support) <= price <= float(result.resistance)


@pytest.mark.asyncio
async def test_classic_pivots_relationships() -> None:
    await _seed_stock()
    result = await _calculate()
    assert result.pivot is not None
    assert result.pivot_r3 is not None
    assert result.pivot_r2 >= result.pivot_r1 >= result.pivot
    assert result.pivot_s3 <= result.pivot_s2 <= result.pivot_s1 <= result.pivot


@pytest.mark.asyncio
async def test_fibonacci_pivots_relationships() -> None:
    await _seed_stock()
    result = await _calculate()
    assert result.fib_pivot is not None
    assert result.fib_r3 is not None
    assert result.fib_r2 >= result.fib_r1 >= result.fib_pivot
    assert result.fib_s3 <= result.fib_s2 <= result.fib_s1 <= result.fib_pivot


@pytest.mark.asyncio
async def test_relative_volume_and_delivery_strength() -> None:
    await _seed_stock()
    result = await _calculate()
    assert result.relative_volume is not None
    assert result.relative_volume > 0
    assert result.delivery_strength is not None
    assert result.delivery_strength > 0


@pytest.mark.asyncio
async def test_trending_flag_set_for_strong_stock() -> None:
    await _seed_stock(bullish=True)
    result = await _calculate()
    assert result.is_trending is True


@pytest.mark.asyncio
async def test_upsert_is_idempotent() -> None:
    await _seed_stock()
    first = await _calculate()
    second = await _calculate()
    assert first is not None and second is not None
    assert await _count_analytics() == 1
    assert second.overall_score == first.overall_score


@pytest.mark.asyncio
async def test_calculate_all_counts() -> None:
    await _seed_stock()
    factory = get_session_factory()
    async with factory() as session:
        engine = AnalyticsEngine(session)
        summary = await engine.calculate_all()
        await session.commit()
    assert summary["calculated"] == 1
    assert summary["skipped"] == 0


@pytest.mark.asyncio
async def test_calculate_for_unknown_stock_raises() -> None:
    factory = get_session_factory()
    async with factory() as session:
        engine = AnalyticsEngine(session)
        with pytest.raises(AnalyticsEngineError):
            await engine.calculate_for_stock("NOPE123")


@pytest.mark.asyncio
async def test_breakout_flag_when_price_above_resistance() -> None:
    await _seed_stock()
    factory = get_session_factory()
    async with factory() as session:
        stock = await session.scalar(select(Stock).where(Stock.symbol == "ANALYTX"))
        prices = list(
            await session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id == stock.id)
                .order_by(DailyPrice.trade_date.desc())
            )
        )
        latest = prices[0]
        latest.close = latest.close + 500
        latest.volume = 4000000
        await session.commit()

    result = await _calculate()
    assert result.is_breakout is True
    assert result.relative_volume is not None
    assert result.relative_volume >= 1.5


@pytest.mark.asyncio
async def test_get_analytics_and_list_methods() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        engine = AnalyticsEngine(session)
        found = await engine.get_analytics("ANALYTX")
        assert found is not None
        assert len(await engine.top_rated(limit=5)) == 1
        assert len(await engine.trending(limit=5)) == 1
        assert len(await engine.breakouts(limit=5)) >= 0


# ---------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_analytics_api_symbol() -> None:
    await _seed_stock()
    await _calculate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/analytics/ANALYTX")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "ANALYTX"
    assert "overall_score" in body
    assert "support" in body
    assert "pivot_r1" in body
    assert body["trend_rating"] is not None


@pytest.mark.asyncio
async def test_analytics_api_symbol_404() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/analytics/NOPE123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_analytics_api_top_rated() -> None:
    await _seed_stock()
    await _calculate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/analytics/top-rated")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["symbol"] == "ANALYTX"


@pytest.mark.asyncio
async def test_analytics_api_trending_and_breakouts() -> None:
    await _seed_stock()
    await _calculate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trending_response = await client.get("/api/v1/analytics/trending")
        breakout_response = await client.get("/api/v1/analytics/breakouts")
    assert trending_response.status_code == 200
    assert breakout_response.status_code == 200


# ---------------------------------------------------------------- pipeline


def _bhavcopy_bytes(trade_date: date, rows: int = 1) -> bytes:
    lines = [
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN"
    ]
    day = trade_date
    for i in range(rows):
        stamp = day.strftime("%d-%b-%Y").upper()
        base = 1500 + i * 5
        lines.append(
            f"RELIANCE,EQ,{base},{base + 30},{base - 10},{base + 20},{base + 19},{base},"
            f"100000,152000000,{stamp},5000,INE002A01018"
        )
        day -= timedelta(days=1)
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.asyncio
async def test_pipeline_runs_analytics_stage() -> None:
    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=5), bhavcopy_name="eod.csv"
    )
    assert run.status == "completed"
    assert run.analytics_calculated == 1


# ---------------------------------------------------------------- dashboard + detail


@pytest.mark.asyncio
async def test_dashboard_shows_top_rated() -> None:
    await _seed_stock()
    await _calculate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code == 200
    assert "Top Rated Stocks" in response.text
    assert "ANALYTX" in response.text


@pytest.mark.asyncio
async def test_stock_detail_shows_analytics() -> None:
    await _seed_stock()
    await _calculate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stocks/ANALYTX")
        partial = await client.get("/stocks/ANALYTX/analytics")
    assert response.status_code == 200
    assert "Technical Analytics" in response.text
    assert "Overall Technical Score" in response.text
    assert partial.status_code == 200
    assert "Overall Technical Score" in partial.text
