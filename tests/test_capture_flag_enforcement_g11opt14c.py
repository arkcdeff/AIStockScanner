"""G.11-OPT-14C — capture-flag enforcement tests.

``settings.derivatives_observation_capture_enabled`` must be a real safety gate:
while it is disabled capture cannot start, no worker is created, no option-chain
observation (snapshot or quote) is persisted, and no other validation is bypassed
when it is enabled.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.providers.models import NormalizedOptionChain, NormalizedQuote
from app.services.derivatives_capture_service import (
    DerivativesCaptureConfig,
    DerivativesCaptureService,
    observation_capture_enabled,
)
from app.services.option_chain_persistence_service import (
    OptionChainCaptureDisabledError,
    OptionChainPersistenceError,
    OptionChainPersistenceService,
    UnderlyingResolutionError,
)

SESSION = date(2026, 10, 8)
BASE = datetime(2026, 10, 6, 4, 0, tzinfo=UTC)


# ===========================================================================
# Fixtures / builders (TEST_FIXTURE_ONLY)
# ===========================================================================

def _quote(strike: int, option_type: str) -> NormalizedQuote:
    return NormalizedQuote(
        symbol=f"NIFTY26OCT{strike}{option_type}",
        instrument_type="OPTION",
        ltp=Decimal("100.00"),
        strike_price=Decimal(str(strike)),
        option_type=option_type,
        expiry_date=SESSION,
        timestamp=BASE,
        source_name="UPSTOX_REST",
    )


def _chain(provider_id: str) -> NormalizedOptionChain:
    """A one-strike chain tagged with the given provider identity."""
    return NormalizedOptionChain(
        underlying_symbol="NIFTY",
        as_of_timestamp=BASE,
        spot_price=Decimal("24550.25"),
        expiries=[SESSION],
        calls=[_quote(24500, "CE")],
        puts=[_quote(24500, "PE")],
        provider_id=provider_id,
        source_name="UPSTOX_REST",
        spot_provenance="UPSTOX_CHAIN_UNDERLYING_SPOT",
    )


def _live_chain() -> NormalizedOptionChain:
    return _chain("UPSTOX_LIVE")


def _fixture_chain() -> NormalizedOptionChain:
    return _chain("NSE_TEST_PROVIDER")


@pytest.fixture
def capture_service(tmp_path: Path) -> DerivativesCaptureService:
    from app.services.derivatives_observation_service import FuturesObservationStore

    service = DerivativesCaptureService()
    service.futures_store = FuturesObservationStore(base_dir=tmp_path)
    return service


async def _observation_counts() -> tuple[int, int]:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        snapshots = (
            await session.execute(text("SELECT COUNT(*) FROM option_chain_snapshots"))
        ).scalar()
        quotes = (
            await session.execute(text("SELECT COUNT(*) FROM option_chain_quotes"))
        ).scalar()
    return int(snapshots), int(quotes)


# ===========================================================================
# A / F / G / H — disabled flag refuses capture and persists nothing
# ===========================================================================

@pytest.mark.asyncio
async def test_a_capture_start_rejected_when_disabled(capture_service) -> None:
    assert observation_capture_enabled() is False

    ok, msg, _ = await capture_service.start_capture(
        DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True),
        authorized=True,
    )

    assert ok is False
    assert "CAPTURE_DISABLED" in msg


@pytest.mark.asyncio
async def test_f_disabled_flag_creates_no_worker(capture_service) -> None:
    await capture_service.start_capture(
        DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True),
        authorized=True,
    )

    assert capture_service._active_task is None
    status = capture_service.get_status()
    assert status["is_running"] is False
    assert status["state"] != "RUNNING"


@pytest.mark.asyncio
async def test_g_h_disabled_flag_creates_no_snapshot_or_quotes(capture_service) -> None:
    before = await _observation_counts()

    # A rejected start must not reach the provider or the persistence layer.
    with patch.object(
        capture_service, "_capture_cycle", AsyncMock(return_value=True)
    ) as cycle:
        await capture_service.start_capture(
            DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True),
            authorized=True,
        )
    cycle.assert_not_called()

    after = await _observation_counts()
    assert after == before


# ===========================================================================
# B — enabled flag still goes through the existing validation path
# ===========================================================================

@pytest.mark.asyncio
async def test_b_capture_start_proceeds_when_enabled(
    capture_service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "derivatives_observation_capture_enabled", True)
    assert observation_capture_enabled() is True

    config = DerivativesCaptureConfig(
        underlying="NIFTY", interval_seconds=60, allow_outside_market_hours=True
    )
    try:
        with patch.object(
            capture_service, "_capture_cycle", AsyncMock(return_value=True)
        ):
            ok, msg, status = await capture_service.start_capture(
                config, authorized=True, operator_username="test_admin"
            )
            assert ok is True
            assert msg == "CAPTURE_STARTED"
            assert status["state"] == "RUNNING"

            # Existing validations are untouched by the new gate.
            overlap_ok, overlap_msg, _ = await capture_service.start_capture(
                config, authorized=True
            )
            assert overlap_ok is False
            assert "ALREADY_RUNNING" in overlap_msg
    finally:
        await capture_service.stop_capture(authorized=True)


# ===========================================================================
# C / D — persistence defense in depth
# ===========================================================================

@pytest.mark.asyncio
async def test_c_disabled_flag_blocks_live_observation_persistence() -> None:
    service = OptionChainPersistenceService()

    with pytest.raises(OptionChainCaptureDisabledError) as excinfo:
        await service.persist_snapshot(_live_chain(), expiry_scope=SESSION)

    assert "derivatives_observation_capture_enabled=False" in str(excinfo.value)


@pytest.mark.asyncio
async def test_d_fixture_persistence_path_is_not_blocked() -> None:
    """Historical/test fixtures carry a non-live provider identity and stay exempt."""
    service = OptionChainPersistenceService()

    with pytest.raises(OptionChainPersistenceError) as excinfo:
        await service.persist_snapshot(_fixture_chain(), expiry_scope=SESSION)

    # It fails on the underlying (no MarketIndex fixture exists here) rather than
    # on the capture gate: the historical/test persistence path is not blocked.
    assert not isinstance(excinfo.value, OptionChainCaptureDisabledError)


# ===========================================================================
# I — enabling the flag does not bypass provider / MarketIndex validation
# ===========================================================================

@pytest.mark.asyncio
async def test_i_enabled_flag_does_not_bypass_underlying_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With capture enabled, a live chain still fails MarketIndex resolution."""
    monkeypatch.setattr(settings, "derivatives_observation_capture_enabled", True)
    service = OptionChainPersistenceService()

    with pytest.raises(UnderlyingResolutionError):
        await service.persist_snapshot(_live_chain(), expiry_scope=SESSION)


# ===========================================================================
# J — nothing enables the flag as a side effect
# ===========================================================================

@pytest.mark.asyncio
async def test_j_catalog_seeding_does_not_enable_capture() -> None:
    from app.db.session import get_session_factory
    from app.market_intelligence.index_catalog import seed_market_index_catalog

    assert settings.derivatives_observation_capture_enabled is False

    factory = get_session_factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        await session.commit()

    assert settings.derivatives_observation_capture_enabled is False
    assert observation_capture_enabled() is False


# ===========================================================================
# E — RBAC preserved, plus the HTTP entrypoint gate
# ===========================================================================

def _authorized_dependencies() -> list:
    """Return the permission dependency callables guarding /capture/start.

    The router is mounted with prefix ``/market-data/options``, so the recorded
    route path is ``/market-data/options/capture/start``. The route carries TWO
    RBAC closures: the router-level ``stocks.read`` dependency (inherited by
    every route) and a route-level ``market_data.write`` dependency; both must
    be overridden for an authorized call to pass.
    """
    from app.api.routes import options as options_routes

    route = next(
        r
        for r in options_routes.router.routes
        if getattr(r, "path", "").endswith("/capture/start")
    )
    return [d.dependency for d in route.dependencies]


def _override_auth(app) -> list:
    from unittest.mock import MagicMock

    from app.models.auth import User
    from app.services.auth_service import AuthenticatedUser

    dummy = AuthenticatedUser(
        user=MagicMock(spec=User, id=1, is_superuser=True),
        principal=MagicMock(),
        roles=("admin",),
        permissions=("stocks.read", "market_data.write"),
    )
    deps = _authorized_dependencies()
    assert deps, "capture/start must remain RBAC-guarded"
    for dep in deps:
        app.dependency_overrides[dep] = lambda: dummy
    return deps


@pytest.mark.asyncio
async def test_e_unauthenticated_capture_start_is_still_401_or_403() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/market-data/options/capture/start", json={"underlying": "NIFTY"}
        )
    assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_e_authorized_capture_start_is_503_when_disabled() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deps = _override_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/market-data/options/capture/start", json={"underlying": "NIFTY"}
            )
    finally:
        for dep in deps:
            app.dependency_overrides.pop(dep, None)

    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_e_authorized_capture_start_passes_gate_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled flag: the request reaches existing validation, which still rejects it.

    An unsupported underlying is used so the request can never start a real
    capture session.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(settings, "derivatives_observation_capture_enabled", True)
    deps = _override_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/market-data/options/capture/start",
                json={"underlying": "NOT_A_REAL_INDEX"},
            )
    finally:
        for dep in deps:
            app.dependency_overrides.pop(dep, None)

    assert resp.status_code == 400
    assert resp.status_code != 503
