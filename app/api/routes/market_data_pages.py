from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import finalize_page_response, stage_csrf_token
from app.auth import csrf
from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.market_data import MarketDataRepository
from app.services.market_data_service import MarketDataService

logger = logging.getLogger(__name__)


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from app.services.derivatives_scope import format_display_number

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    # Provider-derived numbers reach templates as Decimal, float or an exact
    # decimal string, depending on which service produced them. ``fmt_num``
    # renders all three identically instead of raising on the odd type out.
    env.globals["fmt_num"] = format_display_number
    return env.get_template(template_name).render(**context)


def _flash_url(**params: str | int) -> str:
    parts = [f"{k}={v}" for k, v in params.items()]
    return "/market-data?" + "&".join(parts) if parts else "/market-data"


router = APIRouter(dependencies=[Depends(require_browser_permission("stocks.read"))])
WRITE_DEPENDENCIES = [Depends(require_browser_permission("market_data.write"))]


async def _safe_scope(service: Any, symbol: str, expiry: date | None) -> dict | None:
    """Resolve the provider-grounded scope for a page, tolerating read failures.

    A failed scope read must not break the page: the filter panel renders an
    explicit unavailable state instead of a guessed value.
    """
    try:
        return await service.get_scope(symbol, expiry)
    except Exception:  # noqa: BLE001 - provider outage must not 500 the page
        return None


async def _canonical_listed_expiry(
    service: Any,
    symbol: str,
    expiry: date | None,
    *,
    expiries: list[dict] | None = None,
) -> str | None:
    """Resolve the one expiry a view may actually use for the selected underlying.

    An expiry reaches a route from a URL that is a durable copy of the control
    state, so an expiry belonging to a previously selected underlying can survive
    inside a bookmark, a refresh, a shared link or a filter-panel submission.
    Only an expiry actually listed for the selected underlying may drive a view:
    anything else falls back to the nearest listed expiry — exactly the value the
    expiry control selects when no expiry is supplied. An expiry is never invented
    and an unlisted expiry is never silently used, which is how a previous
    underlying's expiry used to leak into another underlying's tabs and charts.

    The same rule is applied by the full workspace page, its tab partials and the
    standalone derivatives pages, so full-page and partial behavior cannot
    disagree.
    """
    if expiries is None:
        try:
            expiries = await service.get_available_expiries(symbol)
        except Exception as exc:  # noqa: BLE001 - no listed expiry is known
            logger.warning("Expiry lookup failed for %s: %s", symbol, exc)
            return None
    listed_tokens = {expiry_row["expiry_date"] for expiry_row in expiries}
    if expiry is not None and expiry.isoformat() in listed_tokens:
        return expiry.isoformat()
    if expiries:
        selected_expiry = expiries[0]["expiry_date"]
        if expiry is not None:
            logger.warning(
                "Expiry %s is not listed for %s; using nearest listed expiry %s",
                expiry.isoformat(),
                symbol,
                selected_expiry,
            )
        return selected_expiry
    return None


async def _derivatives_filter_context(
    service: Any,
    symbol: str,
    *,
    form_action: str,
    expiry: date | None = None,
    interval: str = "5m",
    strikes: str = "5",
    call_strikes: str | None = None,
    put_strikes: str | None = None,
    min_strike: str | None = None,
    max_strike: str | None = None,
    oi_unit: str = "CONTRACTS",
    show_expiry: bool = True,
    show_strike_count: bool = True,
    show_manual_strikes: bool = True,
    show_interval: bool = True,
    show_oi_unit: bool = False,
) -> dict[str, Any]:
    """Shared context for the reusable derivatives filter/selection panel.

    The same keys feed every derivatives page, so filter state stays consistent
    across views while remaining scoped to the selected underlying and expiry.
    """
    from app.services.derivatives_capture_service import get_capture_service
    from app.services.derivatives_scope import parse_interval_token

    minutes = parse_interval_token(interval) or 5
    return {
        "scope": await _safe_scope(service, symbol, expiry),
        "form_action": form_action,
        "selected_interval": f"{minutes}m",
        "selected_strikes": strikes,
        "selected_call_strikes": call_strikes or "",
        "selected_put_strikes": put_strikes or "",
        "selected_min_strike": min_strike or "",
        "selected_max_strike": max_strike or "",
        "selected_oi_unit": oi_unit.upper(),
        "show_expiry": show_expiry,
        "show_strike_count": show_strike_count,
        "show_manual_strikes": show_manual_strikes,
        "show_interval": show_interval,
        "show_oi_unit": show_oi_unit,
        "capture_status": get_capture_service().get_status(),
    }


def _require_csrf(request: Request, form: dict | None = None) -> None:
    csrf.verify_request_csrf(request, form)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def market_data_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    service = MarketDataService(session)
    summary = await repo.product_summary()
    imports = await repo.list_imports(limit=15)
    prices = await repo.list_prices(limit=10, product_only=True)
    status = await service.data_status()
    readiness = await service.scanner_readiness()

    today = datetime.now(UTC).date()
    coverage_start = today - timedelta(days=30)
    coverage = await service.coverage(coverage_start, today)
    coverage_summary = {
        "complete": sum(1 for row in coverage if row["status"] == "complete"),
        "partial": sum(1 for row in coverage if row["status"] == "partial"),
        "missing": sum(1 for row in coverage if row["status"] == "missing"),
    }

    price_rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "close": price.close,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    csrf_token = stage_csrf_token(request)
    html = _render(
        "market_data.html",
        {
            "request": request,
            "summary": summary,
            "status": status,
            "readiness": readiness,
            "imports": imports,
            "prices": price_rows,
            "coverage": coverage,
            "coverage_summary": coverage_summary,
            "settings_eod_catchup_days": settings.eod_scheduler_catchup_max_days,
            "csrf_token": csrf_token,
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives", response_class=HTMLResponse)
async def derivatives_workspace_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Full-page wrapper for the existing deterministic derivatives research services."""
    from app.services.derivative_coverage_service import DerivativeCoverageService

    coverage = await DerivativeCoverageService(session).get_derivatives_coverage()
    html = _render(
        "derivatives.html",
        {
            "request": request,
            "coverage": coverage,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/option-chain", response_class=HTMLResponse)
@router.get("/options", response_class=HTMLResponse)
async def options_workspace_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    strike_count: str = Query(
        default="10", description="Strike-range control value: 5, 10, 15, 20, 25 or 'all'"
    ),
) -> HTMLResponse:
    """Full-page view for the Live Intraday Options Workbench.

    The query string is a durable copy of the workspace control state (the
    workspace script mirrors the controls into it), so the page renders the same
    three controls it was asked for: underlying, a **listed** expiry for that
    underlying, and the strike-range the tabs consuming it must use. Rendering a
    different strike range than the control showed left the pre-rendered OI tabs
    silently disagreeing with the control on a reloaded URL.
    """
    from app.services.derivatives_scope import parse_strike_count_token
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()

    # The strike-range control is part of the workspace state: parse it with the
    # project's own vocabulary so the control and the tabs that consume it agree.
    # It is validated before any provider read, because a malformed request must
    # be rejected rather than served with a substituted default.
    parsed_strike_count, strike_problem = parse_strike_count_token(strike_count)
    if strike_problem:
        raise HTTPException(status_code=400, detail=strike_problem)
    selected_strike_token = "all" if parsed_strike_count is None else str(parsed_strike_count)

    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    # Only an expiry listed for this underlying may drive the tab views; an
    # unlisted one falls back to the nearest listed expiry (never invented,
    # never the outgoing underlying's expiry). Shared with the tab partials.
    selected_expiry = await _canonical_listed_expiry(
        service, symbol, expiry, expiries=expiries
    )
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None

    chain = None
    dashboard = None
    pcr_data = None
    oi_data = None
    oi_change_data = None
    try:
        chain = await service.get_option_chain_ladder(
            symbol, expiry_date=sel_exp_date, strike_count=parsed_strike_count
        )
        dashboard = await service.get_option_dashboard(symbol, expiry_date=sel_exp_date)
        pcr_data = await service.get_live_pcr(symbol, expiry_date=sel_exp_date)
        oi_data = await service.get_call_vs_put_oi(
            symbol, expiry_date=sel_exp_date, strike_count=parsed_strike_count
        )
        oi_change_data = await service.get_oi_change_analysis(
            symbol, expiry_date=sel_exp_date, strike_count=parsed_strike_count
        )
    except Exception as exc:
        logger.warning("Error pre-loading intraday options: %s", exc)

    html = _render(
        "options_workspace.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "selected_strike_count": selected_strike_token,
            "chain": chain,
            "dashboard": dashboard,
            "pcr_data": pcr_data,
            "oi_data": oi_data,
            "oi_change_data": oi_change_data,
            "show_buildup": False,
            "show_greeks": False,
            "show_iv": False,
            "show_delta": False,
            "show_gamma": False,
            "show_vega": False,
            "is_market_open": chain["is_market_open"] if chain else False,
            "provider_name": service.adapter.source_name,
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/oi-statistics", response_class=HTMLResponse)
async def derivatives_oi_statistics_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    range: str | None = Query(default=None),
    strikes: str | None = Query(default=None),
) -> HTMLResponse:
    """Full-page view for the institutional OI Statistics Dashboard.

    G.11-OPT-9 (D-02): this page renders TWO strike controls — its own ``range``
    select and the shared derivatives filter panel's Strike Count (``strikes``).
    The route previously read only ``range``, so the shared panel's control was
    rendered but ignored: submitting it navigated to a URL that changed nothing.
    Both controls express the same thing (N strikes per side around ATM) and both
    drive the same **display** filter — the certified aggregates (totals, PCR, Max
    Pain, concentration, additions/reductions) are always computed over every
    listed strike, so this is analytics-neutral. ``range`` keeps precedence when
    the page's own control submits it.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    # The shared filter panel re-submits the expiry select it was rendered with,
    # so after a symbol change it can still name the previous underlying's
    # expiry. Only this underlying's listed expiries may drive the read.
    selected_expiry = await _canonical_listed_expiry(service, symbol, expiry, expiries=expiries)
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None

    effective_range = range if range is not None else (strikes or "15")
    stats = await service.get_oi_statistics(symbol, expiry_date=sel_exp_date, strike_range=effective_range)
    html = _render(
        "derivatives_oi_statistics.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "selected_range": effective_range,
            "stats": stats,
            **(await _derivatives_filter_context(
                service,
                symbol,
                form_action="/market-data/derivatives/oi-statistics",
                strikes=effective_range,
                expiry=sel_exp_date,
                show_interval=False,
            )),
            "provider_name": service.adapter.source_name,
            "is_market_open": stats.get("is_market_open", False),
            "as_of_ist": stats.get("as_of_ist", "09:15 IST"),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/oi-change", response_class=HTMLResponse)
async def derivatives_oi_change_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    interval: str = Query(default="15m"),
    strikes: str = Query(default="15", description="Strike count per side, or 'all'"),
) -> HTMLResponse:
    """Full-page view for the Intraday OI Change and Interval Analytics view.

    G.11-OPT-9: ``strikes`` accepts the same vocabulary the shared derivatives
    filter panel actually emits (a per-side count or ``all``). Declaring it as
    ``int`` made the panel's "All listed strikes" option a user-reachable HTTP 422,
    because the select submits the literal string ``all``. The token is now parsed
    with the project's own ``parse_strike_count_token``; ``None`` means every
    listed strike, which the service layer already supports.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    # The shared filter panel re-submits the expiry select it was rendered with,
    # so after a symbol change it can still name the previous underlying's
    # expiry. Only this underlying's listed expiries may drive the read.
    selected_expiry = await _canonical_listed_expiry(service, symbol, expiry, expiries=expiries)
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None

    from app.services.derivatives_scope import parse_strike_count_token

    strike_count, strike_problem = parse_strike_count_token(strikes)
    if strike_problem:
        # Only genuinely malformed input is rejected; every value the UI emits
        # parses. Mirrors the REST twin's 400 for an invalid spec.
        raise HTTPException(status_code=400, detail=strike_problem)
    selected_strikes_token = "all" if strike_count is None else str(strike_count)

    oi_data = await service.get_interval_oi_change(
        symbol, expiry_date=sel_exp_date, strike_count=strike_count, interval=interval
    )
    html = _render(
        "derivatives_oi_change.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "selected_interval": interval,
            "selected_strikes": selected_strikes_token,
            "oi_data": oi_data,
            **(await _derivatives_filter_context(
                service,
                symbol,
                form_action="/market-data/derivatives/oi-change",
                expiry=sel_exp_date,
                interval=interval,
                strikes=selected_strikes_token,
            )),
            "provider_name": service.adapter.source_name,
            "is_market_open": oi_data.get("is_market_open", False),
            "as_of_ist": oi_data.get("as_of_ist", "09:15 IST"),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/multistrike-oi", response_class=HTMLResponse)
async def derivatives_multistrike_oi_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    interval: str = Query(default="5m"),
    strikes: str = Query(default="5", description="Strike count per side, or 'all'"),
) -> HTMLResponse:
    """Full-page view for Multistrike OI comparison view.

    The filter panel re-submits the symbol and expiry selects it was rendered
    with, so after a symbol change the expiry can still belong to the previous
    underlying. Only this underlying's listed expiries may drive the read.
    """
    from app.services.derivatives_scope import parse_interval_token, parse_strike_count_token
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    selected_expiry = await _canonical_listed_expiry(service, symbol, expiry, expiries=expiries)
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None

    strike_count, strike_problem = parse_strike_count_token(strikes)
    if strike_problem:
        raise HTTPException(status_code=400, detail=strike_problem)
    selected_strikes_token = "all" if strike_count is None else str(strike_count)
    interval_minutes = parse_interval_token(interval) or 5

    multi_oi = await service.get_multistrike_oi(
        symbol,
        expiry_date=sel_exp_date,
        strike_count=strike_count,
        interval_minutes=interval_minutes,
    )
    html = _render(
        "derivatives_multistrike_oi.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "selected_interval": f"{interval_minutes}m",
            "selected_strikes": selected_strikes_token,
            "multi_oi": multi_oi,
            **(await _derivatives_filter_context(
                service,
                symbol,
                form_action="/market-data/derivatives/multistrike-oi",
                expiry=sel_exp_date,
                interval=f"{interval_minutes}m",
                strikes=selected_strikes_token,
            )),
            "provider_name": service.adapter.source_name,
            "is_market_open": False,
            "as_of_ist": multi_oi.get("as_of_ist", "09:15 IST"),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/options-oi-time", response_class=HTMLResponse)
async def derivatives_options_oi_time_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    interval: str = Query(default="5m"),
    strikes: str = Query(default="5", description="Strike count per side, or 'all'"),
) -> HTMLResponse:
    """Full-page view for Options OI vs Time synchronized time-series view.

    The filter panel re-submits the expiry select it was rendered with, so after
    a symbol change the expiry can still belong to the previous underlying. Only
    this underlying's listed expiries may drive the read.
    """
    from app.services.derivatives_scope import parse_interval_token, parse_strike_count_token
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    selected_expiry = await _canonical_listed_expiry(service, symbol, expiry, expiries=expiries)
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None

    strike_count, strike_problem = parse_strike_count_token(strikes)
    if strike_problem:
        raise HTTPException(status_code=400, detail=strike_problem)
    selected_strikes_token = "all" if strike_count is None else str(strike_count)
    interval_minutes = parse_interval_token(interval) or 5

    time_data = await service.get_options_oi_vs_time(
        symbol,
        expiry_date=sel_exp_date,
        strike_count=strike_count,
        interval_minutes=interval_minutes,
    )
    html = _render(
        "derivatives_oi_time.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "selected_interval": f"{interval_minutes}m",
            "selected_strikes": selected_strikes_token,
            "time_data": time_data,
            **(await _derivatives_filter_context(
                service,
                symbol,
                form_action="/market-data/derivatives/options-oi-time",
                expiry=sel_exp_date,
                interval=f"{interval_minutes}m",
                strikes=selected_strikes_token,
            )),
            "provider_name": service.adapter.source_name,
            "is_market_open": False,
            "as_of_ist": time_data.get("as_of_ist", "09:15 IST"),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/futures-oi-time", response_class=HTMLResponse)
async def derivatives_futures_oi_time_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    interval: str = Query(default="5m"),
    oi_unit: str = Query(default="CONTRACTS"),
    strikes: str = Query(default="5"),
    call_strikes: str | None = Query(default=None),
    put_strikes: str | None = Query(default=None),
    min_strike: str | None = Query(default=None),
    max_strike: str | None = Query(default=None),
) -> HTMLResponse:
    """Futures OI and price vs time, with the real interval ledger.

    All numbers come from real provider futures observations. The page never
    displays a modelled cost-of-carry price in place of a traded futures price.
    """
    from app.services.derivatives_capture_service import get_capture_service
    from app.services.derivatives_scope import parse_interval_token
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    interval_minutes = parse_interval_token(interval) or 5

    fut_data = await service.get_futures_oi_vs_time(
        symbol,
        interval_minutes=interval_minutes,
        expiry_date=expiry,
        oi_unit=oi_unit.upper(),
    )
    scope = await _safe_scope(service, symbol, expiry)
    html = _render(
        "derivatives_futures_oi_time.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "selected_expiry": fut_data.get("selected_expiry"),
            "selected_interval": f"{interval_minutes}m",
            "selected_strikes": strikes,
            "selected_oi_unit": oi_unit.upper(),
            "selected_call_strikes": call_strikes or "",
            "selected_put_strikes": put_strikes or "",
            "selected_min_strike": min_strike or "",
            "selected_max_strike": max_strike or "",
            "expiries": fut_data.get("expiries") or [],
            "scope": scope,
            "form_action": "/market-data/derivatives/futures-oi-time",
            "show_oi_unit": True,
            "show_expiry": True,
            "fut_data": fut_data,
            # G.11-OPT-5 (Phase D): keep the operator capture badge visible on
            # this page too — it was silently missing because this route built
            # its context by hand instead of via _derivatives_filter_context.
            "capture_status": get_capture_service().get_status(),
            "provider_name": service.adapter.source_name,
            "is_market_open": False,
            "as_of_ist": (
                (fut_data.get("observation") or {}).get("observed_at_ist")
                or "unavailable"
            ),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives/oi-replay", response_class=HTMLResponse)
async def derivatives_oi_replay_page(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    interval: int = Query(default=15, description="Replay interval in minutes"),
    strikes: str = Query(default="5", description="Strike count per side, or 'all'"),
    session_date: date | None = Query(default=None, description="Trade session date for replay"),
) -> HTMLResponse:
    """Full-page view for Point-in-Time Intraday Option Chain Replay.

    Only the replay *timeline* (real observation instants) is rendered here. The
    ladder itself is fetched per step from the point-in-time endpoint, so the page
    can never display today's live chain as a historical state.
    """
    from app.services.derivatives_scope import ANALYSIS_WINDOW_CHOICES_MINUTES, parse_interval_token
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    underlyings = await service.get_supported_underlyings()
    expiries = await service.get_available_expiries(symbol)
    selected_expiry = expiry.isoformat() if expiry else (expiries[0]["expiry_date"] if expiries else None)
    sel_exp_date = date.fromisoformat(selected_expiry) if selected_expiry else None
    replay_interval = parse_interval_token(interval) or 15

    replay_timeline = await service.get_session_replay_timeline(
        symbol, session_date=session_date, interval_minutes=replay_interval
    )
    active_session_date = session_date
    if (
        not replay_timeline.get("replay_available")
        and replay_timeline.get("available_sessions")
        and session_date is None
    ):
        latest_trade_date_str = replay_timeline["available_sessions"][0]["trade_date"]
        active_session_date = date.fromisoformat(latest_trade_date_str)
        replay_timeline = await service.get_session_replay_timeline(
            symbol, session_date=active_session_date, interval_minutes=replay_interval
        )

    session_expiries = replay_timeline.get("session_expiries", [])
    if session_expiries and active_session_date and active_session_date < date.today():
        exp_dates = {e["expiry_date"] for e in expiries}
        for se_iso in reversed(session_expiries):
            if se_iso not in exp_dates:
                expiries.insert(
                    0,
                    {
                        "expiry_date": se_iso,
                        "display_label": f"{se_iso} (Recorded Expiry)",
                        "days_to_expiry": 0,
                        "expiry_type": "Historical",
                    },
                )
        if expiry is None:
            selected_expiry = session_expiries[0]
            sel_exp_date = date.fromisoformat(selected_expiry)

    html = _render(
        "derivatives_oi_replay.html",
        {
            "request": request,
            "underlyings": underlyings,
            "selected_symbol": symbol.upper(),
            "expiries": expiries,
            "selected_expiry": selected_expiry,
            "replay_timeline": replay_timeline,
            "selected_session_date": (
                active_session_date.isoformat()
                if active_session_date
                else replay_timeline.get("session_date")
            ),
            "replay_interval_choices": ANALYSIS_WINDOW_CHOICES_MINUTES,
            "replay_interval_minutes": replay_interval,
            **(await _derivatives_filter_context(
                service,
                symbol,
                form_action="/market-data/derivatives/oi-replay",
                expiry=sel_exp_date,
                strikes=strikes,
                show_interval=False,
            )),
            "provider_name": service.adapter.source_name,
            "is_market_open": False,
            "as_of_ist": next(
                (
                    step["timestamp_ist"]
                    for step in reversed(replay_timeline.get("timeline_steps") or [])
                    if step.get("has_snapshot")
                ),
                "no persisted observation",
            ),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/options/chain-table", response_class=HTMLResponse)
async def options_chain_table_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    strike_count: int | None = Query(default=10),
    show_buildup: bool = Query(default=False),
    show_iv: bool = Query(default=False),
    show_delta: bool = Query(default=False),
    show_gamma: bool = Query(default=False),
    show_vega: bool = Query(default=False),
    show_greeks: bool = Query(default=False),
) -> HTMLResponse:
    """HTMX partial returning the live strike ladder with optional Greeks/buildup columns.

    The requested expiry is validated against the selected underlying's listed
    expiries: a partial request can still carry the outgoing underlying's expiry
    (the container was rendered before the control moved), and querying it would
    render an empty ladder for a contract that does not exist.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    effective_expiry = await _canonical_listed_expiry(service, symbol, expiry)
    effective_date = date.fromisoformat(effective_expiry) if effective_expiry else None
    chain = None
    try:
        chain = await service.get_option_chain_ladder(
            underlying=symbol, expiry_date=effective_date, strike_count=strike_count
        )
    except Exception as exc:
        logger.warning("Error fetching option chain ladder partial: %s", exc)
        chain = None

    iv_visible = show_iv or show_greeks
    delta_visible = show_delta or show_greeks
    gamma_visible = show_gamma or show_greeks
    vega_visible = show_vega or show_greeks

    html = _render(
        "components/intraday_option_chain_table.html",
        {
            "request": request,
            "chain": chain,
            "underlying": symbol,
            "expiry": effective_expiry,
            "show_buildup": show_buildup,
            "show_iv": iv_visible,
            "show_delta": delta_visible,
            "show_gamma": gamma_visible,
            "show_vega": vega_visible,
            "show_greeks": show_greeks,
        },
    )
    return HTMLResponse(content=html)


@router.get("/options/dashboard-cards", response_class=HTMLResponse)
async def options_dashboard_cards_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
) -> HTMLResponse:
    """HTMX partial returning the options dashboard summary cards.

    The expiry is resolved against the selected underlying's listed expiries so
    the dashboard can never summarise a contract the underlying does not have.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    effective_expiry = await _canonical_listed_expiry(service, symbol, expiry)
    effective_date = date.fromisoformat(effective_expiry) if effective_expiry else None
    dashboard = None
    try:
        dashboard = await service.get_option_dashboard(underlying=symbol, expiry_date=effective_date)
    except Exception as exc:
        logger.warning("Error fetching option dashboard partial: %s", exc)
        dashboard = None

    html = _render(
        "components/intraday_option_dashboard_cards.html",
        {"request": request, "dashboard": dashboard, "underlying": symbol, "expiry": effective_expiry},
    )
    return HTMLResponse(content=html)


@router.get("/options/pcr-view", response_class=HTMLResponse)
async def options_pcr_view_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
) -> HTMLResponse:
    """HTMX partial returning the Live PCR analytics view.

    The expiry is resolved against the selected underlying's listed expiries so
    the ratio can never be aggregated over another underlying's contract.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    effective_expiry = await _canonical_listed_expiry(service, symbol, expiry)
    effective_date = date.fromisoformat(effective_expiry) if effective_expiry else None
    pcr_data = None
    try:
        pcr_data = await service.get_live_pcr(underlying=symbol, expiry_date=effective_date)
    except Exception as exc:
        logger.warning("Error fetching live PCR partial: %s", exc)
        pcr_data = None

    html = _render(
        "components/intraday_option_pcr_view.html",
        {"request": request, "pcr_data": pcr_data, "underlying": symbol, "expiry": effective_expiry},
    )
    return HTMLResponse(content=html)


@router.get("/options/oi-chart", response_class=HTMLResponse)
async def options_oi_chart_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    strike_count: int = Query(default=15),
) -> HTMLResponse:
    """HTMX partial returning the strike-wise Call vs Put OI comparison.

    The expiry is resolved against the selected underlying's listed expiries so
    the comparison can never be built from another underlying's contract.
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    effective_expiry = await _canonical_listed_expiry(service, symbol, expiry)
    effective_date = date.fromisoformat(effective_expiry) if effective_expiry else None
    oi_data = None
    try:
        oi_data = await service.get_call_vs_put_oi(
            underlying=symbol, expiry_date=effective_date, strike_count=strike_count
        )
    except Exception as exc:
        logger.warning("Error fetching OI comparison partial: %s", exc)
        oi_data = None

    html = _render(
        "components/intraday_option_oi_comparison.html",
        {"request": request, "oi_data": oi_data, "underlying": symbol, "expiry": effective_expiry},
    )
    # G.11-OPT-12B Phase 2B: the refreshed fragment must re-render the new
    # tornado chart component so the visualization survives HTMX swaps with a
    # truthful empty state (no chart canvas at all) when no data exists.
    chart_html = _render(
        "components/option_oi_tornado_chart.html",
        {"request": request, "oi_data": oi_data, "underlying": symbol, "expiry": effective_expiry},
    )
    return HTMLResponse(content=html + chart_html)


@router.get("/options/oi-change-view", response_class=HTMLResponse)
async def options_oi_change_view_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    expiry: date | None = Query(default=None),
    strike_count: int = Query(default=15),
    buildup_filter: str | None = Query(default="ALL"),
) -> HTMLResponse:
    """HTMX partial returning the strike-wise OI Change analysis tab.

    The expiry is resolved against the selected underlying's listed expiries so
    the change can never be measured against another underlying's contract (the
    defect an operator saw as an empty, wrong-underlying OI Change tab).
    """
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    effective_expiry = await _canonical_listed_expiry(service, symbol, expiry)
    effective_date = date.fromisoformat(effective_expiry) if effective_expiry else None
    oi_change_data = None
    try:
        oi_change_data = await service.get_oi_change_analysis(
            underlying=symbol,
            expiry_date=effective_date,
            strike_count=strike_count,
            buildup_filter=buildup_filter,
        )
    except Exception as exc:
        logger.warning("Error fetching OI change partial: %s", exc)
        oi_change_data = None

    html = _render(
        "components/intraday_option_oi_change_view.html",
        {
            "request": request,
            "oi_change_data": oi_change_data,
            "underlying": symbol,
            "expiry": effective_expiry,
        },
    )
    # G.11-OPT-12B Phase 2B: the refreshed fragment must re-render the new ΔOI
    # chart component so the visualization survives HTMX swaps with a truthful
    # empty state (no chart canvas at all) when no data exists.
    chart_html = _render(
        "components/option_doi_chart.html",
        {
            "request": request,
            "oi_change_data": oi_change_data,
            "underlying": symbol,
            "expiry": effective_expiry,
        },
    )
    return HTMLResponse(content=html + chart_html)


@router.get("/options/expiries-select", response_class=HTMLResponse)
async def options_expiries_select_partial(
    symbol: str = Query(default="NIFTY"),
    selected_expiry: date | None = Query(default=None),
) -> HTMLResponse:
    """Render dynamic HTML select dropdown of valid non-expired expiries."""
    from app.services.intraday_options_service import IntradayOptionsService

    service = IntradayOptionsService()
    expiries = await service.get_available_expiries(symbol)

    if not expiries:
        return HTMLResponse(
            '<select class="form-select form-select-sm bg-dark text-light border-secondary" name="expiry_date" id="opt-expiry">'
            '<option value="">No active expiries</option></select>'
        )

    options = ['<select class="form-select form-select-sm bg-dark text-light border-secondary" name="expiry_date" id="opt-expiry">']
    for idx, exp in enumerate(expiries):
        exp_val = exp["expiry_date"]
        dte = exp["days_to_expiry"]
        exp_type = exp["expiry_type"]
        is_sel = (selected_expiry and selected_expiry.isoformat() == exp_val) or (selected_expiry is None and idx == 0)
        sel_attr = " selected" if is_sel else ""
        options.append(f'<option value="{exp_val}"{sel_attr}>{exp_val} ({dte} DTE · {exp_type})</option>')
    options.append("</select>")
    return HTMLResponse("".join(options))


@router.post(
    "/import",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def import_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    bhavcopy = form.get("bhavcopy")
    if bhavcopy is None or not getattr(bhavcopy, "filename", None):
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    payload = await bhavcopy.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(payload) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail="File too large")

    file_name = bhavcopy.filename or "bhavcopy.csv"
    job = await MarketDataService(session).import_upload(
        file_name=file_name, payload=payload, mode="UPLOAD"
    )
    details = job.details or {}
    if job.status == "completed":
        return RedirectResponse(
            url=_flash_url(
                level="success",
                message="Import completed",
                imported=job.status,
                rows=job.rows_inserted + job.rows_updated,
                inserted=job.rows_inserted,
                updated=job.rows_updated,
                rejected=job.rows_rejected,
                format=details.get("format") or "unknown",
                trade_date=job.trade_date.isoformat() if job.trade_date else "",
            ),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="danger",
            message=str(job.error_message or "Import failed"),
            imported=job.status,
        ),
        status_code=303,
    )


@router.post(
    "/download-latest",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def download_latest_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    try:
        result = await MarketDataService(session).download_and_import_latest()
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Download failed: {exc}"),
            status_code=303,
        )
    if result.get("status") == "failed":
        return RedirectResponse(
            url=_flash_url(level="danger", message=str(result.get("message"))),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Downloaded {result.get('trade_date')}",
            imported=result.get("status"),
            rows=result.get("rows_inserted", 0) + result.get("rows_updated", 0),
            inserted=result.get("rows_inserted", 0),
            updated=result.get("rows_updated", 0),
            rejected=result.get("rows_rejected", 0),
            format=result.get("format") or "unknown",
        ),
        status_code=303,
    )


@router.post(
    "/download-date",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def download_date_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    try:
        trade_date = date.fromisoformat(str(form.get("trade_date", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid trade date") from exc
    try:
        result = await MarketDataService(session).download_and_import_date(trade_date)
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Download failed: {exc}"),
            status_code=303,
        )
    if result.status in ("blocked_403", "failed"):
        return RedirectResponse(
            url=_flash_url(level="danger", message=result.message),
            status_code=303,
        )
    if result.status == "skipped_weekend":
        return RedirectResponse(
            url=_flash_url(level="warning", message=f"{trade_date} is a weekend"),
            status_code=303,
        )
    if result.status == "skipped_holiday":
        return RedirectResponse(
            url=_flash_url(level="warning", message=f"No bhavcopy for {trade_date}"),
            status_code=303,
        )
    if result.status == "already_present":
        return RedirectResponse(
            url=_flash_url(level="info", message=f"{trade_date} already imported"),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Downloaded {trade_date}",
            imported="completed",
            inserted=result.rows_inserted,
            updated=result.rows_updated,
            format="nse",
        ),
        status_code=303,
    )


@router.post(
    "/backfill",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def backfill_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    try:
        start_date = date.fromisoformat(str(form.get("start_date", "")))
        end_date = date.fromisoformat(str(form.get("end_date", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid backfill date range") from exc
    from app.services.nse_historical import MAX_RANGE_DAYS

    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be <= end_date")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail="Backfill range too large")
    try:
        summary = await MarketDataService(session).backfill(start_date, end_date)
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Backfill failed: {exc}"),
            status_code=303,
        )
    downloaded = summary.get("downloaded", 0)
    already_present = summary.get("already_present", 0)
    skipped = summary.get("skipped", 0)
    blocked = summary.get("blocked", 0)
    failed = summary.get("failed", 0)

    if downloaded == 0 and already_present == 0 and (blocked > 0 or failed > 0):
        level = "danger"
        message = f"Backfill failed: {blocked} blocked, {failed} failed (0 downloaded)"
    elif blocked > 0 or failed > 0:
        level = "warning"
        message = (
            f"Backfill partially completed: {downloaded} downloaded, {already_present} present, "
            f"{blocked} blocked, {failed} failed"
        )
    else:
        level = "success"
        message = f"Backfill completed: {downloaded} downloaded, {already_present} already present"

    return RedirectResponse(
        url=_flash_url(
            level=level,
            message=message,
            backfill="completed",
            downloaded=downloaded,
            already_present=already_present,
            skipped=skipped,
            blocked=blocked,
            failed=failed,
        ),
        status_code=303,
    )


@router.post(
    "/run-indicators",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def run_indicators(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    from app.services.indicator_engine import IndicatorEngine

    summary = await IndicatorEngine(session).calculate_all()
    await session.commit()
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Indicators calculated: {summary.get('calculated', 0)}",
        ),
        status_code=303,
    )


@router.post(
    "/run-scanners",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def run_scanners(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    from app.services.scanner_engine import ScannerService

    service = ScannerService(session)
    results = await service.run_all()
    await service.persist_results(results)
    await session.commit()
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Scanners run: {len(results)}",
        ),
        status_code=303,
    )


@router.get("/prices", response_class=HTMLResponse)
async def market_prices_partial(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    prices = await repo.list_prices(symbol=symbol, limit=limit, product_only=True)
    rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "close": price.close,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    html = _render(
        "components/market_prices_table.html",
        {"request": request, "prices": rows},
    )
    return HTMLResponse(content=html)


@router.get("/imports", response_class=HTMLResponse)
async def market_imports_partial(
    request: Request,
    limit: int = Query(default=15, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    imports = await MarketDataRepository(session).list_imports(limit=limit)
    html = _render(
        "components/market_imports_table.html",
        {"request": request, "imports": imports},
    )
    return HTMLResponse(content=html)


@router.get("/coverage", response_class=HTMLResponse)
async def market_coverage_partial(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    today = datetime.now(UTC).date()
    rows = await MarketDataService(session).coverage(today - timedelta(days=days), today)
    html = _render(
        "components/market_coverage_table.html",
        {"request": request, "coverage": rows},
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options/chain/table", response_class=HTMLResponse)
async def option_chain_table_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    expiry_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_option_chain import HistoricalOptionChainService

    service = HistoricalOptionChainService(session)
    chain = None
    try:
        chain = await service.get_historical_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
    except Exception:
        chain = None

    html = _render(
        "components/option_chain_table.html",
        {"request": request, "chain": chain},
    )
    return HTMLResponse(content=html)



@router.get("/derivatives/options/expiries-select", response_class=HTMLResponse)
async def option_expiries_select_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    selected_expiry: date | None = Query(default=None),
    select_name: str = Query(default="expiry_date"),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render dynamic HTML select dropdown of valid non-expired expiries with DTE."""
    from app.services.historical_option_chain import HistoricalOptionChainService

    service = HistoricalOptionChainService(session)
    expiries = []
    try:
        all_expiries = await service.get_available_expiries(symbol, trade_date, as_of_date)
        # Filter expiries >= trade_date to guarantee non-expired
        expiries = [d for d in all_expiries if d >= trade_date]
    except Exception:
        expiries = []

    if not expiries:
        html = (
            f'<input type="date" class="form-control form-control-sm" name="{select_name}" '
            f'value="{selected_expiry.isoformat() if selected_expiry else ""}" '
            f'placeholder="No stored expiries">'
        )
        return HTMLResponse(content=html)

    options = [f'<select class="form-select form-select-sm" name="{select_name}" required>']
    for idx, exp in enumerate(expiries):
        dte = (exp - trade_date).days
        is_sel = (selected_expiry == exp) or (selected_expiry is None and idx == 0)
        sel_attr = " selected" if is_sel else ""
        nearest_badge = " [Nearest]" if idx == 0 else ""
        options.append(
            f'<option value="{exp.isoformat()}"{sel_attr}>{exp.isoformat()} ({dte}d DTE){nearest_badge}</option>'
        )
    options.append("</select>")
    return HTMLResponse(content="".join(options))


@router.get("/derivatives/options/oi-intelligence/cards", response_class=HTMLResponse)
async def oi_intelligence_cards_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    expiry_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render deterministic Open Interest Intelligence cards component."""
    from app.services.historical_oi_intelligence_service import HistoricalOIIntelligenceService

    service = HistoricalOIIntelligenceService(session)
    snapshot = None
    error_msg = None
    try:
        snapshot = await service.get_oi_intelligence(symbol, trade_date, expiry_date, as_of_date)
    except Exception as exc:
        error_msg = str(exc)

    html = _render(
        "components/oi_intelligence_cards.html",
        {"request": request, "snapshot": snapshot, "error_message": error_msg},
    )
    return HTMLResponse(content=html)

@router.get("/derivatives/futures/analytics/table", response_class=HTMLResponse)
async def futures_analytics_table_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService

    service = HistoricalFuturesAnalyticsService(session)
    snapshot = None
    rollover = None
    contract_analytics_map = {}

    try:
        snapshot = await service.get_underlying_futures_snapshot(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )
        rollover = await service.get_rollover_analytics(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )

        for item in snapshot.items:
            try:
                analytics = await service.get_contract_analytics(
                    contract_id=item.contract_id,
                    trade_date=trade_date,
                    as_of_date=as_of_date,
                )
                contract_analytics_map[item.contract_id] = analytics
            except Exception:
                pass
    except Exception:
        snapshot = None

    html = _render(
        "components/futures_analytics_table.html",
        {
            "request": request,
            "snapshot": snapshot,
            "rollover": rollover,
            "contract_analytics_map": contract_analytics_map,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options/analytics/cards", response_class=HTMLResponse)
async def options_analytics_cards_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    expiry_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

    service = HistoricalOptionsAnalyticsService(session)
    analytics = None
    expiry_analytics = None
    try:
        analytics = await service.get_option_chain_analytics(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        expiry_analytics = await service.get_expiry_options_analytics(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )
    except Exception:
        analytics = None
        expiry_analytics = None

    html = _render(
        "components/options_analytics_cards.html",
        {
            "request": request,
            "analytics": analytics,
            "expiry_analytics": expiry_analytics,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/participants/cards", response_class=HTMLResponse)
async def participant_positioning_cards_partial(
    request: Request,
    report_date: date = Query(...),
    dataset_type: str = Query(default="OPEN_INTEREST"),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_participant_analytics import HistoricalParticipantAnalyticsService

    service = HistoricalParticipantAnalyticsService(session)
    snapshot = None
    try:
        snapshot = await service.get_participant_snapshot(
            report_date=report_date,
            dataset_type=dataset_type,
            as_of_date=as_of_date,
        )
    except Exception:
        snapshot = None

    html = _render(
        "components/participant_positioning_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/replay/cards", response_class=HTMLResponse)
async def derivatives_replay_cards_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    trade_date: date | None = Query(default=None),
    expiry_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.derivative_coverage_service import DerivativeCoverageService
    from app.services.historical_derivatives_replay import HistoricalDerivativesReplayService

    if trade_date is None:
        trade_date = as_of_date or date(2026, 8, 5)

    replay_service = HistoricalDerivativesReplayService(session)
    cov_service = DerivativeCoverageService(session)

    replay = await replay_service.get_historical_replay(
        symbol=symbol,
        trade_date=trade_date,
        expiry_date=expiry_date,
        as_of_date=as_of_date,
    )
    coverage = await cov_service.get_derivatives_coverage(as_of_date=as_of_date)

    html = _render(
        "components/derivatives_replay_cards.html",
        {
            "request": request,
            "replay": replay,
            "coverage": coverage,
        },
    )
    return HTMLResponse(content=html)


@router.get("/fundamentals/cards", response_class=HTMLResponse)
async def fundamentals_cards_partial(
    request: Request,
    symbol: str = Query(...),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    statement_basis: str = Query(default="CONSOLIDATED"),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.fundamental_corporate_service import FundamentalCorporateService

    eval_d = evaluation_date or date.today()
    service = FundamentalCorporateService(session)
    try:
        snapshot = await service.get_company_fundamental_snapshot(
            symbol=symbol,
            evaluation_date=eval_d,
            as_of_date=as_of_date,
            statement_basis=statement_basis,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/fundamentals_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/news/cards", response_class=HTMLResponse)
async def news_cards_partial(
    request: Request,
    symbol: str = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from datetime import datetime

    from app.services.news_policy_service import NewsPolicyService

    eval_dt = datetime.combine(as_of_date or date.today(), datetime.min.time(), tzinfo=UTC)
    service = NewsPolicyService(session)
    try:
        snapshot = await service.get_company_news_policy_snapshot(
            symbol=symbol, evaluation_datetime=eval_dt
        )
    except ValueError as exc:
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/news_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/stock360/cards", response_class=HTMLResponse)
async def stock360_cards_partial(
    request: Request,
    symbol: str = Query(...),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.stock360_service import Stock360Service

    service = Stock360Service(session)
    try:
        snapshot = await service.get_stock360_snapshot(
            symbol=symbol,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/stock360_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/opportunities/board", response_class=HTMLResponse)
async def opportunity_board_partial(
    request: Request,
    profile: str = Query(default="BREAKOUT"),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    sector: str | None = Query(default=None),
    fno_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.opportunity_ranking_service import OpportunityRankingService

    service = OpportunityRankingService(session)
    summary = await service.rank_opportunities(
        profile=profile,
        evaluation_date=evaluation_date,
        as_of_date=as_of_date,
        sector_name=sector,
        fno_only=fno_only,
        limit=limit,
    )

    html = _render(
        "components/opportunity_board.html",
        {
            "request": request,
            "summary": summary,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/advanced-options/tab")
async def advanced_options_tab_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    trade_date: date | None = None,
    expiry_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render HTMX partial for Advanced Options Analytics (IV, Greeks, Volatility Structure, Liquidity)."""
    from app.services.advanced_options_service import AdvancedOptionsService
    from app.services.historical_option_chain import HistoricalOptionChainService

    t_date = trade_date or date.today()
    as_of = as_of_date or t_date
    chain_service = HistoricalOptionChainService(session)
    expiries = await chain_service.get_available_expiries(symbol, t_date, as_of)

    target_exp = expiry_date or (expiries[0] if expiries else t_date)

    adv_service = AdvancedOptionsService(session)
    enriched_rows = []
    expected_move = None
    skew = None
    term_structure = []

    try:
        enriched_rows = await adv_service.get_enriched_option_chain(
            symbol, t_date, target_exp, as_of
        )
        expected_move = await adv_service.get_expected_move(symbol, t_date, target_exp, as_of)
        skew = await adv_service.get_iv_skew(symbol, t_date, target_exp, as_of)
        term_structure = await adv_service.get_iv_term_structure(symbol, t_date, as_of)
    except Exception:
        pass

    html = _render(
        "components/advanced_options_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "expiry_date": target_exp,
            "available_expiries": expiries,
            "rows": enriched_rows,
            "expected_move": expected_move,
            "skew": skew,
            "term_structure": term_structure,
        },
    )
    return HTMLResponse(content=html)


def _coerce_date(val: date | str | None, param_name: str) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        return date.fromisoformat(val_str)
    except (ValueError, TypeError):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=[{
                "type": "date_from_datetime_parsing",
                "loc": ["query", param_name],
                "msg": f"Input should be a valid date, invalid date in '{val_str}'",
                "input": val_str,
            }],
        ) from None


@router.get("/derivatives/advanced-exposure/tab", response_class=HTMLResponse)
async def advanced_exposure_tab_partial(
    request: Request,
    symbol: str = "NIFTY",
    trade_date: str | date | None = None,
    expiry_date: str | date | None = None,
    as_of_date: str | date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Advanced Exposure tab component."""
    from app.services.advanced_exposure_service import (
        PROFILE_DEALER_HEURISTIC,
        AdvancedExposureService,
    )
    from app.services.historical_option_chain import HistoricalOptionChainService

    parsed_trade_date = _coerce_date(trade_date, "trade_date")
    parsed_as_of = _coerce_date(as_of_date, "as_of_date")
    parsed_expiry = _coerce_date(expiry_date, "expiry_date")

    t_date = parsed_trade_date or date.today()
    as_of = parsed_as_of or t_date

    chain_service = HistoricalOptionChainService(session)
    exp_service = AdvancedExposureService(session)

    expiries = await chain_service.get_available_expiries(symbol, t_date, as_of)
    target_exp = parsed_expiry or (expiries[0] if expiries else t_date)

    snapshot = None
    strikes: list = []
    gamma_profile: list = []

    unavailable_reason: str | None = None

    try:
        # G.11-OPT-12B Phase 2A hardening: use the service's supported signed
        # scenario profile (DEALER_SHORT_CALL_LONG_PUT_HEURISTIC, the exact
        # profile get_gamma_flip already uses internally) consistently for the
        # snapshot, strike table and gamma profile. This makes scenario_net_gex
        # a genuine signed quantity (PE gross − CE gross) and lets gamma_flip
        # return a real zero-crossing level; the snapshot's signed KPIs and the
        # scenario grid then agree. No formula or schema change: the profile
        # constant already exists in the service and is validated by its tests.
        assumption_profile = PROFILE_DEALER_HEURISTIC
        snapshot = await exp_service.get_exposure_snapshot(
            symbol, t_date, target_exp, as_of, assumption_profile
        )
        strikes = await exp_service.get_strike_exposure_table(
            symbol, t_date, target_exp, as_of, assumption_profile
        )
        gamma_profile = await exp_service.get_gamma_profile(
            symbol, t_date, target_exp, as_of, assumption_profile
        )
    except ValueError as exc:
        # Expected data-availability failure: the underlying is not present in the
        # stored market indices, or no stored history exists for the requested
        # date/expiry range. Render an explicit unavailable state carrying a reason
        # code instead of an unhandled server error.
        #
        # NOTE: this deliberately does NOT catch broad ``Exception``. A genuine
        # programming error must stay visible rather than being masked as "no data".
        unavailable_reason = f"UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES: {exc}"

    html = _render(
        "components/advanced_exposure_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "expiry_date": target_exp,
            "available_expiries": expiries,
            "snapshot": snapshot,
            "strikes": strikes,
            "gamma_profile": gamma_profile,
            "unavailable_reason": unavailable_reason,
            "assumption_profile": assumption_profile,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options-opportunity/tab", response_class=HTMLResponse)
async def options_opportunity_tab_partial(
    request: Request,
    symbol: str = "NIFTY",
    trade_date: date | None = None,
    as_of_date: date | None = None,
    setup_profile: str = "SWING",
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Options Opportunity Research tab component."""
    from app.services.options_opportunity_service import OptionOpportunityService

    t_date = trade_date or date.today()
    as_of = as_of_date or t_date

    opp_service = OptionOpportunityService(session)
    assessment = None

    try:
        assessment = await opp_service.assess_opportunity(
            symbol=symbol,
            trade_date=t_date,
            as_of_date=as_of,
            setup_profile=setup_profile,
        )
    except Exception:
        pass

    html = _render(
        "components/options_opportunity_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "assessment": assessment,
        },
    )
    return HTMLResponse(content=html)


@router.get("/validation/lab", response_class=HTMLResponse)
async def validation_lab_tab_partial(
    request: Request,
    profile: str = "SWING",
    symbol: str = "NIFTY",
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Historical Validation & Calibration Lab component."""
    from app.services.historical_coverage_certification_service import (
        HistoricalCoverageCertificationService,
    )
    from app.services.historical_opportunity_validation_service import (
        HistoricalOpportunityValidationService,
    )
    from app.services.historical_options_validation_service import (
        HistoricalOptionsValidationService,
    )

    val_service = HistoricalOpportunityValidationService(session)
    opt_val_service = HistoricalOptionsValidationService(session)
    cert_service = HistoricalCoverageCertificationService(session)

    stock_summary = None
    options_summary = None
    coverage_cert = None

    try:
        stock_summary = await val_service.validate_opportunities(profile=profile)
        options_summary = await opt_val_service.validate_options(symbol=symbol)
        coverage_cert = await cert_service.certify_coverage()
    except Exception:
        pass

    html = _render(
        "components/validation_lab_tab.html",
        {
            "request": request,
            "profile": profile,
            "symbol": symbol.upper(),
            "stock_summary": stock_summary,
            "options_summary": options_summary,
            "coverage_cert": coverage_cert,
        },
    )
    return HTMLResponse(content=html)
