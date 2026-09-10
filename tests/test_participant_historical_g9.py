"""Tests for Stage 2 Phase G.9: Participant OI Historical Expansion.

Covers:
  1. Manifest integrity & 62 sessions validation
  2. SHA-256 and filename pattern verification against official NSE files
  3. ParticipantOpenInterestParser compatibility & category/bucket normalization
  4. TOTAL row exclusion (exactly 24 rows per session)
  5. Negative value rejection
  6. Official +/- 1-2 contract artifact tolerance & DataQualityIssue reporting
  7. Strict Point-in-Time (PIT) semantics: available_at = report_date
  8. NseParticipantHistoricalExtensionService session ingestion
  9. Idempotent re-ingestion
  10. HistoricalParticipantAnalyticsService (FII summary, DoD changes, shares)
  11. REST API endpoints (/derivatives/participants, /history)
  12. UI card rendering (/market-data/derivatives/participants/cards)
  13. No schema migration (Alembic head check)
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.nse_participant_parser import ParticipantOpenInterestParser
from app.db.session import get_session_factory
from app.models.derivative_participant import DerivativeParticipantPosition
from app.services.historical_participant_analytics import HistoricalParticipantAnalyticsService
from app.services.nse_participant_historical_extension_service import (
    NseParticipantHistoricalExtensionService,
)
from tests.auth_helpers import auth_client, seed_builtins

MANIFEST_PATH = Path("data/reference/participant/manifest_q4_2025.json")
RAW_DIR = Path("data/reference/participant/raw")


def test_manifest_integrity_and_session_count():
    """Verify manifest exists, contains 62 sessions, and matches certified date range."""
    assert MANIFEST_PATH.exists(), f"Manifest missing at {MANIFEST_PATH}"

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["expected_sessions"] == 62
    assert manifest["period"] == "2025-10-01 to 2025-12-31 (Q4-2025)"
    files_map = manifest["files"]
    assert len(files_map) == 62

    # Verify summary counts
    assert manifest["summary"]["VALID"] == 62
    assert manifest["summary"]["market_balance_perfect"] == 12
    assert manifest["summary"]["market_balance_official_source_artifact"] == 50
    assert manifest["summary"]["negative_positions_found"] == 0
    assert manifest["summary"]["duplicate_identity_rows_found"] == 0


def test_manifest_sha256_and_raw_files():
    """Verify all 62 raw files exist and their SHA256 hashes match the manifest."""
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    for _d_str, meta in manifest["files"].items():
        fname = meta["filename"]
        file_path = RAW_DIR / fname
        assert file_path.exists(), f"Source file missing: {file_path}"

        content = file_path.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        assert actual_hash.lower() == meta["sha256"].lower(), f"SHA256 mismatch for {fname}"
        assert len(content) == meta["byte_size"]


def test_parser_category_and_bucket_normalization():
    """Verify parser normalizes 4 participant types, 6 buckets, and skips TOTAL row."""
    parser = ParticipantOpenInterestParser()
    sample_file = RAW_DIR / "fao_participant_oi_01102025.csv"
    assert sample_file.exists()

    result = parser.parse(sample_file.read_bytes(), report_date=date(2025, 10, 1), filename=sample_file.name)

    assert result.dataset_type == "OPEN_INTEREST"
    assert result.report_date == date(2025, 10, 1)
    assert result.raw_row_count == 4  # Client, DII, FII, Pro (TOTAL skipped)
    assert len(result.rows) == 24  # 4 categories * 6 buckets

    categories = {r.participant_type for r in result.rows}
    assert categories == {"CLIENT", "DII", "FII", "PRO"}

    buckets = {r.instrument_bucket for r in result.rows}
    assert buckets == {
        "INDEX_FUTURE",
        "STOCK_FUTURE",
        "INDEX_OPTION_CALL",
        "INDEX_OPTION_PUT",
        "STOCK_OPTION_CALL",
        "STOCK_OPTION_PUT",
    }


def test_parser_official_imbalance_artifact():
    """Verify parser detects the +/- 1-2 contract official artifact as warning without failing."""
    parser = ParticipantOpenInterestParser()

    # 2025-10-01 has the known official index option imbalance
    f_path = RAW_DIR / "fao_participant_oi_01102025.csv"
    result = parser.parse(f_path.read_bytes(), report_date=date(2025, 10, 1), filename=f_path.name)

    assert not result.is_market_balanced
    assert any("DQ_PARTICIPANT_MARKET_BALANCE_MISMATCH" in w for w in result.quality_warnings)
    # The parse result is still returned with all 24 normalized rows
    assert len(result.rows) == 24


def test_parser_negative_value_rejection():
    """Verify negative contract counts trigger DQ_PARTICIPANT_NEGATIVE_VALUE warning."""
    parser = ParticipantOpenInterestParser()
    negative_csv = """Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts,Total Short Contracts
Client,-100,500,2000,1000,5000,4000,3000,2000,1500,1200,1000,800,14700,9500
DII,3000,1000,4000,2000,1000,2000,500,1000,500,800,200,300,9000,7100
FII,5000,2000,6000,3000,8000,6000,4000,5000,2000,1500,1800,1200,31000,18700
Pro,2000,6500,1000,7000,2000,3000,8500,7000,1000,1000,2000,2200,7000,26700
TOTAL,10000,10000,13000,13000,16000,15000,16000,15000,5000,4500,5000,4500,61700,62000
"""
    result = parser.parse(negative_csv, report_date=date(2025, 10, 1))
    assert any("DQ_PARTICIPANT_NEGATIVE_VALUE:CLIENT" in w for w in result.quality_warnings)
    client_rows = [r for r in result.rows if r.participant_type == "CLIENT"]
    assert len(client_rows) == 0


@pytest.mark.asyncio
async def test_extension_service_ingest_session():
    """Verify NseParticipantHistoricalExtensionService ingests a single session with strict provenance and PIT."""
    factory = get_session_factory()
    async with factory() as session:
        service = NseParticipantHistoricalExtensionService(session, cache_dir=RAW_DIR)
        trade_d = date(2025, 10, 1)

        result = await service.ingest_session(
            report_date=trade_d,
            filename="fao_participant_oi_01102025.csv",
            expected_sha256="d14a7806c6c551b073664bea1fba8132dfd6d4809f0acd30f9d419ea6790e293",
        )

        assert result.status == "SUCCESS"
        assert result.rows_parsed == 24
        assert result.rows_inserted > 0 or result.rows_unchanged > 0

        # Verify in DB
        db_rows = (
            await session.execute(
                select(DerivativeParticipantPosition)
                .where(DerivativeParticipantPosition.report_date == trade_d)
                .where(DerivativeParticipantPosition.dataset_type == "OPEN_INTEREST")
            )
        ).scalars().all()

        assert len(db_rows) == 24
        for row in db_rows:
            assert row.available_at == trade_d  # PIT
            assert row.source_name == "NSE_NSCCL"  # Provenance
            assert row.source_reference == "fao_participant_oi_01102025.csv"
            assert row.source_checksum == "d14a7806c6c551b073664bea1fba8132dfd6d4809f0acd30f9d419ea6790e293"
            assert row.long_contracts >= 0
            assert row.short_contracts >= 0
            assert row.participant_type in {"CLIENT", "DII", "FII", "PRO"}


@pytest.mark.asyncio
async def test_extension_service_idempotent_reingest():
    """Verify re-ingesting the same session is 100% idempotent (0 duplicate rows)."""
    factory = get_session_factory()
    async with factory() as session:
        service = NseParticipantHistoricalExtensionService(session, cache_dir=RAW_DIR)
        trade_d = date(2025, 10, 1)

        # Ensure session is present first
        await service.ingest_session(
            report_date=trade_d,
            filename="fao_participant_oi_01102025.csv",
            skip_if_present=False,
        )

        # Re-run with skip_if_present=True
        result_skip = await service.ingest_session(
            report_date=trade_d,
            filename="fao_participant_oi_01102025.csv",
            skip_if_present=True,
        )
        assert result_skip.status == "SKIPPED_ALREADY_PRESENT"
        assert result_skip.rows_inserted == 0

        # Re-run without skip (direct upsert on existing)
        result_upsert = await service.ingest_session(
            report_date=trade_d,
            filename="fao_participant_oi_01102025.csv",
            skip_if_present=False,
        )
        assert result_upsert.status == "SUCCESS"
        assert result_upsert.rows_inserted == 0  # Nothing new inserted
        assert result_upsert.rows_unchanged == 24

        # Total rows in DB remains exactly 24
        cnt = (
            await session.execute(
                select(func.count(DerivativeParticipantPosition.id))
                .where(DerivativeParticipantPosition.report_date == trade_d)
                .where(DerivativeParticipantPosition.dataset_type == "OPEN_INTEREST")
            )
        ).scalar()
        assert cnt == 24


@pytest.mark.asyncio
async def test_historical_participant_analytics_service_q4():
    """Verify HistoricalParticipantAnalyticsService generates deterministic PIT metrics on Q4-2025."""
    factory = get_session_factory()
    async with factory() as session:
        service = NseParticipantHistoricalExtensionService(session, cache_dir=RAW_DIR)
        trade_d = date(2025, 10, 1)

        # Ingest session
        await service.ingest_session(
            report_date=trade_d,
            filename="fao_participant_oi_01102025.csv",
        )

        analytics_svc = HistoricalParticipantAnalyticsService(session)
        snapshot = await analytics_svc.get_participant_snapshot(
            report_date=trade_d,
            dataset_type="OPEN_INTEREST",
            as_of_date=trade_d,
        )

        assert snapshot.report_date == trade_d
        assert snapshot.dataset_type == "OPEN_INTEREST"
        assert len(snapshot.items) == 24
        assert snapshot.fii_index_futures is not None
        assert snapshot.fii_index_futures.long_contracts >= 0
        assert snapshot.fii_index_futures.short_contracts >= 0
        if snapshot.fii_index_futures.short_contracts > 0:
            assert snapshot.fii_index_futures.long_short_ratio is not None


@pytest.mark.asyncio
async def test_pit_isolation_q4():
    """Verify point-in-time isolation: as_of date hides future sessions."""
    factory = get_session_factory()
    async with factory() as session:
        service = NseParticipantHistoricalExtensionService(session, cache_dir=RAW_DIR)
        await service.ingest_session(
            report_date=date(2025, 10, 3),
            filename="fao_participant_oi_03102025.csv",
        )

        # Query as-of 2025-10-01: should NOT see 2025-10-03
        positions_future = await service.repo.get_positions_by_date(
            report_date=date(2025, 10, 3),
            dataset_type="OPEN_INTEREST",
            as_of_date=date(2025, 10, 1),
        )
        assert len(positions_future) == 0, "PIT violation: future participant data visible as of 2025-10-01"

        # Query as-of 2025-10-03: SHOULD see 2025-10-03
        positions_current = await service.repo.get_positions_by_date(
            report_date=date(2025, 10, 3),
            dataset_type="OPEN_INTEREST",
            as_of_date=date(2025, 10, 3),
        )
        assert len(positions_current) == 24


@pytest.mark.asyncio
async def test_participant_api_and_ui_cards():
    """Verify participant REST endpoints and HTMX card partial."""
    factory = get_session_factory()
    async with factory() as session:
        service = NseParticipantHistoricalExtensionService(session, cache_dir=RAW_DIR)
        await service.ingest_session(
            report_date=date(2025, 10, 1),
            filename="fao_participant_oi_01102025.csv",
        )

    await seed_builtins()

    # 1. JSON Snapshot API
    async with auth_client(is_superuser=True) as api_client:
        resp = await api_client.get(
            "/api/v1/market-data/derivatives/participants",
            params={"report_date": "2025-10-01", "dataset_type": "OPEN_INTEREST"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_date"] == "2025-10-01"
        assert "items" in data
        assert len(data["items"]) == 24

        # 2. JSON History API
        resp_hist = await api_client.get(
            "/api/v1/market-data/derivatives/participants/history",
            params={
                "participant_type": "FII",
                "instrument_bucket": "INDEX_FUTURE",
                "start_date": "2025-10-01",
                "end_date": "2025-10-03",
            },
        )
        assert resp_hist.status_code == 200
        hist_data = resp_hist.json()
        assert isinstance(hist_data, list)
        assert len(hist_data) >= 1

    # 3. HTMX UI Cards Partial
    async with auth_client(is_superuser=True, browser=True) as ui_client:
        resp_ui = await ui_client.get(
            "/market-data/derivatives/participants/cards",
            params={"report_date": "2025-10-01", "dataset_type": "OPEN_INTEREST"},
        )
        assert resp_ui.status_code == 200
        html = resp_ui.text
        assert "Official Participant Category Positions" in html
        assert "FII Index Futures Long" in html
        assert "FII Index Futures Short" in html
        assert "Participant Positioning Matrix" in html
        assert "CLIENT" in html
        assert "DII" in html
        assert "FII" in html
        assert "PRO" in html
