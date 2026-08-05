# Sprint 15: Notification System

## Release

- Version: `v0.11.0`
- Date: 2026-08-05
- Status: Complete
- Migration head: `22d886d53759`
- Test baseline: 279 tests

## Objective

Deliver a local-first notification system over the existing alert and analytics data without
adding authentication, distributed queues, browser push, real-time data, or a second scheduler.

## Completed tasks

### 15.1 - Database foundation

- Added `NotificationChannel`, `NotificationPreference`, `NotificationDelivery`, and
  `DailySummaryRun`.
- Added migration `22d886d53759` with required foreign keys, indexes, and uniqueness rules.
- Added persistence tests against PostgreSQL.

### 15.2 - Provider layer

- Added provider abstraction, manager, exceptions, and deterministic mock provider.
- Added Telegram, SMTP email, and in-app providers with configuration validation and health
  checks.
- Added concise alert and daily-summary templates with HTML escaping.

### 15.3 - Notification service and retry engine

- Added centralized preference eligibility, quiet hours, rendering, delivery persistence, retry,
  mark-read, duplicate prevention, batch delivery, and stale-send recovery.
- Transient failures follow capped retry rules; permanent failures never retry.
- Provider errors are sanitized before storage or logging.

### 15.4 - REST APIs

- Added notification status, channel/preference CRUD, delivery, history/detail, retry/read,
  provider-test, daily-summary, and scheduler endpoints under `/api/v1/notifications`.
- Added validation, pagination, filtering, friendly conflicts, and masked configuration output.

### 15.5 - UI and integrations

- Added `/notifications`, `/notifications/settings`, and `/notifications/history` using existing
  Bootstrap 5 and HTMX conventions.
- Added targeted HTMX actions and partial refreshes.
- Added compact dashboard and stock-detail notification sections without redesigning either page.

### 15.6 - Scheduler and daily summary

- Added deterministic, read-only, idempotent daily summaries with optional cached AI market text.
- Added delivery and weekday daily-summary jobs to the existing APScheduler instance.
- Both jobs are disabled by default, non-overlapping, coalesced, and use fresh sessions.
- Successful EOD runs trigger notification orchestration safely; notification failure cannot
  change a completed pipeline result.

### 15.7 - Final verification and release preparation

- Confirmed Alembic current equals head (`22d886d53759`).
- Confirmed 44 Sprint 15 API/page operations are registered in OpenAPI.
- Passed 109 focused Sprint 15 tests and three consecutive 279-test full-suite runs.
- Confirmed Ruff is clean.
- Audited the Task 15.1-15.6 commit range for accidental/unrelated changes, temporary files,
  debug code, placeholders, and notification credentials.
- Prepared v0.11.0 changelog, architecture, API, deployment, and project-state documentation.

## Final guarantees

- Secrets are masked from notification APIs and HTML; stored message content is escaped.
- One alert event/channel pair cannot create duplicate delivery rows.
- Retry behavior distinguishes transient and permanent failures and enforces maximum attempts.
- A completed daily summary is returned idempotently and is never resent for the same date.
- Scheduler jobs are disabled by default and do not register twice.
- Notification failures cannot fail an already completed EOD pipeline.
- All notification pages and required HTMX partials return HTML successfully.

## Out of scope

- Authentication and per-user notification ownership
- Browser push
- Redis, Celery, or distributed job execution
- OpenAlgo, backtesting, or real-time data
- Notification UI redesign
