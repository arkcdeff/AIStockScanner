# Alert API

Define alert rules and run a deterministic engine that evaluates them against the latest
analytics / indicators / prices, persisting matching `AlertEvent` rows. Notifications are
in-app only (no email/push/webhook yet).

## Base URL
`/api/v1/alerts`

## Alert Rule Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Rule id |
| `name` | string | Rule name |
| `enabled` | bool | Active rules are evaluated by the engine |
| `stock_id` | int \| null | Scope to one stock |
| `watchlist_id` | int \| null | Scope to a watchlist's stocks |
| `rule_type` | string | See rule types below |
| `operator` | string | Compare or signal operator (see below) |
| `value` | float \| null | Threshold for compare rules |
| `notification_type` | string | Defaults to `in_app` |
| `cooldown_minutes` | int | Min minutes between triggers per rule |
| `last_triggered` | datetime \| null | Last time the rule fired |
| `created_at` / `updated_at` | datetime | Timestamps |

If neither `stock_id` nor `watchlist_id` is set, the rule applies to **all active stocks**.

## Rule Types & Operators

| Rule type | Evaluates | Operator | Value |
|-----------|-----------|----------|-------|
| `overall_score` | TechnicalAnalysis overall score | `>`, `>=`, `<`, `<=` | 0-100 |
| `rsi` | Latest IndicatorResult RSI(14) | `>`, `>=`, `<`, `<=` | 0-100 |
| `ema_cross` | EMA20 vs EMA50 cross (latest + previous day) | `crosses_above`, `crosses_below`, `true`, `false` | null |
| `delivery` | Latest DailyPrice delivery % | `>`, `>=`, `<`, `<=` | % |
| `volume` | Relative volume vs 20-day average | `>`, `>=`, `<`, `<=` | x |
| `breakout` | TechnicalAnalysis `is_breakout` flag | `true`, `false` | null |
| `support_broken` | Close below 20-day support | `true`, `false` | null |
| `resistance_broken` | Close above 20-day resistance | `true`, `false` | null |

## Alert Event Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Event id |
| `alert_rule_id` | int | Firing rule |
| `stock_id` | int | Stock |
| `trade_date` | date \| null | Pipeline trade date at trigger time |
| `trigger_value` | float \| null | Actual value at trigger |
| `message` | string | Human-readable trigger message |
| `status` | string | `triggered` / `acknowledged` |
| `created_at` | datetime | When fired |
| `rule_name` | string | Rule name (read responses) |
| `symbol` | string | Stock symbol (read responses) |

## Endpoints

### GET /alerts/rules
List rules (enabled first).

**Query Parameters:**
- `limit` (default: 100, max: 500)
- `offset` (default: 0)

### POST /alerts/rules
Create a rule. `400` for unsupported `rule_type` / `operator`.

**Body:**
```json
{
  "name": "RSI overbought",
  "enabled": true,
  "stock_id": null,
  "watchlist_id": null,
  "rule_type": "rsi",
  "operator": ">",
  "value": 70,
  "notification_type": "in_app",
  "cooldown_minutes": 0
}
```

### GET /alerts/rules/{rule_id}
Rule detail. `404` if not found.

### PUT /alerts/rules/{rule_id}
Update provided fields only (e.g. toggle `enabled`).

### DELETE /alerts/rules/{rule_id}
Delete the rule (cascades to its events). `204` on success.

### POST /alerts/run
Run the engine immediately against the latest state.

**Response:**
```json
{ "checked": 12, "triggered": 2 }
```
`checked` counts evaluated (rule, stock) pairs; `triggered` counts newly created events.
Rules suppressed by `cooldown_minutes` are checked but not triggered.

### GET /alerts/events
Recent events.

**Query Parameters:**
- `limit` (default: 50, max: 200)
- `offset` (default: 0)
- `status` (optional filter: `triggered` / `acknowledged`)

### GET /alerts/events/pending
Events with status `triggered`.

### GET /alerts/events/symbol/{symbol}
Events for a symbol.

### PATCH /alerts/events/{event_id}
Update event status.

**Body:**
```json
{ "status": "acknowledged" }
```
`404` if the event is not found.

## Engine behavior
- Runs automatically as the final stage of the EOD pipeline (after analytics) and records
  `alerts_triggered` on the pipeline run
- Evaluates the latest `TechnicalAnalysis`, `IndicatorResult` (latest + previous), and
  `DailyPrice` rows per stock
- Events are persisted in `alert_events`; no external notifications are sent yet

## Status Codes
- `200 OK`: Success
- `204 No Content`: Delete succeeded
- `400 Bad Request`: Unsupported rule type/operator or invalid body
- `404 Not Found`: Rule/event not found
- `422 Unprocessable Entity`: Invalid body
