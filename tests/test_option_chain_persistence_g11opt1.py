"""Step 4 acceptance tests for G.11-OPT-1 OptionChainPersistenceService.

Coverage (required acceptance cases 1-10 plus review-hardening cases):

1.  Atomic success: header + multiple quotes commit together.
2.  Injected quote insertion failure rolls back the header, every quote and the
    partition DDL issued in the same unit of work.
3.  Identical payload replay returns DUPLICATE_IGNORED without duplicating data.
4.  Corrected payload creates a new immutable revision (old revision untouched).
5.  PIT cutoff excludes snapshots that were not yet available (incl. the exact
    ``available_at == evaluation_timestamp`` boundary).
6.  Highest PIT-visible accepted revision is selected; a revision that becomes
    available later is never exposed early.
7.  REJECTED snapshots are excluded from point-in-time retrieval.
8.  Provenance, source timestamps, source Greeks, ΔOI metadata and
    source-vs-calculated separation survive persistence and retrieval.
9.  A missing monthly partition is provisioned through the existing
    ``ensure_quote_partition_exists`` service (and rolled back on failure).
10. Concurrent persistence is safe: unique revisions, no partial snapshots, and
    concurrent identical payloads collapse to a single snapshot.
11. PIT quote queries filter by snapshot_id AND trade_date for partition pruning.
12. A caller-owned session is never committed by the service.
13. SINGLE_EXPIRY and ALL_SUPPORTED_EXPIRIES are distinct logical observations.
14. Unresolvable underlyings are refused (no fabricated identifiers or contracts).
15. Off-grid strikes are rejected by the existing OPT-1 validation.
16. Quotes without an authoritative contract are excluded and flagged, never
    silently invented.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.providers.models import NormalizedOptionChain, NormalizedQuote
from app.services.option_chain_config import (
    DQ_FLAG_MISSING_CONTRACT,
    DQ_STATUS_ACCEPT_WITH_FLAG,
    DQ_STATUS_REJECTED,
    DQ_STATUS_VALID,
)
from app.services.option_chain_partition_service import (
    check_quote_partition_exists,
    get_monthly_partition_bounds,
)
from app.services.option_chain_persistence_service import (
    CHAIN_SCOPE_ALL_EXPIRIES,
    CHAIN_SCOPE_SINGLE_EXPIRY,
    PERSIST_STATUS_DUPLICATE_IGNORED,
    PERSIST_STATUS_PERSISTED,
    CalculationContext,
    OptionChainPersistenceService,
    OptionChainValidationRejectedError,
    QuoteEnrichment,
    UnderlyingResolutionError,
    build_pit_quote_statement,
    derive_trade_date,
)

# ---------------------------------------------------------------------------
# Shared fixtures/constants
# ---------------------------------------------------------------------------

EXPIRY = date(2026, 9, 24)
STRIKES = (Decimal("24000"), Decimal("24050"))
MARKET_TS = datetime(2026, 9, 24, 4, 15, 0, tzinfo=UTC)  # 09:45 IST
SOURCE_TS = MARKET_TS + timedelta(seconds=1)
INGEST_TS = MARKET_TS + timedelta(seconds=2)
AVAILABLE_TS = INGEST_TS
SOURCE_NAME = "NSE_TEST"
EXPIRY_2035 = date(2035, 1, 28)
GREEKS: dict[str, Decimal] = {
    "iv": Decimal("0.155"),
    "delta": Decimal("0.52"),
    "gamma": Decimal("0.002"),
    "theta": Decimal("-12.4"),
    "vega": Decimal("18.1"),
    "rho": Decimal("0.05"),
}


def _observation_timestamps() -> tuple[datetime, datetime, datetime]:
    """Fresh, validation-safe market/source/available timestamps for one test.

    ``available_at`` must never precede the service-assigned wall-clock
    ``ingestion_timestamp`` (``datetime.now(UTC)`` computed during planning),
    otherwise OPT-1 flags ``AVAILABLE_AT_INVERSION`` and downgrades the
    snapshot to ``WARNING``. Anchoring availability only one second past a
    second-truncated ``market_ts`` leaves that window open to ordinary database
    latency, which made the suite timing-flaky; use a deterministic buffer.
    """

    market_ts = datetime.now(UTC).replace(microsecond=0)
    return market_ts, market_ts, market_ts + timedelta(minutes=1)


@pytest.fixture(autouse=True)
async def _clean_option_chain_tables(_clean_test_db):
    """Truncate OPT-1 tables around each test (conftest predates this schema)."""

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM option_chain_quotes"))
        await session.execute(text("DELETE FROM option_chain_snapshots"))
        await session.execute(text("DELETE FROM derivative_contracts"))
        await session.execute(text("DELETE FROM market_indices"))
        await session.commit()
    yield
    async with factory() as session:
        await session.execute(text("DELETE FROM option_chain_quotes"))
        await session.execute(text("DELETE FROM option_chain_snapshots"))
        await session.execute(text("DELETE FROM derivative_contracts"))
        await session.execute(text("DELETE FROM market_indices"))
        await session.commit()


async def _fresh_underlying(
    code: str = "NIFTY",
) -> tuple[int, dict[tuple[str, str], int]]:
    """Seed one isolated underlying plus two strikes x CE/PE contracts."""

    return await _seed_reference_data(code=code, name=f"{code} Index")


async def _seed_reference_data(
    *,
    code: str = "NIFTY",
    name: str = "Nifty 50",
    timezone_name: str = "Asia/Kolkata",
    expiry: date = EXPIRY,
    strikes: tuple[Decimal, ...] = STRIKES,
    exchange: str = "NSE",
    segment: str = "NFO",
) -> tuple[int, dict[tuple[str, str], int]]:
    """Create an authoritative MarketIndex plus its canonical OPTION contracts."""
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(
            code=code,
            name=name,
            country="INDIA",
            category="indian_index",
            exchange=exchange,
            currency="INR",
            timezone=timezone_name,
            is_active=True,
        )
        session.add(index)
        await session.flush()

        contracts: dict[tuple[str, str], int] = {}
        for strike in strikes:
            for option_type in ("CE", "PE"):
                contract = DerivativeContract(
                    exchange=exchange,
                    segment=segment,
                    instrument_type="OPTION",
                    underlying_index_id=index.id,
                    trading_symbol=(
                        f"{code}{expiry.strftime('%d%b%y').upper()}"
                        f"{int(strike)}{option_type}"
                    ),
                    expiry_date=expiry,
                    strike_price=strike,
                    option_type=option_type,
                    lot_size=25,
                    tick_size=Decimal("0.05"),
                    lifecycle_status="ACTIVE",
                    available_at=date(2020, 1, 1),
                    source_name="NSE",
                    source_reference="seed_contract_master",
                    fetched_at=datetime.now(UTC),
                )
                session.add(contract)
                await session.flush()
                contracts[(str(strike), option_type)] = contract.id

        index_id = index.id
        await session.commit()
        return index_id, contracts


def _build_chain(
    contracts: dict[tuple[str, str], int],
    *,
    market_timestamp: datetime = MARKET_TS,
    source_timestamp: datetime | None = SOURCE_TS,
    strikes: tuple[Decimal, ...] = STRIKES,
    expiry: date = EXPIRY,
    underlying: str = "NIFTY",
    ltp_shift: Decimal = Decimal("0"),
    quality_status: str = DQ_STATUS_VALID,
    expected_strike_count: int | None = None,
    source_name: str = SOURCE_NAME,
    source_reference: str | None = "NSE_SNAPSHOT_0001",
    raw_payload_hash: str | None = "payload-sha-0001",
    chain_scope: str = CHAIN_SCOPE_SINGLE_EXPIRY,
    expiry_scope: date | None = None,
    is_truncated: bool = False,
    spot_price: Decimal = Decimal("24550.2500"),
    contract_overrides: dict[tuple[str, str], int] | None = None,
    source_greeks: dict[str, Decimal | float] | None = None,
) -> NormalizedOptionChain:
    """Build a valid NormalizedOptionChain bound to authoritative contracts."""
    overrides = contract_overrides or {}
    greeks = source_greeks if source_greeks is not None else dict(GREEKS)
    calls: list[NormalizedQuote] = []
    puts: list[NormalizedQuote] = []
    for strike in strikes:
        for option_type, bucket in (("CE", calls), ("PE", puts)):
            key = (str(strike), option_type)
            base = Decimal("150.00") + (strike - strikes[0]) + ltp_shift
            bucket.append(
                NormalizedQuote(
                    symbol=(
                        f"{underlying}{expiry.strftime('%d%b%y').upper()}"
                        f"{int(strike)}{option_type}"
                    ),
                    instrument_type="OPTION",
                    ltp=base,
                    bid=base - Decimal("0.50"),
                    ask=base + Decimal("0.50"),
                    bid_qty=100,
                    ask_qty=110,
                    volume=5000,
                    open_interest=50000,
                    change_in_open_interest=1200 if option_type == "CE" else -800,
                    source_change_in_oi=1200 if option_type == "CE" else -800,
                    strike_price=strike,
                    option_type=option_type,
                    expiry_date=expiry,
                    timestamp=market_timestamp,
                    source_name=source_name,
                    provider_id="NSE_TEST_PROVIDER",
                    contract_id=overrides.get(key, contracts.get(key)),
                    provider_instrument_id=(
                        f"NSE_FO|{underlying}_{int(strike)}_{option_type}"
                    ),
                    source_greeks=dict(greeks),
                )
            )
    return NormalizedOptionChain(
        underlying_symbol=underlying,
        as_of_timestamp=market_timestamp,
        spot_price=spot_price,
        expiries=[expiry],
        calls=calls,
        puts=puts,
        ce_pe_paired_pct=100.0,
        provider_id="NSE_TEST_PROVIDER",
        source_name=source_name,
        chain_scope=chain_scope,
        expiry_scope=(
            expiry_scope
            if expiry_scope is not None
            else (expiry if chain_scope == CHAIN_SCOPE_SINGLE_EXPIRY else None)
        ),
        market_timestamp=market_timestamp,
        source_timestamp=source_timestamp,
        raw_payload_hash=raw_payload_hash,
        source_reference=source_reference,
        is_truncated=is_truncated,
        expected_strike_count=expected_strike_count,
        observed_strike_count=len(strikes),
        quality_status=quality_status,
        quality_flags={},
    )


async def test_01_atomic_success_persists_header_and_all_quotes() -> None:
    """1. Atomic success: header + multiple quotes commit together."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY", market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    result = await service.persist_snapshot(chain, available_at=available_ts)

    assert result.status == PERSIST_STATUS_PERSISTED
    assert result.revision == 1
    assert result.quote_count == 4
    assert result.underlying_index_id == index_id
    factory = get_session_factory()
    async with factory() as session:
        snapshot = (
            await session.execute(
                select(OptionChainSnapshot).where(OptionChainSnapshot.id == result.snapshot_id)
            )
        ).scalar_one()
        assert snapshot.revision == 1
        assert snapshot.quality_status == DQ_STATUS_VALID
        assert await _count_quotes(session, result.snapshot_id) == 4


async def test_02_quote_failure_rolls_back_header_quotes_and_partition() -> None:
    """2. Quote failure rolls back header, quotes and partition DDL in one unit."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    # A fixed past observation month whose quote partition is provably absent,
    # so the partition DDL issued inside the failing unit of work can be
    # observed rolling back together with the header and quote rows.
    market_ts = datetime(2025, 3, 17, 4, 15, 0, tzinfo=UTC)
    source_ts = market_ts
    available_ts = market_ts + timedelta(seconds=2)
    month_start, _, partition_name = get_monthly_partition_bounds(
        derive_trade_date(market_ts)
    )
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
        await session.commit()
    assert await check_quote_partition_exists(month_start) is False

    chain = _build_chain(
        contracts,
        underlying="NIFTY",
        market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    # Injected quote-insertion failure: bid above ask violates
    # ck_option_chain_quotes_bid_ask_valid when the quote rows flush.
    chain.calls[0].bid = chain.calls[0].ask + Decimal("10.00")

    with pytest.raises(IntegrityError):
        await service.persist_snapshot(chain, available_at=available_ts)
    async with factory() as session:
        assert await _count_snapshots(session, underlying_index_id=index_id) == 0
        quote_total = int(
            await session.scalar(select(func.count()).select_from(OptionChainQuote)) or 0
        )
        assert quote_total == 0
    assert await check_quote_partition_exists(month_start) is False


async def test_03_identical_payload_replay_is_duplicate_ignored() -> None:
    """3. Identical payload replay returns DUPLICATE_IGNORED without new rows."""

    _, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY", market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    first = await service.persist_snapshot(chain, available_at=available_ts)
    replay = await service.persist_snapshot(chain, available_at=available_ts)
    assert first.status == PERSIST_STATUS_PERSISTED
    assert replay.status == PERSIST_STATUS_DUPLICATE_IGNORED
    assert replay.snapshot_id == first.snapshot_id
    assert replay.revision == first.revision
    factory = get_session_factory()
    async with factory() as session:
        assert await _count_quotes(session, first.snapshot_id) == 4


async def test_04_corrected_payload_creates_new_immutable_revision() -> None:
    """4. Corrected payload creates a new immutable revision; rev 1 is untouched."""

    _, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    first = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    second = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts, ltp_shift=Decimal("7.50"),
            raw_payload_hash="payload-sha-0002",
        ),
        available_at=available_ts,
    )
    assert first.revision == 1
    assert second.revision == 2
    assert second.snapshot_id != first.snapshot_id
    factory = get_session_factory()
    async with factory() as session:
        first_row = (
            await session.execute(
                select(OptionChainSnapshot).where(OptionChainSnapshot.id == first.snapshot_id)
            )
        ).scalar_one()
        second_row = (
            await session.execute(
                select(OptionChainSnapshot).where(OptionChainSnapshot.id == second.snapshot_id)
            )
        ).scalar_one()
        assert first_row.revision == 1
        assert second_row.revision == 2
        assert first_row.raw_payload_hash != second_row.raw_payload_hash


async def test_05_pit_cutoff_excludes_unavailable_and_includes_exact_boundary() -> None:
    """5. PIT uses available_at <= evaluation; the exact boundary is visible."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    result = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    before = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=available_ts - timedelta(milliseconds=1),
    )
    assert before is None
    boundary = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=available_ts,
    )
    assert boundary is not None
    assert boundary.snapshot_id == result.snapshot_id
    assert len(boundary.quotes) == 4


async def test_06_pit_selects_highest_visible_revision_never_future() -> None:
    """6. PIT selects highest visible accepted revision; future revs stay hidden."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    rev2_available = available_ts + timedelta(minutes=5)
    first = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    second = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts, ltp_shift=Decimal("3.25"),
        ),
        available_at=rev2_available,
    )
    early = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=rev2_available - timedelta(seconds=1),
    )
    assert early is not None
    assert early.snapshot_id == first.snapshot_id
    late = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=rev2_available + timedelta(seconds=1),
    )
    assert late is not None
    assert late.snapshot_id == second.snapshot_id
    assert late.revision == 2


async def test_07_rejected_snapshots_are_excluded_from_pit() -> None:
    """7. REJECTED snapshots are never returned by point-in-time retrieval."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    accepted = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    rejected_market_ts = market_ts + timedelta(minutes=1)
    rejected = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY",
            market_timestamp=rejected_market_ts,
            source_timestamp=rejected_market_ts,
            quality_status=DQ_STATUS_REJECTED,
        ),
        available_at=available_ts + timedelta(minutes=1),
        allow_rejected=True,
    )
    assert rejected.quality_status == DQ_STATUS_REJECTED
    pit = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=available_ts + timedelta(minutes=2),
    )
    assert pit is not None
    assert pit.snapshot_id == accepted.snapshot_id
    explicit = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=available_ts + timedelta(minutes=2),
        market_timestamp=rejected_market_ts,
        include_rejected=True,
    )
    assert explicit is not None
    assert explicit.snapshot_id == rejected.snapshot_id


async def test_08_provenance_greeks_doi_and_calculation_separation() -> None:
    """8. Provenance, timestamps, Greeks, ΔOI and calc separation round-trip."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    enriched_contract_id = next(iter(contracts.values()))
    result = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
        source_reference="unit-test-provenance-08",
        quote_enrichments={
            enriched_contract_id: QuoteEnrichment(
                interval_seconds=60,
                interval_change_in_oi=321,
                session_change_in_oi=654,
                calculated_delta=Decimal("0.512345"),
            )
        },
        calculation_context=CalculationContext(
            calculation_spot_price=Decimal("24010.5000"),
            risk_free_rate_used=Decimal("0.065000"),
            pricing_model_version="BLACK76-V1",
        ),
    )
    pit = await service.get_pit_snapshot(
        underlying_index_id=index_id,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY,
        evaluation_timestamp=available_ts + timedelta(seconds=1),
    )
    assert pit is not None
    assert pit.snapshot_id == result.snapshot_id
    assert pit.market_timestamp == market_ts
    assert pit.source_timestamp == source_ts
    assert pit.available_at == available_ts
    assert pit.source_name == SOURCE_NAME
    assert pit.risk_free_rate_used == Decimal("0.065000")
    assert pit.pricing_model_version == "BLACK76-V1"
    quote = pit.quote_by_contract(enriched_contract_id)
    assert quote is not None
    assert quote.source_iv == GREEKS["iv"]
    assert quote.source_delta == GREEKS["delta"]
    assert quote.interval_seconds == 60
    assert quote.interval_change_in_oi == 321
    assert quote.session_change_in_oi == 654
    assert quote.calculated_delta == Decimal("0.512345")
    assert quote.calculated_iv is None


async def test_09_missing_monthly_partition_is_provisioned() -> None:
    """9. Missing monthly partitions are provisioned and hold persisted quotes."""

    _, contracts = await _fresh_underlying()
    service = _service()
    # Fixed past month: drop its partition first so provisioning is observable.
    market_ts = datetime(2025, 4, 15, 4, 15, 0, tzinfo=UTC)
    source_ts = market_ts
    available_ts = market_ts + timedelta(seconds=2)
    month_start, _, partition_name = get_monthly_partition_bounds(
        derive_trade_date(market_ts)
    )
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
        await session.commit()
    assert await check_quote_partition_exists(month_start) is False

    result = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    assert result.partition_name == partition_name
    assert result.trade_date == derive_trade_date(market_ts)
    assert await check_quote_partition_exists(month_start) is True
    compiled = str(
        build_pit_quote_statement(result.snapshot_id, result.trade_date).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "option_chain_quotes" in compiled
    assert str(result.snapshot_id) in compiled


async def test_10_concurrent_persistence_is_safe_and_replays_collapse() -> None:
    """10. Concurrent writers are safe; identical replays collapse to one row."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY", market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    duplicates = await asyncio.gather(
        *(service.persist_snapshot(chain, available_at=available_ts) for _ in range(10))
    )
    assert len({item.snapshot_id for item in duplicates}) == 1
    assert any(item.status == PERSIST_STATUS_PERSISTED for item in duplicates)
    revised = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts, ltp_shift=Decimal("1.25"),
        ),
        available_at=available_ts + timedelta(seconds=1),
    )
    assert revised.revision == 2
    factory = get_session_factory()
    async with factory() as session:
        assert await _count_snapshots(session, underlying_index_id=index_id) == 2
        assert await _count_quotes(session, revised.snapshot_id) == 4


async def test_11_pit_quote_statement_filters_snapshot_and_trade_date() -> None:
    """11. PIT quote queries filter by snapshot_id AND trade_date for pruning."""

    _, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    result = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", market_timestamp=market_ts,
            source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    statement = build_pit_quote_statement(result.snapshot_id, result.trade_date)
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "snapshot_id" in compiled
    assert "trade_date" in compiled
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(statement)).scalars().all()
        assert len(rows) == 4
        assert {row.snapshot_id for row in rows} == {result.snapshot_id}


async def test_12_caller_owned_session_is_never_committed() -> None:
    """12. A caller-owned session is never committed by the service."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY", market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    factory = get_session_factory()
    async with factory() as session:
        result = await service.persist_snapshot(
            chain, available_at=available_ts, session=session
        )
        assert result.status == PERSIST_STATUS_PERSISTED
        assert session.in_transaction()
        await session.rollback()
    async with factory() as session:
        assert await _count_snapshots(session, underlying_index_id=index_id) == 0
    committed = await service.persist_snapshot(chain, available_at=available_ts)
    assert committed.revision == 1


async def test_13_single_and_all_expiries_are_distinct_observations() -> None:
    """13. SINGLE_EXPIRY and ALL_SUPPORTED_EXPIRIES never share revisions."""

    index_id, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    single = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
            market_timestamp=market_ts, source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    all_expiries = await service.persist_snapshot(
        _build_chain(
            contracts, underlying="NIFTY", chain_scope=CHAIN_SCOPE_ALL_EXPIRIES,
            expiry_scope=None, market_timestamp=market_ts, source_timestamp=source_ts,
        ),
        available_at=available_ts,
    )
    assert single.chain_scope == CHAIN_SCOPE_SINGLE_EXPIRY
    assert all_expiries.chain_scope == CHAIN_SCOPE_ALL_EXPIRIES
    assert all_expiries.expiry_scope is None
    assert single.revision == 1
    assert all_expiries.revision == 1
    assert single.snapshot_id != all_expiries.snapshot_id
    factory = get_session_factory()
    async with factory() as session:
        assert (
            await _count_snapshots(
                session, underlying_index_id=index_id,
                chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY, expiry_scope=EXPIRY,
            )
            == 1
        )


async def test_14_unresolvable_underlying_is_refused_without_fabrication() -> None:
    """14. Unknown underlyings raise; no identifiers/contracts are fabricated."""

    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    factory = get_session_factory()
    async with factory() as session:
        indices_before = int(
            await session.scalar(select(func.count()).select_from(MarketIndex)) or 0
        )
        contracts_before = int(
            await session.scalar(select(func.count()).select_from(DerivativeContract)) or 0
        )
    chain = NormalizedOptionChain(
        underlying_symbol="UNKNOWN_INDEX_XYZ_G11",
        as_of_timestamp=market_ts,
        spot_price=Decimal("24000.00"),
        expiries=[EXPIRY_2035],
        calls=[],
        puts=[],
        provider_id="NSE_TEST_PROVIDER",
        source_name=SOURCE_NAME,
        chain_scope=CHAIN_SCOPE_SINGLE_EXPIRY,
        expiry_scope=EXPIRY_2035,
        market_timestamp=market_ts,
        source_timestamp=source_ts,
    )
    with pytest.raises(UnderlyingResolutionError):
        await service.persist_snapshot(chain, available_at=available_ts)
    async with factory() as session:
        assert (
            int(await session.scalar(select(func.count()).select_from(MarketIndex)) or 0)
            == indices_before
        )
        assert (
            int(
                await session.scalar(select(func.count()).select_from(DerivativeContract))
                or 0
            )
            == contracts_before
        )


async def test_15_off_grid_strikes_are_rejected_by_opt1_validation() -> None:
    """15. Off-grid strikes are REJECTED by OPT-1 validation; nothing persists."""

    # Seed contracts for the off-grid strike too: validation only checks strikes
    # from quotes that resolve to an authoritative contract.
    index_id, contracts = await _seed_reference_data(
        strikes=(Decimal("24000"), Decimal("24017")),
    )
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY",
        strikes=(Decimal("24000"), Decimal("24017")),
        market_timestamp=market_ts, source_timestamp=source_ts,
    )
    with pytest.raises(OptionChainValidationRejectedError):
        await service.persist_snapshot(chain, available_at=available_ts)
    factory = get_session_factory()
    async with factory() as session:
        assert await _count_snapshots(session, underlying_index_id=index_id) == 0


async def test_16_quotes_without_contract_are_flagged_not_invented() -> None:
    """16. Unmapped quotes are excluded/flagged MISSING_CONTRACT, never invented."""

    _, contracts = await _fresh_underlying()
    service = _service()
    market_ts, source_ts, available_ts = _observation_timestamps()
    chain = _build_chain(
        contracts, underlying="NIFTY", market_timestamp=market_ts,
        source_timestamp=source_ts,
        strikes=(Decimal("24000"), Decimal("24100")),
        contract_overrides={("24000", "CE"): contracts[("24000", "CE")]},
    )
    result = await service.persist_snapshot(chain, available_at=available_ts)
    assert result.status == PERSIST_STATUS_PERSISTED
    assert result.quality_status == DQ_STATUS_ACCEPT_WITH_FLAG
    assert DQ_FLAG_MISSING_CONTRACT in result.quality_flags
    # 24100 CE/PE have no authoritative contract; 24000 CE/PE resolve (via the
    # explicit override and the authoritative contract cache respectively).
    assert result.quality_flags[DQ_FLAG_MISSING_CONTRACT]["count"] == 2
    assert result.quote_count == 2
    assert len(result.skipped_quotes) == 2
    factory = get_session_factory()
    async with factory() as session:
        assert await _count_quotes(session, result.snapshot_id) == 2
        assert (
            int(
                await session.scalar(select(func.count()).select_from(DerivativeContract))
                or 0
            )
            == 4
        )


async def _count_snapshots(
    session: AsyncSession,
    *,
    underlying_index_id: int,
    chain_scope: str = CHAIN_SCOPE_SINGLE_EXPIRY,
    expiry_scope: date | None = EXPIRY,
) -> int:
    stmt = select(func.count()).select_from(OptionChainSnapshot).where(
        OptionChainSnapshot.underlying_index_id == underlying_index_id,
        OptionChainSnapshot.chain_scope == chain_scope,
    )
    if chain_scope == CHAIN_SCOPE_SINGLE_EXPIRY:
        stmt = stmt.where(OptionChainSnapshot.expiry_scope == expiry_scope)
    return int(await session.scalar(stmt) or 0)


def _service() -> OptionChainPersistenceService:
    return OptionChainPersistenceService()


async def _count_quotes(session: AsyncSession, snapshot_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(OptionChainQuote)
            .where(OptionChainQuote.snapshot_id == snapshot_id)
        )
        or 0
    )
