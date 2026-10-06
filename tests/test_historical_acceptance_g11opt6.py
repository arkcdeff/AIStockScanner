"""G.11-OPT-6 Phase D — Historical analytics acceptance tests.

Deterministic, clearly-labelled ``NSE_TEST`` fixtures written into the isolated
test database (conftest enforces a ``*test*`` database and truncates per test).
Nothing here touches production history or represents fixture data as live.

Acceptance coverage (task §5):
* multiple genuine observation timestamps;
* 3/5/10/15/30/45/60-minute OI deltas from real baselines only;
* call/put OI and volume aggregation over selected strikes;
* futures ledger: direction/interpretation incl. insufficient-data refusals;
* options CE/PE comparisons across timestamps;
* replay at user-selected intervals incl. missing observations and session edges;
* no lookahead (``available_at <= evaluation_time``);
* DUPLICATE / OUT_OF_ORDER / STALE / MISSING / provider-error classification;
* NIFTY vs BANKNIFTY ATM and strike-grid separation;
* contract-master (derivative_contracts) resolution.

Every computed metric discloses provenance and its actual baseline timestamps.
Unavailable intervals are never pro-rated or substituted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.derivatives_observation_service import (
    ISSUE_DUPLICATE_TIMESTAMP,
    ISSUE_OUT_OF_ORDER,
    QUALITY_DUPLICATE,
    QUALITY_INSUFFICIENT_HISTORY,
    QUALITY_MISSING,
    QUALITY_OUT_OF_ORDER,
    QUALITY_STALE,
    QUALITY_VALID,
    ContractObservation,
    ObservationSeries,
    OptionObservationQuery,
    OptionObservationRepository,
    compute_interval_deltas,
    ist_trade_date,
    locate_baseline,
    order_and_audit,
    quality_status_for_series,
    quality_summary,
    tolerance_seconds_for,
)
from app.services.derivatives_scope import (
    BASELINE_INTRADAY_INTERVAL,
    infer_strike_grid,
    resolve_atm,
    resolve_lot_size,
)
from app.services.intraday_options_service import _aggregate_by_timestamp

SESSION = date(2026, 9, 24)
EXPIRY = date(2026, 9, 29)
# IST session 09:30..10:00 == 04:00..04:30 UTC on 2026-09-24
T0 = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
STRIKES = tuple(Decimal(s) for s in ("23100", "23150", "23200"))


# ---------------------------------------------------------------------------
# Fixture helpers (TEST_FIXTURE_ONLY)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_derivative_tables(_clean_test_db):
    factory = get_session_factory()
    for _ in range(2):
        async with factory() as session:
            await session.execute(text("DELETE FROM option_chain_quotes"))
            await session.execute(text("DELETE FROM option_chain_snapshots"))
            await session.execute(text("DELETE FROM derivative_daily_prices"))
            await session.execute(text("DELETE FROM derivative_contracts"))
            await session.execute(text("DELETE FROM market_indices"))
            await session.commit()
        yield
        break


async def _seed_index(code: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(code=code, name=f"{code} TEST INDEX", country="IN", exchange="NSE")
        session.add(index)
        await session.commit()
        return index.id


async def _seed_contracts(index_id: int, code: str, strikes=STRIKES) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for strike in strikes:
            for option_type in ("CE", "PE"):
                symbol = f"{code}{int(strike)}{option_type}"
                session.add(
                    DerivativeContract(
                        exchange="NSE",
                        segment="NFO",
                        instrument_type="OPTION",
                        underlying_index_id=index_id,
                        trading_symbol=symbol,
                        expiry_date=EXPIRY,
                        strike_price=strike,
                        option_type=option_type,
                        lot_size=65,
                        idempotency_hash=f"hash-{symbol}",
                        available_at=SESSION,
                        source_name="NSE_TEST",
                    )
                )
        await session.commit()


async def _seed_snapshot(
    index_id: int,
    code: str,
    *,
    market_ts: datetime,
    available_at: datetime,
    spot: str,
    oi_ltp_by_strike: dict[tuple[Decimal, str], tuple[int, str, int]],
    revision: int = 1,
) -> int:
    """One snapshot + quotes. ``oi_ltp_by_strike``: (strike, type) -> (oi, ltp, vol)."""
    factory = get_session_factory()
    async with factory() as session:
        snapshot = OptionChainSnapshot(
            underlying_index_id=index_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=EXPIRY,
            spot_price=Decimal(spot),
            market_timestamp=market_ts,
            source_timestamp=market_ts,
            ingestion_timestamp=available_at,
            available_at=available_at,
            source_name="NSE_TEST",
            raw_payload_hash=f"payload-{market_ts.isoformat()}-{revision}",
            normalization_version="TEST-1",
            batch_id=f"batch-{market_ts.isoformat()}-{revision}",
            revision=revision,
            quality_status="VALID",
            quality_flags={},
        )
        session.add(snapshot)
        await session.flush()

        for strike, option_type in oi_ltp_by_strike:
            symbol = f"{code}{int(strike)}{option_type}"
            row = await session.execute(
                text("SELECT id FROM derivative_contracts WHERE trading_symbol = :s"),
                {"s": symbol},
            )
            contract_id = row.scalar()
            assert contract_id is not None, f"contract {symbol} must be seeded first"
            oi, ltp, vol = oi_ltp_by_strike[(strike, option_type)]
            session.add(
                OptionChainQuote(
                    snapshot_id=snapshot.id,
                    contract_id=contract_id,
                    trade_date=ist_trade_date(market_ts),
                    strike_price=strike,
                    option_type=option_type,
                    expiry_date=EXPIRY,
                    ltp=Decimal(ltp),
                    volume=vol,
                    open_interest=oi,
                    quality_flags={},
                )
            )
        await session.commit()
        return snapshot.id


async def _seed_lattice(
    index_id: int,
    code: str,
    *,
    step_minutes: int,
    count: int,
) -> list[int]:
    """Deterministic observation lattice with rising OI.

    Snapshot i (i = 0..count-1) at T0 + i*step_minutes:
      OI(strike K, type T) = 100_000 + 10_000*i + 500*strike_index + type_offset
      LTP = 100 + 10*i + strike_index (+25 for PE); volume = 1000 + 100*i.
    Every expected interval delta is therefore exactly computable.
    """
    type_offset = {"CE": 0, "PE": 37}
    out: list[int] = []
    for i in range(count):
        ts = T0 + timedelta(minutes=step_minutes * i)
        data = {}
        for s_idx, strike in enumerate(STRIKES):
            for option_type in ("CE", "PE"):
                oi = 100_000 + 10_000 * i + 500 * s_idx + type_offset[option_type]
                ltp = 100 + 10 * i + s_idx + (0 if option_type == "CE" else 25)
                data[(strike, option_type)] = (oi, str(ltp), 1000 + 100 * i)
        out.append(
            await _seed_snapshot(
                index_id,
                code,
                market_ts=ts,
                available_at=ts + timedelta(seconds=2),
                spot=str(23150 + 5 * i),
                oi_ltp_by_strike=data,
            )
        )
    return out


async def _load_series(index_id: int) -> tuple[list[ObservationSeries], dict]:
    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        resolved = await repo.resolve_underlying_index_id("NIFTY")
        assert resolved == index_id
        return await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id,
                underlying_symbol="NIFTY",
                expiry=EXPIRY,
                session_date=SESSION,
            )
        )


def _obs(minutes: int, oi: int | None, ltp: str | None, *, key="NIFTY:23150:CE") -> ContractObservation:
    observed = T0 + timedelta(minutes=minutes)
    return ContractObservation(
        contract_key=key,
        label="TEST 23150 CE",
        observed_at=observed,
        available_at=observed,
        source="NSE_TEST",
        strike=Decimal("23150"),
        option_type="CE",
        expiry=EXPIRY,
        ltp=Decimal(ltp) if ltp is not None else None,
        open_interest=oi,
    )


def _series_from(points: list[ContractObservation]) -> ObservationSeries:
    series = ObservationSeries(contract_key=points[0].contract_key, label=points[0].label)
    series.observations, series.issues = order_and_audit(points)
    return series


# ---------------------------------------------------------------------------
# 1. Multiple genuine timestamps + contract resolution
# ---------------------------------------------------------------------------


async def test_multi_timestamp_series_load_and_contract_resolution():
    index_id = await _seed_index("NIFTY")
    await _seed_contracts(index_id, "NIFTY")
    snap_ids = await _seed_lattice(index_id, "NIFTY", step_minutes=5, count=7)
    assert len(snap_ids) == 7  # seven genuine observation instants

    series, meta = await _load_series(index_id)
    assert meta["available"] is True
    assert meta["snapshot_count"] == 7
    ce = [s for s in series if s.first() and s.first().option_type == "CE"]
    pe = [s for s in series if s.first() and s.first().option_type == "PE"]
    assert len(ce) == 3 and len(pe) == 3
    for s in series:
        assert s.count == 7
        assert {o.observed_at for o in s.observations} == {
            T0 + timedelta(minutes=5 * m) for m in range(7)
        }
    # contract identity resolution: every observation carries its master contract id
    assert all(o.contract_id is not None for s in series for o in s.observations)
    assert meta["quality_summary"]["overall"] == QUALITY_VALID


# ---------------------------------------------------------------------------
# 2. Interval windows from real baselines only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("minutes", [5, 10, 15, 30])
def test_interval_windows_on_five_minute_lattice(minutes: int):
    """5/10/15/30 m measure exactly from a 5-minute lattice within the span."""
    points = [
        _obs(m, 100_000 + 10_000 * (m // 5), str(100 + 10 * (m // 5)))
        for m in range(0, 35, 5)
    ]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, minutes)
    measured = [d for d in deltas if d.is_available]
    assert measured, f"{minutes}m should measure on the lattice"
    for d in measured:
        # exact lattice arithmetic between two real rows — never scaled
        assert d.oi_change == d.oi_end - d.oi_start
        assert d.within_tolerance is True
        assert d.baseline_provenance == BASELINE_INTRADAY_INTERVAL
        assert d.tolerance_seconds == tolerance_seconds_for(minutes)
    if minutes == 5:
        # clean case: end 09:35, baseline exactly 09:30
        first = next(d for d in measured if d.end_at == T0 + timedelta(minutes=5))
        assert first.oi_start == 100_000 and first.oi_end == 110_000
        assert first.oi_change == 10_000
        assert first.start_at == T0
    if minutes == 30:
        # only one 30-minute pair exists in the span: 09:30 -> 10:00
        exact = next(d for d in measured if d.end_at == T0 + timedelta(minutes=30))
        assert exact.start_at == T0
        assert exact.oi_change == 60_000


def test_interval_3m_on_minute_lattice():
    """3-minute windows measure exactly on a 1-minute lattice."""
    points = [
        _obs(m, 100_000 + 10_000 * m, str(100 + 10 * m)) for m in range(0, 11)
    ]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, 3)
    measured = [d for d in deltas if d.is_available]
    assert measured
    # end 09:04 baseline exactly 09:01: +30_000, both real rows
    target = next(d for d in measured if d.end_at == T0 + timedelta(minutes=4))
    assert target.start_at == T0 + timedelta(minutes=1)
    assert target.oi_change == 30_000


def test_interval_3m_cannot_measure_on_five_minute_lattice():
    """A 3m request on a 5-minute lattice has no baseline within tolerance —
    nothing is measured and nothing is pro-rated."""
    points = [_obs(m, 100_000 + 10_000 * (m // 5), "100") for m in range(0, 35, 5)]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, 3)
    assert all(not d.is_available for d in deltas)
    assert all(d.oi_change is None for d in deltas)


@pytest.mark.parametrize("minutes", [45, 60])
def test_intervals_beyond_history_are_insufficient(minutes: int):
    """45/60 m windows exceed the 30-minute fixture span — INSUFFICIENT, not scaled."""
    points = [
        _obs(m, 100_000 + 10_000 * (m // 5), "100") for m in range(0, 35, 5)
    ]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, minutes)
    assert all(not d.is_available for d in deltas)


def test_interval_never_prorates_a_gap():
    """09:30 + 10:00 observations: a '5m' delta at 10:00 must be MISSING
    (nearest baseline 30 min away), never scaled from the far pair."""
    points = [_obs(0, 100_000, "100"), _obs(30, 160_000, "130")]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, 5)
    measured = [d for d in deltas if d.is_available]
    assert measured == []


def test_baseline_tolerance_formula_and_lookup():
    assert tolerance_seconds_for(3) == max(30.0, 3 * 60 * 0.34)
    assert tolerance_seconds_for(60) == max(30.0, 60 * 60 * 0.34)

    points = [_obs(0, 100_000, "100"), _obs(5, 110_000, "110")]
    ordered, _ = order_and_audit(points)
    hit = locate_baseline(ordered, T0 + timedelta(minutes=5, seconds=10), tolerance_seconds_for(5))
    assert hit is not None and hit.open_interest == 110_000
    miss = locate_baseline(ordered, T0 + timedelta(minutes=25), tolerance_seconds_for(5))
    assert miss is None  # nearest observation 20 min away — outside tolerance


# ---------------------------------------------------------------------------
# 3. CE/PE aggregation over selected strikes
# ---------------------------------------------------------------------------


async def test_ce_pe_aggregation_over_selected_strikes():
    index_id = await _seed_index("NIFTY")
    await _seed_contracts(index_id, "NIFTY")
    await _seed_lattice(index_id, "NIFTY", step_minutes=5, count=7)

    series, _meta = await _load_series(index_id)
    assert len(series) == 6  # 3 strikes x CE/PE

    agg = _aggregate_by_timestamp(series)
    last_label = sorted(agg.keys())[-1]
    bucket = agg[last_label]

    # exact sums at the final instant (i=6): OI = 160_000 + 500*s_idx + offset
    expected_ce = sum(160_000 + 500 * s for s in range(3))
    expected_pe = sum(160_000 + 500 * s + 37 for s in range(3))
    assert bucket["call_oi"] == expected_ce
    assert bucket["put_oi"] == expected_pe
    assert bucket["expiry_labels"] == [EXPIRY.isoformat()]


async def test_ce_pe_ltp_series_comparisons():
    index_id = await _seed_index("NIFTY")
    await _seed_contracts(index_id, "NIFTY")
    await _seed_lattice(index_id, "NIFTY", step_minutes=5, count=7)

    series, _meta = await _load_series(index_id)
    ce_23150 = next(
        s for s in series
        if s.first() and s.first().strike == Decimal("23150") and s.first().option_type == "CE"
    )
    pe_23150 = next(
        s for s in series
        if s.first() and s.first().strike == Decimal("23150") and s.first().option_type == "PE"
    )
    ce_ltps = [float(o.ltp) for o in ce_23150.observations]
    pe_ltps = [float(o.ltp) for o in pe_23150.observations]
    # strike 23150 has s_idx=1: LTP = 100 + 10*i + 1 (+25 for PE)
    assert ce_ltps == [101.0, 111.0, 121.0, 131.0, 141.0, 151.0, 161.0]
    assert pe_ltps == [126.0, 136.0, 146.0, 156.0, 166.0, 176.0, 186.0]
    # OI trajectories: identical +10_000 steps for both types at the same strike
    ce_oi = [o.open_interest for o in ce_23150.observations]
    pe_oi = [o.open_interest for o in pe_23150.observations]
    assert [b - a for a, b in zip(ce_oi, pe_oi, strict=True)] == [37] * 7


# ---------------------------------------------------------------------------
# 4. Futures ledger direction / interpretation from real deltas
# ---------------------------------------------------------------------------


def test_futures_ledger_direction_and_interpretation():
    from app.services.derivatives_futures_ledger import (
        classify_direction,
        classify_futures_interval,
    )

    long_buildup = classify_futures_interval(Decimal("2"), 200)
    assert long_buildup["classification"] == "LONG_BUILDUP"
    assert long_buildup["oi_change"] == 200
    assert long_buildup["price_change"] == 2.0

    short_buildup = classify_futures_interval(Decimal("-1.5"), 300)
    assert short_buildup["classification"] == "SHORT_BUILDUP"

    short_covering = classify_futures_interval(Decimal("1"), -150)
    assert short_covering["classification"] == "SHORT_COVERING"

    long_unwinding = classify_futures_interval(Decimal("-2"), -100)
    assert long_unwinding["classification"] == "LONG_UNWINDING"

    neutral = classify_futures_interval(Decimal("0"), 0)
    assert neutral["classification"] == "NEUTRAL"

    # Missing either side -> INSUFFICIENT, never fabricated
    insuff = classify_futures_interval(None, None)
    assert insuff["classification"] == "INSUFFICIENT_DATA"
    assert insuff["reason"] == "MISSING_COMPARABLE_OBSERVATION"

    # A stale observation can never be classified as a genuine buildup
    stale = classify_futures_interval(Decimal("2"), 200, is_stale=True)
    assert stale["classification"] == "INSUFFICIENT_DATA"
    assert stale["reason"] == "STALE_OBSERVATION"

    assert classify_direction(Decimal("2"))["code"] == "UP"
    assert classify_direction(Decimal("-2"))["code"] == "DOWN"
    assert classify_direction(None)["code"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 5. Replay boundaries + no lookahead
# ---------------------------------------------------------------------------


async def test_replay_no_lookahead_and_session_boundaries():
    index_id = await _seed_index("NIFTY")
    await _seed_contracts(index_id, "NIFTY")
    await _seed_lattice(index_id, "NIFTY", step_minutes=5, count=7)

    factory = get_session_factory()

    async def visible(at_utc: datetime) -> list[datetime]:
        async with factory() as session:
            repo = OptionObservationRepository(session)
            series, _ = await repo.load_series(
                OptionObservationQuery(
                    underlying_index_id=index_id,
                    underlying_symbol="NIFTY",
                    expiry=EXPIRY,
                    session_date=SESSION,
                    evaluation_at=at_utc,
                )
            )
            return sorted({o.observed_at for s in series for o in s.observations})

    # PIT availability: snapshot written at ts+2s becomes visible at ts+3s
    stamps = await visible(T0 + timedelta(minutes=10, seconds=3))
    assert stamps == [T0 + timedelta(minutes=m) for m in (0, 5, 10)]

    # one second earlier: the 10-minute snapshot is not yet available
    stamps_early = await visible(T0 + timedelta(minutes=10, seconds=1))
    assert stamps_early == [T0 + timedelta(minutes=m) for m in (0, 5)]

    # session edge: replay at 09:29 IST sees nothing (session starts 09:30)
    before_open = await visible(datetime(2026, 9, 24, 3, 59, tzinfo=UTC))
    assert before_open == []

    # far-future evaluation sees exactly the recorded session — no more
    everything = await visible(T0 + timedelta(hours=6))
    assert len(everything) == 7


# ---------------------------------------------------------------------------
# 6. Quality classifications
# ---------------------------------------------------------------------------


def test_quality_duplicate_and_out_of_order():
    dup = _obs(5, 110_000, "110")
    series = _series_from([_obs(0, 100_000, "100"), dup, dup])
    assert ISSUE_DUPLICATE_TIMESTAMP in series.issues
    assert quality_status_for_series(series) == QUALITY_DUPLICATE

    late = _obs(30, 160_000, "130")
    series2 = _series_from([late, _obs(0, 100_000, "100")])
    assert ISSUE_OUT_OF_ORDER in series2.issues
    assert quality_status_for_series(series2) == QUALITY_OUT_OF_ORDER


def test_quality_missing_and_stale():
    series = _series_from([_obs(0, 100_000, "100"), _obs(5, None, "110")])
    assert quality_status_for_series(series) == QUALITY_MISSING

    stale = _series_from([_obs(0, 100_000, "100")])
    stale.issues = ("STALE_OBSERVATION",)
    assert quality_status_for_series(stale) == QUALITY_STALE


def test_quality_summary_prefers_observed_verdicts():
    good = _series_from([_obs(0, 100_000, "100"), _obs(5, 110_000, "110")])
    empty = ObservationSeries(contract_key="X", label="X")
    empty.reason = "NO_PERSISTED_OBSERVATIONS"
    summary = quality_summary([good, empty])
    assert summary["overall"] == QUALITY_VALID
    assert summary["counts"][QUALITY_INSUFFICIENT_HISTORY] == 1


def test_provider_error_is_never_zero_oi():
    """A provider outage must be rejected before persistence — never stored as
    a zero-OI or zero-volume observation."""
    from app.providers.models import NormalizedOptionChain
    from app.services.derivatives_capture_service import _chain_is_real_observation

    outage = NormalizedOptionChain(
        underlying_symbol="NIFTY",
        as_of_timestamp=T0,
        spot_price=None,
        expiries=[EXPIRY],
        calls=[],
        puts=[],
        source_name="UNAVAILABLE",
        spot_provenance="PROVIDER_ERROR",
        quality_flags={"PROVIDER_ERROR": "HTTP_429"},
    )
    ok, reason = _chain_is_real_observation(outage)
    assert not ok and reason


# ---------------------------------------------------------------------------
# 7. Cross-underlying ATM / strike-grid separation + lot resolution
# ---------------------------------------------------------------------------


def test_nifty_and_banknifty_atm_never_share_values():
    nifty_grid = infer_strike_grid(
        [Decimal(23100 + 50 * i) for i in range(0, 3)], source="NSE_TEST"
    )  # 23100 / 23150 / 23200 — brackets the NIFTY spot
    bank_grid = infer_strike_grid(
        [Decimal(55500 + 100 * i) for i in range(0, 3)], source="NSE_TEST"
    )  # 55500 / 55600 / 55700 — brackets the BANKNIFTY spot
    assert nifty_grid.step == Decimal("50")
    assert bank_grid.step == Decimal("100")

    nifty_atm = resolve_atm(Decimal("23140.5"), nifty_grid)
    bank_atm = resolve_atm(Decimal("55580.4"), bank_grid)
    assert nifty_atm.atm_strike == Decimal("23150")  # nearest listed (tie-break LOWER unused here)
    assert bank_atm.atm_strike == Decimal("55600")
    assert nifty_atm.atm_strike != bank_atm.atm_strike
    # a BANKNIFTY spot must never resolve onto the NIFTY grid (isolation proof):
    # forced onto the wrong grid it lands on the ceiling 23200, never 55600
    bank_on_nifty = resolve_atm(Decimal("55580.4"), nifty_grid)
    assert bank_on_nifty.atm_strike in nifty_grid.strikes
    assert bank_on_nifty.atm_strike != bank_atm.atm_strike


def test_lot_size_resolution_contract():
    rows = [{"lot_size": 65, "tick_size": 5}, {"lot_size": 65, "tick_size": 5}]
    resolution = resolve_lot_size(rows)
    assert resolution.lot_size == 65
    assert resolution.is_verified is True

    refusal = resolve_lot_size([])
    assert refusal.lot_size is None
    assert refusal.is_verified is False


# ---------------------------------------------------------------------------
# 8. Session-date boundary (IST midnight roll)
# ---------------------------------------------------------------------------


def test_session_date_assignment_at_utc_midnight():
    assert ist_trade_date(datetime(2026, 9, 24, 16, 0, tzinfo=UTC)) == date(2026, 9, 24)
    assert ist_trade_date(datetime(2026, 9, 24, 21, 30, tzinfo=UTC)) == date(2026, 9, 25)
