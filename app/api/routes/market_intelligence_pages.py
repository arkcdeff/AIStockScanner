"""Task 18.11: Browser pages and HTMX partials for Market Intelligence Dashboard.

Exposes the market intelligence system through authenticated browser routes.
No business logic — only service orchestration and template rendering.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import (
    ForbiddenError,
    finalize_page_response,
    get_browser_user,
    stage_csrf_token,
)
from app.auth import csrf
from app.auth.authorization import require_browser_permission, user_has_permission
from app.core.config import settings
from app.db.session import get_db_session, get_session_factory
from app.market_intelligence.sector_universe import SECTOR_INDEX_CODES
from app.repositories.market_intelligence_snapshot import (
    MarketIntelligenceSnapshotRepository,
)
from app.schemas.sector_evidence import (
    SectorEvidenceBreadthRequest,
)
from app.services.ai_market_explanation import AIMarketExplanationService
from app.services.composite_market_intelligence import CompositeMarketIntelligence
from app.services.external_market_factor_intelligence import (
    ExternalMarketFactorIntelligence,
)
from app.services.gift_nifty_intelligence import GiftNiftyIntelligence
from app.services.global_market_intelligence import GlobalMarketIntelligence
from app.services.index_price_action import IndexPriceAction
from app.services.institutional_intelligence import InstitutionalIntelligence
from app.services.macro_intelligence import MacroIntelligence
from app.services.market_breadth import MarketBreadth
from app.services.news_policy_service import NewsPolicyService
from app.services.sector_evidence_aggregator_service import (
    SectorEvidenceAggregatorService,
)
from app.services.sector_intelligence import SectorIntelligence

router = APIRouter(
    dependencies=[Depends(require_browser_permission("market_intelligence.read"))]
)

env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)


# ---------------------------------------------------------------------------
# Jinja2 filters
# ---------------------------------------------------------------------------


def _fmt_score(value: Any) -> str:
    if value is None:
        return "--"
    try:
        v = float(value)
        return f"{v:+.1f}" if v != 0 else "0.0"
    except (TypeError, ValueError):
        return "--"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "--"


def _fmt_int(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "--"


def _fmt_price(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "--"


def _sign_class(value: Any) -> str:
    if value is None:
        return "text-muted"
    try:
        v = float(value)
        if v > 0:
            return "text-success"
        elif v < 0:
            return "text-danger"
        return "text-muted"
    except (TypeError, ValueError):
        return "text-muted"


def _regime_class(regime: str | None) -> str:
    if not regime:
        return "bg-secondary"
    mapping = {
        "strong_bullish": "bg-success",
        "bullish": "bg-success bg-opacity-75",
        "neutral": "bg-warning text-dark",
        "bearish": "bg-danger bg-opacity-75",
        "strong_bearish": "bg-danger",
    }
    return mapping.get(regime, "bg-secondary")


def _freshness_class(freshness: str | None) -> str:
    mapping = {
        "live": "text-success",
        "recent": "text-info",
        "stale": "text-warning",
        "missing": "text-danger",
    }
    return mapping.get(freshness or "", "text-muted")


def _label(value: Any) -> str:
    if value is None:
        return "--"
    return str(value).replace("_", " ").title()


for _name, _func in {
    "mi_score": _fmt_score,
    "mi_pct": _fmt_pct,
    "mi_int": _fmt_int,
    "mi_price": _fmt_price,
    "mi_sign_class": _sign_class,
    "mi_regime_class": _regime_class,
    "mi_freshness_class": _freshness_class,
    "mi_label": _label,
}.items():
    env.filters[_name] = _func


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render(template_name: str, context: dict[str, Any]) -> str:
    return env.get_template(template_name).render(**context)


def _page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    html = _render(template_name, context)
    return finalize_page_response(request, HTMLResponse(content=html, status_code=status_code))


def _partial(request: Request, template_name: str, context: dict[str, Any]) -> HTMLResponse:
    html = _render(template_name, context)
    return HTMLResponse(content=html)


def _existing_csrf_token(request: Request) -> str:
    token = request.cookies.get(settings.csrf_cookie_name, "")
    return token or stage_csrf_token(request)


async def _browser_user(request: Request, session: AsyncSession):
    user = await get_browser_user(request, session)
    if user is None:
        raise ForbiddenError()
    return user


# ---------------------------------------------------------------------------
# Service factory helpers
# ---------------------------------------------------------------------------


async def _build_composite(session: AsyncSession) -> CompositeMarketIntelligence:
    from app.repositories.market_index_price import MarketIndexPriceRepository

    price_repo = MarketIndexPriceRepository(session)
    snapshot_repo = MarketIntelligenceSnapshotRepository(session)
    breadth_engine = MarketBreadth(session)
    sector_engine = SectorIntelligence(session)
    institutional_engine = InstitutionalIntelligence(session)
    external_engine = ExternalMarketFactorIntelligence(session)
    global_engine = GlobalMarketIntelligence(session)
    gift_nifty_engine = GiftNiftyIntelligence(session)
    macro_engine = MacroIntelligence(session)
    return CompositeMarketIntelligence(
        session,
        price_repo=price_repo,
        snapshot_repo=snapshot_repo,
        breadth_engine=breadth_engine,
        sector_engine=sector_engine,
        institutional_engine=institutional_engine,
        external_factor_engine=external_engine,
        global_engine=global_engine,
        gift_nifty_engine=gift_nifty_engine,
        macro_engine=macro_engine,
    )


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def market_intelligence_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Main Market Intelligence Dashboard page."""
    user = await _browser_user(request, session)
    context = await _build_dashboard_context(request, session, user=user)
    return _page(request, "market_intelligence.html", context)


async def _build_dashboard_context(
    request: Request, session: AsyncSession, user: Any = None
) -> dict[str, Any]:
    """Build the full dashboard context from services."""
    from datetime import UTC

    now = datetime.now(UTC)
    composite = await _build_composite(session)
    result = await composite.evaluate(now)

    # AI explanation
    explanation_svc = AIMarketExplanationService(result)
    explanation = explanation_svc.generate_explanation()

    # External factors
    ext_engine = ExternalMarketFactorIntelligence(session)
    ext_result = await ext_engine.evaluate(now)

    # Breadth
    breadth_engine = MarketBreadth(session)
    breadth_result = await breadth_engine.evaluate(date.today(), evaluation_time=now)

    # Sectors
    sector_engine = SectorIntelligence(session)
    sector_result = await sector_engine.evaluate(date.today())

    # Macro
    macro_engine = MacroIntelligence(session)
    macro_result = await macro_engine.evaluate(now)

    # GIFT Nifty
    gift_engine = GiftNiftyIntelligence(session)
    gift_result = await gift_engine.evaluate(now)

    # Institutional
    inst_engine = InstitutionalIntelligence(session)
    inst_result = await inst_engine.evaluate(now)

    # Global
    global_engine = GlobalMarketIntelligence(session)
    global_result = await global_engine.evaluate(now)

    # Indian indices
    from app.market_intelligence.index_catalog import INDEX_CATALOG
    from app.repositories.market_index_price import MarketIndexPriceRepository

    price_repo = MarketIndexPriceRepository(session)
    indian_indices = []
    for entry in INDEX_CATALOG:
        if entry.country != "INDIA":
            continue
        code = entry.code
        engine = IndexPriceAction(code, date.today())
        engine.set_repo(price_repo)
        try:
            basic = await engine.basic_price()
            returns = await engine.returns()
            trend_result = await engine.trend()
            score_result = await engine.price_action_score()
        except Exception:
            basic, returns, trend_result, score_result = {}, {}, {}, {}
        indian_indices.append({
            "code": code,
            "name": entry.name,
            "close": basic.get("close"),
            "change_pct": basic.get("change_percent"),
            "return_1d": returns.get(1),
            "return_5d": returns.get(5),
            "return_20d": returns.get(20),
            "trend": trend_result.get("trend"),
            "score": score_result.get("price_action_score"),
            "freshness": "recent" if basic.get("close") else "missing",
        })

    # News & Government Policy Intelligence
    news_service = NewsPolicyService(session)
    try:
        news_policy = await news_service.get_market_news_policy_snapshot(
            evaluation_datetime=now,
            as_of_date=now.date()
        )
    except Exception:
        news_policy = None

    return {
        "request": request,
        "browser_user": user,
        "csrf_token": stage_csrf_token(request),
        "body_theme": "light",
        "composite": result,
        "explanation": explanation,
        "external": ext_result,
        "breadth": breadth_result,
        "sectors": sector_result,
        "macro": macro_result,
        "gift_nifty": gift_result,
        "institutional": inst_result,
        "global": global_result,
        "indian_indices": indian_indices,
        "news_policy": news_policy,
        "can_refresh": user_has_permission(user, "market_intelligence.refresh"),
    }


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


@router.get("/partial/hero", response_class=HTMLResponse)
async def hero_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial: hero section."""
    from datetime import UTC

    await _browser_user(request, session)
    composite = await _build_composite(session)
    result = await composite.evaluate(datetime.now(UTC))
    return _partial(
        request,
        "market_intelligence/components/hero.html",
        {"request": request, "composite": result},
    )


@router.get("/partial/factors", response_class=HTMLResponse)
async def factors_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial: factor score cards."""
    from datetime import UTC

    composite = await _build_composite(session)
    result = await composite.evaluate(datetime.now(UTC))
    return _partial(
        request,
        "market_intelligence/components/factor_cards.html",
        {"request": request, "composite": result},
    )


@router.get("/partial/explanation", response_class=HTMLResponse)
async def explanation_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial: AI explanation panel."""
    from datetime import UTC

    composite = await _build_composite(session)
    result = await composite.evaluate(datetime.now(UTC))
    service = AIMarketExplanationService(result)
    explanation = service.generate_explanation()
    return _partial(
        request,
        "market_intelligence/components/ai_explanation.html",
        {"request": request, "explanation": explanation, "composite": result},
    )


# ---------------------------------------------------------------------------
# Manual refresh (HTMX)
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("market_intelligence.refresh"))],
)
async def refresh_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """POST refresh: re-evaluate intelligence and return updated dashboard."""
    user = await _browser_user(request, session)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        raise ForbiddenError() from exc

    from datetime import UTC

    engine = await _build_composite(session)
    await engine.evaluate(datetime.now(UTC), persistence=True)

    context = await _build_dashboard_context(request, session, user=user)
    context["flash"] = {"level": "success", "message": "Market intelligence refreshed."}
    return _page(request, "market_intelligence.html", context)


# ---------------------------------------------------------------------------
# G.11-D-6 Sector Evidence Breadth Page & HTMX Partials
# ---------------------------------------------------------------------------


def _parse_opt_date(val: str | date | None) -> date | None:
    if val is None or isinstance(val, date):
        return val
    text = val.strip()
    if not text:
        return None
    return date.fromisoformat(text)


@router.get("/sectors/evidence-breadth", response_class=HTMLResponse)
async def sector_evidence_breadth_page(
    request: Request,
    sectors: str | None = Query(default=None),
    evaluation_date: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
    comparison_window: int = Query(default=1, ge=1, le=30),
    min_data_coverage_pct: float = Query(default=50.0, ge=0.0, le=100.0),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the Sector Evidence Breadth dashboard or HTMX matrix partial."""
    user = await _browser_user(request, session)

    parsed_eval_date = _parse_opt_date(evaluation_date)
    parsed_as_of_date = _parse_opt_date(as_of_date)

    sector_list: list[str]
    if not sectors or sectors.strip() == "" or sectors.strip().upper() == "ALL_SECTORS":
        sector_list = ["ALL_SECTORS"]
    else:
        sector_list = [s.strip().upper() for s in sectors.split(",") if s.strip()]

    breadth_req = SectorEvidenceBreadthRequest(
        sectors=sector_list,
        evaluation_date=parsed_eval_date,
        as_of_date=parsed_as_of_date,
        comparison_window=comparison_window,
        min_data_coverage_pct=min_data_coverage_pct,
    )

    aggregator = SectorEvidenceAggregatorService(
        session=session,
        session_factory=get_session_factory(),
    )
    error_flash = None
    try:
        breadth_response = await aggregator.get_sector_evidence_breadth(breadth_req)
    except ValueError as e:
        error_flash = str(e)
        breadth_req = SectorEvidenceBreadthRequest(
            sectors=["ALL_SECTORS"],
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
            comparison_window=comparison_window,
            min_data_coverage_pct=min_data_coverage_pct,
        )
        breadth_response = await aggregator.get_sector_evidence_breadth(breadth_req)

    context = {
        "request": request,
        "user": user,
        "response": breadth_response,
        "csrf_token": _existing_csrf_token(request),
        "current_sectors": sectors or "ALL_SECTORS",
        "evaluation_date": (
            breadth_response.evaluation_date.isoformat()
            if breadth_response.evaluation_date
            else ""
        ),
        "as_of_date": (
            breadth_response.as_of_date.isoformat()
            if breadth_response.as_of_date
            else ""
        ),
        "comparison_window": breadth_response.comparison_window,
        "min_data_coverage_pct": min_data_coverage_pct,
        "all_sector_codes": list(SECTOR_INDEX_CODES),
        "error_flash": error_flash,
    }

    is_htmx = (
        request.headers.get("hx-request") == "true"
        or request.headers.get("HX-Request") == "true"
        or request.query_params.get("partial") == "true"
    )
    if is_htmx:
        return _partial(request, "components/sector_evidence_matrix.html", context)

    return _page(request, "sector_evidence_breadth.html", context)
