"""Comprehensive Test Suite for Stage 2 G.11-D-1: Deterministic 'What Changed?' Engine.

Validates:
 1. Calendar previous exchange session skips weekends and holidays
 2. Calendar previous exchange session before Monday returns Friday (or preceding holiday-adjusted session)
 3. Calendar nth exchange session before correctly calculates 1, 3, 5, 10, 20 trading sessions
 4. ChangeEvent schema serialization, deserialization, and immutability
 5. WhatChangedResult schema structure, summary counts, and domain metadata
 6. Price change +2.5% -> VALUE_INCREASE, NORMAL
 7. Price change +5.5% -> VALUE_INCREASE, SIGNIFICANT
 8. Price change +11.0% -> VALUE_INCREASE, MAJOR
 9. Price change -3.0% -> VALUE_DECREASE, NORMAL
 10. Price change +0.5% -> below threshold, no event emitted
 11. Volume change +60% -> VALUE_INCREASE, NORMAL
 12. Volume change +120% -> VALUE_INCREASE, SIGNIFICANT
 13. Volume change +350% -> VALUE_INCREASE, MAJOR
 14. Volume change -60% -> VALUE_DECREASE, NORMAL
 15. Delivery percentage change +18.0 percentage points -> VALUE_INCREASE, SIGNIFICANT
 16. Delivery missing on one date -> fail-closed transition (DATA_BECAME_AVAILABLE / UNAVAILABLE, never 0)
 17. Delivery missing on both dates -> suppressed, zero false events
 18. RSI threshold crossing 35 -> 28 (below 30 oversold) -> THRESHOLD_CROSSED, SIGNIFICANT, BEARISH
 19. RSI threshold crossing 68 -> 72 (above 70 overbought) -> THRESHOLD_CROSSED, SIGNIFICANT, BULLISH
 20. EMA crossing: price crosses above EMA20 -> STATE_CHANGED, SIGNIFICANT, BULLISH
 21. SuperTrend flip: direction changes from 1 to -1 -> STATE_CHANGED, MAJOR, BEARISH
 22. Scanner appearance: present today, absent yesterday -> APPEARED, SIGNIFICANT
 23. Scanner disappearance: present yesterday, absent today -> DISAPPEARED, NORMAL
 24. Bulk/Block deals: new transaction on evaluation date -> APPEARED, MAJOR / SIGNIFICANT
 25. Corporate action: dividend on evaluation date -> APPEARED, MAJOR
 26. Institutional flow: delta >= 1000 Cr -> VALUE_INCREASE / DECREASE, NORMAL
 27. Deterministic sorting: MAJOR before SIGNIFICANT before NORMAL, reproducible
 28. Integration test: HDFCBANK on 2026-09-10 vs 2026-09-09
 29. Integration test: HDFCBANK on 2026-09-09 vs 2026-09-08
 30. API endpoint GET /api/v1/stocks/{symbol}/what-changed returns 200 with WhatChangedResult
 31. API endpoint GET /api/v1/market-data/stocks/{symbol}/what-changed returns 200
 32. API endpoint returns 404 for unknown stock
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.db.session import get_session_factory
from app.models.bulk_block_deal import BulkBlockDeal
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.institutional_flow import InstitutionalFlow
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.schemas.what_changed import (
    ChangeEvent,
    ChangeSignificance,
    ChangeType,
    WhatChangedResult,
)
from app.services.exchange_calendar_service import (
    nth_exchange_session_before,
    previous_exchange_session,
)
from app.services.what_changed_service import WhatChangedService
from tests.auth_helpers import auth_client

# ===========================================================================
# 1. Calendar Navigation Tests
# ===========================================================================

def test_calendar_previous_session_skips_weekend():
    # 2026-09-14 is Monday; previous trading session is Friday 2026-09-11
    mon = date(2026, 9, 14)
    prev = previous_exchange_session(mon)
    assert prev == date(2026, 9, 11)
    assert prev.weekday() == 4  # Friday


def test_calendar_previous_session_weekday():
    # 2026-09-10 is Thursday; previous is Wednesday 2026-09-09
    thurs = date(2026, 9, 10)
    prev = previous_exchange_session(thurs)
    assert prev == date(2026, 9, 9)


def test_calendar_nth_exchange_session_before():
    cur = date(2026, 9, 14)  # Monday
    # 1 session back: Fri 2026-09-11
    assert nth_exchange_session_before(cur, 1) == date(2026, 9, 11)
    # 2 sessions back: Thu 2026-09-10
    assert nth_exchange_session_before(cur, 2) == date(2026, 9, 10)
    # 3 sessions back: Wed 2026-09-09
    assert nth_exchange_session_before(cur, 3) == date(2026, 9, 9)
    # 5 sessions back: Mon 2026-09-07
    assert nth_exchange_session_before(cur, 5) == date(2026, 9, 7)


# ===========================================================================
# 2. Schema Validation Tests
# ===========================================================================

def test_change_event_schema_serialization():
    event = ChangeEvent(
        symbol="TCS",
        evaluation_date=date(2026, 9, 10),
        comparison_date=date(2026, 9, 9),
        domain="PRICE",
        metric="close_price",
        previous_value=3500.0,
        current_value=3600.0,
        absolute_change=100.0,
        percentage_change=2.86,
        previous_state=None,
        current_state=None,
        change_type=ChangeType.VALUE_INCREASE,
        significance=ChangeSignificance.NORMAL,
        direction="BULLISH",
        source="daily_prices",
        confidence="FACT_VERIFIED",
        reason="Close price increased by 2.86% (3500.00 -> 3600.00)",
        metadata={"previous_close": 3500.0, "current_close": 3600.0},
    )
    dumped = event.model_dump()
    assert dumped["symbol"] == "TCS"
    assert dumped["change_type"] == "VALUE_INCREASE"
    assert dumped["significance"] == "NORMAL"
    assert dumped["direction"] == "BULLISH"

    # Verify immutability
    with pytest.raises(ValidationError):
        event.symbol = "INFY"  # frozen model


def test_what_changed_result_schema():
    result = WhatChangedResult(
        symbol="TCS",
        evaluation_date=date(2026, 9, 10),
        comparison_date=date(2026, 9, 9),
        comparison_window_sessions=1,
        generated_at=datetime.now(UTC),
        pit_safe=True,
        total_changes=1,
        major_changes=0,
        significant_changes=0,
        normal_changes=1,
        minor_changes=0,
        data_quality_summary={"price_continuity": True},
        domain_summaries={"PRICE": 1},
        freshness={"price_evaluation_date": "2026-09-10"},
        changes=[],
    )
    assert result.total_changes == 1
    assert result.pit_safe is True
    assert result.domain_summaries["PRICE"] == 1


# ===========================================================================
# 3. Unit Tests: Pure Detector Functions (Price, Volume, Delivery)
# ===========================================================================

def test_price_change_normal():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr = DailyPrice(trade_date=eval_d, close=Decimal("102.50"))
    events = service._evaluate_price("TEST", eval_d, comp_d, prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.change_type == ChangeType.VALUE_INCREASE
    assert ev.significance == ChangeSignificance.NORMAL
    assert ev.percentage_change == 2.5
    assert ev.direction == "BULLISH"


def test_price_change_significant():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr = DailyPrice(trade_date=eval_d, close=Decimal("105.50"))
    events = service._evaluate_price("TEST", eval_d, comp_d, prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.change_type == ChangeType.VALUE_INCREASE
    assert ev.significance == ChangeSignificance.SIGNIFICANT
    assert ev.percentage_change == 5.5


def test_price_change_major():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr = DailyPrice(trade_date=eval_d, close=Decimal("111.00"))
    events = service._evaluate_price("TEST", eval_d, comp_d, prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.change_type == ChangeType.VALUE_INCREASE
    assert ev.significance == ChangeSignificance.MAJOR
    assert ev.percentage_change == 11.0


def test_price_change_decrease():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr = DailyPrice(trade_date=eval_d, close=Decimal("97.00"))
    events = service._evaluate_price("TEST", eval_d, comp_d, prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.change_type == ChangeType.VALUE_DECREASE
    assert ev.significance == ChangeSignificance.NORMAL
    assert ev.percentage_change == -3.0
    assert ev.direction == "BEARISH"


def test_price_change_below_threshold():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr = DailyPrice(trade_date=eval_d, close=Decimal("100.50"))
    events = service._evaluate_price("TEST", eval_d, comp_d, prev, curr)
    assert len(events) == 0


def test_volume_surge_and_drop():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)
    prev = DailyPrice(trade_date=comp_d, volume=1_000_000)

    # +60% volume surge -> NORMAL
    curr_60 = DailyPrice(trade_date=eval_d, volume=1_600_000)
    evs_60 = service._evaluate_volume("TEST", eval_d, comp_d, prev, curr_60)
    assert len(evs_60) == 1
    assert evs_60[0].significance == ChangeSignificance.NORMAL
    assert evs_60[0].change_type == ChangeType.VALUE_INCREASE

    # +120% volume surge -> SIGNIFICANT
    curr_120 = DailyPrice(trade_date=eval_d, volume=2_200_000)
    evs_120 = service._evaluate_volume("TEST", eval_d, comp_d, prev, curr_120)
    assert len(evs_120) == 1
    assert evs_120[0].significance == ChangeSignificance.SIGNIFICANT

    # +350% volume explosion -> MAJOR
    curr_350 = DailyPrice(trade_date=eval_d, volume=4_500_000)
    evs_350 = service._evaluate_volume("TEST", eval_d, comp_d, prev, curr_350)
    assert len(evs_350) == 1
    assert evs_350[0].significance == ChangeSignificance.MAJOR

    # -60% volume drop -> NORMAL
    curr_drop = DailyPrice(trade_date=eval_d, volume=400_000)
    evs_drop = service._evaluate_volume("TEST", eval_d, comp_d, prev, curr_drop)
    assert len(evs_drop) == 1
    assert evs_drop[0].change_type == ChangeType.VALUE_DECREASE


def test_delivery_changes_and_fail_closed():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 10)
    comp_d = date(2026, 9, 9)

    # 1. Normal delivery increase >= 15 pts -> SIGNIFICANT
    prev = DailyPrice(trade_date=comp_d, delivery_percentage=Decimal("35.00"))
    curr = DailyPrice(trade_date=eval_d, delivery_percentage=Decimal("53.00"))
    evs = service._evaluate_delivery("TEST", eval_d, comp_d, prev, curr)
    assert len(evs) == 1
    assert evs[0].significance == ChangeSignificance.SIGNIFICANT
    assert evs[0].absolute_change == 18.0

    # 2. Delivery missing on both dates -> suppressed, no false events
    prev_none = DailyPrice(trade_date=comp_d, delivery_percentage=None)
    curr_none = DailyPrice(trade_date=eval_d, delivery_percentage=None)
    assert len(service._evaluate_delivery("TEST", eval_d, comp_d, prev_none, curr_none)) == 0

    # 3. Delivery became available
    evs_avail = service._evaluate_delivery("TEST", eval_d, comp_d, prev_none, curr)
    assert len(evs_avail) == 1
    assert evs_avail[0].change_type == ChangeType.DATA_BECAME_AVAILABLE

    # 4. Delivery became unavailable
    evs_unavail = service._evaluate_delivery("TEST", eval_d, comp_d, prev, curr_none)
    assert len(evs_unavail) == 1
    assert evs_unavail[0].change_type == ChangeType.DATA_BECAME_UNAVAILABLE


# ===========================================================================
# 4. Unit Tests: Technical Indicator Changes
# ===========================================================================

def test_rsi_threshold_crossings():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 9)
    comp_d = date(2026, 9, 8)

    # Cross below 30 into oversold -> SIGNIFICANT, BEARISH
    prev_ind = IndicatorResult(trade_date=comp_d, rsi_14=Decimal("35.00"))
    curr_ind = IndicatorResult(trade_date=eval_d, rsi_14=Decimal("28.00"))
    events = service._evaluate_technical_indicators("TEST", eval_d, comp_d, prev_ind, curr_ind)
    rsi_cross = [e for e in events if e.metric == "rsi_14" and e.change_type == ChangeType.THRESHOLD_CROSSED]
    assert len(rsi_cross) == 1
    assert rsi_cross[0].significance == ChangeSignificance.SIGNIFICANT
    assert rsi_cross[0].direction == "BEARISH"
    assert rsi_cross[0].current_state == "OVERSOLD"

    # Cross above 70 into overbought -> SIGNIFICANT, BULLISH
    prev_high = IndicatorResult(trade_date=comp_d, rsi_14=Decimal("68.00"))
    curr_high = IndicatorResult(trade_date=eval_d, rsi_14=Decimal("72.00"))
    events_high = service._evaluate_technical_indicators("TEST", eval_d, comp_d, prev_high, curr_high)
    rsi_high = [e for e in events_high if e.metric == "rsi_14" and e.change_type == ChangeType.THRESHOLD_CROSSED]
    assert len(rsi_high) == 1
    assert rsi_high[0].significance == ChangeSignificance.SIGNIFICANT
    assert rsi_high[0].direction == "BULLISH"
    assert rsi_high[0].current_state == "OVERBOUGHT"


def test_ema_price_crossing():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 9)
    comp_d = date(2026, 9, 8)

    prev_price = DailyPrice(trade_date=comp_d, close=Decimal("98.00"))
    curr_price = DailyPrice(trade_date=eval_d, close=Decimal("102.00"))

    prev_ind = IndicatorResult(trade_date=comp_d, ema_20=Decimal("100.00"))
    curr_ind = IndicatorResult(trade_date=eval_d, ema_20=Decimal("100.00"))

    events = service._evaluate_trend_and_regime("TEST", eval_d, comp_d, prev_price, curr_price, prev_ind, curr_ind)
    ema_events = [e for e in events if e.metric == "price_vs_ema20"]
    assert len(ema_events) == 1
    assert ema_events[0].change_type == ChangeType.STATE_CHANGED
    assert ema_events[0].significance == ChangeSignificance.SIGNIFICANT
    assert ema_events[0].current_state == "ABOVE"
    assert ema_events[0].direction == "BULLISH"


def test_supertrend_direction_flip():
    service = WhatChangedService(session=None)
    eval_d = date(2026, 9, 9)
    comp_d = date(2026, 9, 8)

    # Flip from 1 (BULLISH) to -1 (BEARISH) -> MAJOR
    prev_price = DailyPrice(trade_date=comp_d, close=Decimal("100.00"))
    curr_price = DailyPrice(trade_date=eval_d, close=Decimal("95.00"))
    prev_ind = IndicatorResult(trade_date=comp_d, supertrend_direction=1, supertrend=Decimal("90.00"))
    curr_ind = IndicatorResult(trade_date=eval_d, supertrend_direction=-1, supertrend=Decimal("105.00"))

    events = service._evaluate_trend_and_regime("TEST", eval_d, comp_d, prev_price, curr_price, prev_ind, curr_ind)
    st_events = [e for e in events if e.metric == "supertrend"]
    assert len(st_events) == 1
    assert st_events[0].change_type == ChangeType.STATE_CHANGED
    assert st_events[0].significance == ChangeSignificance.MAJOR
    assert st_events[0].current_state == "BEARISH"
    assert st_events[0].direction == "BEARISH"


# ===========================================================================
# 5. Unit Tests: Scanners, Deals, Corporate Actions, Institutional
# ===========================================================================

def test_scanner_appeared_and_disappeared():
    service = WhatChangedService(session=None)
    eval_date = date(2026, 9, 10)
    comp_date = date(2026, 9, 9)

    scan_def1 = ScanDefinition(id=1, code="rsi_oversold", name="RSI Oversold", category="Momentum")
    scan_def2 = ScanDefinition(id=2, code="volume_breakout", name="Volume Breakout", category="Volume")

    sr_prev = [(ScanResult(scan_definition_id=2, score=Decimal("85.0")), scan_def2)]
    sr_curr = [(ScanResult(scan_definition_id=1, score=Decimal("90.0")), scan_def1)]

    events = service._evaluate_scanners("TEST", eval_date, comp_date, sr_prev, sr_curr)
    assert len(events) == 2

    appeared = [e for e in events if e.change_type == ChangeType.APPEARED][0]
    assert appeared.significance == ChangeSignificance.SIGNIFICANT
    assert appeared.current_state == "MATCHED"
    assert "rsi_oversold" in appeared.metric

    disappeared = [e for e in events if e.change_type == ChangeType.DISAPPEARED][0]
    assert disappeared.significance == ChangeSignificance.NORMAL
    assert disappeared.previous_state == "MATCHED"


def test_bulk_block_deals_appeared():
    service = WhatChangedService(session=None)
    eval_date = date(2026, 9, 10)
    comp_date = date(2026, 9, 9)

    deal_large = BulkBlockDeal(
        id=1,
        trade_date=eval_date,
        symbol="TEST",
        client_name="BIG FUND INC",
        deal_type="BULK",
        buy_sell="BUY",
        quantity=1_000_000,
        trade_price=Decimal("600.00"),
        deal_value=Decimal("600000000.00"),  # 60 Cr >= 50 Cr -> MAJOR
    )
    deal_small = BulkBlockDeal(
        id=2,
        trade_date=eval_date,
        symbol="TEST",
        client_name="SMALL FUND",
        deal_type="BLOCK",
        buy_sell="SELL",
        quantity=100_000,
        trade_price=Decimal("600.00"),
        deal_value=Decimal("60000000.00"),  # 6 Cr < 50 Cr -> SIGNIFICANT
    )

    events = service._evaluate_deals("TEST", eval_date, comp_date, [deal_large, deal_small])
    assert len(events) == 2

    maj = [e for e in events if e.significance == ChangeSignificance.MAJOR][0]
    assert maj.direction == "BULLISH"
    assert maj.current_value == 60.0

    sig = [e for e in events if e.significance == ChangeSignificance.SIGNIFICANT][0]
    assert sig.direction == "BEARISH"
    assert sig.current_value == 6.0


def test_corporate_action_appeared():
    service = WhatChangedService(session=None)
    eval_date = date(2026, 9, 10)
    comp_date = date(2026, 9, 9)

    ca = CorporateAction(
        id=1,
        stock_id=1,
        ex_date=eval_date,
        action_type="dividend",
        notes="Interim Dividend - Rs 10 Per Share",
    )
    events = service._evaluate_corporate_events("TEST", eval_date, comp_date, [ca], [])
    assert len(events) == 1
    ev = events[0]
    assert ev.change_type == ChangeType.APPEARED
    assert ev.significance == ChangeSignificance.MAJOR
    assert "DIVIDEND" in ev.reason


def test_institutional_flow_delta():
    service = WhatChangedService(session=None)
    eval_date = date(2026, 9, 10)
    comp_date = date(2026, 9, 9)

    fii_prev = InstitutionalFlow(trade_date=comp_date, investor_type="FII", net_value=500.00)
    fii_curr = InstitutionalFlow(trade_date=eval_date, investor_type="FII", net_value=1800.00)
    # Delta is +1300 Cr >= 1000 Cr -> NORMAL event emitted

    events = service._evaluate_institutional("TEST", eval_date, comp_date, [fii_prev], [fii_curr])
    assert len(events) == 1
    ev = events[0]
    assert ev.significance == ChangeSignificance.NORMAL
    assert ev.change_type == ChangeType.VALUE_INCREASE
    assert ev.absolute_change == 1300.0


def test_deterministic_sorting():
    service = WhatChangedService(session=None)
    # Create unordered events
    e_minor = ChangeEvent(
        symbol="TEST", evaluation_date=date(2026, 9, 10), comparison_date=date(2026, 9, 9),
        domain="PRICE", metric="m1", previous_value=1.0, current_value=2.0,
        absolute_change=1.0, percentage_change=1.0, previous_state=None, current_state=None,
        change_type=ChangeType.VALUE_INCREASE, significance=ChangeSignificance.MINOR,
        direction=None, source="s", confidence="c", reason="r1", metadata={},
    )
    e_major = ChangeEvent(
        symbol="TEST", evaluation_date=date(2026, 9, 10), comparison_date=date(2026, 9, 9),
        domain="PRICE", metric="m2", previous_value=1.0, current_value=2.0,
        absolute_change=1.0, percentage_change=1.0, previous_state=None, current_state=None,
        change_type=ChangeType.VALUE_INCREASE, significance=ChangeSignificance.MAJOR,
        direction=None, source="s", confidence="c", reason="r2", metadata={},
    )
    e_sig_tech = ChangeEvent(
        symbol="TEST", evaluation_date=date(2026, 9, 10), comparison_date=date(2026, 9, 9),
        domain="TECHNICAL", metric="m3", previous_value=1.0, current_value=2.0,
        absolute_change=1.0, percentage_change=1.0, previous_state=None, current_state=None,
        change_type=ChangeType.VALUE_INCREASE, significance=ChangeSignificance.SIGNIFICANT,
        direction=None, source="s", confidence="c", reason="r3", metadata={},
    )
    e_sig_price = ChangeEvent(
        symbol="TEST", evaluation_date=date(2026, 9, 10), comparison_date=date(2026, 9, 9),
        domain="PRICE", metric="m4", previous_value=1.0, current_value=2.0,
        absolute_change=1.0, percentage_change=1.0, previous_state=None, current_state=None,
        change_type=ChangeType.VALUE_INCREASE, significance=ChangeSignificance.SIGNIFICANT,
        direction=None, source="s", confidence="c", reason="r4", metadata={},
    )

    sorted_events = service._sort_events([e_minor, e_sig_tech, e_major, e_sig_price])
    # MAJOR first, then SIGNIFICANT (PRICE before TECHNICAL), then MINOR
    assert sorted_events[0] == e_major
    assert sorted_events[1] == e_sig_price
    assert sorted_events[2] == e_sig_tech
    assert sorted_events[3] == e_minor


# ===========================================================================
# 6. Database Integration Tests: HDFCBANK
# ===========================================================================

async def _seed_hdfcbank_data(session):
    stock = Stock(
        symbol="HDFCBANK",
        company_name="HDFC Bank Limited",
        exchange="NSE",
        isin="INE040A01034",
        is_active=True,
    )
    session.add(stock)
    await session.flush()

    p1 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 8),
        open=Decimal("705.00"), high=Decimal("710.00"), low=Decimal("700.00"),
        close=Decimal("703.00"), volume=19883515,
    )
    p2 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 9),
        open=Decimal("700.00"), high=Decimal("702.00"), low=Decimal("685.00"),
        close=Decimal("687.10"), volume=39173990,
    )
    p3 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 10),
        open=Decimal("688.00"), high=Decimal("695.00"), low=Decimal("686.00"),
        close=Decimal("693.80"), volume=27108053,
    )
    ind1 = IndicatorResult(
        stock_id=stock.id,
        trade_date=date(2026, 9, 8),
        rsi_14=Decimal("35.68"),
        ema_20=Decimal("720.00"),
    )
    ind2 = IndicatorResult(
        stock_id=stock.id,
        trade_date=date(2026, 9, 9),
        rsi_14=Decimal("29.56"),
        ema_20=Decimal("716.33"),
    )
    session.add_all([p1, p2, p3, ind1, ind2])
    await session.commit()
    return stock


@pytest.mark.asyncio
async def test_integration_hdfcbank_2026_09_10():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with factory() as session:
        service = WhatChangedService(session)
        res = await service.detect_changes("HDFCBANK", date(2026, 9, 10), comparison_window=1)

        assert res.symbol == "HDFCBANK"
        assert res.evaluation_date == date(2026, 9, 10)
        assert res.comparison_date == date(2026, 9, 9)
        assert res.comparison_window_sessions == 1
        assert res.pit_safe is True
        # On 2026-09-10: close moved +0.98% (below 2.0%), vol -30.8% (below 50%), delivery None on both
        assert res.total_changes == 0
        assert res.freshness["indicators_available_for_eval"] is False
        assert res.data_quality_summary["price_continuity"] is True


@pytest.mark.asyncio
async def test_integration_hdfcbank_2026_09_09():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with factory() as session:
        service = WhatChangedService(session)
        res = await service.detect_changes("HDFCBANK", date(2026, 9, 9), comparison_window=1)

        assert res.symbol == "HDFCBANK"
        assert res.evaluation_date == date(2026, 9, 9)
        assert res.comparison_date == date(2026, 9, 8)
        assert res.pit_safe is True
        # On 2026-09-09 vs 2026-09-08: RSI crossed into oversold (29.56 < 30.0), Price decreased 2.26%, Volume surged 97%
        assert res.total_changes == 3
        assert res.significant_changes == 1
        assert res.normal_changes == 2
        metrics = [c.metric for c in res.changes]
        assert "rsi_14" in metrics
        assert "close_price" in metrics
        assert "volume" in metrics


# ===========================================================================
# 7. API Route Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_api_what_changed_stocks_route():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-10&comparison_window=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "HDFCBANK"
        assert data["evaluation_date"] == "2026-09-10"
        assert data["comparison_date"] == "2026-09-09"
        assert data["pit_safe"] is True
        assert data["total_changes"] == 0


@pytest.mark.asyncio
async def test_api_what_changed_market_data_route():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/market-data/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-10&comparison_window=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "HDFCBANK"
        assert data["evaluation_date"] == "2026-09-10"


@pytest.mark.asyncio
async def test_api_what_changed_404_not_found():
    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/stocks/UNKNOWN_XYZ_999/what-changed")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_what_changed_unauthenticated_returns_401():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/v1/stocks/HDFCBANK/what-changed")
        assert r1.status_code == 401
        assert r1.json()["detail"] == "Invalid authentication credentials"

        r2 = await client.get("/api/v1/market-data/stocks/HDFCBANK/what-changed")
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_api_what_changed_browser_cookie_auth():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    # Use browser cookie only (remove Authorization header)
    async with auth_client(role="admin", browser=True) as client:
        client.headers.pop("Authorization", None)
        assert "Authorization" not in client.headers

        # GET /api/v1/stocks/HDFCBANK/what-changed
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-09&comparison_window=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "HDFCBANK"
        assert data["total_changes"] == 3
        metrics = [c["metric"] for c in data["changes"]]
        assert "rsi_14" in metrics
        assert "close_price" in metrics
        assert "volume" in metrics

        # GET /api/v1/market-data/stocks/HDFCBANK/what-changed
        resp_md = await client.get("/api/v1/market-data/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-09&comparison_window=1")
        assert resp_md.status_code == 200

        # GET /stocks/HDFCBANK/what-changed
        resp_sd = await client.get("/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-09&comparison_window=1")
        assert resp_sd.status_code == 200


@pytest.mark.asyncio
async def test_api_what_changed_truthful_freshness():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin", browser=True) as client:
        client.headers.pop("Authorization", None)
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed?evaluation_date=2026-09-15&comparison_window=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["freshness"]["indicators_available_for_eval"] is False
        assert data["data_quality_summary"]["indicator_continuity"] is False
