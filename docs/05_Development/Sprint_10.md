# Sprint 10: Stock Detail Stabilization & Scanner Persistence

## Duration
TBD

## Status
In progress

## Objectives
- Stabilize the stock detail feature and make the test suite reliably green
- Persist scanner results so the workspace/dashboard show real data
- Remove N+1 and heavy-query hotspots
- Targeted cleanup and documentation refresh

## Deliverables
- [x] Stock detail works for stocks with and without price history (`sector_name` always present)
- [x] `POST /api/v1/scanners/run` and `/run-all` persist matches to `scan_results`
- [x] Repeated scanner execution is idempotent (PostgreSQL `ON CONFLICT DO NOTHING`)
- [x] Transaction rolls back on persistence failure
- [x] Conftest scan-definition seeding is idempotent (repeatable, order-independent)
- [x] Chart marker query has no per-marker DB lookup (N+1 removed)
- [x] Dashboard scanner summary uses a single grouped query
- [x] Indicator status uses aggregate counts instead of full result-set loads
- [x] `IndicatorResult` added to `app/models/__init__.py` `__all__`
- [x] HTMX partials (overview/indicators/scanner-matches) wired with refresh triggers
- [x] Sidebar/navbar no longer link to JSON endpoints
- [x] fpdf2 `dest="S"` deprecation removed
- [x] Duplicate `python-multipart` dependency removed
- [x] Regression tests added (no-price stock, scanner persistence, idempotency)

## Definition of Done
- [ ] pytest passes 3 consecutive times
- [ ] ruff passes
- [ ] No MissingGreenlet
- [ ] No `sector_name` KeyError
- [ ] Scanner results persist
- [ ] Repeated scanner execution is idempotent
- [ ] Chart marker query has no N+1
- [ ] Dashboard scanner summary uses grouped query
- [ ] Stock detail works with no price rows
- [ ] Manual route verification succeeds

## Notes
- No migration created (no schema change)
- `app/scanner/` dead code intentionally left in place
- IndicatorEngine auto-run after import deferred to Sprint 11
