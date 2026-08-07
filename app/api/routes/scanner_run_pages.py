from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
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
RUN_DEPENDENCIES = [Depends(require_browser_permission("scanners.run"))]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def scanner_run_page(
    request: Request,
    scan_code: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    definitions = await ScanDefinitionRepository(session).list_active()
    result_rows: list[dict] = []
    selected = None
    if scan_code:
        selected = await ScanDefinitionRepository(session).get_by_code(scan_code)
        results = await session.execute(
            select(
                Stock.symbol,
                Stock.company_name,
                ScanResult.scan_date,
                ScanResult.score,
                ScanResult.metrics,
            )
            .join(ScanDefinition, ScanResult.scan_definition_id == ScanDefinition.id)
            .join(Stock, ScanResult.stock_id == Stock.id)
            .where(ScanDefinition.code == scan_code)
            .order_by(ScanResult.scan_date.desc(), ScanResult.score.desc())
            .limit(50)
        )
        for symbol, company_name, scan_date, score, metrics in results:
            result_rows.append(
                {
                    "symbol": symbol,
                    "company_name": company_name,
                    "scan_date": scan_date,
                    "score": float(score) if score is not None else None,
                    "reason": (metrics or {}).get("reason", ""),
                    "trade_date": (metrics or {}).get("trade_date"),
                }
            )
    html = _render(
        "scanner_run.html",
        {
            "request": request,
            "definitions": definitions,
            "selected_code": scan_code or "",
            "selected_name": selected.name if selected else None,
            "results": result_rows,
        },
    )
    return HTMLResponse(content=html)


@router.post(
    "/run",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_scanner(
    request: Request,
    scan_code: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.scanner_engine import ScannerService

    service = ScannerService(session)
    try:
        results = await service.run_scanner(scan_code)
        await service.persist_results({scan_code: results})
        return RedirectResponse(
            url=f"/scanner-run?scan_code={scan_code}&ran=1", status_code=303
        )
    except ValueError:
        return RedirectResponse(
            url=f"/scanner-run?scan_code={scan_code}&error=1", status_code=303
        )


@router.post(
    "/run-all",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_all_scanners(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.scanner_engine import ScannerService

    service = ScannerService(session)
    results = await service.run_all()
    await service.persist_results(results)
    return RedirectResponse(url="/scanner-run?ran_all=1", status_code=303)