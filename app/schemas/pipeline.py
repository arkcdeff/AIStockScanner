from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class PipelineRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    trade_date: date | None
    status: str
    current_stage: str | None
    import_job_id: int | None
    imported_rows: int
    inserted_rows: int
    updated_rows: int
    rejected_rows: int
    stocks_processed: int
    indicators_calculated: int
    scanners_executed: int
    scanner_matches: int
    analytics_calculated: int
    failed_stage: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None


class PipelineRunDateRequest(BaseModel):
    trade_date: date


class PipelineRunResponse(BaseModel):
    run_id: int
    status: str
    trade_date: date | None
    current_stage: str | None
    message: str
    run: PipelineRunRead


class PipelineStatusResponse(BaseModel):
    scheduler_enabled: bool
    scheduler_running: bool
    latest_run: PipelineRunRead | None
    total_runs: int
    running_runs: int
