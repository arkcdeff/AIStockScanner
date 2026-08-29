from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.scan import ScanDefinitionRepository
from app.services.market_data_service import MarketDataService
from app.services.scanner_readiness import scanner_requirement


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(dependencies=[Depends(require_browser_permission("scanners.read"))])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def scanner_library_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    definitions = await ScanDefinitionRepository(session).list_active()
    readiness = await MarketDataService(session).scanner_readiness()
    grouped: dict[str, list] = defaultdict(list)
    scanner_requirements: dict[str, dict[str, object]] = {}
    for definition in definitions:
        grouped[definition.category or "Other"].append(definition)
        requirement = scanner_requirement(definition.code)
        eligible_history = readiness["eligible_sessions"].get(
            str(requirement.required_sessions), 0
        )
        scanner_requirements[definition.code] = {
            "required_sessions": requirement.required_sessions,
            "indicator_inputs": requirement.indicator_inputs,
            "eligible_history_count": eligible_history,
            "eligible_count": (
                eligible_history if readiness["latest_indicator_count"] else 0
            ),
            "state": (
                "READY"
                if readiness["latest_indicator_count"] and eligible_history
                else "NOT READY"
            ),
        }
    html = _render(
        "scanner_library.html",
        {
            "request": request,
            "grouped": dict(grouped),
            "scanners_by_category": dict(grouped),
            "total_scanners": len(definitions),
            "readiness": readiness,
            "scanner_requirements": scanner_requirements,
        },
    )
    return HTMLResponse(content=html)
