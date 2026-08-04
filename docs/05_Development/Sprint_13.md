# Sprint 13: AI Explanation Engine

## Duration
TBD

## Status
Complete

## Objectives
- Build an AI Explanation Engine as a provider-abstraction layer (Ollama / DeepSeek / OpenAI)
- AI explains existing deterministic analytics only — it never computes indicators, scores, or
  scanner logic; `AnalyticsEngine` remains the single source of truth
- Cache AI explanations (in-memory + persistent) and invalidate when underlying data changes
- Expose 3 AI APIs and surface AI sections on the dashboard and stock detail page

## Deliverables
- [x] `app/ai/` package: `base.py` (ABC), `provider.py` (factory), `explanation_service.py`,
      `prompt_builder.py`, `response_parser.py`, `cache.py`, `templates/*.md` (4 templates)
- [x] Async providers: `mock` (default, deterministic, no network), `ollama`, `deepseek`,
      `openai` — all implement `generate()`, `health()`, `model_name()`
- [x] Provider selected from `.env` (`AI_PROVIDER`) — never hardcoded; unknown provider raises
      `AIProviderError`
- [x] Prompt builder receives validated Pydantic DTOs only (Stock / TechnicalAnalysis /
      IndicatorResult / ScannerResults / pipeline status) — no raw SQL/ORM objects
- [x] `response_parser` sanitizes AI output; malformed/empty responses fall back to a
      deterministic plain-language explanation
- [x] `ai_explanations` table (unique on `stock_id, trade_date, provider`) + migration
      `f0a1b2c3d4e5`
- [x] Caching: in-memory TTL (provider + prompt hash) and DB persistence; prompt hash changes
      when trade date / indicators / analytics / scanner results / pipeline rerun change
- [x] APIs: `GET /api/v1/ai/stock/{symbol}`, `GET /api/v1/ai/top-rated`,
      `GET /api/v1/ai/market-summary`
- [x] Stock detail AI card + HTMX partial (`/stocks/{symbol}/ai`)
- [x] Dashboard AI sections: Today's AI Insights, Top Opportunities, Highest Confidence,
      Recent AI Explanations
- [x] 34 new tests (provider factory, prompt builder, response parser, caching, service
      fallbacks, API endpoints, UI rendering) — no real AI calls in tests

## Definition of Done
- [x] `uv run alembic upgrade head` passes on dev + test DBs
- [x] `uv run ruff check .` passes
- [x] `uv run pytest -q` passes 3 consecutive times (129 tests)
- [x] AI endpoints verified (`/api/v1/ai/stock/{symbol}`, `/top-rated`, `/market-summary`)
- [x] Stock detail AI card renders
- [x] Dashboard AI sections render
- [x] Documentation updated (CHANGELOG, PROJECT_STATE, Sprint_13, AI_API, AI_Architecture)

## Notes
- The default provider is `mock`, so the app is safe to run without any AI credentials and
  never makes outbound calls unless explicitly configured
- AI is never the source of truth: all numbers shown come from `AnalyticsEngine` /
  `TechnicalAnalysis`; AI only narrates them
- API keys are only read from environment variables and are never logged or returned
