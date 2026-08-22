from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.scan import ScanDefinitionRepository


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(
    dependencies=[Depends(require_browser_permission("scanners.read"))]
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def scanner_library_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    definitions = await ScanDefinitionRepository(session).list_active()
    grouped: dict[str, list] = defaultdict(list)
    for definition in definitions:
        grouped[definition.category or "Other"].append(definition)
    html = _render(
        "scanner_library.html",
        {
            "request": request,
            "grouped": dict(grouped),
            "scanners_by_category": dict(grouped),
            "total_scanners": len(definitions),
        },
    )
    return HTMLResponse(content=html)