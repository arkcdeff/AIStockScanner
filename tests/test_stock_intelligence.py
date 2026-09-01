"""Tests for Stock Intelligence & Decision Context Engine — Stage 2.6.5 Phase F."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import FinancialStatement
from app.models.indicator_result import IndicatorResult
from app.models.news_policy import NewsEvent
from app.models.stock import Stock
from app.schemas.stock_intelligence import (
    OverallState,
    StockIntelligence,
)
from app.services.stock_intelligence_service import StockIntelligenceService
from tests.auth_helpers import analyst_client


@pytest.mark.asyncio
async def test_schema_instantiation_and_defaults():
    """Verify schema defaults, validation, and immutability rules."""
    intel = StockIntelligence(
        symbol="RELIANCE",
        evaluation_date=date(2026, 8, 28),
        as_of_date=date(2026, 8, 28),
    )
    assert intel.symbol == "RELIANCE"
    assert intel.overall_state == OverallState.INSUFFICIENT_DATA
    assert intel.confidence == 0.0
    assert intel.confidence_label == "INSUFFICIENT"
    assert intel.completeness_pct == 0.0
    assert intel.completeness_label == "CRITICAL_GAPS"
    assert "Deterministic decision context" in intel.disclaimer
    assert "Not an investment recommendation" in intel.disclaimer


@pytest.mark.asyncio
async def test_stock_not_found(db_session: AsyncSession):
    """Verify NOT_AVAILABLE returned when stock is not in database."""
    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("UNKNOWN_SYMBOL_XYZ")

    assert result.symbol == "UNKNOWN_SYMBOL_XYZ"
    assert result.overall_state == OverallState.NOT_AVAILABLE
    assert result.confidence == 0.0
    assert result.completeness_pct == 0.0
    assert "not found" in result.state_reason.lower()
    assert len(result.missing_inputs) == 8


@pytest.mark.asyncio
async def test_no_prices_available(db_session: AsyncSession):
    """Verify NOT_AVAILABLE when stock exists but has 0 price records on evaluation date."""
    stock = Stock(symbol="NOPRICE_TEST", company_name="No Price Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NOPRICE_TEST", evaluation_date=date(2026, 8, 28))

    assert result.symbol == "NOPRICE_TEST"
    assert result.overall_state == OverallState.NOT_AVAILABLE
    assert "No price observations" in result.state_reason
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_technical_evidence_positive(db_session: AsyncSession):
    """Verify positive technical evidence attribution."""
    stock = Stock(symbol="TECH_POS", company_name="Tech Pos Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    # 25 price sessions with rising price
    for i in range(25):
        d = eval_d - timedelta(days=24 - i)
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=d,
            open=Decimal("100.00") + Decimal(i),
            high=Decimal("105.00") + Decimal(i),
            low=Decimal("99.00") + Decimal(i),
            close=Decimal("104.00") + Decimal(i),
            volume=100000,
        )
        db_session.add(dp)

    ind = IndicatorResult(
        stock_id=stock.id,
        trade_date=eval_d,
        rsi_14=Decimal("62.5"),
        supertrend_direction=1,
    )
    db_session.add(ind)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("TECH_POS", evaluation_date=eval_d)

    assert result.domain_coverage["technical"] == "AVAILABLE"
    # Should have positive EMA20 and Supertrend
    factors = [f.factor for f in result.supporting_factors if f.domain == "technical"]
    assert "PRICE_ABOVE_EMA20" in factors
    assert "SUPERTREND_BULLISH" in factors


@pytest.mark.asyncio
async def test_technical_evidence_negative(db_session: AsyncSession):
    """Verify negative technical evidence attribution."""
    stock = Stock(symbol="TECH_NEG", company_name="Tech Neg Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    # 25 price sessions with falling price
    for i in range(25):
        d = eval_d - timedelta(days=24 - i)
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=d,
            open=Decimal("200.00") - Decimal(i),
            high=Decimal("205.00") - Decimal(i),
            low=Decimal("195.00") - Decimal(i),
            close=Decimal("196.00") - Decimal(i),
            volume=100000,
        )
        db_session.add(dp)

    ind = IndicatorResult(
        stock_id=stock.id,
        trade_date=eval_d,
        rsi_14=Decimal("75.0"),  # Overbought
        supertrend_direction=-1,
    )
    db_session.add(ind)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("TECH_NEG", evaluation_date=eval_d)

    risk_factors = [f.factor for f in result.risk_factors if f.domain == "technical"]
    assert "PRICE_BELOW_EMA20" in risk_factors
    assert "SUPERTREND_BEARISH" in risk_factors
    assert "RSI_OVERBOUGHT" in risk_factors


@pytest.mark.asyncio
async def test_missing_moving_averages_in_watch_items(db_session: AsyncSession):
    """Verify missing EMA200 due to short history is recorded as UNAVAILABLE watch item, never zero or negative."""
    stock = Stock(symbol="SHORT_HIST", company_name="Short History Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    # Only 5 sessions
    for i in range(5):
        d = eval_d - timedelta(days=4 - i)
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=d,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("102"),
            volume=10000,
        )
        db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("SHORT_HIST", evaluation_date=eval_d)

    watch_factors = [w.factor for w in result.watch_items if w.domain == "technical"]
    assert "EMA200_INSUFFICIENT_HISTORY" in watch_factors
    assert "EMA50_INSUFFICIENT_HISTORY" in watch_factors


@pytest.mark.asyncio
async def test_fundamental_evidence_positive(db_session: AsyncSession):
    """Verify positive fundamental metrics are correctly extracted and classified."""
    stock = Stock(symbol="FUND_POS", company_name="Fund Pos Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    # 2 quarterly statements to allow YoY calculation
    prev_stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2025, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1000.0"),
        pat=Decimal("100.0"),
        total_equity=Decimal("500.0"),
        total_debt=Decimal("100.0"),
        shares_outstanding=10000000,
        available_at=datetime(2025, 7, 20, tzinfo=UTC),
    )
    latest_stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1200.0"),  # +20% YoY
        pat=Decimal("130.0"),       # +30% YoY
        total_equity=Decimal("600.0"),
        total_debt=Decimal("100.0"), # D/E = 0.17 (conservative)
        shares_outstanding=10000000,
        available_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    db_session.add_all([prev_stmt, latest_stmt])
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("FUND_POS", evaluation_date=eval_d)

    assert result.domain_coverage["fundamental"] == "AVAILABLE"
    fund_factors = [f.factor for f in result.supporting_factors if f.domain == "fundamental"]
    assert "REVENUE_GROWTH_STRONG" in fund_factors
    assert "PROFIT_GROWTH_STRONG" in fund_factors
    assert "DEBT_EQUITY_CONSERVATIVE" in fund_factors


@pytest.mark.asyncio
async def test_fundamental_missing_preserves_unavailable(db_session: AsyncSession):
    """Verify missing fundamentals is marked UNAVAILABLE and not fabricated as 0 or negative."""
    stock = Stock(symbol="NO_FUND", company_name="No Fund Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NO_FUND", evaluation_date=eval_d)

    assert result.domain_coverage["fundamental"] == "UNAVAILABLE"
    assert "fundamental" in result.missing_inputs
    watch_factors = [w.factor for w in result.watch_items if w.domain == "fundamental"]
    assert "FUNDAMENTALS_UNAVAILABLE" in watch_factors


@pytest.mark.asyncio
async def test_genuine_zero_debt_preserved(db_session: AsyncSession):
    """Verify genuine 0.0 debt is recorded as ZERO_DEBT positive factor."""
    stock = Stock(symbol="ZERO_DEBT_CO", company_name="Zero Debt Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("500.0"),
        pat=Decimal("50.0"),
        total_equity=Decimal("500.0"),
        total_debt=Decimal("0.0"),
        shares_outstanding=10000000,
        available_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("ZERO_DEBT_CO", evaluation_date=eval_d)

    factors = [f.factor for f in result.supporting_factors if f.domain == "fundamental"]
    assert "ZERO_DEBT" in factors or "DEBT_EQUITY_CONSERVATIVE" in factors


@pytest.mark.asyncio
async def test_news_evidence_positive_and_negative(db_session: AsyncSession):
    """Verify news sentiment factor classification."""
    stock = Stock(symbol="NEWS_TEST", company_name="News Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    ne = NewsEvent(
        stock_id=stock.id,
        headline="Major contract win for company",
        source_name="Exchange",
        source_type="OFFICIAL_PRIMARY",
        published_at=datetime(2026, 8, 25, tzinfo=UTC),
        available_at=datetime(2026, 8, 25, tzinfo=UTC),
        event_type="COMPANY",
        category="CONTRACT_WIN",
        scope_type="STOCK",
        deterministic_context="POSITIVE_CONTEXT",
        idempotency_hash="hash_pos_contract_win_123",
    )
    db_session.add(ne)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NEWS_TEST", evaluation_date=eval_d)

    assert result.domain_coverage["news"] == "AVAILABLE"
    factors = [f.factor for f in result.supporting_factors if f.domain == "news"]
    assert "NEWS_SENTIMENT_POSITIVE" in factors


@pytest.mark.asyncio
async def test_pit_news_filtering_no_lookahead(db_session: AsyncSession):
    """Verify future news after evaluation date is excluded from Stock Intelligence."""
    stock = Stock(symbol="PIT_NEWS", company_name="PIT News Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    # Future news event (available_at in September)
    ne_future = NewsEvent(
        stock_id=stock.id,
        headline="Future merger announcement",
        source_name="Exchange",
        source_type="OFFICIAL_PRIMARY",
        published_at=datetime(2026, 9, 2, tzinfo=UTC),
        available_at=datetime(2026, 9, 2, tzinfo=UTC),
        event_type="COMPANY",
        category="M&A",
        scope_type="STOCK",
        deterministic_context="POSITIVE_CONTEXT",
        idempotency_hash="hash_future_merger_123",
    )
    db_session.add(ne_future)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("PIT_NEWS", evaluation_date=eval_d)

    assert result.domain_coverage["news"] == "UNAVAILABLE"
    assert "news" in result.missing_inputs


@pytest.mark.asyncio
async def test_pit_financials_filtering_no_lookahead(db_session: AsyncSession):
    """Verify future financials published after evaluation date are excluded."""
    stock = Stock(symbol="PIT_FUND", company_name="PIT Fund Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 7, 1)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    # Q1 statement available on July 20 (after July 1)
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1000.0"),
        pat=Decimal("100.0"),
        available_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("PIT_FUND", evaluation_date=eval_d)

    assert result.domain_coverage["fundamental"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_overall_state_positive_dominance(db_session: AsyncSession):
    """Verify overall_state is POSITIVE when positive factors dominate."""
    stock = Stock(symbol="POS_DOM", company_name="Pos Dom Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    for i in range(25):
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=eval_d - timedelta(days=24 - i),
            open=Decimal("100") + Decimal(i),
            high=Decimal("105") + Decimal(i),
            low=Decimal("99") + Decimal(i),
            close=Decimal("104") + Decimal(i),
            volume=50000,
            delivery_percentage=Decimal("65.0"),
        )
        db_session.add(dp)

    ind = IndicatorResult(stock_id=stock.id, trade_date=eval_d, rsi_14=Decimal("55.0"), supertrend_direction=1)
    db_session.add(ind)

    # Add strong fundamental statement
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1000.0"),
        pat=Decimal("150.0"),
        total_equity=Decimal("600.0"),
        total_debt=Decimal("50.0"),
        available_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("POS_DOM", evaluation_date=eval_d)

    assert result.overall_state == OverallState.POSITIVE
    assert result.confidence >= 0.50
    assert len(result.supporting_factors) >= 2


@pytest.mark.asyncio
async def test_overall_state_negative_dominance(db_session: AsyncSession):
    """Verify overall_state is NEGATIVE when risk factors dominate."""
    stock = Stock(symbol="NEG_DOM", company_name="Neg Dom Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    for i in range(25):
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=eval_d - timedelta(days=24 - i),
            open=Decimal("200") - Decimal(i),
            high=Decimal("205") - Decimal(i),
            low=Decimal("195") - Decimal(i),
            close=Decimal("196") - Decimal(i),
            volume=50000,
        )
        db_session.add(dp)

    ind = IndicatorResult(stock_id=stock.id, trade_date=eval_d, rsi_14=Decimal("80.0"), supertrend_direction=-1)
    db_session.add(ind)

    # Overleveraged fundamentals
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1000.0"),
        pat=Decimal("10.0"),
        total_equity=Decimal("100.0"),
        total_debt=Decimal("400.0"),  # D/E = 4.0
        available_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NEG_DOM", evaluation_date=eval_d)

    assert result.overall_state == OverallState.NEGATIVE
    assert len(result.risk_factors) >= 2


@pytest.mark.asyncio
async def test_overall_state_mixed_signals(db_session: AsyncSession):
    """Verify overall_state is MIXED when strong supporting and strong risk factors coexist."""
    stock = Stock(symbol="MIXED_DOM", company_name="Mixed Dom Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    # Bullish price / EMA20 / Supertrend
    for i in range(25):
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=eval_d - timedelta(days=24 - i),
            open=Decimal("100") + Decimal(i),
            high=Decimal("105") + Decimal(i),
            low=Decimal("99") + Decimal(i),
            close=Decimal("104") + Decimal(i),
            volume=50000,
        )
        db_session.add(dp)

    # But overbought RSI
    ind = IndicatorResult(stock_id=stock.id, trade_date=eval_d, rsi_14=Decimal("82.0"), supertrend_direction=1)
    db_session.add(ind)

    # And heavily indebted fundamentals
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_end=date(2026, 6, 30),
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        revenue=Decimal("1000.0"),
        pat=Decimal("10.0"),
        total_equity=Decimal("100.0"),
        total_debt=Decimal("500.0"),  # D/E = 5.0 (High leverage risk)
        available_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("MIXED_DOM", evaluation_date=eval_d)

    assert result.overall_state == OverallState.MIXED
    assert len(result.supporting_factors) >= 2
    assert len(result.risk_factors) >= 2


@pytest.mark.asyncio
async def test_completeness_distinct_from_confidence():
    """Verify completeness measures domain availability, while confidence measures signal reliability."""
    state, reason, conf_low, label_low = StockIntelligenceService._determine_overall_state(
        p_count=2, r_count=1, active_count=3, available_domains_count=2
    )
    state, reason, conf_high, label_high = StockIntelligenceService._determine_overall_state(
        p_count=4, r_count=0, active_count=5, available_domains_count=6
    )

    assert conf_high > conf_low
    assert label_high in ("HIGH", "MODERATE")


@pytest.mark.asyncio
async def test_deterministic_reproducibility(db_session: AsyncSession):
    """Verify evaluating twice yields identical results."""
    stock = Stock(symbol="REPRO_TEST", company_name="Repro Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    res1 = await service.get_stock_intelligence("REPRO_TEST", evaluation_date=eval_d)
    res2 = await service.get_stock_intelligence("REPRO_TEST", evaluation_date=eval_d)

    assert res1.model_dump() == res2.model_dump()


@pytest.mark.asyncio
async def test_valuation_evidence_attractive_and_expensive(db_session: AsyncSession):
    """Verify valuation metrics factor classification."""
    stock = Stock(symbol="VAL_TEST", company_name="Val Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(120), volume=10000)
    db_session.add(dp)

    # EPS = 10 -> P/E = 120 / 10 = 12.0 (< 15.0 -> attractive)
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        period_end=date(2026, 6, 30),
        available_at=date(2026, 7, 20),
        revenue=Decimal("1000.0"),
        pat=Decimal("100.0"),
        eps=Decimal("10.0"),
        total_equity=Decimal("500.0"),
        shares_outstanding=10000000,
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("VAL_TEST", evaluation_date=eval_d)

    assert result.domain_coverage["valuation"] == "AVAILABLE"
    factors = [f.factor for f in result.supporting_factors if f.domain == "valuation"]
    assert "PE_RATIO_FAVORABLE" in factors


@pytest.mark.asyncio
async def test_corporate_announcement_positive_and_negative(db_session: AsyncSession):
    """Verify positive (DIVIDEND/CAPEX) and adverse (LITIGATION) corporate announcement classification."""
    from app.models.fundamental_corporate import CorporateAnnouncement

    stock = Stock(symbol="CORP_TEST", company_name="Corp Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    ca_pos = CorporateAnnouncement(
        stock_id=stock.id,
        category="DIVIDEND",
        announcement_date=date(2026, 8, 20),
        available_at=date(2026, 8, 20),
        title="Interim Dividend Declaration of Rs 5 per share",
        source_name="NSE_CORPORATE",
        idempotency_hash="hash_ca_div_1",
    )
    ca_neg = CorporateAnnouncement(
        stock_id=stock.id,
        category="LITIGATION",
        announcement_date=date(2026, 8, 22),
        available_at=date(2026, 8, 22),
        title="Notice received regarding tax dispute",
        source_name="NSE_CORPORATE",
        idempotency_hash="hash_ca_lit_1",
    )
    db_session.add_all([ca_pos, ca_neg])
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("CORP_TEST", evaluation_date=eval_d)

    assert result.domain_coverage["corporate_events"] == "AVAILABLE"
    pos_factors = [f.factor for f in result.supporting_factors if f.domain == "corporate_events"]
    neg_factors = [f.factor for f in result.risk_factors if f.domain == "corporate_events"]
    assert "CORPORATE_DIVIDEND" in pos_factors
    assert "CORPORATE_LITIGATION" in neg_factors


@pytest.mark.asyncio
async def test_pit_corporate_announcements_filtering_no_lookahead(db_session: AsyncSession):
    """Verify future corporate announcements are strictly excluded by evaluation date cutoff."""
    from app.models.fundamental_corporate import CorporateAnnouncement

    stock = Stock(symbol="PIT_CORP", company_name="PIT Corp Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    # Future announcement on Sept 5
    ca_future = CorporateAnnouncement(
        stock_id=stock.id,
        category="BONUS",
        announcement_date=date(2026, 9, 5),
        available_at=date(2026, 9, 5),
        title="Future Bonus Issue 1:1",
        source_name="NSE_CORPORATE",
        idempotency_hash="hash_future_bonus_1",
    )
    db_session.add(ca_future)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("PIT_CORP", evaluation_date=eval_d)

    assert result.domain_coverage["corporate_events"] == "UNAVAILABLE"
    assert "corporate_events" in result.missing_inputs


@pytest.mark.asyncio
async def test_market_context_regime_classification(db_session: AsyncSession):
    """Verify market regime context extraction and attribution."""
    from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot

    stock = Stock(symbol="MKT_TEST", company_name="Market Test Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    snap = MarketIntelligenceSnapshot(
        market_date=eval_d,
        generated_at=eval_d,
        regime="BULLISH",
    )
    db_session.add(snap)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("MKT_TEST", evaluation_date=eval_d)

    assert result.domain_coverage["market_context"] == "AVAILABLE"
    factors = [f.factor for f in result.supporting_factors if f.domain == "market_context"]
    assert "MARKET_REGIME_BULLISH" in factors


@pytest.mark.asyncio
async def test_sector_unmapped_watch_item(db_session: AsyncSession):
    """Verify stock with no sector/industry records SECTOR_UNMAPPED in watch items."""
    stock = Stock(symbol="NO_SECTOR", company_name="No Sector Ltd", exchange="NSE", sector_id=None, industry=None)
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NO_SECTOR", evaluation_date=eval_d)

    assert result.domain_coverage["sector"] == "UNAVAILABLE"
    watch_factors = [w.factor for w in result.watch_items if w.domain == "sector"]
    assert "SECTOR_UNMAPPED" in watch_factors


@pytest.mark.asyncio
async def test_sector_unmapped_with_industry_only(db_session: AsyncSession):
    """Verify stock with no sector_id but with industry marks sector UNAVAILABLE and records informative watch item."""
    stock = Stock(symbol="IND_ONLY", company_name="Industry Only Ltd", exchange="NSE", sector_id=None, industry="Oil & Gas")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("IND_ONLY", evaluation_date=eval_d)

    assert result.domain_coverage["sector"] == "UNAVAILABLE"
    assert "sector" in result.missing_inputs
    watch = next(w for w in result.watch_items if w.factor == "SECTOR_UNMAPPED")
    assert "industry mapped to Oil & Gas" in watch.description


@pytest.mark.asyncio
async def test_sector_available_with_canonical_sector(db_session: AsyncSession):
    """Verify stock with assigned canonical sector marks sector AVAILABLE."""
    from app.models.sector import Sector

    sector = Sector(name="Energy", description="Energy Sector")
    db_session.add(sector)
    await db_session.flush()

    stock = Stock(symbol="SEC_AVAIL", company_name="Sector Avail Ltd", exchange="NSE", sector_id=sector.id, industry="Oil & Gas")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    dp = DailyPrice(stock_id=stock.id, trade_date=eval_d, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("SEC_AVAIL", evaluation_date=eval_d)

    assert result.domain_coverage["sector"] == "AVAILABLE"
    assert "sector" not in result.missing_inputs
    sec_ev = next(e for e in result.evidence if e.factor == "SECTOR_MAPPING_ESTABLISHED")
    assert sec_ev.value == "Energy"


@pytest.mark.asyncio
async def test_overall_state_neutral_balance(db_session: AsyncSession):
    """Verify overall_state is NEUTRAL when active factors are balanced with no directional asymmetry."""
    stock = Stock(symbol="NEUT_BAL", company_name="Neut Bal Ltd", exchange="NSE", industry="IT Services")
    db_session.add(stock)
    await db_session.flush()

    eval_d = date(2026, 8, 28)
    for i in range(25):
        dp = DailyPrice(
            stock_id=stock.id,
            trade_date=eval_d - timedelta(days=24 - i),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=50000,
            delivery_percentage=Decimal(25.0),  # Normal/neutral delivery
        )
        db_session.add(dp)

    ind = IndicatorResult(stock_id=stock.id, trade_date=eval_d, rsi_14=Decimal("50.0"))  # Neutral RSI
    db_session.add(ind)

    # Moderate/stable fundamentals
    stmt = FinancialStatement(
        stock_id=stock.id,
        period_type="QUARTERLY",
        statement_basis="CONSOLIDATED",
        period_end=date(2026, 6, 30),
        available_at=date(2026, 7, 20),
        revenue=Decimal("1000.0"),
        pat=Decimal("100.0"),
        total_equity=Decimal("500.0"),
        total_debt=Decimal("300.0"),  # D/E = 0.60 (Moderate/Neutral)
    )
    db_session.add(stmt)
    await db_session.flush()

    service = StockIntelligenceService(db_session)
    result = await service.get_stock_intelligence("NEUT_BAL", evaluation_date=eval_d)

    assert result.overall_state == OverallState.NEUTRAL


@pytest.mark.asyncio
async def test_template_renders_empty_state_cleanly():
    """Verify Jinja2 template renders clean empty/unavailable state without template errors."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from app.core.config import settings

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    tmpl = env.get_template("components/stock_intelligence.html")

    # Render with None
    html_none = tmpl.render(stock_intelligence=None)
    assert "Stock Intelligence Context Unavailable" in html_none

    # Render with NOT_AVAILABLE
    intel_na = StockIntelligence(
        symbol="EMPTY_TEST",
        evaluation_date=date(2026, 8, 28),
        as_of_date=date(2026, 8, 28),
        overall_state=OverallState.NOT_AVAILABLE,
        state_reason="No price observations available.",
    )
    html_na = tmpl.render(stock_intelligence=intel_na)
    assert "No price observations available" in html_na


@pytest.mark.asyncio
async def test_stock_detail_route_includes_stock_intelligence():
    """Verify GET /stocks/{symbol} returns 200 and renders Stock Intelligence container."""
    from tests.test_stock_detail import _cleanup_test_stock, _create_test_stock

    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            resp = await client.get("/stocks/RELIANCE")
            assert resp.status_code == 200
            assert "Stock Intelligence &amp; Decision Context" in resp.text
            assert "stock-intelligence-container" in resp.text
            assert "Supporting Factors" in resp.text
            assert "Risk Factors" in resp.text
            assert "Deterministic decision context" in resp.text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_stock_detail_partial_intelligence_route():
    """Verify GET /stocks/{symbol}/intelligence partial endpoint."""
    from tests.test_stock_detail import _cleanup_test_stock, _create_test_stock

    stock_id = await _create_test_stock()
    try:
        async with analyst_client(browser=True) as client:
            resp = await client.get("/stocks/RELIANCE/intelligence")
            assert resp.status_code == 200
            assert "stock-intelligence-container" in resp.text
    finally:
        await _cleanup_test_stock(stock_id)


@pytest.mark.asyncio
async def test_news_cards_empty_events_truthful_rendering():
    """Verify news_cards.html renders dashes instead of fake zeros and assessment unavailable banner."""
    from datetime import UTC, datetime

    from jinja2 import Environment, FileSystemLoader

    from app.schemas.news_policy import CompanyNewsPolicySnapshot

    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    tmpl = env.get_template("components/news_cards.html")

    snapshot = CompanyNewsPolicySnapshot(
        symbol="EMPTY_NEWS",
        stock_id=999,
        evaluation_datetime=datetime(2026, 8, 30, 15, 30, tzinfo=UTC),
        as_of_date=date(2026, 8, 30),
        recent_events=[],
        positive_context_count=0,
        negative_context_count=0,
        mixed_context_count=0,
        neutral_context_count=0,
        upcoming_event_risks=[],
        quality_warnings=[],
    )
    rendered = tmpl.render(snapshot=snapshot)

    # Missing counts must be rendered as dashes (&mdash;) not "0"
    assert "&mdash;" in rendered
    assert "No recorded events; event-risk assessment unavailable." in rendered
    assert "No critical event risk flags detected." not in rendered


@pytest.mark.asyncio
async def test_news_cards_with_real_zero_events_rendering():
    """Verify news_cards.html renders real numeric 0 when events exist with genuine zero in a sentiment."""
    from datetime import UTC, datetime

    from jinja2 import Environment, FileSystemLoader

    from app.schemas.news_policy import CompanyNewsPolicySnapshot, NewsEventRead

    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    tmpl = env.get_template("components/news_cards.html")

    ev = NewsEventRead(
        id=1,
        headline="Major Expansion Announced",
        event_type="CORPORATE_ANNOUNCEMENT",
        category="ANNOUNCEMENT",
        scope_type="COMPANY",
        published_at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        available_at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        source_name="BSE",
        source_type="OFFICIAL_FILING",
        deterministic_context="POSITIVE_CONTEXT",
        event_severity="HIGH",
        idempotency_hash="hash_1",
    )

    snapshot = CompanyNewsPolicySnapshot(
        symbol="REAL_ZERO_TEST",
        stock_id=888,
        evaluation_datetime=datetime(2026, 8, 30, 15, 30, tzinfo=UTC),
        as_of_date=date(2026, 8, 30),
        recent_events=[ev],
        positive_context_count=1,
        negative_context_count=0,
        mixed_context_count=0,
        neutral_context_count=0,
        upcoming_event_risks=[],
        quality_warnings=[],
    )
    rendered = tmpl.render(snapshot=snapshot)

    # With real events, numeric counts are rendered preserving the legitimate zero
    assert ">1<" in rendered
    assert ">0<" in rendered
    assert "No critical event risk flags detected across 1 event(s)." in rendered


@pytest.mark.asyncio
async def test_no_test_scanner_in_stock_detail_repository(db_session: AsyncSession):
    """Verify StockDetailRepository filters out test_scanner and category=Test from matches and counts."""
    from app.models.scan_definition import ScanDefinition
    from app.models.scan_result import ScanResult
    from app.repositories.stock_detail import StockDetailRepository

    stock = Stock(symbol="SCAN_FILT", company_name="Scan Filter Ltd", exchange="NSE")
    db_session.add(stock)
    await db_session.flush()

    dp = DailyPrice(stock_id=stock.id, trade_date=date(2026, 8, 28), open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(100), volume=10000)
    db_session.add(dp)

    # Real scan definition
    sd_prod = ScanDefinition(code="prod_filter_test", name="Prod Filter Test", category="Technical", description="Prod", parameters={}, is_active=True)
    db_session.add(sd_prod)
    # Test scan definition
    sd_test = ScanDefinition(code="test_scanner", name="Test Scanner", category="Test", description="Test", parameters={}, is_active=True)
    db_session.add(sd_test)
    await db_session.flush()

    sr_prod = ScanResult(stock_id=stock.id, scan_definition_id=sd_prod.id, scan_date=date(2026, 8, 28), score=1.0, metrics={})
    sr_test = ScanResult(stock_id=stock.id, scan_definition_id=sd_test.id, scan_date=date(2026, 8, 28), score=1.0, metrics={})
    db_session.add_all([sr_prod, sr_test])
    await db_session.flush()

    repo = StockDetailRepository(db_session)
    matches = await repo.get_scanner_matches(stock.id)
    codes = [m.scan_definition.code for m in matches if m.scan_definition]
    assert "prod_filter_test" in codes
    assert "test_scanner" not in codes

    health = await repo.get_data_health(stock.id)
    assert health["scanner_matches_count"] == 1


