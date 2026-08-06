from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.watchlist import Watchlist
from app.services.alert import AlertEngineError, AlertService

router = APIRouter(
    dependencies=[Depends(require_browser_permission("alerts.read"))]
)
WRITE_DEPENDENCIES = [Depends(require_browser_permission("alerts.write"))]
_env = None


def _get_env():
    global _env
    if _env is None:
        from jinja2 import Environment, FileSystemLoader

        _env = Environment(
            loader=FileSystemLoader(str(settings.base_dir / "app" / "templates"))
        )
    return _env


def _render(template_name: str, context: dict) -> str:
    env = _get_env()
    return env.get_template(template_name).render(**context)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    rules = await service.list_rules(limit=200)
    events = await service.list_recent_events(limit=50)
    pending = await service.count_pending()
    watchlists = (
        await session.scalars(select(Watchlist).order_by(Watchlist.name))
    ).all()
    html = _render(
        "alerts.html",
        {
            "request": request,
            "rules": rules,
            "events": events,
            "pending": pending,
            "watchlists": list(watchlists),
        },
    )
    return HTMLResponse(content=html)


@router.post("/rules", response_class=HTMLResponse, dependencies=WRITE_DEPENDENCIES)
async def create_rule(
    request: Request,
    name: str = Form(...),
    rule_type: str = Form(...),
    operator: str = Form(...),
    value: str | None = Form(default=None),
    stock_id: int | None = Form(default=None),
    watchlist_id: int | None = Form(default=None),
    cooldown_minutes: int = Form(default=0),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    try:
        await service.create_rule(
            {
                "name": name,
                "rule_type": rule_type,
                "operator": operator,
                "value": float(value) if value not in (None, "") else None,
                "stock_id": stock_id,
                "watchlist_id": watchlist_id,
                "cooldown_minutes": cooldown_minutes,
            }
        )
    except (AlertEngineError, ValueError):
        pass
    return RedirectResponse(url="/alerts", status_code=303)


@router.post(
        "/rules/{rule_id}/toggle",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def toggle_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    rule = await service.get_rule(rule_id)
    if rule is not None:
        try:
            await service.update_rule(rule_id, {"enabled": not rule.enabled})
        except AlertEngineError:
            pass
    return RedirectResponse(url="/alerts", status_code=303)


@router.post(
        "/rules/{rule_id}/delete",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def delete_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    try:
        await service.delete_rule(rule_id)
    except AlertEngineError:
        pass
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/run", response_class=HTMLResponse, dependencies=WRITE_DEPENDENCIES)
async def run_alerts(
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    await service.run_for_trade_date()
    return RedirectResponse(url="/alerts", status_code=303)


@router.get("/events", response_class=HTMLResponse)
async def events_partial(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AlertService(session)
    events = await service.list_recent_events(limit=limit)
    html = _render(
        "components/alert_events_table.html", {"request": request, "events": events}
    )
    return HTMLResponse(content=html)
