from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.scheduler.eod_scheduler import get_scheduler


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(
    dependencies=[Depends(require_browser_permission("stocks.read"))]
)
RUN_DEPENDENCIES = [Depends(require_browser_permission("scheduler.run"))]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def scheduler_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    status = get_scheduler().status()
    html = _render(
        "scheduler.html",
        {
            "request": request,
            "status": status,
        },
    )
    return HTMLResponse(content=html)


@router.get("/status", response_class=HTMLResponse)
async def scheduler_status_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    html = _render(
        "components/scheduler_status_cards.html",
        {"request": request, "status": get_scheduler().status()},
    )
    return HTMLResponse(content=html)


@router.post(
    "/run-delivery",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_delivery_now(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    result = await get_scheduler().run_delivery_now()
    status = result.get("status", "ok")
    count = len(result.get("delivery_ids", []))
    return RedirectResponse(
        url=f"/scheduler?run_delivery={status}&count={count}", status_code=303
    )


@router.post(
    "/run-summary",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_summary_now(
    request: Request,
    summary_date: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    requested = None
    if summary_date:
        try:
            requested = date.fromisoformat(summary_date)
        except ValueError:
            return RedirectResponse(url="/scheduler?bad_date=1", status_code=303)
    result = await get_scheduler().run_daily_summary_now(requested)
    status = result.get("status", "unknown")
    return RedirectResponse(
        url=f"/scheduler?run_summary={status}", status_code=303
    )