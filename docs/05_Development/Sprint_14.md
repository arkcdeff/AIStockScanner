# Sprint 14: Portfolio, Watchlists & Alert Rules

## Duration
TBD

## Status
Complete

## Objectives
- Build watchlist management (create/edit/delete lists, add/remove stocks, search)
- Build portfolio tracking (portfolios, holdings, live valuations, P&L summaries)
- Build an alert rules engine that evaluates the latest analytics/indicators/prices after
  the EOD pipeline analytics stage and persists alert events (in-app only, no notifications)
- Expose JSON APIs + HTML/HTMX pages for all three features, wire into the dashboard and
  stock detail page, and cover everything with tests

## Deliverables
- [x] Models: `Watchlist`, `WatchlistStock`, `Portfolio`, `Holding`, `AlertRule`, `AlertEvent`
      (+ relationships on `Stock`, `alerts_triggered` metric on `EODPipelineRun`)
- [x] Migration `fd96a19d8b61` (chains from `f0a1b2c3d4e5`) — applied to dev + test DBs
- [x] Repositories + services: `app/services/watchlist.py`, `portfolio.py`, `alert.py`
- [x] Alert engine (`AlertService.run_for_trade_date`) supporting `overall_score`, `rsi`,
      `ema_cross`, `delivery`, `volume`, `breakout`, `support_broken`, `resistance_broken` —
      with cooldown via `last_triggered` and persisted `AlertEvent` rows
- [x] Schemas: `app/schemas/watchlist.py`, `portfolio.py`, `alert.py`
- [x] JSON APIs: `/api/v1/watchlists/...`, `/api/v1/portfolios/...`, `/api/v1/alerts/...`
- [x] HTML pages + HTMX partials: `/watchlists`, `/portfolio`, `/alerts`
- [x] Dashboard cards (portfolio summary, watchlist summary, alert summary) +
      stock detail panels (watchlist, portfolio position, matching alerts)
- [x] EOD pipeline alert stage runs after analytics (`metrics["alerts_triggered"]`)
- [x] 41 new tests (services, APIs, UI rendering, engine rules, pipeline integration)

## Definition of Done
- [x] `uv run alembic upgrade head` passes on dev + test DBs
- [x] `uv run ruff check .` passes
- [x] `uv run pytest -q` passes 3 consecutive times (170 tests)
- [x] Watchlist APIs verified (CRUD, add/list/remove stocks, 404)
- [x] Portfolio APIs verified (CRUD, holdings, summary, valuation, 404)
- [x] Alert APIs verified (rules CRUD, run, events, pending, ack)
- [x] `/watchlists`, `/portfolio`, `/alerts` pages render
- [x] Dashboard + stock detail panels render
- [x] Documentation updated (CHANGELOG, PROJECT_STATE, Sprint_14, Watchlist/Portfolio/Alert APIs)

## Notes
- No authentication — single local user; the schema (no `user_id`) keeps per-user tables
  straightforward to add later
- Alert events are stored in-app only; notification delivery is a future sprint
- Alert rules operate deterministically on `technical_analysis`, `indicator_results`, and
  `daily_prices` — the engine is the only writer of `alert_events`
