# Pipeline API

Automated end-of-day processing pipeline: import NSE EOD data → calculate indicators →
run all active scanners → persist results. Each run is tracked in `eod_pipeline_runs`.

## Base URL
`/api/v1/pipeline`

## Run Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | uuid | Run identifier |
| `status` | string | `running` \| `completed` \| `failed` |
| `failed_stage` | string\|null | Stage that failed (`import`/`indicators`/`scanners`) |
| `error_message` | string\|null | Sanitized error (credentials redacted) |
| `trade_date` | date\|null | Import trade date |
| `import_job_id` | uuid\|null | Link to the `ImportJob` |
| `imported_rows` | int | Rows read from source |
| `inserted_rows` | int | New daily-price rows |
| `updated_rows` | int | Updated daily-price rows |
| `rejected_rows` | int | Skipped/unparseable rows |
| `indicators_calculated` | int | Indicator series computed |
| `scanners_executed` | int | Scanners run |
| `scanner_matches` | int | Matches persisted |
| `duration_ms` | int\|null | Total wall-clock time |
| `started_at` / `finished_at` | datetime\|null | Timeline |

## Endpoints

### POST /pipeline/run-file
Trigger a pipeline run from an uploaded CSV or ZIP bhavcopy file.

**Body:** `multipart/form-data` with a `file` field.

**Response (synchronous):**
```json
{
  "id": "uuid",
  "status": "completed",
  "failed_stage": null,
  "trade_date": "2026-08-03",
  "imported_rows": 2600,
  "inserted_rows": 2200,
  "updated_rows": 400,
  "rejected_rows": 0,
  "indicators_calculated": 2200,
  "scanners_executed": 36,
  "scanner_matches": 3,
  "duration_ms": 18420,
  "started_at": "2026-08-03T18:00:01Z",
  "finished_at": "2026-08-03T18:00:20Z"
}
```

### POST /pipeline/run-date
Trigger a pipeline run for a specific trade date (downloads the NSE bhavcopy).

**Body:**
```json
{ "trade_date": "2026-08-03" }
```

**Response:** Same shape as `/run-file`. Non-trading dates (e.g. weekends) fail the
import stage with `status: "failed"`, `failed_stage: "import"`.

### POST /pipeline/run-latest
Trigger a pipeline run for the last non-empty trading date. No body.

**Response:** Same shape as `/run-file`.

### GET /pipeline/runs
List pipeline runs (newest first).

**Query Parameters:**
- `limit` (default: 20, max: 100)
- `offset` (default: 0)

**Response:**
```json
{
  "total": 1,
  "runs": [ { "id": "uuid", "status": "completed", "trade_date": "2026-08-03", "...": "..." } ]
}
```

### GET /pipeline/runs/{run_id}
Get a single pipeline run by ID.

**Response:** The run object shown above. `404` if not found.

### GET /pipeline/status
Current pipeline status and scheduler state.

**Response:**
```json
{
  "latest_run": { "id": "uuid", "status": "completed", "trade_date": "2026-08-03", "scanner_matches": 3 },
  "has_run": true,
  "scheduler_enabled": false,
  "next_scheduled_run": null,
  "running": false
}
```

## Concurrency
Only one pipeline run may execute at a time. If a run is already in progress, the
request fails with `409 Conflict` and the active run's ID.

## Scheduler
Scheduled execution is opt-in via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EOD_SCHEDULER_ENABLED` | `false` | Enable the daily APScheduler job |
| `EOD_SCHEDULER_TIMEZONE` | `Asia/Kolkata` | Run timezone |
| `EOD_SCHEDULER_HOUR` | `18` | Run hour (24h) |
| `EOD_SCHEDULER_MINUTE` | `30` | Run minute |
| `EOD_SCHEDULER_MAX_INSTANCES` | `1` | Never run concurrently |
| `EOD_SCHEDULER_COALESCE` | `true` | Coalesce missed runs |

When enabled, the app also attempts to recover abandoned `running` runs on startup.

## Status Codes
- `200 OK`: Successful execution / queries
- `409 Conflict`: Another run is already in progress
- `422 Unprocessable Entity`: Invalid body / file type
- `500 Internal Server Error`: Unexpected pipeline failure
