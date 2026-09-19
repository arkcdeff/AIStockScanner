from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import finalize_page_response, stage_csrf_token
from app.auth import csrf
from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session, get_session_factory
from app.schemas.what_changed_screening import (
    ScreeningFilterCriteria,
    ScreeningRequest,
    ScreeningResponse,
)
from app.services.stock import StockService
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    OverallState,
)
from app.services.what_changed_screening_config import ScreeningUniverse
from app.services.what_changed_screening_service import WhatChangedScreeningService


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(dependencies=[Depends(require_browser_permission("stocks.read"))])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def stocks_page(
    request: Request,
    q: str = Query(default=""),
    readiness: str = Query(default="all", pattern="^(all|ready|limited|missing)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    explorer = await StockService(session).list_explorer(
        query=q, readiness=readiness, page=page, page_size=page_size
    )
    html = _render(
        "stocks.html",
        {
            "request": request,
            "stocks": explorer["rows"],
            "explorer": explorer,
            "query": q,
            "readiness_filter": readiness,
            "csrf_token": stage_csrf_token(request),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/table", response_class=HTMLResponse)
async def stocks_table_partial(
    request: Request,
    q: str = Query(default=""),
    readiness: str = Query(default="all", pattern="^(all|ready|limited|missing)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    explorer = await StockService(session).list_explorer(
        query=q, readiness=readiness, page=page, page_size=page_size
    )
    html = _render(
        "components/stocks_table.html",
        {
            "request": request,
            "stocks": explorer["rows"],
            "explorer": explorer,
            "query": q,
            "readiness_filter": readiness,
        },
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# G.11-D-4 — What-Changed Universe Screener (HTML/HTMX UI layer)
#
# UI request → HTML-rendering route → WhatChangedScreeningService → HTML
# partial. The JSON REST API under /api/v1/stocks/screening/what-changed is
# untouched and remains a pure JSON API. This layer only parses form fields
# into a ScreeningRequest — no screening business logic is duplicated here.
# ---------------------------------------------------------------------------


@router.get("/screening/what-changed", response_class=HTMLResponse)
async def what_changed_screening_page(request: Request) -> HTMLResponse:
    """Render the What-Changed Universe Screener page."""
    html = _render(
        "what_changed_screening.html",
        {
            "request": request,
            "csrf_token": stage_csrf_token(request),
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


def _form_str(form: object, key: str) -> str | None:
    """Read a single string form field, returning None when absent/blank."""
    value = form.get(key)  # type: ignore[attr-defined]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _form_int(form: object, key: str) -> int | None:
    text = _form_str(form, key)
    if text is None:
        return None
    return int(text)


def _screening_request_from_form(form: object) -> ScreeningRequest:
    """Parse screener form fields into a :class:`ScreeningRequest`.

    Presentation-layer parsing only: every business rule (validation,
    universe resolution, filtering, ranking, truncation) lives in the
    schema/service layer.
    """
    universe_text = _form_str(form, "universe")
    universe: ScreeningUniverse | None = None
    if universe_text:
        universe = ScreeningUniverse(universe_text.upper())

    symbols_csv = _form_str(form, "symbols_csv")
    symbols: list[str] | None = None
    if symbols_csv:
        symbols = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()] or None

    as_of_text = _form_str(form, "as_of_date")
    as_of_date: date_type | None = (
        date_type.fromisoformat(as_of_text) if as_of_text else None
    )

    overall_states_raw = form.getlist("filter_criteria[overall_states]")  # type: ignore[attr-defined]
    directional_biases_raw = form.getlist("filter_criteria[directional_biases]")  # type: ignore[attr-defined]

    min_convergence_text = _form_str(form, "filter_criteria[min_convergence_ratio]")
    min_convergence = float(min_convergence_text) if min_convergence_text else None

    criteria = ScreeningFilterCriteria(
        overall_states=[OverallState(v) for v in overall_states_raw] or None,
        directional_biases=[DirectionalBias(v) for v in directional_biases_raw] or None,
        min_convergence_ratio=min_convergence,
    )

    return ScreeningRequest(
        universe=universe,
        watchlist_id=_form_int(form, "watchlist_id"),
        symbols=symbols,
        as_of_date=as_of_date,
        comparison_window=_form_int(form, "comparison_window") or 1,
        filter_criteria=criteria,
    )


@router.post("/screening/what-changed", response_class=HTMLResponse)
async def run_what_changed_screening(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Execute a screening run from the HTMX form and return the HTML fragment.

    CSRF is enforced (the shell auto-attaches the ``x-csrf-token`` header on
    every HTMX request). Validation errors re-render the results area with a
    danger flash instead of raising, so the browser target stays consistent.
    """
    form = await request.form()
    csrf.verify_request_csrf(request, form)

    response: ScreeningResponse | None = None
    flash: dict[str, str] | None = None
    try:
        screening_request = _screening_request_from_form(form)
        service = WhatChangedScreeningService(
            session=session,
            session_factory=get_session_factory(),
        )
        response = await service.screen(screening_request)
    except (ValidationError, ValueError) as exc:
        flash = {"level": "danger", "message": str(exc)}

    html = _render(
        "components/what_changed_screening_results.html",
        {
            "request": request,
            "response": response,
            "flash": flash,
        },
    )
    return HTMLResponse(content=html)
