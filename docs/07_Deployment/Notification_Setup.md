# Notification Setup

Notification delivery and both notification scheduler jobs are disabled by default. Configure a
mock channel first, then enable only the jobs required for the deployment.

## Prerequisites

- Application dependencies installed with `uv sync`
- PostgreSQL configured and reachable
- Migration `22d886d53759` applied
- Application kept on a trusted network (there is currently no authentication or global CSRF)

## Apply and verify migrations

```powershell
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

Both revision commands must report `22d886d53759 (head)`.

## Environment settings

```dotenv
# Global notification execution
NOTIFICATIONS_ENABLED=false
NOTIFICATION_PROVIDER=mock
NOTIFICATION_TIMEOUT=15
NOTIFICATION_MAX_ATTEMPTS=3
NOTIFICATION_RETRY_MINUTES=15
NOTIFICATION_BATCH_SIZE=100
NOTIFICATION_STALE_MINUTES=30

# Pending/retry delivery job (disabled by default)
NOTIFICATION_DELIVERY_JOB_ENABLED=false
NOTIFICATION_DELIVERY_INTERVAL_MINUTES=5
NOTIFICATION_DELIVERY_MAX_INSTANCES=1
NOTIFICATION_DELIVERY_COALESCE=true

# Weekday daily summary (disabled by default)
DAILY_SUMMARY_ENABLED=false
DAILY_SUMMARY_HOUR=20
DAILY_SUMMARY_MINUTE=0
DAILY_SUMMARY_TIMEZONE=Asia/Kolkata
DAILY_SUMMARY_MAX_INSTANCES=1
DAILY_SUMMARY_COALESCE=true
```

`NOTIFICATIONS_ENABLED` controls sending. Scheduler settings control registration; leaving all
scheduler flags false means the application scheduler does not start.

## Telegram

Global defaults may be placed in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_DEFAULT_CHAT_ID=
TELEGRAM_HTML_MODE=true
TELEGRAM_MAX_MESSAGE_LENGTH=4096
```

Create a Telegram channel in `/notifications/settings`. Its token is write-only and rendered as a
mask after saving. Never place a token in source control, logs, screenshots, or API query strings.

## Email

```dotenv
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_USERNAME=
EMAIL_PASSWORD=
EMAIL_FROM=
EMAIL_USE_TLS=false
EMAIL_USE_STARTTLS=true
EMAIL_TIMEOUT=15
```

Create an email channel with host, port, from/to addresses, TLS mode, and credentials. The
password is write-only. Use a dedicated SMTP credential and protect database backups because
channel-specific provider configuration is operational data.

## Mock and in-app channels

- `mock`: deterministic local verification with no network; supports optional test failure modes
  `timeout`, `transient`, and `permanent`.
- `in_app`: records local delivery state without Telegram or SMTP credentials.

Create an enabled preference for every event type the channel should receive. Daily summaries
require an explicit `daily_summary` preference.

## Safe enablement sequence

1. Keep all scheduler flags and `NOTIFICATIONS_ENABLED` false.
2. Start the application and create one mock channel plus required preferences.
3. Use the mock provider test from `/notifications/settings`.
4. Set `NOTIFICATIONS_ENABLED=true` and restart.
5. Verify manual pending delivery and one dated daily summary.
6. Enable `NOTIFICATION_DELIVERY_JOB_ENABLED` if interval processing is required.
7. Enable `DAILY_SUMMARY_ENABLED` only after the desired timezone/time is confirmed.
8. Replace the mock channel with Telegram/email only after local verification.

## Operational checks

```text
GET  /api/v1/notifications/status
GET  /api/v1/notifications/scheduler/status
POST /api/v1/notifications/scheduler/run-delivery
POST /api/v1/notifications/daily-summary/run
GET  /api/v1/notifications/daily-summary/history
```

Confirm:

- Scheduler flags match the environment and registered IDs are unique.
- Repeating a completed summary date returns `idempotent: true` with no new delivery.
- Pending/retry counts fall after successful mock processing.
- Provider failures update delivery state without changing a completed EOD pipeline.
- API responses and `/notifications/settings` contain masks, never credential values.

## Verification

```powershell
uv run ruff check .
uv run pytest -q
uv run pytest -q
uv run pytest -q
```

The v0.11.0 baseline is 279 passing tests on each run.

## Troubleshooting

- No jobs registered: expected while scheduler enable flags are false; restart after changing env.
- Daily summary has no deliveries: add an enabled `daily_summary` preference to an enabled
  channel and confirm `NOTIFICATIONS_ENABLED=true`.
- Delivery remains scheduled: verify its `next_retry_at`, provider configuration, and job flag.
- Uvicorn reload: stable scheduler job IDs and guarded startup prevent duplicate registration;
  production should run without `--reload`.
- Provider errors: inspect sanitized delivery state/history; credentials are intentionally absent.
