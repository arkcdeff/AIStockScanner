from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.services.scanner_workspace import ScannerWorkspaceService

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def scanner_results_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=_render_empty(request))


@router.get("/table", response_class=HTMLResponse)
async def scanner_results_table(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    search: str = Query(default=""),
    scanner: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_delivery: float | None = Query(default=None),
    max_delivery: float | None = Query(default=None),
    min_volume: int | None = Query(default=None),
    max_volume: int | None = Query(default=None),
    sort_by: str = Query(default="scan_date"),
    sort_dir: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> HTMLResponse:
    service = ScannerWorkspaceService(session)
    data = await service.get_results(
        search=search,
        scanner=scanner,
        sector=sector,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        min_delivery=min_delivery,
        max_delivery=max_delivery,
        min_volume=min_volume,
        max_volume=max_volume,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    options = await service.get_filter_options()
    html = _render_table(request, data, options, {
        "search": search,
        "scanner": scanner,
        "sector": sector,
        "date_from": date_from,
        "date_to": date_to,
        "min_price": min_price,
        "max_price": max_price,
        "min_delivery": min_delivery,
        "max_delivery": max_delivery,
        "min_volume": min_volume,
        "max_volume": max_volume,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page": page,
        "page_size": page_size,
    })
    return HTMLResponse(content=html)


@router.get("/export/csv")
async def export_csv(
    session: AsyncSession = Depends(get_db_session),
    search: str = Query(default=""),
    scanner: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_delivery: float | None = Query(default=None),
    max_delivery: float | None = Query(default=None),
    min_volume: int | None = Query(default=None),
    max_volume: int | None = Query(default=None),
    sort_by: str = Query(default="scan_date"),
    sort_dir: str = Query(default="desc"),
) -> Response:
    service = ScannerWorkspaceService(session)
    rows = await service.get_export_data(
        search=search,
        scanner=scanner,
        sector=sector,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        min_delivery=min_delivery,
        max_delivery=max_delivery,
        min_volume=min_volume,
        max_volume=max_volume,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    content = service.to_csv(rows)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scanner_results.csv"},
    )


@router.get("/export/excel")
async def export_excel(
    session: AsyncSession = Depends(get_db_session),
    search: str = Query(default=""),
    scanner: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_delivery: float | None = Query(default=None),
    max_delivery: float | None = Query(default=None),
    min_volume: int | None = Query(default=None),
    max_volume: int | None = Query(default=None),
    sort_by: str = Query(default="scan_date"),
    sort_dir: str = Query(default="desc"),
) -> Response:
    service = ScannerWorkspaceService(session)
    rows = await service.get_export_data(
        search=search,
        scanner=scanner,
        sector=sector,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        min_delivery=min_delivery,
        max_delivery=max_delivery,
        min_volume=min_volume,
        max_volume=max_volume,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    content = service.to_excel(rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=scanner_results.xlsx"},
    )


@router.get("/export/pdf")
async def export_pdf(
    session: AsyncSession = Depends(get_db_session),
    search: str = Query(default=""),
    scanner: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_delivery: float | None = Query(default=None),
    max_delivery: float | None = Query(default=None),
    min_volume: int | None = Query(default=None),
    max_volume: int | None = Query(default=None),
    sort_by: str = Query(default="scan_date"),
    sort_dir: str = Query(default="desc"),
) -> Response:
    service = ScannerWorkspaceService(session)
    rows = await service.get_export_data(
        search=search,
        scanner=scanner,
        sector=sector,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        min_delivery=min_delivery,
        max_delivery=max_delivery,
        min_volume=min_volume,
        max_volume=max_volume,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    content = service.to_pdf(rows)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=scanner_results.pdf"},
    )


def _render_empty(request: Request) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")))
    env.globals["_build_sort_params"] = lambda col, req: _build_sort_params(col, req)
    env.globals["_build_page_params"] = lambda p, req: _build_page_params(p, req)
    template = env.get_template("scanner_results.html")
    return template.render(
        request=request,
        results=[],
        total=0,
        page=1,
        page_size=25,
        pages=0,
        filters={},
        options={"sectors": [], "scanners": {}, "dates": []},
    )


def _render_table(request: Request, data: dict, options: dict, filters: dict) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")))
    env.globals["_build_sort_params"] = lambda col, req: _build_sort_params(col, req)
    env.globals["_build_page_params"] = lambda p, req: _build_page_params(p, req)
    template = env.get_template("components/scanner_results_table.html")
    return template.render(
        request=request,
        results=data.get("results", []),
        total=data.get("total", 0),
        page=data.get("page", 1),
        page_size=data.get("page_size", 25),
        pages=data.get("pages", 0),
        filters=filters,
        options=options,
    )


def _build_sort_params(col: str, request: Request) -> str:
    params = dict(request.query_params)
    current_sort = params.get("sort_by", "")
    current_dir = params.get("sort_dir", "desc")
    if current_sort == col:
        params["sort_dir"] = "asc" if current_dir == "desc" else "desc"
    else:
        params["sort_dir"] = "asc"
    params["sort_by"] = col
    return "&".join(f"{k}={v}" for k, v in params.items())


def _build_page_params(page: int, request: Request) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
    return "&".join(f"{k}={v}" for k, v in params.items())
