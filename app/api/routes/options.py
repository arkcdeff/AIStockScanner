"""Live Intraday Options REST API — Milestone G.11-OPT-1 Phase 1.

Provides normalized REST endpoints for:
  - GET /api/v1/market-data/options/underlyings
  - GET /api/v1/market-data/options/expiries
  - GET /api/v1/market-data/options/contracts
  - GET /api/v1/market-data/options/chain
  - GET /api/v1/market-data/options/dashboard
  - GET /api/v1/market-data/options/pcr
  - GET /api/v1/market-data/options/oi-comparison

Strict Safety Requirements:
  - Read-only analytics endpoints.
  - Zero order execution, buy/sell buttons, or strategy automation.
  - Dynamic contract/expiry resolution without hardcoded calendar dates.
  - Distinct exchange event time, receive time, and provider provenance.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.auth.authorization import require_permission
from app.services.derivatives_export import (
    FUTURES_LEDGER_EXPORT_COLUMNS,
    OPTIONS_INTERVAL_EXPORT_COLUMNS,
    DerivativeExportError,
    build_csv,
    export_filename,
)
from app.services.derivatives_scope import (
    ANALYSIS_WINDOW_CHOICES_MINUTES,
    INTERVAL_CHOICES_MINUTES,
    OI_UNIT_CHOICES,
    STRIKE_COUNT_CHOICES,
    DerivativesFilterSpec,
    parse_evaluation_time,
)
from app.services.intraday_options_service import IntradayOptionsService, get_ist_now

router = APIRouter(
    prefix="/market-data/options",
    tags=["Live Options"],
    dependencies=[Depends(require_permission("stocks.read"))],
)


def _get_service() -> IntradayOptionsService:
    return IntradayOptionsService()


def _filter_spec(
    symbol: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry (YYYY-MM-DD)"),
    strikes: str = Query(
        default="5",
        description="Strike count per side: 5, 10, 15, 20, 25 or 'all'",
    ),
    interval: str = Query(
        default="5m", description="Interval: 1m,2m,3m,5m,10m,15m,30m,45m,60m or session"
    ),
    window: str = Query(default="INTRADAY_INTERVAL", description="INTRADAY_INTERVAL, FULL_SESSION or CUSTOM"),
    call_strikes: str | None = Query(default=None, description="Comma-separated CE strikes (manual selection)"),
    put_strikes: str | None = Query(default=None, description="Comma-separated PE strikes (manual selection)"),
    min_strike: Decimal | None = Query(default=None, description="Explicit minimum strike"),
    max_strike: Decimal | None = Query(default=None, description="Explicit maximum strike"),
    oi_unit: str = Query(default="CONTRACTS", description="CONTRACTS, LOTS or QUANTITY"),
    session_date: date | None = Query(default=None, description="Session date for replay/history"),
    start_time: str | None = Query(default=None, description="Custom range start (HH:MM or ISO)"),
    end_time: str | None = Query(default=None, description="Custom range end (HH:MM or ISO)"),
    page_size: int = Query(default=50, ge=1, le=5000),
    sort_by: str = Query(default="timestamp"),
    sort_dir: str = Query(default="desc"),
) -> DerivativesFilterSpec:
    """Build and validate the shared derivatives filter state.

    One schema validates every derivatives view so no endpoint can silently widen
    the selected strike range or expiry scope. Invalid parameters are rejected
    with 422 rather than being coerced.
    """
    spec = DerivativesFilterSpec.from_query(
        {
            "symbol": symbol,
            "expiry": expiry,
            "strikes": strikes,
            "interval": interval,
            "window": window,
            "call_strikes": call_strikes,
            "put_strikes": put_strikes,
            "min_strike": min_strike,
            "max_strike": max_strike,
            "oi_unit": oi_unit,
            "session_date": session_date,
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    )
    problems = spec.errors()
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"errors": problems, "received": spec.fingerprint()},
        )
    return spec.normalized()


def _with_scope_fields(payload: dict[str, Any], spec: DerivativesFilterSpec) -> dict[str, Any]:
    """Attach the scope fingerprint and control vocabulary to a response.

    ``scope_fingerprint`` lets a client discard a response that belongs to a
    previously selected underlying instead of letting it overwrite the current view.
    """
    payload.setdefault("scope_fingerprint", spec.fingerprint())
    payload.setdefault("filter_state", {
        "underlying": spec.underlying,
        "contract_type": spec.contract_type,
        "expiry": spec.expiry.isoformat() if spec.expiry else None,
        "strike_count": spec.strike_count,
        "strike_count_semantics": "STRIKES_PER_SIDE",
        "interval_minutes": spec.interval_minutes,
        "window": spec.window,
        "oi_unit": spec.oi_unit,
        "call_strikes": [str(s) for s in spec.call_strikes],
        "put_strikes": [str(s) for s in spec.put_strikes],
        "min_strike": str(spec.min_strike) if spec.min_strike is not None else None,
        "max_strike": str(spec.max_strike) if spec.max_strike is not None else None,
    })
    payload.setdefault("control_vocabulary", {
        "strike_count_choices": list(STRIKE_COUNT_CHOICES),
        "interval_choices": list(INTERVAL_CHOICES_MINUTES),
        "analysis_window_choices": list(ANALYSIS_WINDOW_CHOICES_MINUTES),
        "oi_unit_choices": list(OI_UNIT_CHOICES),
    })
    return payload


@router.get("/underlyings", status_code=status.HTTP_200_OK)
async def get_options_underlyings(
    service: IntradayOptionsService = Depends(_get_service),
) -> list[dict[str, Any]]:
    """Return list of supported underlying index symbols and contract specifications."""
    return await service.get_supported_underlyings()


@router.get("/scope", status_code=status.HTTP_200_OK)
async def get_options_scope(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Resolve the provider-grounded scope: real spot, listed expiries, real
    listed strike grid, ATM resolved from that grid, and verified lot/tick size."""
    try:
        scope = await service.get_scope(spec.underlying, spec.expiry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(scope, spec)


@router.get("/expiries", status_code=status.HTTP_200_OK)
async def get_options_expiries(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol (e.g. NIFTY, BANKNIFTY)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Dynamically resolve active non-expired expiry dates for an underlying index."""
    try:
        expiries = await service.get_available_expiries(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "underlying": underlying.upper(),
        "total_expiries": len(expiries),
        "expiries": expiries,
    }


@router.get("/contracts", status_code=status.HTTP_200_OK)
async def get_options_contracts(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry date (YYYY-MM-DD)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Return available option strike prices and basic contract specifications."""
    try:
        ladder_data = await service.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry,
            strike_count=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    strikes = [row["strike_price"] for row in ladder_data["ladder"]]
    return {
        "underlying": underlying.upper(),
        "expiry_date": ladder_data["selected_expiry"],
        "spot_price": ladder_data["spot_price"],
        "atm_strike": ladder_data["atm_strike"],
        "strike_step": ladder_data["strike_step"],
        "total_strikes": len(strikes),
        "strikes": strikes,
        "provider": ladder_data["provider"],
        "market_session": ladder_data["market_session"],
    }


@router.get("/chain", status_code=status.HTTP_200_OK)
async def get_options_chain(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Normalized live strike ladder with a real, listed, per-underlying ATM.

    ``atm_diagnostics`` states the grid source, the resolution method and the
    tie-break used, and ``is_listed`` proves the ATM exists in the listed grid.
    """
    try:
        payload = await service.get_option_chain_ladder(
            underlying=spec.underlying,
            expiry_date=spec.expiry,
            strike_count=spec.strike_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/dashboard", status_code=status.HTTP_200_OK)
async def get_options_dashboard(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry date (YYYY-MM-DD)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Return high-level summary metrics, ATM straddle premium, and PCR."""
    try:
        return await service.get_option_dashboard(
            underlying=underlying,
            expiry_date=expiry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pcr", status_code=status.HTTP_200_OK)
async def get_options_pcr(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry date (YYYY-MM-DD)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Return live Put-Call Ratio metrics with explicit zero-denominator handling."""
    try:
        return await service.get_live_pcr(
            underlying=underlying,
            expiry_date=expiry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oi-comparison", status_code=status.HTTP_200_OK)
async def get_options_oi_comparison(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry date (YYYY-MM-DD)"),
    strike_count: int = Query(default=15, description="Number of strikes around ATM to compare"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Return strike-wise Call vs Put open interest comparison distribution."""
    try:
        return await service.get_call_vs_put_oi(
            underlying=underlying,
            expiry_date=expiry,
            strike_count=strike_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oi-change", status_code=status.HTTP_200_OK)
async def get_options_oi_change(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    buildup_filter: str | None = Query(default="ALL", description="Filter by buildup classification (ALL, LONG_BUILDUP, etc.)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Strike-wise CE/PE open-interest changes for a **measured** interval.

    The interval change is only reported when a real comparable observation pair
    exists. No pro-rata scaling and no previous-close substitution is applied to an
    intraday window. ``session_date`` (when supplied) bounds the historical read to
    that trading session; otherwise the current IST session is used, so a baseline
    can never come from a previous session.
    """
    try:
        payload = await service.get_interval_oi_change(
            underlying=spec.underlying,
            expiry_date=spec.expiry,
            strike_count=spec.strike_count if spec.strike_count is not None else 15,
            interval=f"{spec.interval_minutes}m",
            buildup_filter=buildup_filter,
            session_date=spec.session_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/futures-ledger", status_code=status.HTTP_200_OK)
async def get_futures_ledger(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Detailed futures interval ledger built from real provider observations.

    Contains the fifteen ledger columns, session positioning cards, basis with a
    documented denominator, and an explicit unavailable state when the provider
    supplies no real futures observation.
    """
    try:
        payload = await service.get_futures_oi_vs_time(
            spec.underlying,
            interval_minutes=spec.interval_minutes,
            expiry_date=spec.expiry,
            oi_unit=spec.oi_unit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/export", response_class=PlainTextResponse)
async def export_derivatives_table(
    request: Request,
    kind: str = Query(default="futures-ledger", description="futures-ledger or oi-change"),
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> PlainTextResponse:
    """CSV export of the selected derivatives table.

    Exports carry source and observation timestamps for every row and never include
    tokens, credentials or internal identifiers. Authentication and authorization
    are enforced by the router-level permission dependency.
    """
    try:
        if kind == "futures-ledger":
            payload = await service.get_futures_oi_vs_time(
                spec.underlying,
                interval_minutes=spec.interval_minutes,
                expiry_date=spec.expiry,
                oi_unit=spec.oi_unit,
            )
            ledger = payload.get("ledger") or {}
            rows = ledger.get("rows") or []
            columns = FUTURES_LEDGER_EXPORT_COLUMNS
            metadata = {
                "kind": "futures-ledger",
                "underlying": spec.underlying,
                "contract": payload.get("contract_symbol"),
                "instrument_key": payload.get("instrument_key"),
                "expiry": payload.get("selected_expiry"),
                "interval_minutes": spec.interval_minutes,
                "oi_unit": spec.oi_unit,
                "provider_entitlement": payload.get("provider_entitlement"),
                "ledger_availability": ledger.get("availability"),
                "generated_from": "REAL_PROVIDER_OBSERVATIONS_ONLY",
            }
        elif kind == "oi-change":
            payload = await service.get_interval_oi_change(
                underlying=spec.underlying,
                expiry_date=spec.expiry,
                strike_count=spec.strike_count if spec.strike_count is not None else 15,
                interval=f"{spec.interval_minutes}m",
                session_date=spec.session_date,
            )
            rows = payload.get("strikes") or []
            columns = OPTIONS_INTERVAL_EXPORT_COLUMNS
            metadata = {
                "kind": "oi-change",
                "underlying": spec.underlying,
                "expiry": payload.get("selected_expiry"),
                "interval_minutes": spec.interval_minutes,
                "baseline_type": payload.get("baseline_type"),
                "baseline_label": payload.get("baseline_label"),
                "measurement_available": payload.get("measurement_available"),
                "pro_rata_scaling_applied": payload.get("pro_rata_scaling_applied"),
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="kind must be 'futures-ledger' or 'oi-change'",
            )

        if not rows:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No rows are available for this scope, so there is nothing to "
                    "export. Real observations are required; placeholder rows are "
                    "never generated."
                ),
            )
        csv_text = build_csv(rows, columns=columns, metadata=metadata)
    except DerivativeExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filename = export_filename(
        kind, spec.underlying, spec.session_date.isoformat() if spec.session_date else None
    )
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/replay", status_code=status.HTTP_200_OK)
async def get_options_replay(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Point-in-time replay timeline plus the PIT-safe ladder at each real step.

    Steps are real observation instants. A step without a snapshot is reported as
    uncovered; the current ladder is never substituted for a historical one.
    """
    try:
        timeline = await service.get_session_replay_timeline(
            spec.underlying,
            session_date=spec.session_date,
            interval_minutes=spec.interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(timeline, spec)


@router.get("/replay-ladder", status_code=status.HTTP_200_OK)
async def get_options_replay_ladder(
    at: str = Query(..., description="Replay instant: ISO-8601 or HH:MM IST"),
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Point-in-time ladder reconstructed from observations visible at ``at``.

    The replay timestamp is mandatory: without an explicit instant no
    point-in-time cut can be enforced, so the endpoint refuses rather than
    falling back to the live chain.
    """
    session_date = spec.session_date or get_ist_now().date()
    evaluation_at = parse_evaluation_time(at, session_date=session_date)
    if evaluation_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "errors": [
                    f"at '{at}' is not a valid instant (use ISO-8601 or HH:MM IST)"
                ]
            },
        )
    try:
        payload = await service.get_replay_ladder(
            spec.underlying,
            session_date=session_date,
            expiry_date=spec.expiry,
            evaluation_at=evaluation_at,
            strike_count=spec.strike_count,
            call_strikes=spec.call_strikes,
            put_strikes=spec.put_strikes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/oi-statistics", status_code=status.HTTP_200_OK)
async def get_options_oi_statistics(
    underlying: str = Query(default="NIFTY", description="Underlying index symbol"),
    expiry: date | None = Query(default=None, description="Contract expiry date (YYYY-MM-DD)"),
    range: str | None = Query(default="15", description="Strike range (10, 15, 20, all)"),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Return comprehensive Open Interest statistics, distributions, and Max Pain metrics."""
    try:
        return await service.get_oi_statistics(
            underlying=underlying,
            expiry_date=expiry,
            strike_range=range,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/multistrike-oi", status_code=status.HTTP_200_OK)
async def get_options_multistrike_oi(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Multi-strike OI trajectories over time from real stored observations.

    Strike scope is bounded to the listed grid. Strikes without real observations
    report explicit insufficient coverage rather than a synthesised series.
    ``session_date`` (when supplied) bounds the historical read to that trading
    session; otherwise the current IST session is used, so a trajectory can never
    mix two sessions' observations into one clock-time axis.
    """
    try:
        payload = await service.get_multistrike_oi(
            underlying=spec.underlying,
            expiry_date=spec.expiry,
            selected_strikes=[float(s) for s in (spec.call_strikes + spec.put_strikes)] or None,
            strike_count=spec.strike_count,
            interval_minutes=spec.interval_minutes,
            session_date=spec.session_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/options-oi-time", status_code=status.HTTP_200_OK)
async def get_options_oi_vs_time(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Call vs Put OI over time for the exact selected contract set.

    Aggregates only span the selected expiry and selected contracts; expiries are
    never blended, and the underlying index move is never used as a substitute for
    option premium movement. ``session_date`` (when supplied) bounds the read to
    that trading session; otherwise the current IST session is used.
    """
    try:
        payload = await service.get_options_oi_vs_time(
            underlying=spec.underlying,
            expiry_date=spec.expiry,
            interval_minutes=spec.interval_minutes,
            strike_count=spec.strike_count,
            call_strikes=spec.call_strikes,
            put_strikes=spec.put_strikes,
            session_date=spec.session_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/futures-oi-time", status_code=status.HTTP_200_OK)
async def get_futures_oi_vs_time(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Real futures price, volume and open interest over time with the ledger."""
    try:
        payload = await service.get_futures_oi_vs_time(
            spec.underlying,
            interval_minutes=spec.interval_minutes,
            expiry_date=spec.expiry,
            oi_unit=spec.oi_unit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


@router.get("/replay-timeline", status_code=status.HTTP_200_OK)
async def get_replay_timeline(
    spec: DerivativesFilterSpec = Depends(_filter_spec),
    service: IntradayOptionsService = Depends(_get_service),
) -> dict[str, Any]:
    """Persisted-snapshot timeline available for point-in-time replay."""
    try:
        payload = await service.get_session_replay_timeline(
            spec.underlying,
            session_date=spec.session_date,
            interval_minutes=spec.interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with_scope_fields(payload, spec)


# ===========================================================================
# Operator-Controlled Observation Capture Endpoints (Milestone G.11-OPT-4)
# ===========================================================================

@router.get("/capture/status", status_code=status.HTTP_200_OK)
async def get_capture_status() -> dict[str, Any]:
    """Read-only capture status and health metrics for derivatives observations."""
    from app.services.derivatives_capture_service import get_capture_service

    return get_capture_service().get_status()


@router.post(
    "/capture/start",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("market_data.write"))],
)
async def start_capture_session(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start an operator-controlled, bounded observation capture session.

    Requires explicit 'market_data.write' permission.
    """
    from app.services.derivatives_capture_service import (
        DerivativesCaptureConfig,
        get_capture_service,
    )

    data = payload or {}
    expiry_val = None
    if data.get("expiry"):
        try:
            expiry_val = date.fromisoformat(str(data["expiry"]))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="expiry must be in YYYY-MM-DD format",
            ) from exc

    config = DerivativesCaptureConfig(
        underlying=str(data.get("underlying", "NIFTY")),
        expiry=expiry_val,
        strike_count=int(data.get("strike_count", 10)),
        interval_seconds=int(data.get("interval_seconds", 60)),
        max_duration_minutes=int(data.get("max_duration_minutes", 375)),
        max_contracts=int(data.get("max_contracts", 100)),
        allow_outside_market_hours=bool(data.get("allow_outside_market_hours", False)),
    )

    service = get_capture_service()
    success, msg, telemetry = await service.start_capture(config, authorized=True)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": msg, "telemetry": telemetry},
        )
    return {"status": "SUCCESS", "message": msg, "telemetry": telemetry}


@router.post(
    "/capture/stop",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("market_data.write"))],
)
async def stop_capture_session() -> dict[str, Any]:
    """Stop the active observation capture session gracefully.

    Requires explicit 'market_data.write' permission.
    """
    from app.services.derivatives_capture_service import get_capture_service

    service = get_capture_service()
    success, msg, telemetry = await service.stop_capture(authorized=True)
    return {
        "status": "SUCCESS" if success else "FAILED",
        "message": msg,
        "telemetry": telemetry,
    }


@router.post(
    "/capture/cleanup",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("market_data.write"))],
)
async def cleanup_capture_storage(
    max_age_days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Operator-authorized cleanup of observation files older than max_age_days.

    Requires explicit 'market_data.write' permission.
    """
    from app.services.derivatives_capture_service import get_capture_service

    service = get_capture_service()
    success, msg, stats = await service.cleanup_storage(
        max_age_days=max_age_days, authorized=True
    )
    return {
        "status": "SUCCESS" if success else "FAILED",
        "message": msg,
        "stats": stats,
    }
