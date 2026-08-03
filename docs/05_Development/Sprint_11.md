# Sprint 11: Automated EOD Processing Pipeline & Scheduler

## Duration
TBD

## Status
Complete

## Objectives
- Build an automated end-of-day pipeline that runs import → indicators → all active scanners
- Track each run with status, metrics, and failure isolation per stage
- Expose manual run + status APIs and a scheduled trigger
- Show pipeline status on the dashboard

## Deliverables
- [x] `eod_pipeline_runs` table (status, trade_date, imported/inserted/updated/rejected rows,
      indicators calculated, scanners executed, failed_stage, error_message, duration_ms,
      started_at/finished_at, import_job_id) with migration `d1e2f3a4b5c6`
- [x] `EODPipelineService` orchestrating import → indicators → scanners, one fresh session per
      stage, single-active-run guard (`PipelineOverlapError` → 409), sanitized error messages
      (credentials redacted), abandoned-run recovery
- [x] Sources: `run_from_file` (CSV/ZIP upload), `run_from_date` (NSE bhavcopy URL),
      `run_latest` (last non-empty trading date)
- [x] Manual APIs under `/api/v1/pipeline`: `run-file`, `run-date`, `run-latest`, `runs`,
      `runs/{id}`, `status`
- [x] APScheduler-based EOD scheduler (weekdays 18:30 Asia/Kolkata, single-instance, coalesced,
      replace-existing) — disabled by default via `EOD_SCHEDULER_ENABLED`
- [x] Dashboard pipeline status card (latest run badge, trade date, metrics, failed stage)
- [x] 19 tests: successful full pipeline, per-stage failures, overlap prevention, idempotent
      re-import, abandoned-run recovery, credential redaction, API endpoints, weekend/empty-file
      handling, scheduler (disabled/one-job/no-duplicates), dashboard rendering
- [x] Fixed latent bugs: `pandas_ta` import missing in `IndicatorEngine`; `Decimal` columns broke
      indicator math (coerced to float)

## Definition of Done
- [x] pytest passes 3 consecutive times
- [x] ruff passes
- [x] No MissingGreenlet
- [x] Manual route verification succeeds (run-file, runs, runs/{id}, status, dashboard)

## Notes
- Scheduler is opt-in; default is disabled so tests never start background jobs
- `tests/conftest.py` truncates `eod_pipeline_runs` alongside the other tables
- No AI, auth, alerts, Redis, or Celery involved (per Sprint 11 constraints)
