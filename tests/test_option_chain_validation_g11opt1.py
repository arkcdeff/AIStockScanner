"""Option Chain Validation & Partition Maintenance Tests — Milestone G.11-OPT-1 Step 2.

Tests:
  1. Strike-Grid Validation:
     - NIFTY (50), BANKNIFTY (100), FINNIFTY (50), MIDCPNIFTY (25), SENSEX (100)
     - Valid and off-grid strikes
     - Non-positive and invalid numeric strikes
     - Duplicate strikes, sequence ordering, and spacing
  2. Chain Completeness & Truncation:
     - Complete chains, incomplete chains, truncated provider responses
     - Structural anomalies (negative counts, observed > expected, zero strikes)
  3. Multi-Timestamp & Clock Skew Validation:
     - In-order timestamps, tolerated clock skew, tolerance boundaries, excessive skew
     - Future market timestamps, available_at inversions
  4. Composite Snapshot Header Validation:
     - Quality status priority (REJECTED > WARNING > ACCEPT_WITH_FLAG > VALID)
  5. Partition Maintenance Utility & `ensure_quote_partition_exists`:
     - Boundary calculation [month_start, next_month_start)
     - Existing partition detection (2026_09 to 2026_12)
     - Idempotent creation
     - Current + 3 future months provisioning
     - On-demand historical partition creation
     - Half-open boundary routing [2026-09-30 vs 2026-10-01]
     - Controlled failure for unprovisioned month (no DEFAULT partition)
     - Concurrent partition creation safety
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.option_chain_config import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    DQ_FLAG_AVAILABLE_AT_INVERSION,
    DQ_FLAG_CLOCK_SKEW_EXCESSIVE,
    DQ_FLAG_CLOCK_SKEW_TOLERATED,
    DQ_FLAG_CLOCK_SKEW_WARNING,
    DQ_FLAG_DUPLICATE_STRIKE,
    DQ_FLAG_EMPTY_CHAIN,
    DQ_FLAG_FUTURE_MARKET_TIMESTAMP,
    DQ_FLAG_INCOMPLETE_CHAIN,
    DQ_FLAG_INSUFFICIENT_STRIKES,
    DQ_FLAG_INVALID_NUMERIC_STRIKE,
    DQ_FLAG_IRREGULAR_STRIKE_SPACING,
    DQ_FLAG_NON_POSITIVE_STRIKE,
    DQ_FLAG_OFF_GRID_STRIKE,
    DQ_FLAG_STRUCTURAL_COUNT_ANOMALY,
    DQ_FLAG_TRUNCATED_BY_SOURCE,
    DQ_FLAG_UNORDERED_STRIKES,
    DQ_STATUS_ACCEPT_WITH_FLAG,
    DQ_STATUS_REJECTED,
    DQ_STATUS_VALID,
    DQ_STATUS_WARNING,
    MAX_ALLOWABLE_CLOCK_SKEW_MS,
    get_strike_step,
)
from app.services.option_chain_partition_service import (
    check_quote_partition_exists,
    ensure_quote_partition_exists,
    ensure_quote_partition_exists_sync,
    get_monthly_partition_bounds,
    list_quote_partitions,
    maintain_quote_partitions,
)
from app.services.option_chain_validation_service import (
    OptionChainValidationService,
)

# ===========================================================================
# 1. Strike-Grid Validation Tests
# ===========================================================================


class TestStrikeGridValidation:
    """Test suite for index-specific strike-grid validation primitives."""

    def test_get_strike_step_resolution(self) -> None:
        """Verify strike step resolution across core indices and aliases."""
        assert get_strike_step("NIFTY") == Decimal("50")
        assert get_strike_step("NIFTY 50") == Decimal("50")
        assert get_strike_step("BANKNIFTY") == Decimal("100")
        assert get_strike_step("NIFTY_BANK") == Decimal("100")
        assert get_strike_step("FINNIFTY") == Decimal("50")
        assert get_strike_step("MIDCPNIFTY") == Decimal("25")
        assert get_strike_step("SENSEX") == Decimal("100")
        assert get_strike_step("NIFTY_NEXT_50") == Decimal("100")

    def test_get_strike_step_unrecognized_raises(self) -> None:
        """Unrecognized underlying index raises ValueError."""
        with pytest.raises(ValueError, match="No configured strike step"):
            get_strike_step("UNSUPPORTED_INDEX_XYZ")

    def test_valid_nifty_strikes(self) -> None:
        """NIFTY strikes on 50-step grid pass validation."""
        for strike in [24000, 24050, 24100, 24150, Decimal("25000.00"), "24500"]:
            res = OptionChainValidationService.validate_strike(strike, underlying="NIFTY")
            assert res.is_valid is True
            assert res.status == DQ_STATUS_VALID
            assert res.errors == []

    def test_invalid_nifty_off_grid_strike(self) -> None:
        """NIFTY off-grid strikes (e.g. 24025, 24010) are rejected."""
        res = OptionChainValidationService.validate_strike(24025, underlying="NIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_OFF_GRID_STRIKE in res.flags
        assert res.flags[DQ_FLAG_OFF_GRID_STRIKE]["remainder"] == 25.0

    def test_valid_banknifty_strikes(self) -> None:
        """BANKNIFTY strikes on 100-step grid pass validation."""
        for strike in [51000, 51100, 51200, Decimal("52000.00")]:
            res = OptionChainValidationService.validate_strike(strike, underlying="BANKNIFTY")
            assert res.is_valid is True
            assert res.status == DQ_STATUS_VALID

    def test_invalid_banknifty_off_grid_strike(self) -> None:
        """BANKNIFTY off-grid strike (e.g. 51050) is rejected."""
        res = OptionChainValidationService.validate_strike(51050, underlying="BANKNIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_OFF_GRID_STRIKE in res.flags

    def test_midcpnifty_25_step_grid(self) -> None:
        """MIDCPNIFTY strikes on 25-step grid."""
        # Valid
        for strike in [12000, 12025, 12050, 12075]:
            assert OptionChainValidationService.validate_strike(strike, underlying="MIDCPNIFTY").is_valid is True
        # Off-grid
        res = OptionChainValidationService.validate_strike(12010, underlying="MIDCPNIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED

    def test_non_positive_strikes_rejected(self) -> None:
        """Zero and negative strikes are rejected."""
        for bad_strike in [0, -100, Decimal("-50.0")]:
            res = OptionChainValidationService.validate_strike(bad_strike, underlying="NIFTY")
            assert res.is_valid is False
            assert res.status == DQ_STATUS_REJECTED
            assert DQ_FLAG_NON_POSITIVE_STRIKE in res.flags

    def test_invalid_numeric_strike_rejected(self) -> None:
        """Non-numeric strike strings are rejected."""
        res = OptionChainValidationService.validate_strike("invalid_strike", underlying="NIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_INVALID_NUMERIC_STRIKE in res.flags

    def test_duplicate_strike_detection_in_sequence(self) -> None:
        """Duplicate strikes in a sequence are rejected."""
        strikes = [24000, 24050, 24050, 24100]
        res = OptionChainValidationService.validate_strike_sequence(strikes, underlying="NIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_DUPLICATE_STRIKE in res.flags
        assert 24050.0 in res.flags[DQ_FLAG_DUPLICATE_STRIKE]["duplicates"]

    def test_unordered_strike_sequence_warning(self) -> None:
        """Unordered strikes produce a WARNING flag."""
        strikes = [24100, 24000, 24050]
        res = OptionChainValidationService.validate_strike_sequence(
            strikes, underlying="NIFTY", check_ordering=True
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_UNORDERED_STRIKES in res.flags

    def test_irregular_strike_spacing_warning(self) -> None:
        """Missing strikes creating irregular spacing produce a WARNING flag."""
        # 24000 to 24050 (gap 50), 24050 to 24200 (gap 150 = 3*50, gap in ladder)
        strikes = [24000, 24050, 24200]
        res = OptionChainValidationService.validate_strike_sequence(
            strikes, underlying="NIFTY", check_spacing=True
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_IRREGULAR_STRIKE_SPACING in res.flags

    def test_empty_strike_sequence_rejected(self) -> None:
        """Empty strike list is rejected."""
        res = OptionChainValidationService.validate_strike_sequence([], underlying="NIFTY")
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_EMPTY_CHAIN in res.flags


# ===========================================================================
# 2. Chain Completeness & Truncation Validation Tests
# ===========================================================================


class TestChainCompletenessValidation:
    """Test suite for chain completeness and provider truncation detection."""

    def test_complete_chain_exact_match(self) -> None:
        """Observed count equals expected count without truncation -> VALID."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=20,
            expected_strike_count=20,
            is_truncated=False,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_VALID
        assert res.flags == {}

    def test_incomplete_chain_warning(self) -> None:
        """Observed count less than expected without explicit truncation -> WARNING."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=15,
            expected_strike_count=20,
            is_truncated=False,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_INCOMPLETE_CHAIN in res.flags
        assert res.flags[DQ_FLAG_INCOMPLETE_CHAIN]["missing_count"] == 5

    def test_truncated_chain_accepted_with_flag(self) -> None:
        """Provider explicitly flagged truncation -> ACCEPT_WITH_FLAG."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=10,
            expected_strike_count=20,
            is_truncated=True,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_ACCEPT_WITH_FLAG
        assert DQ_FLAG_TRUNCATED_BY_SOURCE in res.flags

    def test_truncated_without_expected_count(self) -> None:
        """Provider truncated response when expected count is unknown."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=12,
            expected_strike_count=None,
            is_truncated=True,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_ACCEPT_WITH_FLAG
        assert DQ_FLAG_TRUNCATED_BY_SOURCE in res.flags

    def test_structural_anomaly_negative_observed_rejected(self) -> None:
        """Negative observed strike count -> REJECTED."""
        res = OptionChainValidationService.validate_chain_completeness(observed_strike_count=-5)
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_STRUCTURAL_COUNT_ANOMALY in res.flags

    def test_structural_anomaly_zero_observed_rejected(self) -> None:
        """Zero strikes observed -> REJECTED."""
        res = OptionChainValidationService.validate_chain_completeness(observed_strike_count=0)
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_EMPTY_CHAIN in res.flags

    def test_structural_anomaly_observed_exceeds_expected_rejected(self) -> None:
        """Observed strike count exceeding expected count -> REJECTED."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=30,
            expected_strike_count=20,
        )
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_STRUCTURAL_COUNT_ANOMALY in res.flags

    def test_structural_anomaly_negative_expected_rejected(self) -> None:
        """Negative expected strike count -> REJECTED."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=10,
            expected_strike_count=-1,
        )
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED

    def test_insufficient_strikes_warning(self) -> None:
        """Observed strike count below min_expected_strikes -> WARNING."""
        res = OptionChainValidationService.validate_chain_completeness(
            observed_strike_count=2,
            expected_strike_count=None,
            min_expected_strikes=5,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_INSUFFICIENT_STRIKES in res.flags


# ===========================================================================
# 3. Timestamp Data Quality Validation Tests
# ===========================================================================


class TestTimestampDataQualityValidation:
    """Test suite for timestamp relationships and clock skew tolerance."""

    def test_valid_timestamps_in_order(self) -> None:
        """Clean timestamp progression -> VALID."""
        now = datetime.now(UTC)
        mkt = now - timedelta(seconds=2)
        src = now - timedelta(seconds=1)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=ing,
            available_at=avail,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_VALID
        assert res.flags == {}

    def test_clock_skew_within_tolerated_range(self) -> None:
        """Source timestamp earlier than market by 1500ms (within 2000ms) -> ACCEPT_WITH_FLAG."""
        now = datetime.now(UTC)
        mkt = now - timedelta(seconds=1)
        src = mkt - timedelta(milliseconds=1500)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=ing,
            available_at=avail,
            skew_tolerance_ms=2000,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_ACCEPT_WITH_FLAG
        assert DQ_FLAG_CLOCK_SKEW_TOLERATED in res.flags

    def test_clock_skew_at_exact_tolerance_boundary(self) -> None:
        """Source timestamp earlier than market by exactly 2000ms -> ACCEPT_WITH_FLAG."""
        now = datetime.now(UTC)
        mkt = now - timedelta(seconds=1)
        src = mkt - timedelta(milliseconds=2000)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=ing,
            available_at=avail,
            skew_tolerance_ms=2000,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_ACCEPT_WITH_FLAG
        assert DQ_FLAG_CLOCK_SKEW_TOLERATED in res.flags

    def test_clock_skew_exceeds_tolerance_warning(self) -> None:
        """Source timestamp earlier than market by 3000ms (> 2000ms) -> WARNING."""
        now = datetime.now(UTC)
        mkt = now - timedelta(seconds=1)
        src = mkt - timedelta(milliseconds=3000)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=ing,
            available_at=avail,
            skew_tolerance_ms=2000,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_CLOCK_SKEW_WARNING in res.flags

    def test_clock_skew_exceeds_maximum_allowable_rejected(self) -> None:
        """Source timestamp earlier than market by 6 minutes (> 5 minutes max) -> REJECTED."""
        now = datetime.now(UTC)
        mkt = now
        src = mkt - timedelta(milliseconds=MAX_ALLOWABLE_CLOCK_SKEW_MS + 10_000)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=ing,
            available_at=avail,
        )
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_CLOCK_SKEW_EXCESSIVE in res.flags

    def test_future_market_timestamp_rejected(self) -> None:
        """Market timestamp in the future relative to ingestion -> REJECTED."""
        now = datetime.now(UTC)
        mkt = now + timedelta(seconds=10)
        ing = now
        avail = now

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=None,
            ingestion_timestamp=ing,
            available_at=avail,
            skew_tolerance_ms=DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
        )
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_FUTURE_MARKET_TIMESTAMP in res.flags

    def test_available_at_inversion_warning(self) -> None:
        """available_at preceding ingestion_timestamp -> WARNING."""
        now = datetime.now(UTC)
        mkt = now - timedelta(seconds=5)
        ing = now
        avail = now - timedelta(seconds=2)  # Inverted

        res = OptionChainValidationService.validate_timestamps(
            market_timestamp=mkt,
            source_timestamp=None,
            ingestion_timestamp=ing,
            available_at=avail,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_WARNING
        assert DQ_FLAG_AVAILABLE_AT_INVERSION in res.flags


# ===========================================================================
# 4. Consolidated Snapshot Header Validation Tests
# ===========================================================================


class TestConsolidatedSnapshotValidation:
    """Test suite for consolidated snapshot header quality validation."""

    def test_pristine_snapshot_valid(self) -> None:
        """Pristine snapshot has VALID status and clean flags."""
        now = datetime.now(UTC)
        strikes = [24000, 24050, 24100, 24150, 24200]
        res = OptionChainValidationService.validate_snapshot_header(
            underlying="NIFTY",
            strikes=strikes,
            market_timestamp=now - timedelta(seconds=1),
            source_timestamp=now - timedelta(seconds=1),
            ingestion_timestamp=now,
            available_at=now,
            expected_strike_count=5,
            is_truncated=False,
        )
        assert res.is_valid is True
        assert res.status == DQ_STATUS_VALID
        assert res.flags == {}

    def test_status_precedence_rejected_wins(self) -> None:
        """REJECTED takes precedence over WARNING and ACCEPT_WITH_FLAG."""
        now = datetime.now(UTC)
        # Off-grid strike (REJECTED) + Incomplete chain (WARNING) + Tolerated skew (ACCEPT_WITH_FLAG)
        strikes = [24000, 24025, 24100]  # 24025 is off-grid
        mkt = now - timedelta(seconds=1)
        src = mkt - timedelta(milliseconds=1500)  # Skew tolerated

        res = OptionChainValidationService.validate_snapshot_header(
            underlying="NIFTY",
            strikes=strikes,
            market_timestamp=mkt,
            source_timestamp=src,
            ingestion_timestamp=now,
            available_at=now,
            expected_strike_count=10,  # Incomplete
            is_truncated=False,
        )
        assert res.is_valid is False
        assert res.status == DQ_STATUS_REJECTED
        assert DQ_FLAG_OFF_GRID_STRIKE in res.flags
        assert DQ_FLAG_INCOMPLETE_CHAIN in res.flags
        assert DQ_FLAG_CLOCK_SKEW_TOLERATED in res.flags


# ===========================================================================
# 5. Partition Maintenance Utility Tests (Real PostgreSQL Test DB)
# ===========================================================================


class TestQuotePartitionServiceSync:
    """Synchronous test cases for partition bounds and sync maintenance helpers."""

    def test_get_monthly_partition_bounds(self) -> None:
        """Verify dynamic calculation of month boundaries and partition names."""
        m_start, m_next, p_name = get_monthly_partition_bounds(date(2026, 9, 23))
        assert m_start == date(2026, 9, 1)
        assert m_next == date(2026, 10, 1)
        assert p_name == "option_chain_quotes_2026_09"

        # Year rollover (December -> January)
        m_start_dec, m_next_jan, p_name_dec = get_monthly_partition_bounds(date(2026, 12, 31))
        assert m_start_dec == date(2026, 12, 1)
        assert m_next_jan == date(2027, 1, 1)
        assert p_name_dec == "option_chain_quotes_2026_12"

    def test_sync_ensure_and_maintain_helpers(self) -> None:
        """Verify synchronous partition maintenance helpers."""
        from sqlalchemy import create_engine

        import tests.conftest as conftest_mod

        sync_engine = create_engine(conftest_mod._TEST_DB_SYNC)
        target_date = date(2027, 9, 15)
        try:
            name = ensure_quote_partition_exists_sync(target_date, sync_engine=sync_engine)
            assert name == "option_chain_quotes_2027_09"

            # Repeated call is idempotent
            name_repeat = ensure_quote_partition_exists_sync(target_date, sync_engine=sync_engine)
            assert name_repeat == name
        finally:
            with sync_engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS option_chain_quotes_2027_09"))


@pytest.mark.asyncio
class TestQuotePartitionService:
    """Test suite for range partition provisioning, boundary checks, and idempotency."""

    async def test_existing_step1_partitions_detected(self) -> None:
        """Verify that partitions created in Step 1 are accurately detected."""
        for d in [date(2026, 9, 15), date(2026, 10, 15), date(2026, 11, 15), date(2026, 12, 15)]:
            exists = await check_quote_partition_exists(d)
            assert exists is True, f"Expected partition for {d} to exist"

    async def test_list_quote_partitions(self) -> None:
        """Verify list_quote_partitions includes the Step-1 monthly partitions."""
        partitions = await list_quote_partitions()
        assert "option_chain_quotes_2026_09" in partitions
        assert "option_chain_quotes_2026_10" in partitions
        assert "option_chain_quotes_2026_11" in partitions
        assert "option_chain_quotes_2026_12" in partitions

    async def test_ensure_quote_partition_exists_idempotent(self) -> None:
        """Repeated calls to ensure_quote_partition_exists perform clean no-ops."""
        target_date = date(2026, 9, 1)
        name1 = await ensure_quote_partition_exists(target_date)
        name2 = await ensure_quote_partition_exists(target_date)
        assert name1 == "option_chain_quotes_2026_09"
        assert name2 == name1

    async def test_ensure_quote_partition_exists_on_demand_future_and_cleanup(self) -> None:
        """On-demand creation of future month partition (e.g. 2027-02)."""
        future_date = date(2027, 2, 15)
        try:
            # Provision partition
            p_name = await ensure_quote_partition_exists(future_date)
            assert p_name == "option_chain_quotes_2027_02"
            assert await check_quote_partition_exists(future_date) is True

            # Repeated call is idempotent
            p_name_repeat = await ensure_quote_partition_exists(future_date)
            assert p_name_repeat == p_name
        finally:
            # Clean up test partition
            factory = get_session_factory()
            async with factory() as s:
                await s.execute(text("DROP TABLE IF EXISTS option_chain_quotes_2027_02"))
                await s.commit()

    async def test_ensure_quote_partition_exists_on_demand_historical_and_cleanup(self) -> None:
        """On-demand creation of historical month partition (e.g. 2025-05)."""
        hist_date = date(2025, 5, 10)
        try:
            p_name = await ensure_quote_partition_exists(hist_date)
            assert p_name == "option_chain_quotes_2025_05"
            assert await check_quote_partition_exists(hist_date) is True
        finally:
            factory = get_session_factory()
            async with factory() as s:
                await s.execute(text("DROP TABLE IF EXISTS option_chain_quotes_2025_05"))
                await s.commit()

    async def test_maintain_quote_partitions_current_plus_three_months(self) -> None:
        """Verify maintain_quote_partitions provisions reference month + 3 future months."""
        ref_date = date(2026, 9, 1)
        ensured = await maintain_quote_partitions(reference_date=ref_date, ahead_months=3)
        expected = [
            "option_chain_quotes_2026_09",
            "option_chain_quotes_2026_10",
            "option_chain_quotes_2026_11",
            "option_chain_quotes_2026_12",
        ]
        assert ensured == expected

    async def test_half_open_boundaries_routing(self, db_session: AsyncSession, mi_fixture: MarketIndex) -> None:
        """Verify strict half-open boundary routing: [2026-09-01, 2026-10-01).

        2026-09-30 routes to 2026_09.
        2026-10-01 routes to 2026_10 (NOT 2026_09).
        """
        now = datetime.now(UTC)
        snap = OptionChainSnapshot(
            underlying_index_id=mi_fixture.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE_TEST",
            raw_payload_hash="a" * 64,
            normalization_version="1.0",
            batch_id="batch_boundary",
            revision=1,
            quality_status="VALID",
        )
        db_session.add(snap)
        await db_session.flush()

        contract1 = DerivativeContract(
            exchange="NSE",
            underlying_index_id=mi_fixture.id,
            trading_symbol="NIFTY26SEP24000CE",
            instrument_type="OPTION",
            strike_price=Decimal("24000.00"),
            option_type="CE",
            expiry_date=date(2026, 9, 30),
            available_at=date(2026, 9, 1),
            lot_size=25,
        )
        contract2 = DerivativeContract(
            exchange="NSE",
            underlying_index_id=mi_fixture.id,
            trading_symbol="NIFTY26OCT24000PE",
            instrument_type="OPTION",
            strike_price=Decimal("24000.00"),
            option_type="PE",
            expiry_date=date(2026, 10, 1),
            available_at=date(2026, 9, 1),
            lot_size=25,
        )
        db_session.add_all([contract1, contract2])
        await db_session.flush()

        # Quote 1: 2026-09-30 (last day of September)
        q_sep = OptionChainQuote(
            snapshot_id=snap.id,
            contract_id=contract1.id,
            trade_date=date(2026, 9, 30),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            expiry_date=date(2026, 9, 30),
            ltp=Decimal("150.25"),
        )
        # Quote 2: 2026-10-01 (first day of October)
        q_oct = OptionChainQuote(
            snapshot_id=snap.id,
            contract_id=contract2.id,
            trade_date=date(2026, 10, 1),
            strike_price=Decimal("24000.00"),
            option_type="PE",
            expiry_date=date(2026, 10, 1),
            ltp=Decimal("160.75"),
        )
        db_session.add_all([q_sep, q_oct])
        await db_session.flush()

        # Verify physical partition storage in PostgreSQL catalog
        res_sep = await db_session.execute(
            text(
                """
                SELECT tableoid::regclass::text
                FROM option_chain_quotes
                WHERE id = :qid AND trade_date = '2026-09-30'
                """
            ),
            {"qid": q_sep.id},
        )
        part_sep = res_sep.scalar()
        assert part_sep == "option_chain_quotes_2026_09"

        res_oct = await db_session.execute(
            text(
                """
                SELECT tableoid::regclass::text
                FROM option_chain_quotes
                WHERE id = :qid AND trade_date = '2026-10-01'
                """
            ),
            {"qid": q_oct.id},
        )
        part_oct = res_oct.scalar()
        assert part_oct == "option_chain_quotes_2026_10"

    async def test_unprovisioned_month_fails_cleanly_without_default_partition(
        self, db_session: AsyncSession, mi_fixture: MarketIndex
    ) -> None:
        """Inserting a quote for an unprovisioned month fails cleanly; no DEFAULT partition exists."""
        now = datetime.now(UTC)
        snap = OptionChainSnapshot(
            underlying_index_id=mi_fixture.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE_TEST",
            raw_payload_hash="b" * 64,
            normalization_version="1.0",
            batch_id="batch_unprov",
            revision=1,
            quality_status="VALID",
        )
        db_session.add(snap)
        await db_session.flush()

        contract = DerivativeContract(
            exchange="NSE",
            underlying_index_id=mi_fixture.id,
            trading_symbol="NIFTY28MAY24000CE",
            instrument_type="OPTION",
            strike_price=Decimal("24000.00"),
            option_type="CE",
            expiry_date=date(2028, 5, 25),
            available_at=date(2028, 5, 1),
            lot_size=25,
        )
        db_session.add(contract)
        await db_session.flush()

        # Unprovisioned month: 2028-05-15
        q_unprov = OptionChainQuote(
            snapshot_id=snap.id,
            contract_id=contract.id,
            trade_date=date(2028, 5, 15),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            expiry_date=date(2028, 5, 25),
            ltp=Decimal("100.00"),
        )
        db_session.add(q_unprov)

        with pytest.raises(Exception, match=r"no partition of relation.*found for row"):
            await db_session.flush()

    async def test_concurrent_partition_creation(self) -> None:
        """Multiple concurrent tasks calling ensure_quote_partition_exists for the same month succeed safely."""
        target_date = date(2027, 8, 15)
        try:
            # Run 5 concurrent provisioning tasks
            tasks = [ensure_quote_partition_exists(target_date) for _ in range(5)]
            results = await asyncio.gather(*tasks)

            # All 5 return the same partition name without deadlocks or errors
            assert all(r == "option_chain_quotes_2027_08" for r in results)
            assert await check_quote_partition_exists(target_date) is True
        finally:
            factory = get_session_factory()
            async with factory() as s:
                await s.execute(text("DROP TABLE IF EXISTS option_chain_quotes_2027_08"))
                await s.commit()
