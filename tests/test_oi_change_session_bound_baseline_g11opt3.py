"""G.11 OI Change Phase 1 — session-bound historical baseline hardening.

An interval ΔOI baseline must be a real observation from the *same* trading
session as the evaluation instant, in addition to satisfying the existing
interval/tolerance rules. These tests write real-format observations into the
isolated test database (``conftest`` refuses to run unless the database name
contains "test") and assert through the public ``get_interval_oi_change`` path
that a previous-session observation can never be co-located as a baseline.

The observed empty state is preserved on purpose: with no same-session pair the
response must stay ``measurement_available = false`` / ``change_in_oi = null`` /
``pro_rata_scaling_applied = false``, while the provider day change remains
exposed separately and is never substituted for an interval measurement.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.derivatives_observation_service import (
    ContractObservation,
    ObservationSeries,
    ist_trade_date,
)
from app.services.derivatives_scope import (
    BASELINE_INTRADAY_INTERVAL,
    PROV_MODEL_CALCULATED,
    PROV_PERSISTED_OBSERVATION,
    PROV_UNAVAILABLE,
)
from app.services.intraday_options_service import (
    IntradayOptionsService,
    _measured_interval_lookup,
    get_ist_now,
)
from app.services.option_chain_adapter import MockOptionChainAdapter

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------

def _ist(day: date, hour: int, minute: int) -> datetime:
    """An IST wall-clock instant on ``day``."""
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


@pytest.fixture(autouse=True)
async def _clean_derivative_tables(_clean_test_db):
    """Clear the derivative tables ``conftest`` predates (OPT-1 schema)."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM option_chain_quotes"))
        await session.execute(text("DELETE FROM option_chain_snapshots"))
        await session.execute(text("DELETE FROM derivative_daily_prices"))
        await session.execute(text("DELETE FROM derivative_contracts"))
        await session.execute(text("DELETE FROM market_indices"))
        await session.commit()
    yield


@pytest.fixture
async def service():
    """Offline service wired to the real (test) database."""
    factory = get_session_factory()
    session = factory()
    svc = IntradayOptionsService(adapter=MockOptionChainAdapter(), session=session)
    try:
        yield svc
    finally:
        await session.close()


async def _seed_index(code: str = "NIFTY") -> int:
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(code=code, name=f"{code} TEST INDEX", country="IN", exchange="NSE")
        session.add(index)
        await session.commit()
        return index.id


async def _current_scope(service: IntradayOptionsService) -> tuple[date, Decimal]:
    """The expiry and ATM strike the deterministic mock ladder actually lists."""
    ladder = await service.get_option_chain_ladder("NIFTY", strike_count=5)
    assert ladder["selected_expiry"], "the mock ladder must resolve a listed expiry"
    atm = next((r for r in ladder["ladder"] if r["is_atm"]), None)
    assert atm is not None, "the mock ladder must resolve an ATM strike"
    return date.fromisoformat(ladder["selected_expiry"]), Decimal(str(atm["strike_price"]))


async def _seed_contracts(
    index_id: int, expiry: date, strike: Decimal
) -> dict[tuple[Decimal, str], int]:
    """Persist the CE/PE contracts for one strike; return their ids."""
    ids: dict[tuple[Decimal, str], int] = {}
    factory = get_session_factory()
    async with factory() as session:
        for option_type in ("CE", "PE"):
            symbol = f"NIFTY{int(strike)}{option_type}"
            contract = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=index_id,
                trading_symbol=symbol,
                expiry_date=expiry,
                strike_price=strike,
                option_type=option_type,
                lot_size=65,
                idempotency_hash=f"hash-{expiry.isoformat()}-{symbol}",
                available_at=expiry,
                source_name="NSE_TEST",
            )
            session.add(contract)
            await session.flush()
            ids[(strike, option_type)] = contract.id
        await session.commit()
    return ids


async def _seed_observations(
    index_id: int,
    *,
    expiry: date,
    strike: Decimal,
    observations: list[tuple[datetime, int]],
    ltp: str = "120.00",
) -> None:
    """Persist real-format snapshots plus CE/PE quotes for one strike.

    ``observations`` is a list of ``(market_timestamp, open_interest)`` pairs. The
    same OI and LTP are written to both sides so each side measures the same real
    delta.
    """
    contract_ids = await _seed_contracts(index_id, expiry, strike)
    factory = get_session_factory()
    async with factory() as session:
        for market_ts, oi in observations:
            available_at = market_ts + timedelta(seconds=1)
            stamp = market_ts.astimezone(UTC).isoformat()
            snapshot = OptionChainSnapshot(
                underlying_index_id=index_id,
                chain_scope="SINGLE_EXPIRY",
                expiry_scope=expiry,
                spot_price=Decimal("24500.00"),
                market_timestamp=market_ts,
                source_timestamp=market_ts,
                ingestion_timestamp=available_at,
                available_at=available_at,
                source_name="NSE_TEST",
                raw_payload_hash=f"payload-{stamp}",
                normalization_version="TEST-1",
                batch_id=f"batch-{stamp}",
                revision=1,
                quality_status="VALID",
                quality_flags={},
            )
            session.add(snapshot)
            await session.flush()
            for option_type in ("CE", "PE"):
                session.add(
                    OptionChainQuote(
                        snapshot_id=snapshot.id,
                        contract_id=contract_ids[(strike, option_type)],
                        trade_date=ist_trade_date(market_ts),
                        strike_price=strike,
                        option_type=option_type,
                        expiry_date=expiry,
                        ltp=Decimal(ltp),
                        volume=1000,
                        open_interest=oi,
                        quality_flags={},
                    )
                )
        await session.commit()


def _row(payload: dict[str, Any], strike: Decimal) -> dict[str, Any]:
    return next(
        r for r in payload["strikes"] if Decimal(str(r["strike_price"])) == strike
    )


def _obs(day: date, hour: int, minute: int, oi: int) -> ContractObservation:
    observed = _ist(day, hour, minute)
    return ContractObservation(
        contract_key="2026-10-09:24500:CE",
        label="NIFTY 24500 CE",
        observed_at=observed,
        available_at=observed + timedelta(seconds=1),
        source=PROV_PERSISTED_OBSERVATION,
        strike=Decimal("24500"),
        option_type="CE",
        expiry=date(2026, 10, 9),
        ltp=Decimal("120.00"),
        open_interest=oi,
    )


def _series(points: list[ContractObservation]) -> ObservationSeries:
    series = ObservationSeries(
        contract_key=points[0].contract_key, label=points[0].label
    )
    series.observations = sorted(points, key=lambda o: o.observed_at)
    return series


# ---------------------------------------------------------------------------
# 1. Same-session valid baseline
# ---------------------------------------------------------------------------

async def test_same_session_pair_is_measured(service) -> None:
    """A real 15-minute pair inside the current session is accepted."""
    today = get_ist_now().date()
    index_id = await _seed_index()
    expiry, strike = await _current_scope(service)
    await _seed_observations(
        index_id,
        expiry=expiry,
        strike=strike,
        observations=[(_ist(today, 9, 30), 1000), (_ist(today, 9, 45), 1300)],
    )

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    assert payload["baseline_type"] == BASELINE_INTRADAY_INTERVAL
    assert payload["measurement_available"] is True
    assert payload["pro_rata_scaling_applied"] is False
    call = _row(payload, strike)["call"]
    assert call["measurement_available"] is True
    assert call["change_in_oi"] == 300
    assert call["measurement_start_ist"] == "09:30:00"
    assert call["effective_seconds"] == 900.0
    assert call["provenance"] == PROV_MODEL_CALCULATED
    coverage = payload["real_observation_coverage"]
    assert coverage["session_date"] == today.isoformat()
    assert coverage["observation_count"] == 4  # CE + PE, two observations each


# ---------------------------------------------------------------------------
# 2./4. A previous session must be rejected
# ---------------------------------------------------------------------------

async def test_single_current_observation_cannot_borrow_a_previous_session_pair(service) -> None:
    """The current session has one observation; yesterday's valid pair must not serve.

    Yesterday's 09:30 → 09:45 pair satisfies the timestamp-distance logic, which is
    exactly the hazard: the latest measurable delta in the series used to be
    yesterday's. It must never be selected for today's window.
    """
    today = get_ist_now().date()
    previous = today - timedelta(days=1)
    index_id = await _seed_index()
    expiry, strike = await _current_scope(service)
    await _seed_observations(
        index_id,
        expiry=expiry,
        strike=strike,
        observations=[
            (_ist(previous, 9, 30), 1000),
            (_ist(previous, 9, 45), 5000),
            (_ist(previous, 15, 15), 5200),
            (_ist(today, 9, 30), 5300),
        ],
    )

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    assert payload["measurement_available"] is False
    assert payload["pro_rata_scaling_applied"] is False
    row = _row(payload, strike)
    for side in ("call", "put"):
        assert row[side]["change_in_oi"] is None
        assert row[side]["measurement_available"] is False
        assert row[side]["provenance"] == PROV_UNAVAILABLE
    coverage = payload["real_observation_coverage"]
    assert coverage["session_date"] == today.isoformat()
    # Only today's single observation per side was read at all; the three
    # previous-session observations were excluded before any delta was computed.
    assert coverage["observation_count"] == 2
    assert payload["disclosure"] is not None
    assert "no proportional scaling" in payload["disclosure"].lower()


async def test_previous_session_only_yields_unavailable(service) -> None:
    """Only previous-session observations exist: unavailable, ΔOI null."""
    today = get_ist_now().date()
    previous = today - timedelta(days=1)
    index_id = await _seed_index()
    expiry, strike = await _current_scope(service)
    await _seed_observations(
        index_id,
        expiry=expiry,
        strike=strike,
        observations=[(_ist(previous, 9, 30), 1000), (_ist(previous, 9, 45), 5000)],
    )

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    assert payload["measurement_available"] is False
    row = _row(payload, strike)
    assert row["call"]["change_in_oi"] is None
    assert row["put"]["change_in_oi"] is None
    assert payload["real_observation_coverage"]["available"] is False
    # The provider's own day change is still reported, under its own key.
    assert row["call"]["provider_day_change_in_oi"] is not None


# ---------------------------------------------------------------------------
# 3. Multiple candidates across sessions
# ---------------------------------------------------------------------------

async def test_current_session_pair_outranks_previous_session_candidates(service) -> None:
    """Both sessions have measured pairs; only the current session's may be used."""
    today = get_ist_now().date()
    previous = today - timedelta(days=1)
    index_id = await _seed_index()
    expiry, strike = await _current_scope(service)
    await _seed_observations(
        index_id,
        expiry=expiry,
        strike=strike,
        observations=[
            # Previous session: a huge, entirely stale ΔOI (+4000).
            (_ist(previous, 9, 30), 1000),
            (_ist(previous, 9, 45), 5000),
            # Current session: the real pair for today's window (+200).
            (_ist(today, 9, 30), 2000),
            (_ist(today, 9, 45), 2200),
        ],
    )

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    call = _row(payload, strike)["call"]
    assert call["measurement_available"] is True
    assert call["change_in_oi"] == 200, "the stale previous-session pair must not win"
    assert call["measurement_start_ist"] == "09:30:00"
    coverage = payload["real_observation_coverage"]
    assert coverage["session_date"] == today.isoformat()
    # Only the current session's four observations (CE/PE × 2) were read at all.
    assert coverage["observation_count"] == 4


def test_mixed_session_series_can_only_measure_the_requested_session() -> None:
    """The selection layer itself refuses a baseline pair from another session."""
    current = date(2026, 10, 5)
    previous = date(2026, 10, 4)

    mixed = _series(
        [
            _obs(previous, 9, 30, 1000),
            _obs(previous, 9, 45, 5000),
            _obs(current, 9, 30, 2000),
            _obs(current, 9, 45, 2200),
        ]
    )
    bound = _measured_interval_lookup([mixed], 15, session_date=current)
    assert bound[(Decimal("24500"), "CE")]["oi_change"] == 200

    stale = _series([_obs(previous, 9, 30, 1000), _obs(previous, 9, 45, 5000)])
    assert _measured_interval_lookup([stale], 15, session_date=current) == {}
    # Without a session bound the stale pair is measurable — which is precisely what
    # the service-level session bound prevents from ever reaching this stage.
    assert _measured_interval_lookup([stale], 15) != {}


# ---------------------------------------------------------------------------
# 5. Genuine zero stays zero
# ---------------------------------------------------------------------------

async def test_identical_oi_measures_zero_not_null(service) -> None:
    """Two same-session observations with identical OI measure a real 0, not null."""
    today = get_ist_now().date()
    index_id = await _seed_index()
    expiry, strike = await _current_scope(service)
    await _seed_observations(
        index_id,
        expiry=expiry,
        strike=strike,
        observations=[(_ist(today, 9, 30), 1000), (_ist(today, 9, 45), 1000)],
    )

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    assert payload["measurement_available"] is True
    call = _row(payload, strike)["call"]
    assert call["change_in_oi"] == 0
    assert call["change_in_oi"] is not None, "a genuine zero is not a missing value"
    assert call["measurement_start_ist"] == "09:30:00"
    # A flat pair is neutral, and no bar is fabricated from zero.
    assert call["buildup"]["classification"] == "NEUTRAL"
    assert call["bar_pct"] == 0.0
    assert payload["max_abs_oi_change"] == 0


# ---------------------------------------------------------------------------
# 6. Provider day change remains separate
# ---------------------------------------------------------------------------

async def test_provider_day_change_is_never_an_interval_delta(service) -> None:
    """The provider's day change is never converted into an interval ΔOI."""
    await _seed_index()  # NIFTY resolves, but no observations exist at all
    expiry, strike = await _current_scope(service)

    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="15m"
    )

    row = _row(payload, strike)
    for side in ("call", "put"):
        assert row[side]["change_in_oi"] is None
        assert row[side]["measurement_available"] is False
        assert row[side]["provenance"] == PROV_UNAVAILABLE
        # Still exposed, still under its own key, still not the interval answer.
        assert row[side]["provider_day_change_in_oi"] is not None
        assert row[side]["provider_day_change_in_oi"] != row[side]["change_in_oi"]
    assert payload["baseline_type"] == BASELINE_INTRADAY_INTERVAL
    assert payload["measurement_available"] is False
    assert payload["pro_rata_scaling_applied"] is False
    disclosure = payload["disclosure"] or ""
    assert "provider day-change figures are shown separately" in disclosure.lower()
    assert "no proportional scaling" in disclosure.lower()
    assert "No proportional scaling" in payload["measurement_note"]

    # The session view keeps its own documented day-change semantics.
    session_payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=expiry, strike_count=5, interval="session"
    )
    assert session_payload["baseline_type"] == "PREVIOUS_CLOSE"
    assert "Provider day change" in session_payload["baseline_label"]
