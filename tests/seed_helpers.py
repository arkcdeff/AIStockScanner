from datetime import date, timedelta

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.sector import Sector
from app.models.stock import Stock
from app.services.analytics_engine import AnalyticsEngine


async def seed_stock(
    *,
    symbol: str = "SEEDX",
    close_base: float = 2500.0,
    delivery_pct: float = 55.0,
    bullish: bool = True,
    calculate_analytics: bool = True,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        sector = Sector(name=f"{symbol} Sector", description="Test")
        session.add(sector)
        await session.flush()

        stock = Stock(
            symbol=symbol,
            company_name=f"{symbol} Test Ltd",
            exchange="NSE",
            series="EQ",
            isin=f"INE{symbol}1",
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
        indicator = IndicatorResult(
            stock_id=stock.id,
            trade_date=latest_date,
            ema_20=ema_20,
            ema_50=ema_50,
            ema_100=close_base + 5,
            ema_200=close_base if bullish else close_base + 40,
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
        stock_id = stock.id

    if calculate_analytics:
        async with factory() as session:
            await AnalyticsEngine(session).calculate_for_stock(symbol)
            await session.commit()

    return stock_id


async def get_stock(symbol: str = "SEEDX") -> Stock:
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        return await session.scalar(select(Stock).where(Stock.symbol == symbol))


async def count_rows(model) -> int:
    from sqlalchemy import func, select

    factory = get_session_factory()
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0
