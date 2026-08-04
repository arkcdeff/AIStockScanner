# Sprint 12: Technical Analytics Engine

## Duration
TBD

## Status
Complete

## Objectives
- Build a deterministic technical analytics engine that runs after indicators and scanners
- Compute composite scores, support/resistance, pivot points, and ratings per stock
- Expose analytics APIs and surface results on the dashboard and stock detail page
- Add analytics as the final stage of the EOD pipeline

## Deliverables
- [x] `AnalyticsEngine` service (`app/services/analytics_engine.py`) computing:
      trend / momentum / volume / delivery / volatility / overall scores (0-100)
- [x] Support & resistance (20-day swing levels)
- [x] Classic pivot points (R1-R3, S1-S3, PP) and Fibonacci pivot points
- [x] Relative volume, delivery strength, trend/momentum/volatility ratings
- [x] Trending and breakout flags
- [x] `technical_analysis` table (unique per stock/date) + migration `e1f2a3b4c5d6`
- [x] `analytics_calculated` metric on `eod_pipeline_runs`
- [x] APIs: `GET /api/v1/analytics/{symbol}`, `/top-rated`, `/trending`, `/breakouts`
- [x] EOD pipeline Stage 4: analytics runs after scanners
- [x] Dashboard "Top Rated Stocks" card + analytics metric in pipeline status
- [x] Stock detail Technical Analytics card + HTMX refresh partial (`/stocks/{symbol}/analytics`)
- [x] 20 new tests (scoring, pivots, flags, APIs, pipeline integration, UI rendering)

## Definition of Done
- [x] pytest passes 3 consecutive times
- [x] ruff passes
- [x] No MissingGreenlet
- [x] Dashboard displays analytics
- [x] Stock detail displays analytics
- [x] APIs verified (symbol, top-rated, trending, breakouts, 404)
- [x] Documentation updated

## Notes
- No AI / LLM usage — all scoring is deterministic, rule-based
- Scoring formulas are documented in the Analytics API doc
- `tests/conftest.py` truncates `technical_analysis` alongside other tables
