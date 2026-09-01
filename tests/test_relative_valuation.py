from datetime import date
from decimal import Decimal

import pytest

from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import FinancialStatement
from app.models.industry import Industry
from app.models.stock import Stock
from app.schemas.fundamental_corporate import PITValuationMetrics
from app.services.fundamental_corporate_service import FundamentalCorporateService


@pytest.mark.asyncio
async def test_relative_valuation_service(db_session):
    from app.models.sector import Sector
    
    # Create Sector
    sec = Sector(name="IT", description="IT")
    db_session.add(sec)
    await db_session.flush()

    # Create Industry
    ind = Industry(name="Software", sector_id=sec.id)
    db_session.add(ind)
    await db_session.flush()
    
    # Create main stock
    stock1 = Stock(symbol="MAIN", company_name="Main", exchange="NSE", industry_id=ind.id, sector_id=sec.id, is_active=True)
    db_session.add(stock1)
    
    # Create peer stock
    stock2 = Stock(symbol="PEER", company_name="Peer", exchange="NSE", industry_id=ind.id, sector_id=sec.id, is_active=True)
    db_session.add(stock2)
    await db_session.flush()
        
    # Add financials for peer
    stmt = FinancialStatement(
        stock_id=stock2.id,
        period_end=date(2026, 3, 31),
        period_type="ANNUAL",
        statement_basis="CONSOLIDATED",
        available_at=date(2026, 4, 15),
        eps=Decimal("10.0"),
        total_equity=Decimal("100.0"),
        shares_outstanding=Decimal("10000000"), # 1 Cr shares
        revenue=Decimal("500.0"),
        source_name="TEST"
    )
    db_session.add(stmt)
    
    # Add price for peer
    price = DailyPrice(
        stock_id=stock2.id,
        trade_date=date(2026, 8, 1),
        close=Decimal("200.0"), # P/E = 20, P/B = 2, P/S = 0.4
        open=Decimal("200.0"),
        high=Decimal("200.0"),
        low=Decimal("200.0"),
        volume=100
    )
    db_session.add(price)
    await db_session.commit()
    
    # Now test service
    svc = FundamentalCorporateService(db_session)
    
    stock_val = PITValuationMetrics(
        evaluation_date=date(2026, 8, 1),
        close_price=Decimal("150.0"),
        pe_ratio=Decimal("15.0"), # Main P/E = 15 (discount)
        pb_ratio=Decimal("3.0"),  # Main P/B = 3 (premium)
        ps_ratio=Decimal("0.5"),  # Main P/S = 0.5
        dividend_yield_pct=Decimal("1.0")
    )
    
    rel_val = await svc._calculate_relative_valuation(
        stock=stock1,
        evaluation_date=date(2026, 8, 1),
        as_of=date(2026, 8, 1),
        statement_basis="CONSOLIDATED",
        stock_valuation=stock_val
    )
    
    assert rel_val is not None
    assert rel_val.peer_group_type == "INDUSTRY"
    assert rel_val.peer_count == 1
    assert rel_val.median_pe == Decimal("20.0")
    assert rel_val.median_pb == Decimal("2.0")
    assert rel_val.median_ps == Decimal("0.4")
    
    # P/E premium = (15 - 20) / 20 = -25% (Attractive)
    assert rel_val.pe_premium_discount_pct == Decimal("-25.0")
    assert rel_val.valuation_classification == "ATTRACTIVE"
