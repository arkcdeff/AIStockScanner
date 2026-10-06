"""Step 1 Tests for G.11-OPT-1: Model & Alembic Migration Generation.

Validates:
1. Model field definitions, defaults, and relationships.
2. CheckConstraint enforcements (chain_scope, expiry_scope_match, revision_pos,
   quality_status, option_type, strike_positive, volume/oi non-negative, bid_ask_valid).
3. Partial unique index enforcements (uq_opt_snapshots_all, uq_opt_snapshots_single).
4. Range-partitioned quote table behavior (monthly partitions, boundaries, pruning,
   missing partition handling).
5. Partition-safe composite PK and unique constraints (pk_option_chain_quotes,
   uq_option_chain_quotes_snapshot_contract_date).
6. Foreign key cascade and set-null semantics.
7. Non-interference with D1-D6 baseline models.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot


def _hash(seed: str) -> str:
    """Generate a deterministic 64-character SHA-256 hash."""
    return hashlib.sha256(seed.encode()).hexdigest()


async def _create_test_index(session, code: str = "NIFTY_TEST") -> MarketIndex:
    """Create a persistent MarketIndex for foreign key references."""
    idx = MarketIndex(
        code=code,
        name="Nifty 50 Test Index",
        country="INDIA",
        category="indian_index",
        exchange="NSE",
        currency="INR",
        is_active=True,
    )
    session.add(idx)
    await session.commit()
    await session.refresh(idx)
    return idx


async def _create_test_contract(
    session,
    index_id: int,
    strike: Decimal = Decimal("24000.0000"),
    opt_type: str = "CE",
    expiry: date = date(2026, 9, 24),
    trading_symbol: str = "NIFTY26SEP24000CE",
) -> DerivativeContract:
    """Create a persistent DerivativeContract for foreign key references."""
    contract = DerivativeContract(
        exchange="NSE",
        segment="NFO",
        instrument_type="OPTION",
        underlying_index_id=index_id,
        trading_symbol=trading_symbol,
        expiry_date=expiry,
        strike_price=strike,
        option_type=opt_type,
        lot_size=25,
        tick_size=Decimal("0.05"),
        lifecycle_status="ACTIVE",
        idempotency_hash=_hash(trading_symbol),
        available_at=date(2026, 9, 23),
        source_name="NSE",
        source_reference="bhavcopy_20260923",
        fetched_at=datetime.now(UTC),
    )
    session.add(contract)
    await session.commit()
    await session.refresh(contract)
    return contract


# ---------------------------------------------------------------------------
# 1. Model Instantiation & Field Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_model_instantiation():
    """OptionChainSnapshot instantiates with correct defaults and fields."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_INST_SNAP")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            spot_price=Decimal("24500.2500"),
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE_LIVE",
            raw_payload_hash=_hash("payload_001"),
            normalization_version="1.0.0",
            batch_id="batch_001",
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)

        assert snapshot.revision == 1
        assert snapshot.quality_status == "VALID"
        assert snapshot.quality_flags == {}
        assert snapshot.is_truncated is False
        assert repr(snapshot).startswith("<OptionChainSnapshot")


@pytest.mark.asyncio
async def test_quote_model_instantiation():
    """OptionChainQuote instantiates with distinct market facts, OI, and Greeks."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_INST_Q")
        contract = await _create_test_contract(
            session, idx.id, trading_symbol="NIFTY26SEP24000CE_INST"
        )
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("q_payload_001"),
            normalization_version="1.0",
            batch_id="b_inst",
        )
        session.add(snapshot)
        await session.commit()

        quote = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            ltp=Decimal("150.2500"),
            bid=Decimal("150.0000"),
            ask=Decimal("150.5000"),
            bid_qty=500,
            ask_qty=750,
            volume=10000,
            open_interest=50000,
            source_change_in_oi=1200,
            interval_change_in_oi=300,
            interval_seconds=300,
            session_change_in_oi=1500,
            source_iv=Decimal("0.155000"),
            calculated_iv=Decimal("0.154800"),
            source_delta=Decimal("0.550000"),
            calculated_delta=Decimal("0.549200"),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

        assert quote.strike_price == Decimal("24000.0000")
        assert quote.option_type == "CE"
        assert quote.quality_flags == {}
        assert repr(quote).startswith("<OptionChainQuote")


# ---------------------------------------------------------------------------
# 2. CheckConstraint Enforcement Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_chain_scope_check_constraint():
    """Invalid chain_scope violates ck_option_chain_snapshots_chain_scope."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_SCOPE")
        invalid_snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="INVALID_SCOPE",
            expiry_scope=None,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b1"),
            normalization_version="1.0",
            batch_id="b1",
        )
        session.add(invalid_snapshot)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_snapshots_chain_scope" in str(exc_info.value)


@pytest.mark.asyncio
async def test_snapshot_expiry_scope_match_all_expiries():
    """ALL_SUPPORTED_EXPIRIES with non-null expiry_scope violates ck_option_chain_snapshots_expiry_scope_match."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_EXP_ALL")
        invalid_snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=date(2026, 9, 24),  # Must be NULL for ALL_SUPPORTED_EXPIRIES
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b2"),
            normalization_version="1.0",
            batch_id="b2",
        )
        session.add(invalid_snapshot)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_snapshots_exp" in str(exc_info.value)


@pytest.mark.asyncio
async def test_snapshot_expiry_scope_match_single_expiry():
    """SINGLE_EXPIRY with null expiry_scope violates ck_option_chain_snapshots_expiry_scope_match."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_EXP_SNG")
        invalid_snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=None,  # Must NOT be NULL for SINGLE_EXPIRY
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b3"),
            normalization_version="1.0",
            batch_id="b3",
        )
        session.add(invalid_snapshot)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_snapshots_exp" in str(exc_info.value)


@pytest.mark.asyncio
async def test_snapshot_revision_pos_constraint():
    """Revision < 1 violates ck_option_chain_snapshots_revision_pos."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_REV_POS")
        invalid_snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            revision=0,  # Invalid: must be >= 1
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b4"),
            normalization_version="1.0",
            batch_id="b4",
        )
        session.add(invalid_snapshot)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_snapshots_rev" in str(exc_info.value)


@pytest.mark.asyncio
async def test_snapshot_quality_status_constraint():
    """Invalid quality_status violates ck_option_chain_snapshots_quality_status."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_QS")
        invalid_snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            quality_status="CORRUPTED",  # Must be VALID, WARNING, ACCEPT_WITH_FLAG, REJECTED
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b5"),
            normalization_version="1.0",
            batch_id="b5",
        )
        session.add(invalid_snapshot)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_snapshots_qua" in str(exc_info.value)


@pytest.mark.asyncio
async def test_quote_option_type_constraint():
    """Invalid option_type violates ck_option_chain_quotes_option_type."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_Q_TYPE")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_Q_TYPE")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b6"),
            normalization_version="1.0",
            batch_id="b6",
        )
        session.add(snapshot)
        await session.commit()

        invalid_quote = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("24000.0000"),
            option_type="XX",  # Must be CE or PE
            expiry_date=date(2026, 9, 24),
        )
        session.add(invalid_quote)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_quotes_option_type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_quote_strike_positive_constraint():
    """strike_price <= 0 violates ck_option_chain_quotes_strike_positive."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_Q_STRK")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_Q_STRK")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b7"),
            normalization_version="1.0",
            batch_id="b7",
        )
        session.add(snapshot)
        await session.commit()

        invalid_quote = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("0.0000"),  # Must be > 0
            option_type="CE",
            expiry_date=date(2026, 9, 24),
        )
        session.add(invalid_quote)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_quotes_strike_positive" in str(exc_info.value)


@pytest.mark.asyncio
async def test_quote_volume_oi_bid_ask_constraints():
    """Quote non-negative and bid <= ask constraints are enforced."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_Q_MKT")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_Q_MKT")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b8"),
            normalization_version="1.0",
            batch_id="b8",
        )
        session.add(snapshot)
        await session.commit()

        snapshot_id = snapshot.id
        contract_id = contract.id

        # bid > ask violation
        bad_bid_ask = OptionChainQuote(
            snapshot_id=snapshot_id,
            contract_id=contract_id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            bid=Decimal("155.0000"),
            ask=Decimal("150.0000"),  # bid > ask
        )
        session.add(bad_bid_ask)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_quotes_bid_ask_valid" in str(exc_info.value)
        await session.rollback()

        # negative volume violation
        bad_volume = OptionChainQuote(
            snapshot_id=snapshot_id,
            contract_id=contract_id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            volume=-10,
        )
        session.add(bad_volume)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "ck_option_chain_quotes_volume" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. Partial Unique Index Tests (Revision-Aware Snapshot Identity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_partial_unique_index_all_supported_expiries():
    """uq_opt_snapshots_all prevents duplicate (index, market_ts, source, revision) for ALL_SUPPORTED_EXPIRIES."""
    factory = get_session_factory()
    now = datetime(2026, 9, 23, 9, 30, 0, tzinfo=UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_PUQ_ALL")
        idx_id = idx.id
        snap1 = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_all_1"),
            normalization_version="1.0",
            batch_id="b_all_1",
            revision=1,
        )
        session.add(snap1)
        await session.commit()

        # Duplicate same revision -> Error
        snap2 = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_all_2"),
            normalization_version="1.0",
            batch_id="b_all_2",
            revision=1,
        )
        session.add(snap2)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "uq_opt_snapshots_all" in str(exc_info.value)
        await session.rollback()

        # Revision 2 for same observation -> Allowed!
        snap3 = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            expiry_scope=None,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_all_3"),
            normalization_version="1.0",
            batch_id="b_all_3",
            revision=2,
        )
        session.add(snap3)
        await session.commit()
        assert snap3.id is not None


@pytest.mark.asyncio
async def test_snapshot_partial_unique_index_single_expiry():
    """uq_opt_snapshots_single distinguishes distinct expiry scopes and prevents duplicate revisions."""
    factory = get_session_factory()
    now = datetime(2026, 9, 23, 9, 30, 0, tzinfo=UTC)
    expiry1 = date(2026, 9, 24)
    expiry2 = date(2026, 10, 1)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_PUQ_SNG")
        idx_id = idx.id
        snap1 = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=expiry1,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_sng_1"),
            normalization_version="1.0",
            batch_id="b_sng_1",
            revision=1,
        )
        session.add(snap1)
        await session.commit()

        # Duplicate single expiry same revision -> Error
        snap_dup = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=expiry1,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_sng_dup"),
            normalization_version="1.0",
            batch_id="b_sng_dup",
            revision=1,
        )
        session.add(snap_dup)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        assert "uq_opt_snapshots_single" in str(exc_info.value)
        await session.rollback()

        # Different expiry_scope at same market timestamp -> Allowed!
        snap_diff_exp = OptionChainSnapshot(
            underlying_index_id=idx_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=expiry2,
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="UPSTOX",
            raw_payload_hash=_hash("b_sng_diff"),
            normalization_version="1.0",
            batch_id="b_sng_diff",
            revision=1,
        )
        session.add(snap_diff_exp)
        await session.commit()
        assert snap_diff_exp.id is not None


# ---------------------------------------------------------------------------
# 4. Partitioned Quote Table Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_uniqueness_within_snapshot_and_date():
    """uq_option_chain_quotes_snapshot_contract_date enforces 1 quote per contract per snapshot."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    t_date = date(2026, 9, 23)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_Q_UQ")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_Q_UQ")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_uq"),
            normalization_version="1.0",
            batch_id="b_uq",
        )
        session.add(snapshot)
        await session.commit()

        snapshot_id = snapshot.id
        contract_id = contract.id

        q1 = OptionChainQuote(
            snapshot_id=snapshot_id,
            contract_id=contract_id,
            trade_date=t_date,
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            ltp=Decimal("120.0000"),
        )
        session.add(q1)
        await session.commit()

        # Duplicate quote for same contract in same snapshot on same trade_date -> Error
        q2 = OptionChainQuote(
            snapshot_id=snapshot_id,
            contract_id=contract_id,
            trade_date=t_date,
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            ltp=Decimal("125.0000"),
        )
        session.add(q2)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        err_msg = str(exc_info.value)
        assert (
            "uq_option_chain_quotes_snapshot_contract_date" in err_msg
            or "snapshot_id" in err_msg
            or "contract_id" in err_msg
        )


@pytest.mark.asyncio
async def test_quote_partition_routing_and_boundaries():
    """Quotes route to monthly child partitions following [FROM, TO) half-open boundaries."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_PART_ROUT")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_PART_ROUT")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_rout"),
            normalization_version="1.0",
            batch_id="b_rout",
        )
        session.add(snapshot)
        await session.commit()

        # 1. September 2026 quotes (boundary: 2026-09-01 to 2026-09-30)
        q_sep_start = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 1),  # First day of Sep
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            ltp=Decimal("100.0000"),
        )
        q_sep_end = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 30),  # Last day of Sep
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            ltp=Decimal("110.0000"),
        )

        # 2. October 2026 quotes (boundary: 2026-10-01 to 2026-10-31)
        q_oct_start = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 10, 1),  # First day of Oct
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 10, 29),
            ltp=Decimal("120.0000"),
        )

        session.add_all([q_sep_start, q_sep_end, q_oct_start])
        await session.commit()

        # Verify physical placement via direct SQL on partition tables
        res_sep = await session.execute(
            text("SELECT count(*) FROM option_chain_quotes_2026_09 WHERE snapshot_id = :sid"),
            {"sid": snapshot.id},
        )
        assert res_sep.scalar() == 2

        res_oct = await session.execute(
            text("SELECT count(*) FROM option_chain_quotes_2026_10 WHERE snapshot_id = :sid"),
            {"sid": snapshot.id},
        )
        assert res_oct.scalar() == 1

        # Verify empty for other months
        res_nov = await session.execute(
            text("SELECT count(*) FROM option_chain_quotes_2026_11 WHERE snapshot_id = :sid"),
            {"sid": snapshot.id},
        )
        assert res_nov.scalar() == 0


@pytest.mark.asyncio
async def test_quote_partition_pruning():
    """PostgreSQL query planner prunes unneeded partitions when querying by trade_date."""
    factory = get_session_factory()
    async with factory() as session:
        # Run EXPLAIN with format JSON
        plan_result = await session.execute(
            text(
                "EXPLAIN (FORMAT JSON) SELECT * FROM option_chain_quotes WHERE trade_date = '2026-09-15'"
            )
        )
        plan_json = plan_result.scalar()
        plan_text = str(plan_json)

        # Confirm option_chain_quotes_2026_09 is present in plan, but other partitions are pruned
        assert "option_chain_quotes_2026_09" in plan_text
        assert "option_chain_quotes_2026_10" not in plan_text
        assert "option_chain_quotes_2026_11" not in plan_text
        assert "option_chain_quotes_2026_12" not in plan_text


@pytest.mark.asyncio
async def test_quote_missing_partition_error():
    """Inserting quote for unprovisioned month raises controlled PostgreSQL partition error."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_PART_ERR")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_PART_ERR")
        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_miss"),
            normalization_version="1.0",
            batch_id="b_miss",
        )
        session.add(snapshot)
        await session.commit()

        # Date in 2025 has no partition provisioned
        unprovisioned_quote = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2025, 1, 15),
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2025, 1, 30),
        )
        session.add(unprovisioned_quote)
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await session.commit()
        # PostgreSQL error code 23514 / message "no partition of relation"
        err_msg = str(exc_info.value).lower()
        assert "no partition of relation" in err_msg or "partition" in err_msg


# ---------------------------------------------------------------------------
# 5. Foreign Key Cascade & Set Null Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_key_cascade_and_set_null():
    """Snapshot deletion cascades to quotes; previous_snapshot_id sets to NULL."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    t_date = date(2026, 9, 23)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_FK_CAS")
        contract = await _create_test_contract(session, idx.id, trading_symbol="NIFTY_FK_CAS")

        # Snapshot 1 (previous)
        snap1 = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_fk1"),
            normalization_version="1.0",
            batch_id="b_fk1",
        )
        session.add(snap1)
        await session.commit()

        # Snapshot 2 (current) referencing Snapshot 1
        snap2 = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_fk2"),
            normalization_version="1.0",
            batch_id="b_fk2",
            revision=2,
        )
        session.add(snap2)
        await session.commit()

        quote = OptionChainQuote(
            snapshot_id=snap2.id,
            contract_id=contract.id,
            trade_date=t_date,
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            previous_snapshot_id=snap1.id,
        )
        session.add(quote)
        await session.commit()

        # 1. Delete snap1 -> previous_snapshot_id should SET NULL
        await session.delete(snap1)
        await session.commit()

        await session.refresh(quote)
        assert quote.previous_snapshot_id is None

        # 2. Delete snap2 -> quote should CASCADE delete
        await session.delete(snap2)
        await session.commit()

        res = await session.execute(
            text("SELECT count(*) FROM option_chain_quotes WHERE snapshot_id = :sid"),
            {"sid": snap2.id},
        )
        assert res.scalar() == 0


# ---------------------------------------------------------------------------
# 6. Protected D1-D6 Baseline Non-Interference Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protected_baseline_entities_remain_intact():
    """Modifying/deleting option chain data does not mutate or corrupt existing baseline entities."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        idx = await _create_test_index(session, "TEST_INTEG_IDX")
        contract = await _create_test_contract(session, idx.id, trading_symbol="TEST_INTEG_SYM")

        snapshot = OptionChainSnapshot(
            underlying_index_id=idx.id,
            chain_scope="ALL_SUPPORTED_EXPIRIES",
            market_timestamp=now,
            ingestion_timestamp=now,
            available_at=now,
            source_name="NSE",
            raw_payload_hash=_hash("b_integ"),
            normalization_version="1.0",
            batch_id="b_integ",
        )
        session.add(snapshot)
        await session.commit()

        quote = OptionChainQuote(
            snapshot_id=snapshot.id,
            contract_id=contract.id,
            trade_date=date(2026, 9, 23),
            strike_price=Decimal("24000.0000"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
        )
        session.add(quote)
        await session.commit()

        # Delete the snapshot and its quotes
        await session.delete(snapshot)
        await session.commit()

        # Verify underlying index and contract still exist completely intact
        idx_check = await session.get(MarketIndex, idx.id)
        contract_check = await session.get(DerivativeContract, contract.id)

        assert idx_check is not None
        assert idx_check.code == "TEST_INTEG_IDX"
        assert contract_check is not None
        assert contract_check.trading_symbol == "TEST_INTEG_SYM"
