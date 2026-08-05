# Notification API

Manage notification channels and preferences, inspect delivery state, run safe delivery actions,
and operate idempotent daily summaries.

## Base URL

`/api/v1/notifications`

The application currently has no authentication. Keep manual delivery, provider-test, and
daily-summary endpoints on a trusted network.

## Security behavior

- Sensitive channel configuration values are returned as `********`.
- Existing token/password values are retained when an update submits `********` or leaves the
  write-only UI input blank.
- Provider exceptions are sanitized; API responses do not expose credentials or raw provider
  stack traces.
- Notification subject/body values are plain text and HTML-escaped by the UI.

## Status

### `GET /status`

Returns global enablement, provider availability, delivery counts, unread count, last-delivery
time, and stale `sending` count. It never returns provider configuration.

## Channels

### `GET /channels`

Filters: `enabled`, `provider`, `limit`, `offset`.

### `POST /channels`

```json
{
  "name": "Local mock",
  "provider": "mock",
  "enabled": true,
  "config_json": {"recipient": "local-user"}
}
```

### `PUT /channels/{channel_id}`

Updates provided fields. Channel names are unique; duplicates return `409`.

### `DELETE /channels/{channel_id}`

Deletes the channel and owned preferences/deliveries. Returns `204` or `404`.

Supported provider configuration:

| Provider | Fields |
|---|---|
| `mock` | `recipient`, optional `fail_with`, optional `external_id_prefix` |
| `telegram` | `bot_token`, `chat_id`/`recipient`, `html_mode`, `max_message_length`, `timeout` |
| `email` | `host`, `port`, `username`, `password`, `from`, `recipient`, TLS flags, `timeout` |
| `in_app` | `recipient`, optional `external_id_prefix`, `fail_if_not_configured` |

### `POST /test/{provider}`

Tests a supported provider with supplied safe configuration. Validation/provider failures are
returned without raw external exception details. Automated tests use `mock` only.

## Preferences

### `GET /preferences`

Filters: `channel_id`, `event_type`, `enabled`, `limit`, `offset`.

### `POST /preferences`

```json
{
  "channel_id": 1,
  "event_type": "daily_summary",
  "enabled": true,
  "minimum_priority": 0,
  "quiet_hours_start": null,
  "quiet_hours_end": null
}
```

### `PUT /preferences/{preference_id}`

Updates supplied fields. Quiet-hour start/end must be supplied together and must differ.

### `DELETE /preferences/{preference_id}`

Returns `204` or `404`. A channel/event pair is unique; duplicates return `409`.

## Delivery

### `POST /deliver-alert/{alert_event_id}`

Evaluates eligible channel preferences and idempotently creates/sends deliveries for an existing
alert event. A repeat request cannot duplicate an alert/channel delivery.

### `POST /deliver-pending`

Optional body: `{"batch_size": 50}`. Processes due `pending` and `retry_scheduled` deliveries up
to the configured batch maximum.

### `GET /history`

Newest-first delivery history. Filters:

- `status`
- `provider`
- `notification_type`
- `channel_id`
- `stock_symbol`
- `date_from`, `date_to`
- `unread_only`
- `limit`, `offset`

### `GET /history/{delivery_id}`

Returns sanitized delivery detail. HTML clients must escape stored subject/body content; the
included notification UI does so.

### `POST /history/{delivery_id}/retry`

Retries an eligible failed delivery through the existing provider/retry engine. Sent/read
deliveries return `409`; missing deliveries return `404`.

### `POST /history/{delivery_id}/mark-read`

Marks a delivery read and returns its updated state.

## Daily summary

### `POST /daily-summary/run`

Optional body:

```json
{"summary_date": "2026-08-05"}
```

The first successful request creates one run and eligible delivery rows. A repeated request for a
completed date returns the existing run with `idempotent: true` and never sends again.

### `GET /daily-summary/history`

Filters: `status`, `date_from`, `date_to`, `limit`, `offset`.

### `GET /daily-summary/{summary_id}`

Returns status, summary date, collected metrics, timestamps, and a sanitized error when present.

## Scheduler

### `POST /scheduler/run-delivery`

Runs the pending-delivery orchestration manually with a fresh database session.

### `GET /scheduler/status`

Returns scheduler running state, enabled flags, registered job IDs, next-run times, and safe
in-memory last-execution state. It never returns environment secrets.

## HTML routes

- `/notifications`
- `/notifications/settings`
- `/notifications/history`
- `/notifications/status`, `/providers`, `/recent`, `/summary-scheduler`
- `/notifications/history/table`, `/channels`, `/preferences`

HTMX actions cover channel/preference CRUD, provider/channel tests, delivery retry/read, and manual
daily-summary execution. JSON clients should use `/api/v1/notifications`, not the page routes.

## Common status codes

- `200`: success
- `201`: created
- `204`: deleted
- `404`: record not found
- `409`: duplicate/conflict or invalid retry of a completed delivery
- `422`: request, filter, provider configuration, or quiet-hours validation error
