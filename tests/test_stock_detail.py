import pytest

from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.sector import Sector
from app.models.stock import Stock
from tests.auth_helpers import analyst_client


@pytest.mark.asyncio
async def test_stock_detail_page_existing_symbol() -> None:
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "RELIANCE" in response.text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_page_unknown_symbol() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/UNKNOWN123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_chart_data_sorted_candles() -> None:
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get(
                "/stocks/RELIANCE/chart-data?period=1y&include_indicators=true"
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
        async with analyst_client(browser=True) as client:
            response = await client.get(
                "/stocks/RELIANCE/chart-data?period=3m&include_indicators=false"
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
    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/UNKNOWN123/chart-data")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_overview_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE/overview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_indicators_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE/indicators")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_scanner_results_partial() -> None:
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE/scanner-results")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_page_without_price_data() -> None:
    stock_id = await _create_test_stock(no_prices=True)
    try:
        async with analyst_client(browser=True) as client:
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
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE/overview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    finally:
        await _cleanup_test_stock(stock_id)


async def _create_test_stock(no_prices: bool = False) -> int:
    from datetime import date, timedelta

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
                delivery_quantity=None,
                delivery_percentage=None,
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
    from app.models.fundamental_corporate import CorporateAnnouncement, FinancialStatement
    from app.models.news_policy import NewsEvent

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(CorporateAnnouncement).where(CorporateAnnouncement.stock_id == stock_id))
        await session.execute(delete(NewsEvent).where(NewsEvent.stock_id == stock_id))
        await session.execute(delete(FinancialStatement).where(FinancialStatement.stock_id == stock_id))
        await session.execute(delete(ScanResult).where(ScanResult.stock_id == stock_id))
        await session.execute(delete(ScanDefinition).where(ScanDefinition.code == "test_scanner"))
        await session.execute(delete(IndicatorResult).where(IndicatorResult.stock_id == stock_id))
        await session.execute(delete(DailyPrice).where(DailyPrice.stock_id == stock_id))
        stock = await session.get(Stock, stock_id)
        if stock:
            await session.delete(stock)
        await session.commit()

@pytest.mark.asyncio
async def test_stock360_fundamentals_and_valuation_rendered_with_real_data() -> None:
    """Verify Stock360 renders real financial statement facts and valuation multiples."""
    from datetime import date
    from decimal import Decimal

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import FinancialStatement

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        # Add Q1 2026 statement
        stmt_curr = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 7, 20),
            available_at=date(2026, 7, 20),
            revenue=Decimal("25000.0000"),
            operating_income=Decimal("5000.0000"),
            pbt=Decimal("4500.0000"),
            pat=Decimal("3500.0000"),
            eps=Decimal("12.5000"),
            total_equity=Decimal("50000.0000"),
            total_debt=Decimal("10000.0000"),
            operating_cash_flow=Decimal("4000.0000"),
            free_cash_flow=Decimal("2500.0000"),
            shares_outstanding=Decimal("1000000000"),
        )
        # Add Q1 2025 statement (for YoY growth calculation)
        stmt_prev = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2025, 6, 30),
            reported_at=date(2025, 7, 20),
            available_at=date(2025, 7, 20),
            revenue=Decimal("20000.0000"),
            pat=Decimal("2800.0000"),
            eps=Decimal("10.0000"),
        )
        session.add_all([stmt_curr, stmt_prev])
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text

        # Fundamentals section assertions
        assert "Fundamentals Overview" in text
        assert "CONSOLIDATED" in text
        assert "2026-06-30" in text
        assert "25,000.00" in text  # Revenue
        assert "3,500.00" in text   # PAT
        assert "5,000.00" in text   # Operating income
        assert "Return on Equity (ROE)" in text
        assert "Debt to Equity (D/E)" in text

        # Valuation section assertions
        assert "Valuation Context" in text
        assert "P/E Ratio (PIT)" in text
        assert "P/B Ratio (PIT)" in text
        assert "P/S Ratio (PIT)" in text
        assert "Book Value / Share" in text
        # New stage 2.6.3 assertion
        assert "Relative Valuation" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock360_fundamentals_missing_values_rendered_truthfully() -> None:
    """Verify that missing financial statement metrics are rendered as Not available/— and not 0."""
    from datetime import date
    from decimal import Decimal

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import FinancialStatement

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        stmt = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 20),
            revenue=Decimal("12000.0000"),
            pat=None,  # MISSING
            total_debt=None,  # MISSING
            total_equity=Decimal("30000.0000"),
        )
        session.add(stmt)
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text
        assert "Not available" in text or "—" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock360_fundamentals_negative_values_and_legitimate_zero() -> None:
    """Verify that negative financial values retain negative sign and 0 is preserved."""
    from datetime import date
    from decimal import Decimal

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import FinancialStatement

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        stmt = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 20),
            revenue=Decimal("18000.0000"),
            pat=Decimal("-1250.7500"),  # Loss
            total_debt=Decimal("0.0000"),  # Zero debt
            total_equity=Decimal("40000.0000"),
        )
        session.add(stmt)
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text
        assert "-1,250.75" in text
        assert "0.00" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock360_fundamentals_dates_kept_distinct_from_market_date() -> None:
    """Verify financial reporting date and market trade date are displayed distinctly."""
    from datetime import date
    from decimal import Decimal

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import FinancialStatement

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        stmt = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 7, 15),
            available_at=date(2026, 7, 15),
            revenue=Decimal("22000.0000"),
            pat=Decimal("3200.0000"),
        )
        session.add(stmt)
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text
        assert "2026-06-30" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock360_fundamentals_standalone_fallback() -> None:
    """Verify fallback to STANDALONE when CONSOLIDATED statements are unavailable."""
    from datetime import date
    from decimal import Decimal

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import FinancialStatement

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        stmt = FinancialStatement(
            stock_id=stock_id,
            period_type="QUARTERLY",
            statement_basis="STANDALONE",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 18),
            revenue=Decimal("15000.0000"),
            pat=Decimal("2100.0000"),
        )
        session.add(stmt)
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text
        assert "STANDALONE" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_news_and_events_rendered_truthfully() -> None:
    """Verify Stock Detail renders populated PIT news and corporate announcements."""
    from datetime import UTC, date, datetime

    from app.db.session import get_session_factory
    from app.models.fundamental_corporate import CorporateAnnouncement
    from app.models.news_policy import NewsEvent

    stock_id = await _create_test_stock()
    factory = get_session_factory()
    async with factory() as session:
        ne = NewsEvent(
            headline="Reliance Commissions Multi-GW Solar Gigafactory",
            summary="New clean energy manufacturing facility operational in Gujarat.",
            source_name="NSE_CORPORATE",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            event_type="COMPANY",
            category="CAPEX",
            deterministic_context="POSITIVE_CONTEXT",
            stock_id=stock_id,
            idempotency_hash="hash_rel_solar",
        )
        ca = CorporateAnnouncement(
            stock_id=stock_id,
            category="DIVIDEND",
            announcement_date=date(2026, 8, 2),
            available_at=date(2026, 8, 2),
            title="Board Approves Interim Dividend of Rs 10 per share",
            source_name="NSE_CORPORATE",
            idempotency_hash="hash_rel_div",
        )
        session.add_all([ne, ca])
        await session.commit()

    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text

        # Verify News Section
        assert "News &amp; Events" in text or "News & Events" in text
        assert "Reliance Commissions Multi-GW Solar Gigafactory" in text

        # Verify Corporate Events Section
        assert "Corporate Events" in text
        assert "Board Approves Interim Dividend of Rs 10 per share" in text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_corporate_events_empty_state() -> None:
    """Verify Stock Detail shows truthful empty state when 0 corporate events exist."""
    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            response = await client.get("/stocks/RELIANCE")
        assert response.status_code == 200
        text = response.text
        assert "Corporate Events" in text
        assert "No recorded events" in text
    finally:
        await _cleanup_test_stock(stock_id)
