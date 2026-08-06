from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.auth.authorization import require_permission
from app.core.config import settings
from app.schemas.pipeline import (
    PipelineRunDateRequest,
    PipelineRunRead,
    PipelineRunResponse,
    PipelineStatusResponse,
)
from app.services.eod_pipeline import EODPipelineService, PipelineError, PipelineOverlapError

router = APIRouter(
    prefix="/pipeline",
    tags=["Pipeline"],
    dependencies=[Depends(require_permission("stocks.read"))],
)
RUN_DEPENDENCIES = [Depends(require_permission("pipeline.run"))]


def _overlap_error(exc: PipelineOverlapError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/run-file",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=RUN_DEPENDENCIES,
)
async def run_pipeline_from_file(
    file: UploadFile = File(...),
) -> PipelineRunResponse:
    """Run the full EOD pipeline from an uploaded bhavcopy CSV/ZIP.

    NOTE: execution is synchronous. The request blocks until the pipeline
    finishes so the completed run (with metrics) is returned directly.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(file_bytes) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail="File too large")

    service = EODPipelineService()
    try:
        run = await service.run_from_file(bhavcopy=file_bytes, bhavcopy_name=file.filename)
    except PipelineOverlapError as exc:
        raise _overlap_error(exc) from exc
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(run)


@router.post(
    "/run-date",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=RUN_DEPENDENCIES,
)
async def run_pipeline_for_date(
    payload: PipelineRunDateRequest,
) -> PipelineRunResponse:
    """Download the NSE bhavcopy for a trade date and run the full pipeline.

    NOTE: execution is synchronous. The request blocks until the pipeline
    finishes so the completed run (with metrics) is returned directly.
    """
    service = EODPipelineService()
    try:
        run = await service.run_from_date(payload.trade_date)
    except PipelineOverlapError as exc:
        raise _overlap_error(exc) from exc
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(run)


@router.post(
    "/run-latest",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=RUN_DEPENDENCIES,
)
async def run_pipeline_latest() -> PipelineRunResponse:
    """Download the latest available NSE bhavcopy and run the full pipeline.

    NOTE: execution is synchronous. The request blocks until the pipeline
    finishes so the completed run (with metrics) is returned directly.
    """
    service = EODPipelineService()
    try:
        run = await service.run_latest()
    except PipelineOverlapError as exc:
        raise _overlap_error(exc) from exc
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(run)


@router.get("/runs", response_model=list[PipelineRunRead])
async def list_pipeline_runs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[PipelineRunRead]:
    runs = await EODPipelineService().list_runs(limit=limit, offset=offset)
    return [PipelineRunRead.model_validate(run) for run in runs]


@router.get("/runs/{run_id}", response_model=PipelineRunRead)
async def get_pipeline_run(
    run_id: int,
) -> PipelineRunRead:
    run = await EODPipelineService().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Pipeline run {run_id} not found")
    return PipelineRunRead.model_validate(run)


@router.get("/status", response_model=PipelineStatusResponse)
async def pipeline_status() -> PipelineStatusResponse:
    service = EODPipelineService()
    state = await service.status()
    return PipelineStatusResponse(
        scheduler_enabled=settings.eod_scheduler_enabled,
        scheduler_running=_scheduler_running(),
        latest_run=PipelineRunRead.model_validate(state["latest_run"])
        if state["latest_run"]
        else None,
        total_runs=state["total_runs"],
        running_runs=state["running_runs"],
    )


def _response(run: object) -> PipelineRunResponse:
    return PipelineRunResponse(
        run_id=int(run.id),
        status=run.status,
        trade_date=run.trade_date,
        current_stage=run.current_stage,
        message=("Pipeline failed" if run.status == "failed" else "Pipeline completed"),
        run=PipelineRunRead.model_validate(run),
    )


def _scheduler_running() -> bool:
    from app.scheduler.eod_scheduler import get_scheduler

    return get_scheduler().is_running
