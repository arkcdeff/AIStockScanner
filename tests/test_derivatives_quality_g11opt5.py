"""G.11-OPT-5 — Observation quality, capture integrity and provider outage tests.

Covers the defects and fixes documented in docs/G11OPT5_DISCOVERY_REPORT.md:

* D1 — provider outage returns an explicit UNAVAILABLE chain (never the mock);
  capture refuses to persist non-real chains; retry/backoff on transient HTTP.
* D2/D7 — capture status reports durable JSONL history (count, last observation,
  malformed records) so a restarted application shows disk truth.
* D3 — duplicate appends are distinguishable; only genuine appends count.
* D6 — retention cutoff compares against the IST session date.
* F8 — shared quality vocabulary mapping for series, deltas and summaries.

All fixtures are deterministic and TEST_FIXTURE_ONLY. No live provider calls,
no schema changes, capture stays disabled by default.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.providers.models import NormalizedOptionChain, NormalizedQuote
from app.providers.upstox_live_provider import UpstoxLiveProvider
from app.services.derivatives_capture_service import (
    DerivativesCaptureConfig,
    DerivativesCaptureService,
    _chain_is_real_observation,
)
from app.services.derivatives_observation_service import (
    AVAILABILITY_INSUFFICIENT,
    ISSUE_DUPLICATE_TIMESTAMP,
    ISSUE_OUT_OF_ORDER,
    ISSUE_STALE,
    QUALITY_DUPLICATE,
    QUALITY_INSUFFICIENT_HISTORY,
    QUALITY_MISSING,
    QUALITY_OUT_OF_ORDER,
    QUALITY_STALE,
    QUALITY_VALID,
    ContractObservation,
    FuturesObservation,
    FuturesObservationStore,
    IntervalDelta,
    ObservationSeries,
    compute_interval_deltas,
    ist_trade_date,
    order_and_audit,
    quality_status_for_delta,
    quality_status_for_series,
    quality_summary,
    tolerance_seconds_for,
)
from app.services.derivatives_scope import PROV_PERSISTED_OBSERVATION

SESSION = date(2026, 9, 24)
BASE = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)  # 09:30 IST


# ===========================================================================
# Deterministic fixture builders (TEST_FIXTURE_ONLY)
# ===========================================================================


def _quote(strike: Decimal, option_type: str, ltp: str) -> NormalizedQuote:
    return NormalizedQuote(
        symbol=f"NIFTY26SEP{int(strike)}{option_type}",
        instrument_type="OPTION",
        ltp=Decimal(ltp),
        strike_price=strike,
        option_type=option_type,
        expiry_date=SESSION,
        timestamp=BASE,
        source_name="UPSTOX_REST",
    )


def _real_chain() -> NormalizedOptionChain:
    return NormalizedOptionChain(
        underlying_symbol="NIFTY",
        as_of_timestamp=BASE,
        spot_price=Decimal("24550.25"),
        expiries=[SESSION],
        calls=[_quote(Decimal("24500"), "CE", "150.00")],
        puts=[_quote(Decimal("24500"), "PE", "130.00")],
        provider_id="UPSTOX_LIVE",
        source_name="UPSTOX_REST",
        spot_provenance="UPSTOX_CHAIN_UNDERLYING_SPOT",
    )


def _unavailable_chain() -> NormalizedOptionChain:
    return NormalizedOptionChain(
        underlying_symbol="NIFTY",
        as_of_timestamp=BASE,
        spot_price=None,
        expiries=[SESSION],
        calls=[],
        puts=[],
        provider_id="UPSTOX_LIVE",
        source_name="UNAVAILABLE",
        spot_provenance="PROVIDER_ERROR",
        quality_flags={"PROVIDER_ERROR": "HTTP_429"},
    )


def _mock_chain() -> NormalizedOptionChain:
    return NormalizedOptionChain(
        underlying_symbol="NIFTY",
        as_of_timestamp=BASE,
        spot_price=Decimal("24500.00"),
        expiries=[SESSION],
        calls=[_quote(Decimal("24500"), "CE", "150.00")],
        puts=[_quote(Decimal("24500"), "PE", "130.00")],
        provider_id="UPSTOX_LIVE",
        source_name="UPSTOX_MOCK",
        spot_provenance="UPSTOX_CHAIN_UNDERLYING_SPOT",
    )


def _fut_obs(ts: datetime, oi: int = 1_000_000, key: str = "NSE_FO|68407") -> FuturesObservation:
    return FuturesObservation(
        instrument_key=key,
        trading_symbol="NIFTY26SEPFUT",
        underlying="NIFTY",
        expiry=SESSION,
        observed_at=ts,
        received_at=ts + timedelta(seconds=1),
        source="TEST_FIXTURE_ONLY",
        last_price=Decimal("23500.00"),
        open_interest=oi,
    )


# ===========================================================================
# D1 — provider outage behavior (no mock fallback)
# ===========================================================================


@pytest.fixture
def outage_provider() -> UpstoxLiveProvider:
    provider = UpstoxLiveProvider(
        token="test_token", base_url="http://testserver", mock_mode=False
    )
    provider.get_listed_expiries = AsyncMock(return_value=[SESSION])
    provider.get_index_spot = AsyncMock(
        return_value=(Decimal("24550.25"), BASE, "UPSTOX_INDEX_QUOTE")
    )
    return provider


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict:
        return {}


def _install_httpx(monkeypatch, response_factory) -> dict[str, int]:
    """Stub the httpx module used by the provider's function-local import.

    ``get_live_option_chain`` does ``import httpx`` inside its body, which binds
    from ``sys.modules`` — so the stub must be installed there, not as a module
    attribute. No real network call can occur.
    """
    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *a, **kw):
            calls["n"] += 1
            return response_factory(calls["n"])

    class _Httpx:
        @staticmethod
        def AsyncClient(*a, **kw):
            return _Client()

    monkeypatch.setitem(sys.modules, "httpx", _Httpx())
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
async def test_provider_transient_http_never_returns_mock_chain(
    outage_provider, status_code, monkeypatch
):
    """All transient HTTP failures exhaust retries and end in UNAVAILABLE."""
    calls = _install_httpx(monkeypatch, lambda n: _Response(status_code))
    import app.providers.upstox_live_provider as mod

    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    chain = await outage_provider.get_live_option_chain("NIFTY")
    assert chain.source_name == "UNAVAILABLE"
    assert chain.calls == [] and chain.puts == []
    assert chain.spot_price is None
    assert chain.quality_flags.get("PROVIDER_ERROR")
    assert calls["n"] == 3  # exhausted all three attempts


@pytest.mark.asyncio
async def test_provider_429_honors_retry_after(outage_provider, monkeypatch):
    """A Retry-After header raises the backoff floor above the base delay."""
    _install_httpx(monkeypatch, lambda n: _Response(429, headers={"Retry-After": "7"}))
    import app.providers.upstox_live_provider as mod

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    chain = await outage_provider.get_live_option_chain("NIFTY")
    assert chain.source_name == "UNAVAILABLE"
    assert sleeps and sleeps[0] >= 7.0  # Retry-After raised the floor


@pytest.mark.asyncio
async def test_provider_success_after_transient_429(outage_provider, monkeypatch):
    """Retry succeeds: a 429 followed by HTTP 200 still returns the real parse."""
    calls = _install_httpx(monkeypatch, lambda n: _Response(429) if n == 1 else _Response(200))
    import app.providers.upstox_live_provider as mod

    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    parsed = MagicMock()
    outage_provider._parse_upstox_live_option_chain = MagicMock(return_value=parsed)

    result = await outage_provider.get_live_option_chain("NIFTY")
    assert result is parsed
    assert calls["n"] == 2
    outage_provider._parse_upstox_live_option_chain.assert_called_once()


@pytest.mark.asyncio
async def test_provider_permanent_4xx_returns_unavailable(outage_provider, monkeypatch):
    """A non-transient 4xx (e.g. 403) fails immediately without retry."""
    calls = _install_httpx(monkeypatch, lambda n: _Response(403))
    import app.providers.upstox_live_provider as mod

    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    chain = await outage_provider.get_live_option_chain("NIFTY")
    assert chain.source_name == "UNAVAILABLE"
    assert calls["n"] == 1


# ===========================================================================
# D1 — capture must reject non-real chains
# ===========================================================================


def test_chain_rejection_classifier():
    real_ok, reason = _chain_is_real_observation(_real_chain())
    assert real_ok and reason == ""

    for bad in (_mock_chain(), _unavailable_chain()):
        ok, reason = _chain_is_real_observation(bad)
        assert not ok
        assert reason


@pytest.mark.asyncio
async def test_capture_cycle_rejects_mock_chain_and_counts_failure():
    service = DerivativesCaptureService()
    provider = MagicMock()
    provider.get_live_option_chain = AsyncMock(return_value=_mock_chain())
    analytics = MagicMock()
    analytics.get_scope = AsyncMock(
        return_value={"spot_price": "24550.25", "selected_expiry": SESSION.isoformat()}
    )

    cfg = DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True)
    ok = await service._capture_cycle(provider, analytics, cfg)

    assert ok is False
    assert service._telemetry.observations_captured == 0
    assert service._telemetry.last_error
    assert "NON_REAL_CHAIN_SOURCE" in service._telemetry.last_error


@pytest.mark.asyncio
async def test_capture_cycle_rejects_unavailable_chain():
    service = DerivativesCaptureService()
    provider = MagicMock()
    provider.get_live_option_chain = AsyncMock(return_value=_unavailable_chain())
    analytics = MagicMock()
    analytics.get_scope = AsyncMock(return_value={"spot_price": "24550.25"})

    cfg = DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True)
    ok = await service._capture_cycle(provider, analytics, cfg)

    assert ok is False
    # The source-name branch fires before the provenance branch.
    assert service._telemetry.last_error == "NON_REAL_CHAIN_SOURCE_UNAVAILABLE"


def _patch_capture_dependencies(monkeypatch, persist_result) -> MagicMock:
    """Patch persistence + contract-master at their source modules (auto-restored)."""
    import app.services.derivatives_scope as scope_mod
    import app.services.option_chain_persistence_service as persist_mod

    persistence = MagicMock()
    persistence.persist_snapshot = AsyncMock(return_value=persist_result)
    monkeypatch.setattr(
        persist_mod, "OptionChainPersistenceService", MagicMock(return_value=persistence)
    )

    master = MagicMock()
    master.get_futures_contracts = AsyncMock(
        return_value=MagicMock(is_available=False, rows=[])
    )
    monkeypatch.setattr(
        scope_mod, "DerivativesContractMasterClient", MagicMock(return_value=master)
    )
    return persistence


def _persist_result(status: str = "PERSISTED"):
    from app.services.option_chain_persistence_service import PersistSnapshotResult

    return PersistSnapshotResult(
        status=status,
        snapshot_id=1,
        revision=1,
        underlying_index_id=7,
        chain_scope="SINGLE_EXPIRY",
        expiry_scope=SESSION,
        market_timestamp=BASE,
        available_at=BASE,
        trade_date=SESSION,
        partition_name="2026_09",
        quote_count=2,
        canonical_payload_hash="x",
        raw_payload_hash="y",
        quality_status="VALID",
        quality_flags={},
        is_truncated=False,
        validation=MagicMock(),
    )


@pytest.mark.asyncio
async def test_capture_cycle_accepts_real_chain_and_counts_quotes(monkeypatch):
    _patch_capture_dependencies(monkeypatch, _persist_result("PERSISTED"))

    service = DerivativesCaptureService()
    provider = MagicMock()
    provider.get_live_option_chain = AsyncMock(return_value=_real_chain())
    analytics = MagicMock()
    analytics.get_scope = AsyncMock(
        return_value={"spot_price": "24550.25", "selected_expiry": SESSION.isoformat()}
    )

    cfg = DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True)
    ok = await service._capture_cycle(provider, analytics, cfg)

    assert ok is True
    assert service._telemetry.observations_captured == 2  # 1 call + 1 put
    assert service._telemetry.last_error is None


@pytest.mark.asyncio
async def test_capture_cycle_counts_duplicates_as_zero_new_observations(monkeypatch):
    """DUPLICATE_IGNORED persists are successes but not new observations (D3)."""
    _patch_capture_dependencies(monkeypatch, _persist_result("DUPLICATE_IGNORED"))

    service = DerivativesCaptureService()
    provider = MagicMock()
    provider.get_live_option_chain = AsyncMock(return_value=_real_chain())
    analytics = MagicMock()
    analytics.get_scope = AsyncMock(
        return_value={"spot_price": "24550.25", "selected_expiry": SESSION.isoformat()}
    )

    cfg = DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True)
    ok = await service._capture_cycle(provider, analytics, cfg)

    assert ok is True
    assert service._telemetry.observations_captured == 0


@pytest.mark.asyncio
async def test_capture_cycle_still_uses_chain_source_name(monkeypatch):
    """Capture must not stamp a caller label over the chain's own source (F2)."""
    persistence = _patch_capture_dependencies(monkeypatch, _persist_result("PERSISTED"))

    service = DerivativesCaptureService()
    provider = MagicMock()
    provider.get_live_option_chain = AsyncMock(return_value=_real_chain())
    analytics = MagicMock()
    analytics.get_scope = AsyncMock(
        return_value={"spot_price": "24550.25", "selected_expiry": SESSION.isoformat()}
    )

    cfg = DerivativesCaptureConfig(underlying="NIFTY", allow_outside_market_hours=True)
    await service._capture_cycle(provider, analytics, cfg)

    _, kwargs = persistence.persist_snapshot.await_args
    assert "source_name" not in kwargs  # chain's own UPSTOX_REST is preserved


# ===========================================================================
# D3 — duplicate-aware JSONL appends
# ===========================================================================


def test_futures_store_append_with_result_first_vs_duplicate(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    ts = BASE

    first, dup1 = store.append_with_result(_fut_obs(ts), force_enabled=True)
    assert (first, dup1) == (True, False)

    second, dup2 = store.append_with_result(_fut_obs(ts), force_enabled=True)
    assert (second, dup2) == (True, True)

    # Exactly one record on disk despite two appends.
    records = store.read("NIFTY", SESSION)
    assert len(records) == 1


def test_futures_store_append_with_result_disabled(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    appended, duplicate = store.append_with_result(_fut_obs(BASE), force_enabled=False)
    assert appended is False and duplicate is False


def test_futures_store_legacy_append_contract_unchanged(tmp_path: Path):
    """The OPT-4 contract: duplicate append() still returns True (idempotent)."""
    store = FuturesObservationStore(base_dir=tmp_path)
    obs = _fut_obs(BASE)
    assert store.append(obs, force_enabled=True) is True
    assert store.append(obs, force_enabled=True) is True
    assert len(store.read("NIFTY", SESSION)) == 1


def test_futures_store_distinct_timestamps_both_appended(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    a, dup_a = store.append_with_result(_fut_obs(BASE), force_enabled=True)
    b, dup_b = store.append_with_result(_fut_obs(BASE + timedelta(minutes=1)), force_enabled=True)
    assert (a, dup_a) == (True, False)
    assert (b, dup_b) == (True, False)
    assert len(store.read("NIFTY", SESSION)) == 2


# ===========================================================================
# D2/D7 — restart-recoverable disk history in capture status
# ===========================================================================


def test_status_reports_disk_history_after_restart(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    t1 = BASE
    t2 = BASE + timedelta(minutes=5)
    store.append(_fut_obs(t1, oi=111), force_enabled=True)
    store.append(_fut_obs(t2, oi=222), force_enabled=True)

    service = DerivativesCaptureService()
    service.futures_store = store  # fresh instance: telemetry zeroed, disk has data

    status = service.get_status()
    assert status["state"] == "IDLE"  # worker did not survive a restart
    assert status["is_running"] is False
    assert status["observations_on_disk"] == 2
    assert status["last_observation_on_disk_utc"] == t2.isoformat()
    assert status["last_observation_on_disk_ist"] is not None
    assert status["disk_history"]["by_underlying"]["NIFTY"]["observations"] == 2


def test_status_counts_malformed_jsonl_records(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    store.append(_fut_obs(BASE), force_enabled=True)
    path = store.path_for("NIFTY", SESSION)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"instrument_key": "NSE_FO|TORN", "observed_at_ut\n')  # torn tail
        handle.write("not json at all\n")

    service = DerivativesCaptureService()
    service.futures_store = store
    status = service.get_status()

    assert status["malformed_records_on_disk"] == 2
    assert status["observations_on_disk"] == 1
    assert status["disk_history"]["malformed_records"] == 2


def test_status_without_disk_history_reports_nothing_extra(tmp_path: Path):
    service = DerivativesCaptureService()
    service.futures_store = FuturesObservationStore(base_dir=tmp_path / "empty")
    status = service.get_status()
    assert "observations_on_disk" not in status
    assert "disk_history" not in status


# ===========================================================================
# D6 — retention cutoff is IST-correct
# ===========================================================================


def test_retention_cutoff_uses_ist_session_date(tmp_path: Path):
    """Files older than the cutoff (IST) are deleted; today's session never is.

    Regression for D6: the UTC-based cutoff mis-bucketed filenames that encode
    IST session dates between 00:00 and 05:30 IST.
    """
    store = FuturesObservationStore(base_dir=tmp_path)
    now_ist_date = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
    three_days_ago = now_ist_date - timedelta(days=3)

    old_path = tmp_path / f"NIFTY_{three_days_ago.isoformat()}.jsonl"
    old_path.write_text("", encoding="utf-8")
    today_path = tmp_path / f"NIFTY_{now_ist_date.isoformat()}.jsonl"
    today_path.write_text("", encoding="utf-8")

    result = store.cleanup_older_than(7)
    assert result["deleted_files"] == 0
    assert old_path.exists() and today_path.exists()

    result1 = store.cleanup_older_than(1)  # cutoff = IST today - 1 day
    assert result1["deleted_files"] == 1
    assert not old_path.exists()
    assert today_path.exists()  # today's session is never retention-eligible


def test_retention_never_deletes_today(tmp_path: Path):
    store = FuturesObservationStore(base_dir=tmp_path)
    now_ist_date = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
    today_path = tmp_path / f"BANKNIFTY_{now_ist_date.isoformat()}.jsonl"
    today_path.write_text("", encoding="utf-8")
    store.cleanup_older_than(0)  # most aggressive cutoff: IST today
    assert today_path.exists()


# ===========================================================================
# F8 — shared quality vocabulary
# ===========================================================================


def _series_from(points: list[ContractObservation]) -> ObservationSeries:
    series = ObservationSeries(contract_key=points[0].contract_key, label=points[0].label)
    series.observations, series.issues = order_and_audit(points)
    return series


def _obs(
    minutes: int,
    oi: int | None,
    ltp: str | None,
    key: str = "NIFTY:24500:CE",
) -> ContractObservation:
    observed = BASE + timedelta(minutes=minutes)
    return ContractObservation(
        contract_key=key,
        label="NIFTY 24500 CE",
        observed_at=observed,
        available_at=observed,
        source=PROV_PERSISTED_OBSERVATION,
        strike=Decimal("24500"),
        option_type="CE",
        expiry=SESSION,
        ltp=Decimal(ltp) if ltp is not None else None,
        open_interest=oi,
    )


def test_quality_valid_series():
    series = _series_from([_obs(0, 1000, "150.00"), _obs(5, 1100, "152.00")])
    assert quality_status_for_series(series) == QUALITY_VALID
    coverage = series.coverage()
    assert coverage["quality_status"] == QUALITY_VALID
    assert coverage["observation_age_seconds"] is not None
    assert coverage["last_observation_utc"] is not None


def test_quality_missing_when_oi_absent():
    series = _series_from([_obs(0, 1000, "150.00"), _obs(5, None, "152.00")])
    assert quality_status_for_series(series) == QUALITY_MISSING


def test_quality_stale_beats_missing():
    series = _series_from([_obs(0, 1000, "150.00"), _obs(5, None, None)])
    series.issues = (ISSUE_STALE,)
    assert quality_status_for_series(series) == QUALITY_STALE


def test_quality_duplicate_detection():
    dup = _obs(5, 1000, "150.00")
    series = _series_from([_obs(0, 900, "149.00"), dup, dup])
    assert ISSUE_DUPLICATE_TIMESTAMP in series.issues
    assert quality_status_for_series(series) == QUALITY_DUPLICATE


def test_quality_out_of_order_detection():
    late = _obs(30, 1200, "151.00")
    series = _series_from([late, _obs(0, 900, "149.00")])
    assert ISSUE_OUT_OF_ORDER in series.issues
    assert quality_status_for_series(series) == QUALITY_OUT_OF_ORDER


def test_quality_insufficient_history():
    empty = ObservationSeries(contract_key="X", label="X")
    empty.reason = "NO_PERSISTED_OBSERVATIONS"
    assert quality_status_for_series(empty) == QUALITY_INSUFFICIENT_HISTORY


def test_delta_quality_mapping():
    ok = IntervalDelta(
        contract_key="K",
        label="K",
        interval_minutes=5,
        baseline_provenance="PERSISTED",
        availability="OK",
        oi_start=100,
        oi_end=150,
        oi_change=50,
    )
    assert quality_status_for_delta(ok) == QUALITY_VALID
    payload = ok.to_dict()
    assert payload["quality_status"] == QUALITY_VALID

    missing = IntervalDelta(
        contract_key="K",
        label="K",
        interval_minutes=5,
        baseline_provenance="PERSISTED",
        availability=AVAILABILITY_INSUFFICIENT,
        reason="NO_BASELINE_WITHIN_TOLERANCE",
    )
    assert quality_status_for_delta(missing) == QUALITY_INSUFFICIENT_HISTORY


def test_quality_summary_insufficient_history_does_not_contaminate():
    good = _series_from([_obs(0, 1000, "150.00"), _obs(5, 1100, "152.00")])
    empty = ObservationSeries(contract_key="X", label="X")
    empty.reason = "NO_PERSISTED_OBSERVATIONS"

    summary = quality_summary([good, empty])
    assert summary["overall"] == QUALITY_VALID
    assert summary["counts"] == {QUALITY_VALID: 1, QUALITY_INSUFFICIENT_HISTORY: 1}
    assert summary["series_count"] == 2


def test_quality_summary_all_missing_history():
    empty = ObservationSeries(contract_key="X", label="X")
    empty.reason = "NO_PERSISTED_OBSERVATIONS"
    summary = quality_summary([empty])
    assert summary["overall"] == QUALITY_INSUFFICIENT_HISTORY


def test_quality_summary_worst_observed_wins():
    stale = _series_from([_obs(0, 1000, "150.00")])
    stale.issues = (ISSUE_STALE,)
    good = _series_from([_obs(0, 1000, "150.00"), _obs(5, 1100, "152.00")])
    summary = quality_summary([good, stale])
    assert summary["overall"] == QUALITY_STALE


# ===========================================================================
# Interval integrity — supported windows, no pro-rata, session dates
# ===========================================================================


@pytest.mark.parametrize("minutes", [3, 5, 10, 15, 30, 45, 60])
def test_interval_windows_use_real_baselines_only(minutes: int):
    """A gap wider than tolerance yields no measured delta, never a pro-rated one."""
    if minutes >= 30:
        # Second point lies far outside the tolerance window.
        points = [_obs(0, 1000, "150.00"), _obs(minutes + 45, 1500, "155.00")]
    else:
        points = [_obs(0, 1000, "150.00"), _obs(minutes, 1500, "155.00")]
    series = _series_from(points)
    deltas = compute_interval_deltas(series, minutes)

    measured = [d for d in deltas if d.is_available]
    if minutes >= 30:
        assert measured == []  # gap must not be presented as measured
    else:
        assert len(measured) == 1
        assert measured[0].oi_change == 500
    assert tolerance_seconds_for(minutes) == max(30.0, minutes * 60 * 0.34)


def test_ist_session_date_boundary_midnight_utc():
    """21:30 UTC is 03:00 IST next day — session date must roll forward."""
    late_utc = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)
    assert ist_trade_date(late_utc) == date(2026, 9, 25)


# ===========================================================================
# Authorization and defaults regression guards
# ===========================================================================


def test_capture_still_disabled_by_default():
    assert settings.derivatives_observation_capture_enabled is False


@pytest.mark.asyncio
async def test_capture_service_requires_authorization():
    service = DerivativesCaptureService()
    ok, msg, _ = await service.start_capture(DerivativesCaptureConfig(), authorized=False)
    assert ok is False
    assert "AUTHORIZATION_REQUIRED" in msg


def test_capture_rejects_unknown_underlying():
    errors = DerivativesCaptureConfig(underlying="NIFTY-FUT").validate()
    assert errors and "not supported" in errors[0]


# ===========================================================================
# Phase D — in-process page rendering (auth + capture badge + empty states)
# ===========================================================================


DERIVATIVE_PAGES = [
    "/market-data/derivatives/oi-statistics?symbol=NIFTY",
    "/market-data/derivatives/oi-change?symbol=NIFTY&interval=3m&strikes=5",
    "/market-data/derivatives/multistrike-oi?symbol=NIFTY",
    "/market-data/derivatives/options-oi-time?symbol=NIFTY",
    "/market-data/derivatives/futures-oi-time?symbol=NIFTY",
    "/market-data/derivatives/oi-replay?symbol=NIFTY",
]

DERIVATIVE_LANDING = "/market-data/derivatives"


@pytest.fixture
async def browser_client():
    """Authenticated in-process browser client (the safe ASGI fallback path)."""
    from tests.auth_helpers import auth_client

    async with auth_client(is_superuser=True, browser=True) as client:
        yield client


@pytest.mark.parametrize("path", DERIVATIVE_PAGES)
async def test_workbench_pages_render_with_capture_status(path: str, browser_client):
    """Every workbench renders, shows the capture badge and the filter panel."""
    response = await browser_client.get(path)
    assert response.status_code == 200, path
    body = response.text
    assert "Capture:" in body, path  # capture state badge from the filter panel
    assert "Derivatives Filter" in body, path


async def test_landing_page_renders(browser_client):
    """The derivatives landing page renders without a workbench filter panel."""
    response = await browser_client.get(DERIVATIVE_LANDING)
    assert response.status_code == 200


# Pages whose core payload is inherently history-dependent: their main series
# can only come from persisted observations (never the provider), so with no
# accumulated capture an explicit unavailable/insufficient state is structural
# and must render regardless of whether the provider is reachable.
HISTORY_DEPENDENT_PAGES = [
    "/market-data/derivatives/oi-change?symbol=NIFTY&interval=3m&strikes=5",
    "/market-data/derivatives/options-oi-time?symbol=NIFTY",
    "/market-data/derivatives/oi-replay?symbol=NIFTY",
]

FORBIDDEN_FABRICATION_MARKERS = [
    "UPSTOX_MOCK",
    "MOCK_CHAIN",
    "SYNTHETIC_FUTURES",
    "NIFTY-FUT",
    "NIFTY-FUTUR",
    "COST_OF_CARRY_PRICE",
]


@pytest.mark.parametrize("path", HISTORY_DEPENDENT_PAGES)
async def test_history_dependent_pages_show_honest_empty_state(path: str, browser_client):
    """With no persisted history these pages must say so explicitly."""
    body = (await browser_client.get(path)).text
    lowered = body.lower()
    assert (
        "insufficient" in lowered or "no " in lowered or "unavailable" in lowered
    ), path


@pytest.mark.parametrize("path", DERIVATIVE_PAGES)
async def test_workbench_pages_contain_no_fabrication_markers(path: str, browser_client):
    """No mock/synthetic data may appear as live data on any workbench.

    Environment-independent: provider-backed pages legitimately render provider
    values (provenance-labelled); what must never appear is a mock marker or a
    synthetic instrument identifier.
    """
    body = (await browser_client.get(path)).text
    for marker in FORBIDDEN_FABRICATION_MARKERS:
        assert marker not in body, f"{marker} leaked on {path}"
