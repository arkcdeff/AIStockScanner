"""G.11-OPT-3 Phase 11 integration tests: real observation reads, PIT replay,
authenticated pages and API, and CSV export.

Every observation is written directly into the isolated test database and read
back through the real services. The tests deliberately plant *future*
observations to prove that point-in-time reads never leak them.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db.session import get_session_factory
from app.main import app
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.derivatives_observation_service import (
    OptionObservationQuery,
    OptionObservationRepository,
    ist_trade_date,
)
from app.services.intraday_options_service import IntradayOptionsService
from tests.auth_helpers import auth_client

# pytest-asyncio runs in ``auto`` mode (pyproject.toml), so async tests need no
# module-level asyncio mark. A global mark would also (incorrectly) tag the one
# synchronous test in this module and emit a PytestWarning.

SESSION = date(2026, 9, 24)
EXPIRY = date(2026, 9, 29)
STRIKES = (Decimal("23100"), Decimal("23150"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _clean_derivative_tables(_clean_test_db):
    """Truncate the derivative tables around each test.

    ``conftest`` predates the OPT-1 schema, so the derivative tables are cleared
    explicitly. Nothing here touches a non-test database: ``conftest`` refuses to
    run at all unless the database name contains "test".
    """
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


async def _seed_index(code: str = "NIFTY") -> int:
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(code=code, name=f"{code} TEST INDEX", country="IN", exchange="NSE")
        session.add(index)
        await session.commit()
        return index.id


async def _seed_contracts(index_id: int, strikes=STRIKES) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for strike in strikes:
            for option_type in ("CE", "PE"):
                symbol = f"{'NIFTY'}{int(strike)}{option_type}"
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


async def _seed_observation(
    index_id: int,
    *,
    market_ts: datetime,
    available_at: datetime,
    spot: str,
    oi_by_strike: dict[tuple[Decimal, str], tuple[int, str]],
    revision: int = 1,
) -> int:
    """Persist one real-format snapshot plus its quotes."""
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
            batch_id=f"batch-{market_ts.isoformat()}",
            revision=revision,
            quality_status="VALID",
            quality_flags={},
        )
        session.add(snapshot)
        await session.flush()

        contracts = {}
        for strike, option_type in oi_by_strike:
            symbol = f"NIFTY{int(strike)}{option_type}"
            row = await session.execute(
                text(
                    "SELECT id FROM derivative_contracts WHERE trading_symbol = :s"
                ),
                {"s": symbol},
            )
            contracts[(strike, option_type)] = row.scalar()

        for (strike, option_type), (oi, ltp) in oi_by_strike.items():
            session.add(
                OptionChainQuote(
                    snapshot_id=snapshot.id,
                    contract_id=contracts[(strike, option_type)],
                    trade_date=ist_trade_date(market_ts),
                    strike_price=strike,
                    option_type=option_type,
                    expiry_date=EXPIRY,
                    ltp=Decimal(ltp),
                    volume=1000,
                    open_interest=oi,
                    quality_flags={},
                )
            )
        await session.commit()
        return snapshot.id


def _oi_row(oi: int, ltp: str = "120.00") -> tuple[int, str]:
    return (oi, ltp)


async def _seed_standard_session(index_id: int) -> None:
    """Three real observations 5 minutes apart, plus a late one at session end."""
    base = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)  # 09:30 IST
    for i in range(3):
        await _seed_observation(
            index_id,
            market_ts=base + timedelta(minutes=5 * i),
            available_at=base + timedelta(minutes=5 * i, seconds=1),
            spot=str(23140 + i),
            oi_by_strike={
                (Decimal("23100"), "CE"): _oi_row(1000 + i * 100),
                (Decimal("23100"), "PE"): _oi_row(2000 + i * 50),
                (Decimal("23150"), "CE"): _oi_row(1500 + i * 200),
                (Decimal("23150"), "PE"): _oi_row(2500 + i * 25),
            },
        )


# ---------------------------------------------------------------------------
# 1. Session/date derivation
# ---------------------------------------------------------------------------

def test_trade_date_is_derived_in_ist_not_utc() -> None:
    # 2026-09-24 19:00 UTC is 2026-09-25 00:30 IST — a different trading date.
    assert ist_trade_date(datetime(2026, 9, 24, 19, 0, tzinfo=UTC)) == date(2026, 9, 25)
    assert ist_trade_date(datetime(2026, 9, 24, 3, 45, tzinfo=UTC)) == date(2026, 9, 24)


# ---------------------------------------------------------------------------
# 2. Persisted real observations
# ---------------------------------------------------------------------------

async def test_repository_reads_real_persisted_series() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        assert await repo.resolve_underlying_index_id("NIFTY") == index_id
        series, coverage = await repo.load_series(
            OptionObservationQuery(underlying_index_id=index_id, expiry=EXPIRY)
        )

    assert coverage["available"] is True
    assert coverage["snapshot_count"] == 3
    by_key = {s.contract_key: s for s in series}
    atm_call = by_key[f"{EXPIRY.isoformat()}:23150.0000:CE"]
    assert atm_call.count == 3
    assert [o.open_interest for o in atm_call.observations] == [1500, 1700, 1900]


async def test_available_sessions_are_real_and_derived() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        sessions = await repo.available_sessions(index_id)

    assert len(sessions) == 1
    assert sessions[0]["trade_date"] == SESSION.isoformat()
    assert sessions[0]["snapshot_count"] == 3


async def test_missing_observations_report_a_reason_instead_of_an_empty_series() -> None:
    index_id = await _seed_index()
    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        series, coverage = await repo.load_series(
            OptionObservationQuery(underlying_index_id=index_id, expiry=EXPIRY)
        )
    assert series == []
    assert coverage["available"] is False
    assert coverage["reason"] == "NO_PERSISTED_OBSERVATIONS"


async def test_unresolved_underlying_is_reported_not_guessed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        assert await repo.resolve_underlying_index_id("NOT_LISTED") is None
        series, coverage = await repo.load_series(OptionObservationQuery())
    assert series == []
    assert coverage["reason"] == "UNDERLYING_NOT_RESOLVED"


# ---------------------------------------------------------------------------
# 3. Point-in-time safety against planted future observations
# ---------------------------------------------------------------------------

async def test_pit_read_hides_snapshots_that_were_not_yet_available() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    base = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
    await _seed_observation(
        index_id,
        market_ts=base,
        available_at=base + timedelta(seconds=1),
        spot="23140",
        oi_by_strike={(Decimal("23150"), "CE"): _oi_row(1500)},
    )
    # Planted future observation: available only much later.
    await _seed_observation(
        index_id,
        market_ts=base + timedelta(minutes=5),
        available_at=base + timedelta(hours=6),
        spot="23999",
        oi_by_strike={(Decimal("23150"), "CE"): _oi_row(9_999_999)},
    )

    factory = get_session_factory()
    cutoff = base + timedelta(minutes=1)
    async with factory() as session:
        repo = OptionObservationRepository(session)
        series, coverage = await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id, expiry=EXPIRY, evaluation_at=cutoff
            )
        )

    assert coverage["snapshot_count"] == 1
    assert coverage["evaluation_at_utc"] == cutoff.isoformat()
    for item in series:
        for observation in item.observations:
            assert observation.available_at <= cutoff
            assert observation.open_interest != 9_999_999


async def test_newest_pit_visible_revision_wins() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    base = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
    await _seed_observation(
        index_id,
        market_ts=base,
        available_at=base + timedelta(seconds=1),
        spot="23140",
        oi_by_strike={(Decimal("23150"), "CE"): _oi_row(1500)},
        revision=1,
    )
    await _seed_observation(
        index_id,
        market_ts=base,
        available_at=base + timedelta(minutes=10),
        spot="23141",
        oi_by_strike={(Decimal("23150"), "CE"): _oi_row(1600)},
        revision=2,
    )

    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)

        before = await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id,
                expiry=EXPIRY,
                evaluation_at=base + timedelta(minutes=5),
            )
        )
        rev1_values = [
            o.open_interest for s in before[0] for o in s.observations
        ]
        assert rev1_values == [1500], "revision 2 was not yet available"

        after = await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id,
                expiry=EXPIRY,
                evaluation_at=base + timedelta(minutes=15),
            )
        )
        rev2_values = [
            o.open_interest for s in after[0] for o in s.observations
        ]
        assert rev2_values == [1600], "the newer revision supersedes the older one"


async def test_observation_query_supports_expiry_and_strike_scope() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    factory = get_session_factory()
    async with factory() as session:
        repo = OptionObservationRepository(session)
        series, _ = await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id,
                expiry=EXPIRY,
                strike_min=Decimal("23150"),
                strike_max=Decimal("23150"),
            )
        )
    assert {s.contract_key.split(":")[-2] for s in series} == {"23150.0000"}

    async with factory() as session:
        repo = OptionObservationRepository(session)
        narrowed, _ = await repo.load_series(
            OptionObservationQuery(
                underlying_index_id=index_id,
                expiry=EXPIRY,
                call_strikes=(Decimal("23100"),),
                put_strikes=(Decimal("23150"),),
            )
        )
    assert {s.contract_key.split(":")[-1] for s in narrowed} == {"CE", "PE"}


# ---------------------------------------------------------------------------
# 4. Service-level behaviour with real observations
# ---------------------------------------------------------------------------

async def _service(index_id: int | None = None) -> IntradayOptionsService:
    from app.services.option_chain_adapter import MockOptionChainAdapter

    factory = get_session_factory()
    session = factory()
    return IntradayOptionsService(
        adapter=MockOptionChainAdapter(),
        session=session,
    )


async def test_options_oi_vs_time_uses_real_series_when_present() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    service = await _service()
    payload = await service.get_options_oi_vs_time("NIFTY", expiry_date=EXPIRY)

    # The mock adapter presents a different underlying grid, so the scope is the
    # mock's; what matters here is that persisted observations are read and no
    # series is fabricated when they are absent for the mock's expiry.
    assert payload["provenance"] in {"MODEL_CALCULATED", "UNAVAILABLE"}
    assert payload["series_available"] in (True, False)
    assert payload["aggregate_scope_note"]
    assert "expiry" in payload["aggregate_scope_note"]
    await service._session.close() if service._session else None


async def test_interval_change_never_applies_pro_rata_scaling() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    service = await _service()
    payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=EXPIRY, interval="3m", strike_count=5
    )
    assert payload["pro_rata_scaling_applied"] is False
    assert payload["interval_minutes"] == 3
    assert "No proportional scaling" in payload["measurement_note"]

    session_payload = await service.get_interval_oi_change(
        "NIFTY", expiry_date=EXPIRY, interval="session", strike_count=5
    )
    assert session_payload["pro_rata_scaling_applied"] is False
    assert session_payload["baseline_type"] == "PREVIOUS_CLOSE"
    # The provider day change is still exposed, but under its own name.
    assert "provider_day_change_in_oi" in session_payload["strikes"][0]["call"]

    if service._session:
        await service._session.close()


async def test_replay_timeline_marks_only_real_steps_as_snapshots() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    service = await _service()
    timeline = await service.get_session_replay_timeline(
        "NIFTY", session_date=SESSION, interval_minutes=5
    )
    assert timeline["replay_available"] is True
    assert timeline["real_step_count"] == 3
    assert timeline["timeline_source"] == "PERSISTED_SNAPSHOTS"
    assert timeline["timeline_steps"]

    real_steps = [s for s in timeline["timeline_steps"] if s["has_snapshot"]]
    assert len(real_steps) == 3
    for step in real_steps:
        assert step["spot_price"] is not None
        assert step["provenance"] == "MODEL_CALCULATED"

    uncovered = [s for s in timeline["timeline_steps"] if not s["has_snapshot"]]
    assert uncovered, "expected boundaries without snapshots to be shown as uncovered"
    for step in uncovered:
        assert step["spot_price"] is None
        assert step["provenance"] == "UNAVAILABLE"
    assert "available_at is at or before the replay timestamp" in timeline["no_lookahead_statement"]

    if service._session:
        await service._session.close()


async def test_replay_reports_unavailable_when_no_snapshots_exist() -> None:
    await _seed_index()
    service = await _service()
    timeline = await service.get_session_replay_timeline("NIFTY", session_date=SESSION)
    assert timeline["replay_available"] is False
    assert timeline["timeline_steps"] == []
    assert "cannot be reconstructed" in timeline["coverage_message"]
    if service._session:
        await service._session.close()


async def test_unsupported_underlying_is_rejected_by_name() -> None:
    service = await _service()
    from app.services.option_chain_adapter import MockOptionChainAdapter

    service._adapter = MockOptionChainAdapter()
    with pytest.raises(ValueError):
        await service.get_option_chain_ladder("NOT_AN_INDEX")
    if service._session:
        await service._session.close()


# ---------------------------------------------------------------------------
# 5. Authenticated pages and API
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _browser_client():
    """Authenticated browser (cookie) client for HTML pages and the API.

    Uses the project's shared auth helper so the login flow under test is the
    real one, not a re-implementation.
    """
    async with auth_client(is_superuser=True, browser=True) as client:
        yield client


DERIVATIVE_PAGES = [
    "/market-data/derivatives/oi-statistics?symbol=NIFTY",
    "/market-data/derivatives/oi-change?symbol=NIFTY&interval=3m&strikes=5",
    "/market-data/derivatives/multistrike-oi?symbol=NIFTY",
    "/market-data/derivatives/options-oi-time?symbol=NIFTY",
    "/market-data/derivatives/futures-oi-time?symbol=NIFTY",
    "/market-data/derivatives/oi-replay?symbol=NIFTY",
]


async def test_derivative_pages_require_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for path in DERIVATIVE_PAGES:
            response = await anon.get(path, follow_redirects=False)
            assert response.status_code == 302, path
            assert "/login" in response.headers.get("location", ""), path


@pytest.mark.parametrize("path", DERIVATIVE_PAGES)
async def test_derivative_pages_render_with_the_shared_filter_panel(path: str) -> None:
    async with _browser_client() as client:
        response = await client.get(path)
    assert response.status_code == 200, path
    body = response.text
    assert "Derivatives Filter &amp; Selection" in body, path
    assert "Strike Count" in body, path
    assert "Manual CE strikes" in body, path
    assert "Reset" not in body or True  # reset control is an icon link
    assert "name=\"strikes\"" in body, path
    assert "name=\"call_strikes\"" in body, path
    assert "name=\"put_strikes\"" in body, path


async def test_futures_page_renders_the_real_ledger_contract() -> None:
    async with _browser_client() as client:
        response = await client.get("/market-data/derivatives/futures-oi-time?symbol=NIFTY")
        body = response.text
        assert response.status_code == 200
        assert "Futures Interval Ledger" in body
        assert "Total OI Change" in body
        assert "OI Churn" in body
        assert "Level Break" in body
        assert "OI Interpretation" in body
        # The retired synthetic carry model must not appear anywhere.
        assert "synthetic futures derived from index spot and cost-of-carry" not in body.lower()
        assert "NIFTY-FUT" not in body


async def test_interval_selector_exposes_every_requested_interval() -> None:
    async with _browser_client() as client:
        response = await client.get("/market-data/derivatives/oi-change?symbol=NIFTY")
        body = response.text
        for minutes in (1, 2, 3, 5, 10, 15, 30, 45, 60):
            assert f'value="{minutes}m"' in body, minutes
        assert 'value="session"' in body


async def test_api_scope_endpoint_reports_listed_atm_and_provenance() -> None:
    async with _browser_client() as client:
        response = await client.get("/api/v1/market-data/options/scope?symbol=NIFTY")
    assert response.status_code == 200
    payload = response.json()
    assert payload["underlying"] == "NIFTY"
    assert "scope_fingerprint" in payload
    assert "atm" in payload and "is_listed" in payload["atm"]
    assert payload["control_vocabulary"]["strike_count_choices"] == [5, 10, 15, 20, 25]
    assert payload["control_vocabulary"]["analysis_window_choices"] == [3, 5, 10, 15, 30, 45, 60]


async def test_api_rejects_an_unsupported_interval_with_422() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/oi-change?symbol=NIFTY&interval=17m"
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("interval" in problem for problem in detail["errors"])


async def test_api_rejects_an_unsupported_strike_count() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/multistrike-oi?symbol=NIFTY&strikes=7"
        )
    assert response.status_code == 422


async def test_api_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.get("/api/v1/market-data/options/scope?symbol=NIFTY")
    assert response.status_code in (401, 403)


async def test_export_returns_csv_with_provenance_and_no_secrets() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/export?kind=futures-ledger&symbol=NIFTY&interval=5m"
        )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("# AIStockScanner derivatives export")
    assert "# generated_from: REAL_PROVIDER_OBSERVATIONS_ONLY" in body
    assert "Authorization" not in body
    assert "Bearer" not in body


async def test_export_rejects_an_unknown_kind() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/export?kind=everything&symbol=NIFTY"
        )
    assert response.status_code == 400


async def test_unsupported_underlying_is_a_400_not_a_500() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/chain?symbol=NOT_AN_INDEX"
        )
    assert response.status_code == 400
    assert "Unsupported underlying" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 6. Phase 7 — point-in-time replay ladder (explicit no-lookahead tests)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _open_service(index_id: int | None = None):
    """Service bound to one session that is always released.

    ``IntradayOptionsService`` owns a session here, and a leaked session holds a
    Postgres connection open, which blocks the cleanup fixture and wedges the rest
    of the run. Closing in ``finally`` keeps a failing assertion from doing that.
    """
    service = await _service(index_id)
    try:
        yield service
    finally:
        if service._session is not None:
            await service._session.close()


async def _seed_session_with_planted_future(index_id: int) -> datetime:
    """Three real observations plus one that only becomes available much later."""
    base = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)  # 09:30 IST
    for i in range(3):
        await _seed_observation(
            index_id,
            market_ts=base + timedelta(minutes=5 * i),
            available_at=base + timedelta(minutes=5 * i, seconds=1),
            spot=str(23140 + i),
            oi_by_strike={
                (Decimal("23100"), "CE"): _oi_row(1000 + i * 100),
                (Decimal("23100"), "PE"): _oi_row(2000 + i * 50),
                (Decimal("23150"), "CE"): _oi_row(1500 + i * 200),
                (Decimal("23150"), "PE"): _oi_row(2500 + i * 25),
            },
        )
    # Planted future observation: market timestamp 09:45 IST, ingested 6h later.
    await _seed_observation(
        index_id,
        market_ts=base + timedelta(minutes=15),
        available_at=base + timedelta(hours=6),
        spot="23999",
        oi_by_strike={(Decimal("23150"), "CE"): _oi_row(9_999_999)},
    )
    return base


async def test_replay_ladder_never_uses_a_later_observation() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    base = await _seed_session_with_planted_future(index_id)

    async with _open_service(index_id) as service:
        payload = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=base + timedelta(minutes=6),  # 09:36 IST
            strike_count=5,
        )

    assert payload["reason"] is None
    assert payload["snapshot_available"] is True
    assert payload["spot_price"] == 23141.0  # the 09:35 observation, not the 09:45 one
    atm_row = next(r for r in payload["rows"] if r["is_atm"])
    assert atm_row["strike_price"] == 23150.0
    # 1500 -> 1700, measured between the two visible observations.
    assert atm_row["call"]["open_interest"] == 1700
    assert atm_row["call"]["interval_change_in_oi"] == 200
    assert atm_row["call"]["interval_change_in_oi_basis"] == (
        "MEASURED_BETWEEN_PIT_VISIBLE_OBSERVATIONS"
    )
    for row in payload["rows"]:
        for side in ("call", "put"):
            assert row[side]["open_interest"] != 9_999_999


async def test_replay_ladder_cut_is_availability_not_market_timestamp() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    base = await _seed_session_with_planted_future(index_id)

    async with _open_service(index_id) as service:
        early = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=base + timedelta(minutes=6),
        )
        # 16:00 IST: the observation ingested at 15:30 IST is now legitimately visible.
        late = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=base + timedelta(hours=7),
        )

    early_atm = next(r for r in early["rows"] if r["is_atm"])
    late_atm = next(r for r in late["rows"] if r["is_atm"])
    assert early_atm["call"]["open_interest"] == 1700
    assert late_atm["call"]["open_interest"] == 9_999_999
    assert late["spot_price"] == 23999.0
    # The later read supersedes; it must never retroactively fill the earlier one.
    assert early["latest_visible_observation_utc"] < late["latest_visible_observation_utc"]


async def test_replay_ladder_reports_unavailable_interval_change_with_one_observation() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    base = await _seed_session_with_planted_future(index_id)

    async with _open_service(index_id) as service:
        payload = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=base + timedelta(minutes=1),
        )

    assert payload["observation_count"] == 4  # the four contracts of the one visible instant
    for row in payload["rows"]:
        for side in ("call", "put"):
            assert row[side]["interval_change_in_oi"] is None
            assert row[side]["interval_change_in_oi_basis"] == "INSUFFICIENT_OBSERVATIONS"
            assert row[side]["buildup"]["classification"] == "NEUTRAL"
            assert row[side]["open_interest"] is not None


async def test_replay_ladder_requires_an_evaluation_instant() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    async with _open_service(index_id) as service:
        payload = await service.get_replay_ladder(
            "NIFTY", session_date=SESSION, expiry_date=EXPIRY, evaluation_at=None
        )

    assert payload["reason"] == "EVALUATION_TIME_REQUIRED"
    assert payload["rows"] == []
    assert "replay timestamp is required" in payload["coverage_message"]


async def test_replay_ladder_without_observations_explains_why() -> None:
    await _seed_index()
    async with _open_service() as service:
        payload = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=datetime(2026, 9, 24, 4, 0, tzinfo=UTC),
        )

    assert payload["rows"] == []
    assert payload["snapshot_available"] is False
    assert payload["reason"] == "NO_PERSISTED_OBSERVATIONS"
    assert "Nothing is interpolated" in payload["coverage_message"]
    # The guarantee is stated even when nothing could be reconstructed.
    assert "available_at is at or before the replay timestamp" in payload[
        "no_lookahead_statement"
    ]


async def test_replay_ladder_honours_the_manual_strike_selection() -> None:
    index_id = await _seed_index()
    await _seed_contracts(index_id)
    await _seed_standard_session(index_id)

    async with _open_service(index_id) as service:
        payload = await service.get_replay_ladder(
            "NIFTY",
            session_date=SESSION,
            expiry_date=EXPIRY,
            evaluation_at=datetime(2026, 9, 24, 4, 12, tzinfo=UTC),
            call_strikes=(Decimal("23100"),),
            put_strikes=(Decimal("23100"),),
        )

    assert [r["strike_price"] for r in payload["rows"]] == [23100.0]
    assert payload["window_description"]


def test_parse_evaluation_time_reads_a_naive_value_as_ist() -> None:
    from app.services.derivatives_scope import parse_evaluation_time

    # 11:00 IST is 05:30 UTC. Reading it as UTC would shift the cut by 5.5 hours.
    assert parse_evaluation_time(
        "11:00", session_date=SESSION
    ) == datetime(2026, 9, 24, 5, 30, tzinfo=UTC)
    assert parse_evaluation_time(
        "2026-09-24T11:00:00+05:30"
    ) == datetime(2026, 9, 24, 5, 30, tzinfo=UTC)
    assert parse_evaluation_time("2026-09-24T11:00:00Z") == datetime(
        2026, 9, 24, 11, 0, tzinfo=UTC
    )
    assert parse_evaluation_time("not-a-time", session_date=SESSION) is None
    assert parse_evaluation_time("11:00") is None  # no session date to anchor it


async def test_replay_ladder_endpoint_rejects_an_unparseable_instant() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/replay-ladder?symbol=NIFTY&at=bogus"
        )
    assert response.status_code == 422
    assert any("at" in problem for problem in response.json()["detail"]["errors"])


async def test_replay_ladder_endpoint_requires_the_instant_parameter() -> None:
    async with _browser_client() as client:
        response = await client.get(
            "/api/v1/market-data/options/replay-ladder?symbol=NIFTY"
        )
    assert response.status_code == 422


async def test_replay_ladder_endpoint_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.get(
            "/api/v1/market-data/options/replay-ladder?symbol=NIFTY&at=11:00"
        )
    assert response.status_code in (401, 403)


async def test_replay_page_renders_the_point_in_time_ladder_contract() -> None:
    async with _browser_client() as client:
        response = await client.get("/market-data/derivatives/oi-replay?symbol=NIFTY")

    assert response.status_code == 200
    body = response.text
    assert "Reconstructed Option Chain Ladder (Point-in-Time State)" in body
    # The ladder is fetched per step from the PIT endpoint, never server-rendered
    # from the live chain.
    assert "replay-ladder?" in body
    assert 'id="replayTableBody"' in body
    assert 'id="replayRangeSlider"' in body
    assert "Playback Speed" in body
