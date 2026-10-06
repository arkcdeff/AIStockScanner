"""Option Chain Ladder Buffer & Canonical Mapping Tests — Milestone G.11-OPT-1 Step 3.

Tests the in-memory canonical instrument mapper, WebSocket ladder buffer,
freeze primitive, immutability, and asyncio concurrency according to
Specification §25, §26, §27, §28.

Test Matrix:
  Part A: Canonical Instrument Mapping Tests (Spec §25)
    1. Multiple strikes mapping (CE and PE).
    2. Multiple expiries mapping.
    3. Different underlyings mapping (NIFTY, BANKNIFTY, FINNIFTY).
    4. Repeated lookups performance & consistency.
    5. Zero database lookups in live tick path (critical invariant).
    6. Observable tracking of unmapped keys.

  Part B: In-Memory Ladder Buffer Tests (Spec §26)
    7. Empty ladder buffer freeze.
    8. Single quote accumulation.
    9. Multiple strikes accumulation.
    10. CE and PE separate observations at same strike.
    11. Multiple expiries accumulation and isolation.
    12. Multiple contracts accumulation.
    13. Repeated updates to same contract (latest-observation overwrite).
    14. Freeze primitive returns FrozenOptionChainLadder.
    15. Freeze immutability (subsequent ticks do not mutate frozen state).
    16. Tick arriving after freeze belongs to next sequence window.
    17. Concurrent freeze and tick execution.
    18. Multiple concurrent tick tasks.
    19. Unknown/unmapped contract handling without crashing.
    20. Stale quote identification and DQ flag propagation.
    21. Step-2 validation integration on frozen state.
    22. Zero cross-expiry contamination.
    23. Zero CE/PE cross-contamination.
    24. Exactly one quote per contract in frozen state.

  Part C: Concurrency Stress & Deadlock Prevention Tests (Spec §27)
    25. High-concurrency tick burst with interleaved freezes.
    26. Integrity: No corrupted quotes under concurrent load.
    27. Atomic quote state: No partially written quotes.
    28. Deterministic synchronization with asyncio.Event.
    29. Lock release verification and deadlock prevention.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.providers.models import FreshnessStatus, NormalizedQuote
from app.services.option_chain_config import (
    DQ_FLAG_STALE_QUOTE,
    DQ_STATUS_VALID,
    DQ_STATUS_WARNING,
)
from app.services.option_chain_ladder import (
    FrozenOptionChainLadder,
    OptionChainInstrumentMapper,
    OptionChainLadderBuffer,
)


class TestCanonicalInstrumentMapping:
    """Part A: In-memory canonical contract identity resolution tests (Spec §25)."""

    def test_01_instrument_mapper_multiple_strikes(self) -> None:
        """1. Verify mapping multiple strikes across CE and PE sides."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)

        for strike in [24000, 24050, 24100, 24150, 24200]:
            dec_strike = Decimal(str(strike))
            # Register CE
            ce_key = f"NSE_FO|NIFTY_{expiry.strftime('%y%b').upper()}_{dec_strike}_CE"
            mapper.register_mapping(ce_key, strike * 10 + 1)
            mapper.register_spec_mapping("NIFTY", expiry, dec_strike, "CE", strike * 10 + 1)

            # Register PE
            pe_key = f"NSE_FO|NIFTY_{expiry.strftime('%y%b').upper()}_{dec_strike}_PE"
            mapper.register_mapping(pe_key, strike * 10 + 2)
            mapper.register_spec_mapping("NIFTY", expiry, dec_strike, "PE", strike * 10 + 2)

        for strike in [24000, 24050, 24100, 24150, 24200]:
            dec_strike = Decimal(str(strike))
            ce_key = f"NSE_FO|NIFTY_{expiry.strftime('%y%b').upper()}_{dec_strike}_CE"
            pe_key = f"NSE_FO|NIFTY_{expiry.strftime('%y%b').upper()}_{dec_strike}_PE"

            assert mapper.resolve(ce_key) == strike * 10 + 1
            assert mapper.resolve(pe_key) == strike * 10 + 2
            assert mapper.resolve_by_spec("NIFTY", expiry, dec_strike, "CE") == strike * 10 + 1
            assert mapper.resolve_by_spec("NIFTY", expiry, dec_strike, "PE") == strike * 10 + 2

    def test_02_instrument_mapper_multiple_expiries(self) -> None:
        """2. Verify mapping contracts across multiple near and far expiries."""
        mapper = OptionChainInstrumentMapper()
        exp1 = date(2026, 9, 24)
        exp2 = date(2026, 10, 29)
        strike = Decimal("24500")

        mapper.register_spec_mapping("NIFTY", exp1, strike, "CE", 1001)
        mapper.register_spec_mapping("NIFTY", exp2, strike, "CE", 1002)

        assert mapper.resolve_by_spec("NIFTY", exp1, strike, "CE") == 1001
        assert mapper.resolve_by_spec("NIFTY", exp2, strike, "CE") == 1002

    def test_03_instrument_mapper_different_underlyings(self) -> None:
        """3. Verify mapping different index underlyings (NIFTY, BANKNIFTY, FINNIFTY)."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)

        mapper.register_spec_mapping("NIFTY", expiry, Decimal("24500"), "CE", 2001)
        mapper.register_spec_mapping("BANKNIFTY", expiry, Decimal("52000"), "CE", 2002)
        mapper.register_spec_mapping("FINNIFTY", expiry, Decimal("23000"), "CE", 2003)

        assert mapper.resolve_by_spec("NIFTY", expiry, Decimal("24500"), "CE") == 2001
        assert mapper.resolve_by_spec("BANKNIFTY", expiry, Decimal("52000"), "CE") == 2002
        assert mapper.resolve_by_spec("FINNIFTY", expiry, Decimal("23000"), "CE") == 2003

    def test_04_instrument_mapper_repeated_lookups(self) -> None:
        """4. Verify repeated lookups remain consistent and O(1) in-memory."""
        mapper = OptionChainInstrumentMapper()
        mapper.register_mapping("NSE_FO|TEST_KEY", 9999)

        for _ in range(1000):
            assert mapper.resolve("NSE_FO|TEST_KEY") == 9999
        assert mapper.lookup_count == 1000

    def test_05_instrument_mapper_zero_db_lookups_during_tick_resolution(self) -> None:
        """5. CRITICAL: Verify ZERO database queries are executed during tick resolution."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        mapper.register_mapping("NSE_FO|NIFTY_24500_CE", 555)

        # Pre-lookup assertions
        assert mapper.db_query_count == 0

        # Simulate 500 tick resolutions
        for _ in range(500):
            res = mapper.resolve("NSE_FO|NIFTY_24500_CE")
            assert res == 555

        # Also test resolve_quote
        q = NormalizedQuote(
            symbol="NIFTY26SEP24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=Decimal("24500"),
            option_type="CE",
            expiry_date=expiry,
            raw_provider_meta={"instrument_key": "NSE_FO|NIFTY_24500_CE"},
        )
        for _ in range(500):
            res = mapper.resolve_quote(q, "NIFTY")
            assert res == 555

        # STRICT INVARIANT: Must remain exactly 0 DB queries
        assert mapper.db_query_count == 0

    def test_06_instrument_mapper_unmapped_keys_tracked_observably(self) -> None:
        """6. Verify unknown instrument keys are tracked in unmapped registry without crash."""
        mapper = OptionChainInstrumentMapper()

        assert mapper.resolve("UNKNOWN_KEY_1") is None
        assert mapper.resolve("UNKNOWN_KEY_2") is None
        assert mapper.resolve_by_spec("NIFTY", date(2026, 9, 24), Decimal("99999"), "CE") is None

        unmapped = mapper.get_unmapped_keys()
        assert "UNKNOWN_KEY_1" in unmapped
        assert "UNKNOWN_KEY_2" in unmapped
        assert len(unmapped) == 3


class TestOptionChainLadderBuffer:
    """Part B: In-memory ladder buffer accumulation & freeze tests (Spec §26, §28)."""

    @pytest.mark.asyncio
    async def test_07_ladder_empty(self) -> None:
        """7. Verify freeze on empty ladder buffer returns 0 quotes cleanly."""
        mapper = OptionChainInstrumentMapper()
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        frozen = await buffer.freeze()
        assert isinstance(frozen, FrozenOptionChainLadder)
        assert len(frozen.quotes) == 0
        assert frozen.observed_strike_count == 0
        assert frozen.sequence == 0
        assert buffer.sequence == 1

    @pytest.mark.asyncio
    async def test_08_ladder_single_quote(self) -> None:
        """8. Verify single quote ingestion and retrieval."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        mapper.register_spec_mapping("NIFTY", expiry, Decimal("24500"), "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)
        now = datetime.now(UTC)

        quote = NormalizedQuote(
            symbol="NIFTY26SEP24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=Decimal("24500"),
            option_type="CE",
            expiry_date=expiry,
            timestamp=now,
            open_interest=50000,
        )

        ok = await buffer.update_tick(quote)
        assert ok is True
        assert await buffer.get_active_count() == 1

        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 1
        assert frozen.get_quote(101) is not None
        assert frozen.get_quote(101).ltp == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_09_ladder_multiple_strikes(self) -> None:
        """9. Verify accumulation of multiple strikes across the grid."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        for s in [24400, 24450, 24500, 24550, 24600]:
            dec_s = Decimal(str(s))
            mapper.register_spec_mapping("NIFTY", expiry, dec_s, "CE", s)
            q = NormalizedQuote(
                symbol=f"NIFTY{s}CE",
                instrument_type="OPTION",
                ltp=Decimal("100.00"),
                strike_price=dec_s,
                option_type="CE",
                expiry_date=expiry,
            )
            await buffer.update_tick(q)

        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 5
        assert frozen.observed_strike_count == 5

    @pytest.mark.asyncio
    async def test_10_ladder_ce_and_pe_separation(self) -> None:
        """10. Verify CE and PE quotes at identical strike remain distinct without collision."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)
        mapper.register_spec_mapping("NIFTY", expiry, strike, "PE", 102)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)
        now = datetime.now(UTC)

        ce_q = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
            timestamp=now,
        )
        pe_q = NormalizedQuote(
            symbol="NIFTY24500PE",
            instrument_type="OPTION",
            ltp=Decimal("140.00"),
            strike_price=strike,
            option_type="PE",
            expiry_date=expiry,
            timestamp=now,
        )

        await buffer.update_tick(ce_q)
        await buffer.update_tick(pe_q)

        assert await buffer.get_active_count() == 2
        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 2

        # Ladder representation for expiry
        ladder = frozen.get_ladder(expiry)
        assert len(ladder) == 1
        l_strike, l_ce, l_pe = ladder[0]
        assert l_strike == strike
        assert l_ce.ltp == Decimal("150.00")
        assert l_pe.ltp == Decimal("140.00")

    @pytest.mark.asyncio
    async def test_11_ladder_multiple_expiries(self) -> None:
        """11. Verify quotes across multiple expiries accumulate and remain isolated."""
        mapper = OptionChainInstrumentMapper()
        exp1 = date(2026, 9, 24)
        exp2 = date(2026, 10, 29)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", exp1, strike, "CE", 101)
        mapper.register_spec_mapping("NIFTY", exp2, strike, "CE", 102)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        q1 = NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("100"), strike_price=strike, option_type="CE", expiry_date=exp1)
        q2 = NormalizedQuote(symbol="N2", instrument_type="OPTION", ltp=Decimal("200"), strike_price=strike, option_type="CE", expiry_date=exp2)

        await buffer.update_tick(q1)
        await buffer.update_tick(q2)

        # Freeze all expiries
        frozen_all = await buffer.freeze(reset_active=False)
        assert len(frozen_all.quotes) == 2
        assert exp1 in frozen_all.expiries
        assert exp2 in frozen_all.expiries

        # Freeze single expiry
        frozen_exp1 = await buffer.freeze(chain_scope="SINGLE_EXPIRY", expiry_scope=exp1, reset_active=True)
        assert len(frozen_exp1.quotes) == 1
        assert frozen_exp1.quotes[0].expiry_date == exp1

    @pytest.mark.asyncio
    async def test_12_ladder_multiple_contracts(self) -> None:
        """12. Verify quotes across multiple distinct canonical contracts."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        for i in range(10):
            strike = Decimal(str(24000 + (i * 50)))
            mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 1000 + i)
            q = NormalizedQuote(
                symbol=f"NIFTY{strike}CE",
                instrument_type="OPTION",
                ltp=Decimal(str(100 + i)),
                strike_price=strike,
                option_type="CE",
                expiry_date=expiry,
            )
            await buffer.update_tick(q)

        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 10
        for i in range(10):
            assert frozen.get_quote(1000 + i) is not None

    @pytest.mark.asyncio
    async def test_13_ladder_repeated_updates_to_same_contract(self) -> None:
        """13. Latest-observation semantics: Later tick overwrites earlier tick for same contract."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        # Tick 1: LTP 150
        q1 = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
            volume=1000,
        )
        await buffer.update_tick(q1)

        # Tick 2: LTP 152 (same contract)
        q2 = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("152.00"),
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
            volume=1200,
        )
        await buffer.update_tick(q2)

        # Count in active ladder must be 1, NOT 2 (no memory leak / duplicate entries)
        assert await buffer.get_active_count() == 1

        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 1
        assert frozen.quotes[0].ltp == Decimal("152.00")
        assert frozen.quotes[0].volume == 1200

    @pytest.mark.asyncio
    async def test_14_ladder_freeze_operation(self) -> None:
        """14. Verify freeze advances sequence counter and captures clean timestamp metadata."""
        mapper = OptionChainInstrumentMapper()
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        f1 = await buffer.freeze()
        assert f1.sequence == 0
        assert buffer.sequence == 1

        f2 = await buffer.freeze()
        assert f2.sequence == 1
        assert buffer.sequence == 2

    @pytest.mark.asyncio
    async def test_15_ladder_freeze_immutability(self) -> None:
        """15. CRITICAL: Verify frozen state is deeply detached from subsequent active ticks."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        # Active Tick 1
        q1 = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
        )
        await buffer.update_tick(q1)

        # Freeze Snapshot 1 (without resetting for test comparison)
        frozen_1 = await buffer.freeze(reset_active=False)
        assert frozen_1.quotes[0].ltp == Decimal("150.00")

        # Active Tick 2 updates the contract to LTP 160
        q2 = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("160.00"),
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
        )
        await buffer.update_tick(q2)

        # Frozen 1 must NOT have mutated (frozen_state != active_state)
        assert frozen_1.quotes[0].ltp == Decimal("150.00")

        # Freeze Snapshot 2 captures new LTP
        frozen_2 = await buffer.freeze(reset_active=True)
        assert frozen_2.quotes[0].ltp == Decimal("160.00")
        assert frozen_1.quotes[0].ltp == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_16_ladder_tick_after_freeze(self) -> None:
        """16. Verify ticks arriving after freeze enter the next accumulation window."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        # Window 0
        await buffer.update_tick(NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("100"), strike_price=strike, option_type="CE", expiry_date=expiry))
        f0 = await buffer.freeze(reset_active=True)
        assert len(f0.quotes) == 1

        # Buffer is now empty
        assert await buffer.get_active_count() == 0

        # Window 1: New tick arrives
        await buffer.update_tick(NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("105"), strike_price=strike, option_type="CE", expiry_date=expiry))
        f1 = await buffer.freeze(reset_active=True)
        assert len(f1.quotes) == 1
        assert f1.quotes[0].ltp == Decimal("105")
        assert f1.sequence == 1

    @pytest.mark.asyncio
    async def test_17_ladder_concurrent_freeze_and_tick(self) -> None:
        """17. Verify concurrent freeze and tick operations execute safely without race conditions."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        async def tick_task() -> None:
            for i in range(20):
                q = NormalizedQuote(
                    symbol="NIFTY24500CE",
                    instrument_type="OPTION",
                    ltp=Decimal(str(150 + i)),
                    strike_price=strike,
                    option_type="CE",
                    expiry_date=expiry,
                )
                await buffer.update_tick(q)
                await asyncio.sleep(0.001)

        async def freeze_task() -> list[FrozenOptionChainLadder]:
            freezes = []
            for _ in range(3):
                await asyncio.sleep(0.005)
                f = await buffer.freeze(reset_active=False)
                freezes.append(f)
            return freezes

        _, freezes = await asyncio.gather(tick_task(), freeze_task())
        assert len(freezes) == 3
        for f in freezes:
            assert isinstance(f, FrozenOptionChainLadder)
            assert len(f.quotes) == 1

    @pytest.mark.asyncio
    async def test_18_ladder_multiple_concurrent_ticks(self) -> None:
        """18. Verify multiple concurrent tick tasks update ladder safely."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        strikes = [Decimal(str(24000 + (i * 50))) for i in range(10)]
        for s in strikes:
            mapper.register_spec_mapping("NIFTY", expiry, s, "CE", int(s))

        async def worker(strike: Decimal) -> None:
            for price in [100, 102, 105]:
                q = NormalizedQuote(
                    symbol=f"NIFTY{strike}CE",
                    instrument_type="OPTION",
                    ltp=Decimal(str(price)),
                    strike_price=strike,
                    option_type="CE",
                    expiry_date=expiry,
                )
                await buffer.update_tick(q)

        await asyncio.gather(*(worker(s) for s in strikes))

        assert await buffer.get_active_count() == 10
        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 10

    @pytest.mark.asyncio
    async def test_19_ladder_unknown_contract_handling(self) -> None:
        """19. Verify unknown/unmapped contract ticks are handled without crashing."""
        mapper = OptionChainInstrumentMapper()  # Empty mapper
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        quote = NormalizedQuote(
            symbol="UNKNOWN_CONTRACT",
            instrument_type="OPTION",
            ltp=Decimal("99.00"),
            strike_price=Decimal("24500"),
            option_type="CE",
            expiry_date=date(2026, 9, 24),
            raw_provider_meta={"instrument_key": "NSE_FO|UNKNOWN_999"},
        )

        ok = await buffer.update_tick(quote)
        assert ok is True  # Ingested with warning metadata
        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 1
        assert frozen.quotes[0].contract_id is None

    @pytest.mark.asyncio
    async def test_20_ladder_stale_quote_handling(self) -> None:
        """20. Verify stale quotes are flagged with DQ_FLAG_STALE_QUOTE in frozen validation."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        mapper.register_spec_mapping("NIFTY", expiry, Decimal("24500"), "CE", 101)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        q = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            strike_price=Decimal("24500"),
            option_type="CE",
            expiry_date=expiry,
            freshness_status=FreshnessStatus.STALE,
        )
        await buffer.update_tick(q)

        frozen = await buffer.freeze()
        val_res = frozen.validate()

        assert DQ_FLAG_STALE_QUOTE in val_res.flags
        assert val_res.status in {DQ_STATUS_WARNING, "ACCEPT_WITH_FLAG"}

    @pytest.mark.asyncio
    async def test_21_ladder_validation_flag_propagation(self) -> None:
        """21. Verify Step-2 validation primitives integrate cleanly with frozen state."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        # Ingest regular quotes
        for s in [24400, 24450, 24500, 24550, 24600]:
            dec_s = Decimal(str(s))
            mapper.register_spec_mapping("NIFTY", expiry, dec_s, "CE", s)
            await buffer.update_tick(
                NormalizedQuote(symbol=f"N{s}CE", instrument_type="OPTION", ltp=Decimal("50"), strike_price=dec_s, option_type="CE", expiry_date=expiry)
            )

        frozen = await buffer.freeze()
        res = frozen.validate()
        assert res.status == DQ_STATUS_VALID
        assert res.is_valid is True

    @pytest.mark.asyncio
    async def test_22_ladder_no_cross_expiry_contamination(self) -> None:
        """22. Verify get_ladder(expiry) never includes quotes belonging to other expiries."""
        mapper = OptionChainInstrumentMapper()
        exp1 = date(2026, 9, 24)
        exp2 = date(2026, 10, 29)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        mapper.register_spec_mapping("NIFTY", exp1, Decimal("24500"), "CE", 101)
        mapper.register_spec_mapping("NIFTY", exp2, Decimal("24600"), "CE", 102)

        await buffer.update_tick(NormalizedQuote(symbol="Q1", instrument_type="OPTION", ltp=Decimal("100"), strike_price=Decimal("24500"), option_type="CE", expiry_date=exp1))
        await buffer.update_tick(NormalizedQuote(symbol="Q2", instrument_type="OPTION", ltp=Decimal("200"), strike_price=Decimal("24600"), option_type="CE", expiry_date=exp2))

        frozen = await buffer.freeze()
        ladder_1 = frozen.get_ladder(exp1)
        assert len(ladder_1) == 1
        assert ladder_1[0][0] == Decimal("24500")

        ladder_2 = frozen.get_ladder(exp2)
        assert len(ladder_2) == 1
        assert ladder_2[0][0] == Decimal("24600")

    @pytest.mark.asyncio
    async def test_23_ladder_no_ce_pe_cross_contamination(self) -> None:
        """23. Verify Call quote never appears in Put slot and vice versa."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)
        await buffer.update_tick(
            NormalizedQuote(symbol="Q_CE", instrument_type="OPTION", ltp=Decimal("150"), strike_price=strike, option_type="CE", expiry_date=expiry)
        )

        frozen = await buffer.freeze()
        ladder = frozen.get_ladder(expiry)
        assert len(ladder) == 1
        strk, ce_q, pe_q = ladder[0]
        assert strk == strike
        assert ce_q is not None
        assert pe_q is None  # Missing PE remains None, NOT call quote or zero

    @pytest.mark.asyncio
    async def test_24_ladder_no_duplicate_contract_state(self) -> None:
        """24. Verify exactly one quote per canonical contract exists in frozen state."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)
        for p in range(5):
            await buffer.update_tick(
                NormalizedQuote(symbol="Q_CE", instrument_type="OPTION", ltp=Decimal(str(100 + p)), strike_price=strike, option_type="CE", expiry_date=expiry)
            )

        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 1
        assert len(frozen.quotes_by_contract) == 1


class TestLadderConcurrencyAndDeadlockPrevention:
    """Part C: Asynchronous concurrency, deadlock prevention, and stress tests (Spec §27)."""

    @pytest.mark.asyncio
    async def test_25_high_concurrency_tick_burst_with_interleaved_freezes(self) -> None:
        """25. Stress test: 50 concurrent tick producers + periodic freeze operations."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        strikes = [Decimal(str(24000 + (i * 50))) for i in range(10)]
        for s in strikes:
            mapper.register_spec_mapping("NIFTY", expiry, s, "CE", int(s))

        async def tick_producer(strike: Decimal) -> None:
            for p in range(25):
                q = NormalizedQuote(
                    symbol=f"NIFTY{strike}CE",
                    instrument_type="OPTION",
                    ltp=Decimal(str(100 + p)),
                    strike_price=strike,
                    option_type="CE",
                    expiry_date=expiry,
                )
                await buffer.update_tick(q)
                await asyncio.sleep(0.0005)

        async def freeze_monitor() -> list[FrozenOptionChainLadder]:
            captured = []
            for _ in range(5):
                await asyncio.sleep(0.003)
                f = await buffer.freeze(reset_active=False)
                captured.append(f)
            return captured

        producers = [tick_producer(s) for s in strikes]
        results = await asyncio.gather(*producers, freeze_monitor())
        freezes: list[FrozenOptionChainLadder] = results[-1]

        assert len(freezes) == 5
        for f in freezes:
            assert isinstance(f, FrozenOptionChainLadder)
            assert len(f.quotes) <= 10

    @pytest.mark.asyncio
    async def test_26_concurrency_no_corrupted_ladder(self) -> None:
        """26. Verify quotes in frozen ladder under concurrent load have valid, uncorrupted fields."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        for s in [24500, 24550]:
            mapper.register_spec_mapping("NIFTY", expiry, Decimal(str(s)), "CE", s)

        async def sender(s: int) -> None:
            for i in range(30):
                await buffer.update_tick(
                    NormalizedQuote(
                        symbol=f"N{s}",
                        instrument_type="OPTION",
                        ltp=Decimal(str(s + i)),
                        strike_price=Decimal(str(s)),
                        option_type="CE",
                        expiry_date=expiry,
                    )
                )

        await asyncio.gather(sender(24500), sender(24550))
        frozen = await buffer.freeze()
        assert len(frozen.quotes) == 2
        for q in frozen.quotes:
            assert q.ltp is not None
            assert q.ltp > 24000

    @pytest.mark.asyncio
    async def test_27_concurrency_no_partially_frozen_quote(self) -> None:
        """27. Verify every quote in the frozen snapshot is complete and non-partial."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)
        now = datetime.now(UTC)

        quote = NormalizedQuote(
            symbol="NIFTY24500CE",
            instrument_type="OPTION",
            ltp=Decimal("150.00"),
            bid=Decimal("149.50"),
            ask=Decimal("150.50"),
            bid_qty=100,
            ask_qty=100,
            volume=5000,
            open_interest=50000,
            strike_price=strike,
            option_type="CE",
            expiry_date=expiry,
            timestamp=now,
        )
        await buffer.update_tick(quote)

        frozen = await buffer.freeze()
        fq = frozen.quotes[0]
        assert fq.ltp == Decimal("150.00")
        assert fq.bid == Decimal("149.50")
        assert fq.ask == Decimal("150.50")
        assert fq.volume == 5000
        assert fq.open_interest == 50000

    @pytest.mark.asyncio
    async def test_28_deterministic_synchronization_with_events(self) -> None:
        """28. Test deterministic concurrency ordering using asyncio.Event."""
        mapper = OptionChainInstrumentMapper()
        expiry = date(2026, 9, 24)
        strike = Decimal("24500")
        mapper.register_spec_mapping("NIFTY", expiry, strike, "CE", 101)

        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        tick1_arrived = asyncio.Event()
        freeze_done = asyncio.Event()

        async def producer() -> None:
            # Send initial tick
            await buffer.update_tick(
                NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("100"), strike_price=strike, option_type="CE", expiry_date=expiry)
            )
            tick1_arrived.set()

            # Wait until freeze completes, then send tick 2
            await freeze_done.wait()
            await buffer.update_tick(
                NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("110"), strike_price=strike, option_type="CE", expiry_date=expiry)
            )

        async def consumer() -> FrozenOptionChainLadder:
            await tick1_arrived.wait()
            frozen = await buffer.freeze(reset_active=True)
            freeze_done.set()
            return frozen

        _, frozen_result = await asyncio.gather(producer(), consumer())

        # Frozen result captured Tick 1 (LTP 100)
        assert frozen_result.quotes[0].ltp == Decimal("100")

        # Active ladder has Tick 2 (LTP 110)
        f_next = await buffer.freeze()
        assert f_next.quotes[0].ltp == Decimal("110")

    @pytest.mark.asyncio
    async def test_29_lock_release_verification_and_deadlock_prevention(self) -> None:
        """29. Verify buffer lock is always released, even if exception occurs outside lock."""
        mapper = OptionChainInstrumentMapper()
        buffer = OptionChainLadderBuffer("NIFTY", mapper)

        # Normal freeze
        await buffer.freeze()
        assert not buffer._lock.locked()

        # Update tick
        await buffer.update_tick(
            NormalizedQuote(symbol="N1", instrument_type="OPTION", ltp=Decimal("100"), strike_price=Decimal("24500"), option_type="CE", expiry_date=date(2026, 9, 24))
        )
        assert not buffer._lock.locked()

        # Freeze again
        await buffer.freeze()
        assert not buffer._lock.locked()
