# Notification Architecture

Sprint 15 adds notification delivery as a layer over persisted alerts and existing deterministic
analytics. It does not calculate indicators, rerun scanners, or alter EOD pipeline outcomes.

## Components

```text
AlertService / existing data       APScheduler (single instance)
              |                    |-- notification_delivery_interval
              |                    `-- notification_daily_summary
              v                                  |
       NotificationService <---------------------+
              |
              | eligibility + render + persist + send
              v
       ProviderManager
       |-- MockProvider
       |-- TelegramProvider
       |-- EmailProvider
       `-- InAppProvider
              |
              v
       NotificationDelivery
```

UI/page routes use `NotificationUIService` for display aggregation and call
`NotificationService` for delivery actions. They do not duplicate send/retry business logic.

## Persistence

| Model | Purpose | Important constraint |
|---|---|---|
| `NotificationChannel` | Named provider and provider-safe configuration | Unique name |
| `NotificationPreference` | Event eligibility, priority, quiet hours | Unique channel/event |
| `NotificationDelivery` | Rendered payload, state, attempts, retry/sent metadata | Indexed state/retry fields |
| `DailySummaryRun` | One summary execution and aggregate metrics | Unique summary date |

Migration: `22d886d53759`, chained from `fd96a19d8b61`.

## Alert delivery flow

1. `AlertService` persists an `AlertEvent` from deterministic EOD inputs.
2. `NotificationService` loads enabled channels and their preferences in one eligible-channel
   query.
3. The service applies event type, enabled state, minimum priority, and quiet hours.
4. Existing delivery state is checked so the same alert/channel pair is not created twice.
5. A provider-specific, escaped message is rendered and a delivery row is persisted.
6. Network work occurs outside long-running database transactions.
7. Success becomes `sent`; transient failure becomes `retry_scheduled`; permanent/exhausted
   failure becomes `failed` with a sanitized error.

## Retry state

```text
pending --> sending --> sent
             |  |
             |  `--> failed                 (permanent or max attempts)
             `-----> retry_scheduled ------> sending

stale sending ------------------------------> retry_scheduled/failed
```

- Due rows are processed in bounded batches.
- Retry timing and maximum attempts come from environment settings.
- Stale `sending` rows are recovered before scheduled batch processing.
- Manual retry rejects already completed deliveries.

## Daily summary flow

1. Look up `DailySummaryRun` by requested date.
2. Return a completed run idempotently without rendering or sending again.
3. Create/reset a `running` run and collect existing persisted metrics.
4. Read pipeline status, import counts, scanner matches, top ratings/breakouts, high-priority
   alerts, watchlist changes, and portfolio value/P&L where available.
5. Read cached AI market text only if the cache already contains it; otherwise use deterministic
   analytics-based text. No provider generation call is made.
6. Select channels with an explicit enabled `daily_summary` preference.
7. Render Telegram, email HTML, or email/plain text; create delivery rows and send through
   `NotificationService`.
8. Finish the run as `completed` or `failed`; abandoned `running` rows can be recovered.

## Scheduler integration

`app/scheduler/eod_scheduler.py` owns the only APScheduler instance and stable job IDs:

- `eod_pipeline_daily`
- `notification_delivery_interval`
- `notification_daily_summary`

Each job is independently disabled by default. Enabled jobs use `max_instances=1`, coalescing,
stable IDs with replacement, and fresh sessions. Calling scheduler startup twice does not create
duplicate registrations, including under Uvicorn reload.

After a successful EOD pipeline, notification orchestration is invoked through a fresh-session
helper. Exceptions are sanitized/logged and never change the completed pipeline record. Failed
pipelines do not trigger notification delivery.

## UI integration

- `/notifications`: status, provider cards, recent history, daily-summary/scheduler state
- `/notifications/settings`: masked channel configuration, preferences, read-only schedule
- `/notifications/history`: server-side filters/pagination, retry/read/detail actions
- Dashboard: compact counts, latest deliveries, latest daily summary, scheduler state
- Stock detail: latest five stock-related deliveries loaded without N+1 queries

HTMX refreshes only affected sections. Jinja autoescaping is enabled and stored subject/body data
is never marked safe.

## Security boundaries

- Tokens, passwords, recipients, chat IDs, and credential-shaped errors are masked/sanitized.
- Secret inputs are write-only; no secret values are placed in HTML data attributes.
- Provider credentials are never included in scheduler status or provider health responses.
- The application has no authentication or application-wide CSRF protection; operational and
  manual endpoints must remain on a trusted network.
- Browser push and distributed queues are intentionally outside Sprint 15.
