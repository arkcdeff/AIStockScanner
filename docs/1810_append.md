- `app/services/ai_explanation_provider.py` — Task 18.10 provider abstraction, prompt builder,
  `AIExplanationResult` contract, `sanitize_provider_error` (sk-***, password=***), `validate_ai_output`
  (anti-hallucination: regime/score/confidence override rejected).
- `app/services/ai_market_explanation.py` — Task 18.10 `AIMarketExplanationService` +
  `MarketExplanation` output model; deterministic fallback (always available, zero cost, zero HTTP);
  `market_date` bounded evaluation for historical reproducibility; provider fallback order.
- `tests/test_1810_ai_market_explanation.py` — 61 tests covering all 28 mandatory categories per
  18.10M: structured input/output, deterministic fallback, anti-hallucination, historical
  reproducibility, token/cost control, security, provider fallback, no-look-ahead.