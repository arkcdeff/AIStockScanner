from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.services.ai_insights import AIInsightsService


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(
    dependencies=[Depends(require_browser_permission("ai.read"))]
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def ai_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = AIInsightsService(session)
    status = await service.get_ai_status()
    opportunities = await service.get_top_opportunities(limit=5)
    recent = await service.get_recent_explanations(limit=5)
    ai_enabled = bool(settings.ai_enabled)
    html = _render(
        "ai.html",
        {
            "request": request,
            "status": status,
            "opportunities": opportunities,
            "recent": recent,
            "ai_enabled": ai_enabled,
        },
    )
    return HTMLResponse(content=html)


@router.get("/recent", response_class=HTMLResponse)
async def ai_recent_partial(
    request: Request,
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    recent = await AIInsightsService(session).get_recent_explanations(limit=limit)
    html = _render(
        "components/ai_recent_explanations.html",
        {"request": request, "recent": recent},
    )
    return HTMLResponse(content=html)