# AI Explanation Engine — Architecture

The AI layer is a **read-only narrator over deterministic analytics**. It can never influence
scores, indicators, or scanner results: `AnalyticsEngine` is the single source of truth, and
every AI response embeds the deterministic `technical_score` it was derived from.

## Layout

```
app/ai/
├── base.py                 # BaseAIProvider ABC + AIProviderError
├── provider.py             # get_provider() factory (settings.ai_provider, lru_cached)
├── explanation_service.py  # orchestration: DTO build → prompt → generate/parse → cache → persist
├── prompt_builder.py       # Jinja rendering of DTOs into one structured prompt + prompt hash
├── response_parser.py      # safe JSON extraction + sanitization (AIResponseError)
├── cache.py                # in-memory TTL cache keyed by provider + prompt hash
├── templates/              # stock_summary.md, scanner_summary.md, market_summary.md, watchlist_summary.md
└── providers/
    ├── mock_provider.py    # deterministic, no network (default)
    ├── ollama_provider.py  # /api/generate (local)
    ├── deepseek_provider.py# OpenAI-compatible chat/completions
    └── openai_provider.py  # chat/completions
```

Supporting modules:

```
app/models/ai_explanation.py   # AIExplanation ORM model
app/repositories/ai_explanation.py  # DB read/upsert (cache persistence)
app/schemas/ai.py              # DTOs + validated AI output payloads + API responses
app/services/ai_insights.py    # dashboard AI sections
app/api/routes/ai.py           # GET /api/v1/ai/{stock,target,market-summary}
```

## Request flow (`explain_stock`)

1. Repository loads `Stock`, latest `TechnicalAnalysis`, `DailyPrice`, `IndicatorResult`,
   scanner matches, and pipeline status.
2. ORM rows are converted into **validated Pydantic DTOs** (`AIStockContext`,
   `AIAnalyticsContext`, `AIIndicatorContext`, `AIScannerMatchContext`, `AIPipelineContext`).
3. `prompt_builder` renders the `stock_summary.md` template into one structured prompt and
   computes `sha256(prompt)` as the cache key.
4. In-memory cache is checked (provider + prompt hash). On hit → `source: "cache"`.
5. `ai_explanations` is checked for `(stock_id, trade_date, provider)`; if the stored
   `prompt_hash` matches → parse stored response → `source: "cache"`.
6. Miss → `provider.generate(prompt)`, then `response_parser` validates/sanitizes the output.
7. On `AIProviderError` / `AIResponseError` (provider down, malformed, empty) → deterministic
   fallback explanation (`source: "fallback"`).
8. Payload upserted into `ai_explanations`, committed, returned with `source: "generated"`.

`explain_top_rated` and `explain_market_summary` use the same flow with the
`scanner_summary.md` / `market_summary.md` templates (memory-cached, no per-stock row).

## Provider abstraction

```python
class BaseAIProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...          # used for cache/persistence keys
    @abstractmethod
    def model_name(self) -> str: ...
    @abstractmethod
    async def generate(self, prompt: str, *, timeout_seconds: int) -> str: ...
    @abstractmethod
    async def health(self) -> bool: ...
```

The factory `get_provider()` maps `settings.ai_provider` → provider class. `mock` is the
default and never touches the network, so the app is safe to run with no AI credentials.

## Caching & invalidation

- **In-memory:** `AIMemoryCache` (TTL `AI_CACHE_TTL`) keyed by `ai:{provider}:{prompt_hash}`.
- **Persistent:** `ai_explanations(stock_id, trade_date, provider, model, prompt_hash,
  response, confidence, created_at, updated_at)` unique on `(stock_id, trade_date, provider)`.
- **Invalidation:** the prompt includes trade date, all analytics, indicators, scanner match
  reasons, and pipeline status. Any change in those inputs changes the prompt hash → new cache
  key; a stored row with a different `prompt_hash` is treated as stale and regenerated.

## Safety constraints

- AI never computes indicators, scores, or scanner logic — it only references supplied values.
- API keys are read from environment variables only and never logged or returned.
- AI output is sanitized (control characters stripped, length caps, confidence clamped 0–100).
- No blocking HTTP: all providers use `httpx.AsyncClient` inside the async service.
- Fallback explanations keep the UI and APIs functional when the provider is unavailable.
