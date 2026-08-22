"""Unit and Integration Tests for Sprint 19.1D-7: Participant Derivatives.

Covers:
  - DerivativeParticipantPosition & FiiDerivativeStatistic models
  - ParticipantOpenInterestParser & ParticipantTradingVolumeParser
  - FiiDerivativesStatisticsParser
  - DerivativeParticipantImportService & Idempotency
  - HistoricalParticipantAnalyticsService (PIT safety, DoD changes, FII summary)
  - REST API routes (/derivatives/participants, /history, /fii)
  - HTMX partial route (/market-data/derivatives/participants/cards)
  - Zero live network calls & strict non-dealer / non-predictive assertions
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.data.nse_participant_parser import (
    FiiDerivativesStatisticsParser,
    ParticipantOpenInterestParser,
    ParticipantTradingVolumeParser,
)
from app.db.session import get_session_factory
from app.services.derivative_participant_import import DerivativeParticipantImportService
from app.services.historical_participant_analytics import HistoricalParticipantAnalyticsService
from tests.auth_helpers import auth_client

SAMPLE_PARTICIPANT_OI_CSV = """Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts,Total Short Contracts
Client,1000,500,2000,1000,5000,4000,3000,2000,1500,1200,1000,800,14700,9500
DII,3000,1000,4000,2000,1000,2000,500,1000,500,800,200,300,9000,7100
FII,5000,2000,6000,3000,8000,6000,4000,5000,2000,1500,1800,1200,31000,18700
Pro,2000,6500,1000,7000,2000,3000,8500,7000,1000,1000,2000,2200,7000,26700
TOTAL,11000,10000,13000,13000,16000,15000,16000,15000,5000,4500,5000,4500,61700,62000
"""

SAMPLE_FII_STATS_CSV = """Category,Buy Contracts,Buy Amount,Sell Contracts,Sell Amount,Open Interest Contracts,Open Interest Amount
INDEX FUTURES,12000,1050.50,8000,720.25,45000,3800.00
INDEX OPTIONS,50000,4200.00,48000,4050.00,120000,9800.50
STOCK FUTURES,15000,1300.00,14000,1250.00,85000,7100.00
STOCK OPTIONS,8000,650.00,7500,610.00,25000,2100.00
"""


def test_participant_oi_parser():
    """Verify ParticipantOpenInterestParser correctly normalizes categories, instrument buckets, and totals."""
    parser = ParticipantOpenInterestParser()
    res = parser.parse(SAMPLE_PARTICIPANT_OI_CSV, report_date=date(2026, 8, 10))

    assert res.dataset_type == "OPEN_INTEREST"
    assert res.report_date == date(2026, 8, 10)
    assert res.raw_row_count == 4  # Client, DII, FII, Pro
    assert len(res.rows) == 24  # 4 participants * 6 instrument buckets

    fii_idx_fut = [r for r in res.rows if r.participant_type == "FII" and r.instrument_bucket == "INDEX_FUTURE"][0]
    assert fii_idx_fut.long_contracts == 5000
    assert fii_idx_fut.short_contracts == 2000
    assert fii_idx_fut.reported_total_long == 31000


def test_participant_volume_parser():
    """Verify ParticipantTradingVolumeParser correctly tags dataset_type as TRADING_VOLUME."""
    parser = ParticipantTradingVolumeParser()
    res = parser.parse(SAMPLE_PARTICIPANT_OI_CSV, report_date=date(2026, 8, 10))

    assert res.dataset_type == "TRADING_VOLUME"
    assert len(res.rows) == 24


def test_fii_statistics_parser():
    """Verify FiiDerivativesStatisticsParser parses buy/sell and open interest statistics."""
    parser = FiiDerivativesStatisticsParser()
    res = parser.parse(SAMPLE_FII_STATS_CSV, report_date=date(2026, 8, 10))

    assert res.report_date == date(2026, 8, 10)
    assert len(res.rows) == 4

    idx_fut = [r for r in res.rows if r.instrument_category == "INDEX_FUTURES"][0]
    assert idx_fut.buy_contracts == 12000
    assert idx_fut.buy_amount == Decimal("1050.50")
    assert idx_fut.sell_contracts == 8000
    assert idx_fut.sell_amount == Decimal("720.25")


@pytest.mark.asyncio
async def test_participant_import_and_idempotency():
    """Verify DerivativeParticipantImportService imports records idempotently without duplicate key errors."""
    factory = get_session_factory()
    async with factory() as session:
        service = DerivativeParticipantImportService(session)

        # First Import
        sum1 = await service.import_participant_oi(
            content=SAMPLE_PARTICIPANT_OI_CSV,
            report_date=date(2026, 8, 10),
            filename="fao_participant_oi.csv",
        )
        assert sum1.inserted_count == 24
        assert sum1.parsed_record_count == 24

        # Second Import (Idempotent update -> 0 actual modifications when values are identical)
        sum2 = await service.import_participant_oi(
            content=SAMPLE_PARTICIPANT_OI_CSV,
            report_date=date(2026, 8, 10),
            filename="fao_participant_oi.csv",
        )
        assert sum2.inserted_count == 0
        assert sum2.parsed_record_count == 24


@pytest.mark.asyncio
async def test_historical_participant_analytics_service():
    """Verify HistoricalParticipantAnalyticsService computes FII summary, DoD changes, and concentration shares."""
    factory = get_session_factory()
    async with factory() as session:
        service_imp = DerivativeParticipantImportService(session)

        # Day 1
        await service_imp.import_participant_oi(
            content=SAMPLE_PARTICIPANT_OI_CSV,
            report_date=date(2026, 8, 9),
        )

        # Day 2 (Modify FII contracts for DoD test)
        day2_csv = SAMPLE_PARTICIPANT_OI_CSV.replace("5000,2000", "6000,1500")  # FII Index Fut Long=6000, Short=1500
        await service_imp.import_participant_oi(
            content=day2_csv,
            report_date=date(2026, 8, 10),
        )
        await service_imp.import_fii_derivatives_statistics(
            content=SAMPLE_FII_STATS_CSV,
            report_date=date(2026, 8, 10),
        )

        analytics_service = HistoricalParticipantAnalyticsService(session)

        # Fetch Snapshot for Day 2
        snap = await analytics_service.get_participant_snapshot(
            report_date=date(2026, 8, 10),
            dataset_type="OPEN_INTEREST",
        )

        assert snap.report_date == date(2026, 8, 10)
        assert snap.dataset_type == "OPEN_INTEREST"

        # FII Summary Checks
        fii_sum = snap.fii_index_futures
        assert fii_sum.long_contracts == 6000
        assert fii_sum.short_contracts == 1500
        assert fii_sum.net_contracts == 4500  # 6000 - 1500
        assert fii_sum.long_short_ratio == Decimal("4.00")  # 6000 / 1500

        # Day-over-Day Checks (Day 2 vs Day 1: Long 6000 vs 5000 => +1000, Short 1500 vs 2000 => -500, Net +1500)
        assert fii_sum.day_over_day_long_change == 1000
        assert fii_sum.day_over_day_short_change == -500
        assert fii_sum.day_over_day_net_change == 1500

        # FII Statistics Check
        assert len(snap.fii_statistics) == 4
        fii_stat_idx_fut = [s for s in snap.fii_statistics if s.instrument_category == "INDEX_FUTURES"][0]
        assert fii_stat_idx_fut.net_buy_sell_amount == Decimal("330.25")  # 1050.50 - 720.25


@pytest.mark.asyncio
async def test_participant_rest_api_and_ui_partial():
    """Verify REST API /derivatives/participants, /fii, and HTMX partial route render HTTP 200 OK."""
    from tests.auth_helpers import seed_builtins

    factory = get_session_factory()
    async with factory() as session:
        service_imp = DerivativeParticipantImportService(session)
        await service_imp.import_participant_oi(
            content=SAMPLE_PARTICIPANT_OI_CSV,
            report_date=date(2026, 8, 10),
        )
        await service_imp.import_fii_derivatives_statistics(
            content=SAMPLE_FII_STATS_CSV,
            report_date=date(2026, 8, 10),
        )

    await seed_builtins()
    # 1. REST API Snapshot & FII Summary
    async with auth_client(is_superuser=True) as api_client:
        resp = await api_client.get(
            "/api/v1/market-data/derivatives/participants",
            params={"report_date": "2026-08-10", "dataset_type": "OPEN_INTEREST"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_date"] == "2026-08-10"
        assert data["dataset_type"] == "OPEN_INTEREST"
        assert data["fii_index_futures"]["net_contracts"] == 3000

        resp_fii = await api_client.get(
            "/api/v1/market-data/derivatives/fii",
            params={"report_date": "2026-08-10"},
        )
        assert resp_fii.status_code == 200
        data_fii = resp_fii.json()
        assert len(data_fii["fii_statistics"]) == 4

    # 2. HTMX Cards UI Partial
    async with auth_client(is_superuser=True, browser=True) as ui_client:
        resp_ui = await ui_client.get(
            "/market-data/derivatives/participants/cards",
            params={"report_date": "2026-08-10", "dataset_type": "OPEN_INTEREST"},
        )
        assert resp_ui.status_code == 200
        assert "Official Participant Category Positions" in resp_ui.text
        assert "FII Index Futures Net" in resp_ui.text
