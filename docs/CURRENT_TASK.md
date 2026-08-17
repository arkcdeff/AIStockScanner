# Current Task

Last updated: Sprint 18 Task 18.12 (Scheduler + Notifications + v0.14.0 Release) — COMPLETE & VERIFIED.

## In progress / status

- Task 17.1 — COMPLETE & verified (persistence/domain foundation).
- Task 17.2 — VERIFIED (historical NSE fetch + replay; manual local-file fallback confirmed).
- Task 17.3 — COMPLETE (simulation engine; two cost-math bugs fixed in `backtest_simulator.py`;
  23 tests in `tests/test_backtest_simulator.py`).
- Task 17.4 — COMPLETE & VERIFIED (performance metrics engine fully implemented, CLI-wired,
  40 tests in `tests/test_backtest_metrics.py`; benchmark TODO resolved; risk-free = 0).
- Task 17.5 — COMPLETE & VERIFIED (backtesting REST API: strategies, runs, synchronous
  execution, results; 13 routes / 9 paths; 16 tests in `tests/test_backtest_api.py`). Simulator
  settlement fix (entry commission no longer double-counted; 4 SETTLEMENT tests added;
  27 simulator tests). `backtests.read/run/write` permission codes added (25 → 28).
  `backtest_metrics.py` hardened to read committed trades/equity directly from the database.
- Task 17.6 — COMPLETE & VERIFIED (browser backtesting pages: workspace, strategies, runs,
  run detail; HTMX partials for metrics/trades/history/strategy-form; Lightweight Charts equity/drawdown;
  trade table; run-status locking; read/write/run permissions; CSRF on writes; run ownership;
  Indian-formatted currency; 29 tests in `tests/test_backtest_ui.py`. No migration.)
- Task 17.7 — **COMPLETE & VERIFIED (v0.13.0 release).** All release gates passed
  (alembic head `d7b4cc79fd45` clean; ruff clean; `git diff --check` clean; 570 passed ×3).
  Version bumped to `v0.13.0` in `app/main.py`; CHANGELOG v0.13.0 added and the inaccurate
  v0.12.1 version-bump claim corrected; Permission Matrix reconciled to the 28-code catalog;
  annotated tag `v0.13.0`; `sprint-17-backtesting` merged into `main` (fast-forward); pushed.
- CLI regression — **FIXED** (create_admin Typer/legacy dual mode; main() guards added).
- Task 18.1 — COMPLETE & VERIFIED (Market Intelligence Foundation: models, repositories,
  schemas, migration `25e6f159a646`, provider interfaces, freshness/status conventions,
  configuration, 72 foundation tests).
- Task 18.2 — **COMPLETE & VERIFIED (Indian Index Data & Price Action).**
  - `IndexPriceAction` engine (async, deterministic, no-look-ahead) computing: OHLC,
    previous close, daily change %, gap points/%, gap classification, 1/5/20/60-session
    returns, SMA20/50/100/200, above/below SMA, distance from SMA, trend classification,
    momentum, 52-week high/low, distance from 52w high/low, position in 52w range,
    daily range %, 20-session average daily move, price_action_score (−100..+100),
    score breakdown. Missing values remain `None`; never fabricated as zeros.
  - Repository no-look-ahead fix: `get_date_range_by_index_code`/`get_date_range` now
    enforce `trade_date <= end_date` (requested evaluation date) instead of the wall clock.
  - `_fetch_history` now fetches a 400-calendar-day lookback bounded by the requested date
    so multi-session features (returns, SMA, 52-week) are computable historically.
  - SENSEX honesty: SENSEX is a BSE index; the NSE provider does NOT claim to support it.
    `NSEIndexProvider` returns an honest `unavailable` result for SENSEX and
    `supports_indices` contains only NSE indices. No unofficial scraping or anti-bot bypass.
  - Tests rewritten from stubs into real integration tests: `tests/test_index_price_action.py`
    (20 tests) + `tests/test_indian_index.py` (7 tests) = 27 Task 18.2 tests.
  - Mandatory no-look-ahead regression test included
    (`test_no_look_ahead_historical_calculation_unchanged_by_future`).
  - DB isolation verified: sessions closed properly, tests repeatable in any order.
- Task 18.3 — **COMPLETE & VERIFIED (Global & Asian Markets).**
  - Canonical 14-index catalog retains all seven Indian indices and adds exactly SP500,
    NASDAQ_100, DOW_JONES, NIKKEI_225, HANG_SENG, SHANGHAI_COMPOSITE, and KOSPI in the
    existing `market_indices` domain/table. Shanghai uses the Stooq index symbol `^shc`.
  - Separate injected `GlobalIndexProvider`; `NSEIndexProvider` remains NSE-only and does not
    fabricate SENSEX. Status, freshness, Decimal OHLCV, and source provenance are normalized
    through the existing provider and `MarketIndexPrice` contracts.
  - `IndexHistoricalService` supports a single date, ranges, and multiple indices with ordered
    provider fallback, exact DB cache, stale DB fallback, missing outcomes, boundary previous-close
    derivation, idempotent inserts, and one range HTTP call per provider/index (never date×index).
  - Native exchange session cutoffs use aware timestamps plus `zoneinfo`, including US DST.
    `GlobalMarketIntelligence` performs one bulk history query, reuses `IndexPriceAction`, and
    returns deterministic US/Asian/global scores, breadth, breakdown, completeness, and confidence.
  - A short-history trend defect discovered in final self-review was fixed: absent SMA200 no longer
    defaults to `strong_bearish`; available SMA20 evidence is used and otherwise trend is neutral.
  - Task 18.3 tests use injected fakes and `httpx.MockTransport` only—zero external HTTP.
- Task 18.4 — **COMPLETE & VERIFIED (GIFT Nifty Intelligence).**
  - Canonical catalog now has 15 entries and adds only the actual USD-denominated NSE IX NIFTY 50
    index futures identity: `GIFT_NIFTY`, exchange `NSE_IX`, instrument `FUTIDX`, provider symbol
    `NIFTY`. The existing seven-code global intelligence set remains unchanged; no migration.
  - Official-source audit found no documented stable unauthenticated NSE IX quote API or automation
    contract suitable for safe implementation. `GiftNiftyProvider` performs zero HTTP, returns
    truthful `unavailable` without an explicit local CSV, and rejects wrong exchange/instrument/
    underlying/currency, SGX/proxy symbols, invalid prices, naive/future timestamps, and bad files.
  - Current audited NSE IX sessions are centralized as 06:30–15:40 and 16:35–02:45 next day IST.
    `GiftNiftyIntelligence` uses an aware evaluation instant, latest completed knowable NIFTY cash
    close, one bounded bulk query, provider→exact stored→stale stored→unavailable fallback, exact
    gap formulas, five direction labels, bounded score/confidence/completeness, contexts, provenance,
    and limitations. Momentum remains `None` without legitimate same-contract timestamped history.
  - Stored `MarketIndexPrice` fallback is date-only and conservatively excludes same-day GIFT rows;
    timestamp replay is not claimed. Mandatory future-row invariance proves no look-ahead at T.
  - 67 focused tests pass (25 provider/catalog + 42 intelligence/session), with zero real HTTP.
- Task 18.5 — **COMPLETE & VERIFIED (FII/DII + India VIX + USD/INR + Brent Intelligence).**
  - `InstitutionalIntelligence` accepts only canonical FII/FPI and DII provisional cash-market
    observations in `INR_CRORE`; buy/sell use Decimal math and net is always recomputed. It returns
    daily/3/5/20-session investor values, combined daily/5/20 values, FII 65%/DII 35% weighted
    independent score, five direction labels, score breakdown, completeness, and confidence.
  - `ExternalMarketFactorIntelligence` requires exact NSE `INDIA_VIX`, FX spot `USD_INR_SPOT`
    quoted `INR_PER_1_USD`, and physical spot `EUROPE_BRENT_SPOT` in `USD_PER_BARREL`. It rejects
    proxies, reversed FX orientation, futures/expiries, WTI, and MCX substitutions.
  - VIX provides 1/5/20-session returns, a 20-session-minimum percentile/rank, five level regimes,
    and a bounded India-risk-context score. USD/INR and Brent provide 1/5/20/60-session returns,
    moving averages, trend/direction, and independent bounded India-context scores. These are
    factors only; no final composite or market regime was implemented.
  - No suitable common documented unauthenticated automated source contract was established.
    Dedicated providers therefore perform zero HTTP, are unavailable by default, and accept only
    explicit exact-identity local data with aware observation/availability timestamps and source
    reference. Service-boundary validation is repeated for defense in depth.
  - Fallback is primary provider → secondary provider → exact stored observation → stale stored
    observation → unavailable. Provider exceptions continue fallback; missing is never zero; stale
    is never live; provider/DB overlap is de-duplicated by date with provider precedence.
  - Every evaluation timestamp is aware. Conservative completed-session/date-only cutoffs and
    knowability filtering prevent future leakage, including malformed future local-file rows.
    Mandatory future-invariance regressions pass for FII/DII, India VIX, USD/INR, and Brent.
  - Both intelligence services use one bounded bulk DB query and one provider call per supported
    requested range; no N+1 and no service-owned commit/rollback. Existing models/repositories are
    reused, the catalog has 18 entries, and no migration was created.
  - 31 Task 18.5 tests pass: 13 institutional + 18 external-factor. Tests use no live HTTP,
    secrets, or placeholder success responses.
- Task 18.6 — **COMPLETE & VERIFIED (Market Breadth).** Deterministic breadth from advances,
  declines, unchanged, A/D ratio, percent advancing, new-high/low balance, SMA20/50/200
  participation, and momentum/divergence via `app/services/market_breadth.py` with centralized
  `breadth_config.py` constants. Canonical state/direction thresholds at +60/+25/−25/−60
  (not 70/25/−25/−70). Bounded breadth score −100..+100, five directions, completeness,
  confidence, and a single bounded bulk query. 11 tests in `tests/test_market_breadth.py`;
  exact collection 821; full suite ×3; no migration.
- Task 18.7 — **COMPLETE & VERIFIED (Sector Strength & Rotation).** Sector 1D/5D/20D returns,
  relative performance vs NIFTY, trend, rank, leaders/laggards, participation, and rotation state
  via `app/services/sector_intelligence.py` with the canonical sector universe. Deterministic
  bounded sector score and direction. 9 tests in `tests/test_sector_intelligence.py`; exact
  collection 830; full suite ×3; no migration.
- Task 18.8 — **COMPLETE & VERIFIED (Macro & Event Intelligence).**
  - Declarative catalog `app/market_intelligence/macro_event_catalog.py` — exactly **34** tracked
    events (18 core + 16 sector) from constants (never a hardcoded number), each with category,
    importance, frequency, direction (normal/inverted), consensus-forecast availability, timezone,
    unit, and a declarative `surprise_full_scale`; self-validated at import (unique codes, disjoint
    core/sector, valid importance/direction positive scale).
  - Focused `EconEvent` model + **one Alembic migration** `5f7b2c9d4e3a` (revises `25e6f159a646`)
    creating `econ_events` (unique `(event_code, reference_time)`, Numeric actual/forecast/previous,
    aware timestamps, provenance/source fields). Applied to dev and test DBs; `alembic check` clean.
  - `MacroEventProvider` — zero-HTTP, **unavailable by default**, accepts only explicit validated
    local CSV with catalog/unit/identity validation, aware timestamps, and a required source
    reference. No live HTTP in tests or production default path.
  - `MacroIntelligence.evaluate(aware_timestamp)` — deterministic engine: lifecycle
    `upcoming`/`known`/`scheduled_not_knowable`/`missing`; surprise = actual − forecast (else
    actual − previous) normalized to ±100 and sign-flipped for inverted events (inflation,
    unemployment); importance-weighted domains monetary/inflation/growth/employment/other;
    renormalized `macro_score` −100..+100 with five directions at exact **+60/+25/−25/−60**
    (`strong_supportive/supportive/neutral/restrictive/strongly_restrictive`); upcoming events add a
    deterministic event-risk dampener that never reverses direction; completeness/confidence with
    contradiction handling; provenance and limitations; one bounded bulk query + one provider call.
  - Mandatory no-look-ahead: rows are eligible only when observed_at and available_at are on/before
    evaluation_at; future-invariance regressions pass.
  - 24 tests: 8 `test_macro_event_provider.py` + 16 `test_macro_intelligence.py`; exact collection
    854; full suite **854 ×3**; ruff clean; alembic head `5f7b2c9d4e3a`; `app.main` imports.
- Task 18.9 — **COMPLETE & VERIFIED (Market Regime & Composite Intelligence).**
  - Orchestrator `app/services/composite_market_intelligence.py` (`CompositeMarketIntelligence`)
    consumes every upstream factor engine (Tasks 18.2–18.8) at the SAME evaluation boundary and
    never re-implements a factor formula; emits the full Task 18.9 contract on
    `CompositeMarketIntelligenceResult`: `regime`, `composite_score`, `confidence_score`,
    `bullish_factors`, `bearish_factors`, `risk_factors`, `missing_factors`, plus
    `contradictions`, `stale_factors`, `agreement_percent`, and provenance.
  - Pure core `compute_composite_factor_result()` — deterministic weighted composite (−100..+100),
    five regime bands at exact +60/+25/−25/−60, weighted completeness, confidence blend
    (completeness + factor-confidence + freshness + agreement with a low-confidence reliability
    dampener so agreement can never inflate confidence), direction-weighted agreement,
    high-`volatility` flag, deterministic contradiction pairs, and risk-factor rules
    (HIGH_VIX, STALE_MAJOR_DATA, LOW_COMPLETENESS, EXTREME_NEGATIVE_*). Missing factors stay
    `None`; never fabricated as zero.
  - Weights are centralized/configurable via `MarketIntelligenceScoreConfig` (10 factors, sum 1.0).
  - `evaluate(..., persistence=True)` upserts one `MarketIntelligenceSnapshot` row per
    `market_date` (idempotent), persisting per-factor scores, composite, confidence, regime,
    completeness, and bullish/bearish/risk/missing JSON; returns `snapshot_id`.
  - Mandatory no-look-ahead: factor engines and the price-action bulk read are bounded by the
    evaluation `market_date` (never wall clock); a future-dated price/snapshot row cannot alter a
    historical evaluation.
  - Tests: 38 pure composite tests (`tests/test_composite_market_intelligence.py`) covering all
    nine Task 18.9 scenarios — strongly bullish, bullish, mixed/neutral, bearish, strongly
    bearish, high volatility, missing data, contradictory factors, and stale data — plus regime
    exact-boundaries, weight/contribution accounting, determinism, and factor-count/name
    validation; 8 DB-backed integration tests
    (`tests/test_market_intelligence_composite_integration.py`) for snapshot persistence/upsert
    idempotency and no-look-ahead future-invariance. Foundation suite (72) preserved.

## Verification status

- alembic current/heads/check: clean (`5f7b2c9d4e3a (head)`, single head, no new upgrade operations)
- ruff: `All checks passed!`
- pytest ×3: **900 passed** each run, only unrelated
  Starlette/httpx deprecation warnings
- Task 18.8 focused tests: 24 passed (8 provider + 16 intelligence)
- Task 18.9 focused tests: 46 passed (38 pure composite + 8 DB-backed integration)
- Task 18.9 nine mandatory scenarios covered: strongly bullish, bullish, mixed/neutral,
  bearish, strongly bearish, high volatility, missing data, contradictory factors, stale data
- Task 18.9 no-look-ahead future-invariance: passed (repository + orchestrator)
- Task 18.10 focused tests: **61 passed** (`tests/test_1810_ai_market_explanation.py`)
- Task 18.10 28 mandatory categories covered: all 1-28 per 18.10M specification
- Task 18.10 deterministic fallback repeatability: passed
- Task 18.10 anti-hallucination (regime/score/confidence override rejected): passed
- Task 18.10 sanitized provider errors (sk-***, password=***): passed
- Task 18.10 compact JSON prompts, token/cost control: passed
- Task 18.10 historical no-look-ahead (market_date bounded evaluation): passed
- Task 18.10 provider fallback order (primary AI → deterministic): passed
- Task 18.1 foundation preservation suite: 72 passed
- Task 18.6 (breadth) + Task 18.7 (sector) focused tests: 11 + 9 passed
- Tasks 18.1–18.8 preservation suite: passed
- Exact pytest collection: 961 tests (900 preserved + 61 Task 18.10)
- Task 18.5 focused tests: 51 passed (including shared price-action regressions); 31 dedicated
  Task 18.5 cases (13 institutional + 18 VIX/FX/Brent)
- Task 18.4 focused tests: 67 passed (25 provider/catalog + 42 intelligence/session)
- Task 18.4 mandatory future-invariance regression: passed independently
- Task 18.3 focused tests: 43 passed (24 provider/ingestion + 19 intelligence)
- Combined Task 18.2/18.3 calculation/provider tests: 63 passed
- Task 18.2 focused tests: 27 passed (7 in `test_indian_index.py`, 20 in `test_index_price_action.py`)
- Focused tests also pass in reverse order (DB isolation verified)
- `app.main` imports cleanly
- `git diff --check`: clean (only benign LF→CRLF EOL-normalization warning)
- OpenAPI: 241 paths / 278 routes; backtests REST 9 paths + UI 17 paths
- Security: no tracked secrets, `.env` gitignored, `SecretStr` fields, autoescape + CSRF verified

## Next action

Sprint 18 — Market Intelligence — Task 18.12 (Scheduler + Notifications +
v0.14.0 Release) is **COMPLETE & VERIFIED** and handed off.

Sprint 18 — **COMPLETE**.

v0.14.0 — **RELEASED**.

No Sprint 19 work started.

## Key files

## Key files

- `app/services/index_price_action.py` — Task 18.2 price-action engine (async, deterministic,
  no-look-ahead; weighted score bounded −100..+100; missing values stay None).
- `app/repositories/market_index_price.py` — no-look-ahead repository (bounds by requested
  end_date, never wall clock).
- `app/market_intelligence/providers/nse_index_provider.py` — NSE provider; SENSEX honestly
  reported `unavailable` (BSE index), only NSE indices in `supports_indices`.
- `tests/test_index_price_action.py` — 20 real engine tests incl. mandatory no-look-ahead
  regression test.
- `tests/test_indian_index.py` — 7 tests: catalog, freshness/status, SENSEX=BSE, NSE provider
  SENSEX unavailable.
- `app/market_intelligence/index_catalog.py` — canonical Indian/US/Asian index catalog and
  idempotent existing-table seeder.
- `app/market_intelligence/providers/global_index_provider.py` — separate Stooq-compatible
  global adapter with injected HTTP/clock and truthful status/freshness outcomes.
- `app/services/index_historical.py` — provider-neutral date/range/multi-index ingestion and
  primary→secondary→DB fallback.
- `app/market_intelligence/session_dates.py` — aware, DST-safe native market session cutoffs.
- `app/services/global_market_intelligence.py` — deterministic individual/regional/global scoring,
  breadth, breakdown, completeness, and confidence using one bulk history load.
- `tests/test_global_index_provider.py`, `tests/test_global_market_intelligence.py` — 43 Task 18.3
  tests including mocked HTTP, fallback/idempotency, native timezone, no-look-ahead, disagreement,
  missing/stale data, provenance, and score bounds.
- `app/market_intelligence/providers/gift_nifty_provider.py` — no-HTTP, unavailable-by-default,
  exact-identity local CSV provider for actual NSE IX GIFT Nifty futures.
- `app/services/gift_nifty_intelligence.py` — deterministic GIFT/NIFTY reference gap, direction,
  score, confidence, completeness, fallback, provenance, limitations, and no-look-ahead engine.
- `app/market_intelligence/session_dates.py` — current split GIFT Nifty sessions and completed Indian
  cash-reference/date-only fallback cutoffs at aware evaluation instants.
- `tests/test_gift_nifty_provider.py`, `tests/test_gift_nifty_intelligence.py` — 67 Task 18.4 tests,
  including exact identity/proxy rejection, session boundaries, and mandatory future invariance.
- `app/services/institutional_intelligence.py` — deterministic FII/FPI and DII flow rollups,
  weighting, scoring, confidence/completeness, fallback, provenance, and no-look-ahead.
- `app/services/external_market_factor_intelligence.py` — independent India VIX, USD/INR spot,
  and Europe Brent physical-spot returns, regime/trend/direction, scores, fallback, and replay safety.
- `app/market_intelligence/providers/institutional_flow_provider.py` and
  `manual_market_factor_provider.py` — zero-HTTP exact-identity local providers with aware
  knowability validation and bounded range reads.
- `tests/test_institutional_intelligence.py`, `tests/test_fx_crude_intelligence.py` — 31 dedicated
  Task 18.5 tests covering formulas, identity, fallback, exceptions, freshness, and future invariance.
- `app/market_intelligence/macro_event_catalog.py` — Task 18.8 declarative 34-event catalog
  (18 core + 16 sector) with category/importance/frequency/direction/has_forecast/timezone/unit and
  declarative `surprise_full_scale`, self-validated at import.
- `app/models/macro_event.py` + `app/repositories/macro_event.py` — `EconEvent` model and
  `EconEventRepository` (single bounded bulk `get_range_for_codes` query + upsert).
- `migrations/versions/5f7b2c9d4e3a_add_econ_events_table.py` — Task 18.8 migration (head, revises
  `25e6f159a646`) creating one focused `econ_events` table.
- `app/market_intelligence/providers/macro_event_provider.py` — zero-HTTP, unavailable-by-default,
  validated local CSV provider (`validated_manual_macro_events`).
- `app/services/macro_intelligence.py` — deterministic `MacroIntelligence` engine + `classify_macro_direction`.
- `tests/test_macro_event_provider.py`, `tests/test_macro_intelligence.py` — 8 + 16 Task 18.8 tests
  (catalog, thresholds, no-HTTP, unit/identity validation, aware timestamps, surprise/inverted
  semantics, upcoming risk, no-look-ahead invariance, provider/stored merge, one-query efficiency).
- `app/models/{market_index,market_index_price,institutional_flow,market_intelligence_snapshot}.py`,
  `app/repositories/*`, `app/schemas/*`, `app/market_intelligence/*`, `migrations/versions/25e6f159a646*`,
  `tests/test_market_intelligence_foundation.py` — Task 18.1 Market Intelligence Foundation (72 tests).
- `app/services/composite_market_intelligence.py` — Task 18.9 composite orchestrator +
  pure `compute_composite_factor_result()` core (regime/composite/confidence/factors/risks/
  contradictions) and `CompositeMarketIntelligence.evaluate()` with idempotent snapshot upsert.
- `tests/test_composite_market_intelligence.py` — 38 pure Task 18.9 composite tests (nine mandatory
  scenarios + regime boundaries, weights/contributions, determinism, validation).
- `tests/test_market_intelligence_composite_integration.py` — 8 DB-backed integration tests
  (snapshot persistence/upsert idempotency + no-look-ahead future-invariance).-   ` a p p / s e r v i c e s / a i _ e x p l a n a t i o n _ p r o v i d e r . p y `   � �    T a s k   1 8 . 1 0   p r o v i d e r   a b s t r a c t i o n ,   p r o m p t   b u i l d e r ,  
     ` A I E x p l a n a t i o n R e s u l t `   c o n t r a c t ,   ` s a n i t i z e _ p r o v i d e r _ e r r o r `   ( s k - * * * ,   p a s s w o r d = * * * ) ,   ` v a l i d a t e _ a i _ o u t p u t `  
     ( a n t i - h a l l u c i n a t i o n :   r e g i m e / s c o r e / c o n f i d e n c e   o v e r r i d e   r e j e c t e d ) .  
 -   ` a p p / s e r v i c e s / a i _ m a r k e t _ e x p l a n a t i o n . p y `   � �    T a s k   1 8 . 1 0   ` A I M a r k e t E x p l a n a t i o n S e r v i c e `   +  
     ` M a r k e t E x p l a n a t i o n `   o u t p u t   m o d e l ;   d e t e r m i n i s t i c   f a l l b a c k   ( a l w a y s   a v a i l a b l e ,   z e r o   c o s t ,   z e r o   H T T P ) ;  
     ` m a r k e t _ d a t e `   b o u n d e d   e v a l u a t i o n   f o r   h i s t o r i c a l   r e p r o d u c i b i l i t y ;   p r o v i d e r   f a l l b a c k   o r d e r .  
 -   ` t e s t s / t e s t _ 1 8 1 0 _ a i _ m a r k e t _ e x p l a n a t i o n . p y `   � �    6 1   t e s t s   c o v e r i n g   a l l   2 8   m a n d a t o r y   c a t e g o r i e s   p e r  
     1 8 . 1 0 M :   s t r u c t u r e d   i n p u t / o u t p u t ,   d e t e r m i n i s t i c   f a l l b a c k ,   a n t i - h a l l u c i n a t i o n ,   h i s t o r i c a l  
     r e p r o d u c i b i l i t y ,   t o k e n / c o s t   c o n t r o l ,   s e c u r i t y ,   p r o v i d e r   f a l l b a c k ,   n o - l o o k - a h e a d .  
 