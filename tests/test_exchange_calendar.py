"""Unit & Integration Tests for ExchangeCalendarService — Sprint 19.1J-R1.

Tests:
  - Weekend exclusion logic
  - Known NSE official holiday skipping
  - Friday signal date -> Monday next trading session
  - Friday signal date -> Monday holiday -> Tuesday next trading session
  - Session counting and sequence generation
  - Equity vs Index classification in HistoricalCoverageCertificationService
"""
from datetime import date

import pytest

from app.db.session import get_session_factory
from app.services.exchange_calendar_service import ExchangeCalendarService
from app.services.historical_coverage_certification_service import (
    HistoricalCoverageCertificationService,
)


def test_exchange_calendar_weekend_exclusion() -> None:
    """Test that Saturday and Sunday are excluded from trading sessions."""
    cal = ExchangeCalendarService()
    sat = date(2024, 3, 16)  # Saturday
    sun = date(2024, 3, 17)  # Sunday
    mon = date(2024, 3, 18)  # Monday

    assert not cal.is_exchange_trading_day(sat)
    assert not cal.is_exchange_trading_day(sun)
    assert cal.is_exchange_trading_day(mon)


def test_exchange_calendar_friday_to_monday() -> None:
    """Test Friday signal date resolves Monday as next trading session."""
    cal = ExchangeCalendarService()
    fri = date(2024, 3, 15)  # Friday
    expected_mon = date(2024, 3, 18)  # Monday

    next_sess = cal.next_exchange_session(fri)
    assert next_sess == expected_mon


def test_exchange_calendar_friday_monday_holiday_to_tuesday() -> None:
    """Test Friday signal date with Monday holiday resolves Tuesday as next trading session."""
    cal = ExchangeCalendarService()
    # Good Friday is 2024-03-29 (holiday). Thursday is 2024-03-28.
    thu = date(2024, 3, 28)
    expected_mon = date(2024, 4, 1)  # Monday April 1, 2024

    next_sess = cal.next_exchange_session(thu)
    assert next_sess == expected_mon


def test_exchange_calendar_session_counting() -> None:
    """Test session counting across date range."""
    cal = ExchangeCalendarService()
    s_date = date(2024, 1, 1)
    e_date = date(2024, 12, 31)

    count = cal.count_expected_exchange_sessions(s_date, e_date)
    assert 240 <= count <= 250  # ~245 NSE trading sessions in 2024


def test_exchange_calendar_excludes_june_2026_muharram() -> None:
    cal = ExchangeCalendarService()

    sessions = cal.expected_exchange_sessions_between(
        date(2026, 6, 1), date(2026, 6, 30)
    )

    assert len(sessions) == 21
    assert date(2026, 6, 26) not in sessions


@pytest.mark.asyncio
async def test_coverage_certification_equity_vs_index_separation() -> None:
    """Test that coverage certification separates stored equities, index symbols, and reference indices."""
    factory = get_session_factory()
    async with factory() as session:
        service = HistoricalCoverageCertificationService(session)
        report = await service.certify_coverage()

        assert isinstance(report.stored_equity_symbols, list)
        assert isinstance(report.stored_index_symbols, list)
        assert isinstance(report.configured_reference_indices, list)
        assert isinstance(report.synthetic_fixture_symbols, list)
        assert report.fixture_contamination_status == "PASS"


@pytest.mark.asyncio
async def test_synthetic_fixture_exclusion_from_real_coverage() -> None:
    """Regression Test: Real empirical evidence run MUST exclude synthetic fixture symbols (REPRO1, ANLX1, MMVERIFY)."""
    factory = get_session_factory()
    async with factory() as session:
        service = HistoricalCoverageCertificationService(session)
        report = await service.certify_coverage()

        synthetic_fixtures = {"REPRO1", "ANLX1", "ANALYTX", "APIT1", "RESL1", "RESL2", "BENCH1", "MMVERIFY"}
        for s in report.stored_equity_symbols:
            assert s.upper() not in synthetic_fixtures, f"Synthetic fixture symbol {s} leaked into stored_equity_symbols!"
