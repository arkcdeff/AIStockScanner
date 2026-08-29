from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import finalize_page_response, stage_csrf_token
from app.auth import csrf
from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.services.eod_pipeline import EODPipelineService


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


def _flash_url(**params: str | int) -> str:
    parts = [f"{k}={v}" for k, v in params.items()]
    return "/pipeline?" + "&".join(parts) if parts else "/pipeline"


router = APIRouter(
    dependencies=[Depends(require_browser_permission("stocks.read"))]
)
RUN_DEPENDENCIES = [Depends(require_browser_permission("pipeline.run"))]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def pipeline_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = EODPipelineService()
    status = await service.status()
    runs = await service.list_runs(limit=20)
    latest_run = status.get("latest_run")
    latest_success = next((run for run in runs if run.status == "completed"), None)
    latest_failed = next((run for run in runs if run.status == "failed"), None)
    csrf_token = stage_csrf_token(request)
    html = _render(
        "pipeline.html",
        {
            "request": request,
            "status": status,
            "pipeline_status": latest_run,
            "runs": runs,
            "latest_success": latest_success,
            "latest_failed": latest_failed,
            "csrf_token": csrf_token,
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.post(
    "/run-latest",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_pipeline_latest(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.eod_pipeline import PipelineError, PipelineOverlapError

    csrf.verify_request_csrf(request, await request.form())
    try:
        run = await EODPipelineService().run_latest()
        level, message = "success", f"Pipeline completed (run #{run.id}, {run.status})"
    except PipelineOverlapError:
        level, message = "warning", "A pipeline run is already in progress."
    except PipelineError as exc:
        level, message = "danger", str(exc)
    return RedirectResponse(url=_flash_url(level=level, message=message), status_code=303)


@router.post(
    "/run-file",
    response_class=HTMLResponse,
    dependencies=RUN_DEPENDENCIES,
)
async def run_pipeline_file(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.eod_pipeline import PipelineError, PipelineOverlapError

    csrf.verify_request_csrf(request, await request.form())
    file_bytes = await file.read()
    try:
        run = await EODPipelineService().run_from_file(
            bhavcopy=file_bytes,
            bhavcopy_name=file.filename or "bhavcopy.csv",
        )
        level, message = "success", f"Pipeline completed (run #{run.id}, {run.status})"
    except PipelineOverlapError:
        level, message = "warning", "A pipeline run is already in progress."
    except PipelineError as exc:
        level, message = "danger", str(exc)
    return RedirectResponse(url=_flash_url(level=level, message=message), status_code=303)
