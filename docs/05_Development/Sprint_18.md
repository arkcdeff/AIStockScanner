# Sprint 18 — Market Intelligence System

## Release Target
- **Planned release:** AIStockScanner `v0.14.0`
- **Starting baseline:** Release `v0.13.0`, Branch `main`, HEAD `f305937`, Alembic head `d7b4cc79fd45`, Tests `570 passed`, Permission catalog `28`, Sprint 17 Backtesting: COMPLETE

---

# 1. Sprint 18 Objective

Sprint 18 transforms AIStockScanner from a stock/scanner/backtesting application into a broader **Indian Market Intelligence platform**.

The system should help answer questions such as:

> What is the likely market environment today?
> Is NIFTY currently bullish, bearish, sideways, or uncertain?
> Are global markets supporting or opposing Indian markets?
> What happened in the US and Asian markets?
> What is GIFT Nifty indicating before the Indian open?
> Are FIIs buying or selling?
> Are DIIs offsetting FII flows?
> Is India VIX showing increasing risk?
> Are USD/INR and crude oil supportive or adverse for India?
> Which sectors are leading and which are weakening?
> Is market breadth healthy?
> Is today's move supported by price action, volume, breadth and institutional flows?
> Are Fed/RBI decisions or macroeconomic events creating additional risk?

The system must provide both **DATA** and **EXPLANATION**. It must not simply output an unexplained "BUY" or "SELL".

---

# 2. Core Design Principle

Market intelligence must be built in layers.

```text
RAW MARKET DATA
        ↓
NORMALIZED MARKET DATA
        ↓
FEATURE CALCULATION
        ↓
FACTOR SCORES
        ↓
MARKET REGIME
        ↓
MARKET INTELLIGENCE SCORE
        ↓
EXPLANATION
        ↓
REST API
        ↓
DASHBOARD
```

AI is the **explanation layer**, not the source of financial calculations.

Deterministic code must calculate:
- returns, gaps, trends, breadth, volatility
- institutional flows, correlations, sector strength, factor scores
- regime, composite score

AI may explain those results in natural language.

---

# 3. High-Level Architecture

```text
                    ┌────────────────────────┐
                    │   External Sources     │
                    ├────────────────────────┤
                    │ NSE                    │
                    │ Market index sources   │
                    │ Global market sources  │
                    │ Macro/event sources    │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Data Provider Layer    │
                    │ adapters + validation  │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Normalized Database    │
                    └───────────┬────────────┘
                                │
          ┌─────────────────────┼──────────────────────┐
          ▼                     ▼                      ▼
    Price Action           Global Context       Domestic Context
          │                     │                      │
          ▼                     ▼                      ▼
      Technical             Risk sentiment        FII / DII
      Trend                 Asian markets         India VIX
      Breadth               US markets            India VIX
      Volatility            GIFT Nifty            USD/INR
          │                     │                      │
          └─────────────────────┼──────────────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ Factor Score Engine    │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ Market Regime Engine   │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ Intelligence Engine    │
                    ├────────────────────────┤
                    │ Score                  │
                    │ Confidence             │
                    │ Bullish factors        │
                    │ Bearish factors        │
                    │ Risk factors           │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ AI Explanation Layer   │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ API + Dashboard        │
                    └────────────────────────┘
```

---

# 4. Intelligence Categories

## A. Indian Index Price Action

Primary indices: NIFTY 50, NIFTY BANK, NIFTY FINANCIAL SERVICES, NIFTY MIDCAP, NIFTY SMALLCAP

Factors: daily return, opening gap, intraday range, previous close relationship, 5-day return, 20-day return, moving-average relationships, distance from moving averages, breakout/breakdown, recent high/low, trend structure, volatility, volume where meaningful

---

# 5. Global Market Context

### United States

Potential indices: S&P 500, Nasdaq Composite/Nasdaq 100, Dow Jones

Context: previous US session return, overnight futures if reliable, risk-on/risk-off behavior

### Asia

Potential indices: Nikkei 225, Hang Seng, Shanghai Composite, KOSPI

Regional breadth/context score rather than one market

---

# 6. GIFT Nifty

Potential features: latest level, previous reference level, percentage change, implied gap, timestamp, freshness

**Never treat GIFT Nifty alone as a prediction of the entire session.** It is one input.

---

# 7. FII / DII Institutional Flow

Track: FII cash buying/selling, FII net, DII cash buying/selling, DII net

Derived: one-day flow, 3-day cumulative, 5-day cumulative, 20-day cumulative, persistence, FII/DII divergence

---

# 8. India VIX

Track: current value, daily percentage change, 5-day change, 20-day percentile, volatility regime (LOW/NORMAL/ELEVATED/HIGH/EXTREME)

---

# 9. USD/INR

Track: price, daily return, 5-day return, 20-day trend. Interpretation must remain contextual.

---

# 10. Crude Oil

Potential references: Brent crude, WTI (optional secondary)

Track: daily change, 5-day change, 20-day trend, volatility

---

# 11. Market Breadth

Calculate: advancing stocks, declining stocks, unchanged stocks, advance/decline ratio, percentage advancing, new highs, new lows, moving-average participation

Breadth states: STRONG/POSITIVE/NEUTRAL/WEAK/VERY_WEAK

---

# 11. Sector Strength & Rotation

Track sector indices: NIFTY IT, BANK, AUTO, PHARMA, FMCG, METAL, REALTY, ENERGY, PSU BANK, MEDIA

Calculate: 1D/5D/20D return, relative strength vs NIFTY, trend, rank

Generate: leaders, laggards, sector participation, rotation state

---

# 12. Macro & Event Intelligence

Potential events: RBI policy, inflation, GDP, IIP, budget, major policy announcements (India); Fed meetings, rate decisions, CPI, PPI, payrolls, GDP (US)

Fields: event_type, country, event_date, event_time, importance, actual, forecast, previous, source

Distinguish scheduled event risk from actual event outcome.

---

# 13. Fed / RBI Interpretation

Do not allow AI to invent central-bank impact from headlines.

Structured deterministic context first.

Example: FED FUNDS previous 5.25%, current 5.00%, change -25 bps, market context: rate cut. Likewise RBI REPO RATE.

---

# 14. Market Regime Engine

Classify Indian market into regimes: STRONG_BULLISH, BULLISH, SIDEWAYS, BEARISH, STRONG_BEARISH, HIGH_VOLATILITY, UNCERTAIN

Regime must be deterministic. Inputs: index trend, breadth, volatility, sector participation, institutional flows. Global factors should influence but not completely override domestic market structure.

---

# 15. Factor Score Architecture

Each factor group produces a normalized score, recommended range -100 to +100.

---

# 16. Composite Market Intelligence Score

Combined engine produces final score. Example: +41. Interpretation: +60 to +100 Strong Bullish, +25 to +59 Bullish, -24 to +24 Neutral/Mixed, -25 to -59 Bearish, -60 to -100 Strong Bearish.

**Task implementing scoring MUST validate and document final thresholds rather than blindly copying these values.**

---

# 17. Weighted Scoring

Initial architecture could consider weights: Indian Price Action 25%, Breadth 15%, Sector Strength 10%, FII/DII 10%, India VIX 10%, Global Markets 10%, GIFT Nifty 10%, USD/INR 4%, Crude 3%, Macro/Events 3%. Total 100%.

**These weights are NOT yet production truth.** They should be configuration-driven and tested. Never scatter weights across code. Maintain one centralized scoring configuration.

---

# 18. Confidence Score

Market direction and confidence must be separate.

Example:
```
Market Score: +45
Direction: Bullish
Confidence: 72%
```

Confidence should consider: number of available factors, data freshness, agreement between factors, missing data, contradictory signals. If half the data is unavailable, the system should NOT report high confidence.

---

# 19. Data Freshness

Every intelligence source must record freshness. Important fields: source, observed_at, fetched_at, market_date, status. Possible states: LIVE, RECENT, STALE, MISSING. Dashboard must not present yesterday's value as current without indicating its timestamp/date.

---

# 20. Missing Data Philosophy

Market Intelligence must degrade gracefully. Example: GIFT Nifty unavailable, FII/DII available, VIX available, Global markets available, Breadth available. Engine should calculate using available factors and reduce confidence. Never silently convert missing values to zero. Missing is not neutral.

---

# 21. Provider Architecture

Do not couple business logic directly to websites. Use provider adapters.

Conceptual structure:
```
app/market_intelligence/
    providers/
        base.py
        indian_indices.py
        global_markets.py
        institutional_flow.py
        volatility.py
        currency.py
        commodities.py
        macro_events.py
```

Provider responsibilities: fetch, parse, normalize, validate, return typed result. Providers should NOT calculate final market direction.

---

# 22. Provider Fallback Philosophy

Where legally and technically appropriate:
```
Primary Provider
      ↓ failure
Secondary Provider
      ↓ failure
Cached DB Data
      ↓
STALE/MISSING status
```

Never bypass anti-bot or access controls. Never scrape aggressively. Official/public sources preferred where practical.

---

# 23. Proposed Database Domains

Likely concepts:

## MarketIndex
- id, code, name, country, exchange, category, is_active

## MarketIndexPrice
- market_index_id, trade_date, open, high, low, close, previous_close, change_percent, volume

## InstitutionalFlow
- trade_date, investor_type, buy_value, sell_value, net_value

## MarketBreadth
- trade_date, index_code, advances, declines, unchanged, new_highs, new_lows

## MacroEvent
- country, event_type, event_date, event_time, importance, actual, forecast, previous

## MarketIntelligenceSnapshot
- market_date, generated_at
- price_action_score, breadth_score, sector_score, institutional_score, volatility_score, global_score, gift_nifty_score, currency_score, crude_score, macro_score
- composite_score, confidence_score, regime
- bullish_factors_json, bearish_factors_json, risk_factors_json, missing_factors_json

Reuse existing data tables whenever possible.

---

# 24. Historical Intelligence

`generate_market_intelligence(D)` must use only information available on or before D. Enables future evaluation: "When intelligence score > +50, what did NIFTY do next?" No-look-ahead principles from Sprint 17 should continue.

---

# 26. Intelligence Snapshot

Persist daily intelligence snapshots. Example:
```
Date: 2026-08-12
Regime: BULLISH
Score: +43
Confidence: 76
Factors: Price Action +60, Breadth +70, Global +35, FII/DII -20, VIX +10, ...
```

Allows dashboard history, auditing, historical analysis, future backtesting of the intelligence engine.

---

# 27. Explanation Architecture

Deterministic engine output:
```
regime = BULLISH
score = 43
confidence = 76
bullish: - strong breadth - NIFTY above trend - positive Asian session - positive GIFT Nifty
bearish: - FII selling - crude rising
risk: - Fed event tonight
```

Then AI converts it into readable text. AI must not change the deterministic score. Fallback deterministic text must exist if AI is unavailable.

---

# 28. AI Safety / Hallucination Guard

AI prompt context must contain structured facts. AI must not invent index prices, FII values, Fed rates, RBI rates, VIX, market events, economic releases. If data is unavailable, explanation should say it is unavailable.

---

# 29. Market Intelligence Dashboard

Future page concept:
```
MARKET INTELLIGENCE
12 Aug 2026

Market Bias: BULLISH
Score: +43 / 100
Confidence: 76%

NIFTY: 24,520 +0.74%
BANK NIFTY: +0.48%
GIFT NIFTY: +0.61% - Positive Opening Bias
INDIA VIX: 14.3 -2.1%

GLOBAL CONTEXT: S&P 500 +0.8%, Nasdaq +1.2%, Nikkei +0.7%, Hang Seng +0.3% - Global Bias: POSITIVE

INSTITUTIONAL FLOW: FII -₹1,850 Cr, DII +₹2,200 Cr - Net Institutional: +₹350 Cr

MARKET BREADTH: Advances 36, Declines 14 - A/D ratio 2.85 - Breadth: STRONG

SECTOR LEADERS: IT, Metal, AUTO - LAGGARDS: FMCG, PHARMA

RISKS: FII selling continues, Brent crude rising, Fed event tonight

AI MARKET VIEW: Moderately bullish environment... Domestic price action and breadth remain supportive...
```

---

# 30. Pre-Market Intelligence

Potential generation window: before Indian market open. Report sections: US Close, Asian Markets, GIFT Nifty, USD/INR, Crude, Major Events, Previous Indian Market, FII/DII, Expected Opening Context, Risk Factors. Language: Positive opening bias / Negative opening bias / Mixed / High uncertainty. Do not claim exact future NIFTY movement.

---

# 31. Post-Market Intelligence

After market close: What happened today? Why did NIFTY move? Was breadth supportive? Which sectors led? What did FIIs/DIIs do? Did volatility rise? What should be watched tomorrow? This can eventually feed notifications/daily summaries built in Sprint 15.

---

# 32. Existing System Integration

### Notifications
Possible future: Market regime changed: NEUTRAL → BULLISH

### Backtesting
Future: Test strategy performance by market regime.

### Scanner
Future: Only show bullish scanners during bullish regime.

**Do NOT implement these cross-module enhancements prematurely.**

---

# 33. Sprint 18 Task Breakdown

## Task 18.1 — Market Intelligence Foundation

Build: architecture implementation foundation, required models, repositories, schemas, migration, provider interfaces, freshness/status conventions, configuration, tests. Do NOT fetch every external source yet.

## Task 18.2 — Indian Index Data & Price Action — **COMPLETE & VERIFIED**

Implement: NIFTY, BANK NIFTY, FINNIFTY, MIDCAP, SMALLCAP where reliable. Features: daily return, gap, 5D return, 20D return, trend, moving averages, breakout/breakdown, index price-action score. Must support historical dates without future leakage.

### Task 18.2 Verification Summary

- **Engine**: `app/services/index_price_action.py` — async deterministic `IndexPriceAction`
  computing OHLC, previous close, daily change %, gap points/%, gap classification,
  1/5/20/60-session returns, SMA20/50/100/200, above/below SMA, distance from SMA,
  trend classification, momentum, 52-week high/low, distance from 52w high/low,
  position in 52w range, daily range %, 20-session average daily move,
  `price_action_score` (−100..+100), and score breakdown. Missing values remain `None`.
- **No-look-ahead**: repository `get_date_range_by_index_code`/`get_date_range` bound rows by
  the requested `end_date` (never the wall clock); `_fetch_history` uses a 400-calendar-day
  lookback bounded by the requested date. Mandatory regression test
  `test_no_look_ahead_historical_calculation_unchanged_by_future` passes.
- **SENSEX honesty**: SENSEX is a BSE index. `NSEIndexProvider` does NOT claim to support it;
  `supports_indices` contains only NSE indices and `fetch(index_code="SENSEX")` returns an
  honest `unavailable` result. No unofficial scraping or anti-bot bypass.
- **DB isolation**: engine sessions closed properly; focused tests pass in both orders;
  index seeding idempotent; `provider_symbol` unique constraint unchanged.
- **Tests**: 27 Task 18.2 tests (7 in `tests/test_indian_index.py`, 20 in
  `tests/test_index_price_action.py`). Full suite **669 passed ×3**.
- **Gates**: alembic current = head = `25e6f159a646`; single head; `alembic check` clean;
  `ruff check .` → All checks passed; `app.main` imports; `git diff --check` clean.

## Task 18.3 — Global & Asian Markets — **COMPLETE & VERIFIED**

Implement: US (S&P 500, Nasdaq, Dow), Asia (Nikkei, Hang Seng, Shanghai, KOSPI). Calculate: individual return, regional breadth, global risk score, Asian context score. Provider failures must degrade gracefully.

### Task 18.3 Verification Summary

- **Existing domain extended**: `app/market_intelligence/index_catalog.py` defines one canonical
  14-index catalog (seven existing Indian plus exactly seven required US/Asian indices) and an
  idempotent seeder for the existing `market_indices` table. No parallel model/table was created.
- **Provider separation and honesty**: `GlobalIndexProvider` is a separate Stooq-compatible adapter
  with injected `httpx.AsyncClient` and aware clock. `NSEIndexProvider` remains NSE-only; SENSEX
  remains truthfully BSE/unavailable from NSE. Shanghai Composite uses Stooq symbol `^shc`.
- **Normalization and fallback**: Decimal OHLCV, trade date, source reference, provider status,
  freshness, observed date, and fetched date normalize into the existing `MarketIndexPrice` model.
  `IndexHistoricalService` implements primary → optional secondary → exact DB cache → stale DB →
  missing behavior for a date and provider fallback for ranges.
- **Historical ingestion**: supports a single date, date range, and multiple indices; skips existing
  dates, is idempotent, derives the first imported change from the latest stored close before the
  range, handles empty weekends/holidays/provider failures, and performs one range HTTP request per
  provider/index—not one request per calendar date.
- **Native sessions/no look-ahead**: aware evaluation timestamps are converted with `zoneinfo` to
  US, Japan, Hong Kong, Shanghai, and Korea close policies. US DST is tested. Every history is
  filtered to its native cutoff, and one SQL-bounded bulk query loads all seven markets.
- **Scoring**: existing `IndexPriceAction` formulas are reused through an in-memory repository over
  the bulk result. Centralized Decimal weights/thresholds produce deterministic individual,
  US, Asian, and global scores in −100..+100, regional breadth, component/weight breakdown,
  completeness, and freshness/agreement confidence in 0..100. Missing values remain `None` and
  available weights are renormalized. Short histories no longer default to strong bearish when
  SMA200 is unavailable.
- **Tests**: 43 Task 18.3 tests (24 provider/ingestion + 19 intelligence), all external HTTP replaced
  by injected fakes or `httpx.MockTransport`. Tests cover exact catalog/symbols, failure statuses,
  provider and DB fallback, idempotency, range boundary calculations, native/DST cutoffs,
  cross-timezone behavior, weekends, mandatory no-look-ahead, contradictory regions/confidence,
  missing/stale data, provenance/freshness, deterministic output, and score bounds.
- **Verification**: focused calculation/provider suite 63 passed; full suite **712 passed ×3**;
  `ruff check .` clean; Alembic current = heads = `25e6f159a646`; `alembic check` reports no new
  upgrade operations; `app.main` imports; `git diff --check` clean.
- **Migration created: NONE.** Existing schema was sufficient. `observed_at` and `fetched_at` remain
  date-level fields, so timestamp provenance is retained at date granularity. Provider behavior is
  fixture-contract verified; automated tests intentionally perform no live upstream requests.

## Task 18.4 — GIFT Nifty & Pre-Market Context — **COMPLETE & VERIFIED**

Implement: GIFT Nifty provider, timestamp/freshness, change, implied opening context, pre-market factor score. Combine carefully with overnight US/Asian context. Do not interpret GIFT Nifty as a guaranteed market prediction.

### Task 18.4 Verification Summary

- **Actual instrument only**: canonical `GIFT_NIFTY` identifies USD-denominated NSE IX NIFTY 50
  index futures (`FUTIDX`, exchange `NSE_IX`, underlying `NIFTY_50`, provider symbol `NIFTY`).
  It is not NIFTY cash, domestic NSE futures, historical SGX symbols, US futures, or a proxy.
  The existing catalog/table was extended to 15 entries without changing the seven-code
  `GLOBAL_INTELLIGENCE_INDEX_CODES` set. Migration created: NONE.
- **Source audit/provider decision**: the first-party NSE IX/NSE audit found no documented stable
  unauthenticated quote API or GIFT-specific automated EOD contract/usage grant suitable for safe
  implementation. `GiftNiftyProvider` therefore performs zero HTTP, returns truthful `unavailable`
  when no explicit local CSV is configured, and validates exact instrument identity, positive
  Decimal price, contract/expiry, source reference, and aware observed/available timestamps.
- **Sessions/reference**: centralized current audited NSE IX hours are 06:30–15:40 and
  16:35–02:45 next day IST, with the 15:40–16:35 inter-session closure. Historical timing changes
  are not inferred. NIFTY reference selection uses the latest stored completed cash close knowable
  at the aware evaluation instant, with explicit pre-open, cash-open, post-close, weekend, holiday,
  GIFT-session, inter-session, and unavailable labels.
- **Deterministic intelligence**: `GiftNiftyIntelligence` performs one bounded bulk history query,
  then provider order → exact prior-date stored GIFT row → older stale row → unavailable. It returns
  GIFT observation, NIFTY reference, gap points/percent, five direction labels, bounded score,
  confidence, completeness, fallback/provenance, formulas, thresholds, and limitations. The formulas
  are `gift_nifty_price - nifty_reference_close` and
  `indicated_gap_points / nifty_reference_close * 100`; missing/zero reference remains `None`.
  Momentum remains `None` because one quote/date-only row cannot prove same-contract history.
- **Historical honesty/no look-ahead**: provider observations require both `observed_at` and
  `available_at` at or before T. Same-day date-only GIFT rows are never presented as intraday data;
  stored fallback uses only completed prior dates. `MarketIndexPrice.observed_at`/`fetched_at` remain
  date fields, so timestamp replay is explicitly not claimed. The mandatory future-invariance test
  confirms extreme future GIFT and NIFTY rows cannot change reference, gap, direction, score, or
  confidence computed at T.
- **Tests**: 67 Task 18.4 tests (25 provider/catalog + 42 intelligence/session), with zero real HTTP.
  They cover identity/proxy rejection, unavailable/manual CSV behavior, knowability, session and
  threshold boundaries, reference selection, formulas, score/confidence/completeness bounds,
  malformed provider fallback, DB fallback, contexts, determinism, and no-look-ahead.
- **Verification**: Task 18.4 focused suite 67 passed; Tasks 18.1–18.3 preservation suite passed;
  full suite **779 passed ×3**; `ruff check .` clean; Alembic current = heads = `25e6f159a646` and
  `alembic check` clean; `app.main` imports; `git diff --check` clean apart from benign line-ending
  notices; no Task 18.4 HTTP, secrets, fabricated success, proxy substitution, or placeholders.

## Task 18.5 — FII/DII, Volatility & Macro-Market Inputs — **COMPLETE & VERIFIED**

Implemented: FII/FPI and DII cash-market flows; exact India VIX; exact USD/INR spot quoted as INR
per USD; and exact Europe Brent physical spot. All factors preserve source, reference, unit,
market date, aware provider timestamps where available, fallback level, and freshness.

### Task 18.5 Verification Summary

- **Institutional intelligence**: canonical `FII/FPI` and `DII` provisional cash-market activity in
  `INR_CRORE`; buy/sell are Decimal-normalized and net is always recomputed as `buy - sell`.
  Per-investor daily/3/5/20-session values, combined daily/5/20 values, persistence, bounded
  −100..+100 scores, five direction labels, breakdown, completeness, and confidence are returned.
  Configured investor weights are FII 65% and DII 35%; horizon weights are centralized and exposed.
- **Exact external factors**: catalog/provider validation requires NSE `INDIA_VIX`; FX spot
  `USD_INR_SPOT` with quote convention `INR_PER_1_USD`; and physical spot
  `EUROPE_BRENT_SPOT` in `USD_PER_BARREL`. USD/INR is not INR/USD, DXY, or a futures contract;
  Brent is not WTI, MCX, or a futures expiry.
- **Calculations**: VIX returns cover 1/5/20 sessions and percentile/rank require at least 20
  observations; regimes are very-low (<12), normal (<18), elevated (<25), high (<35), and extreme.
  USD/INR and Brent include 1/5/20/60-session returns, moving averages, trend, market direction,
  independent India-context score, score breakdown, completeness, confidence, and freshness.
  External-factor directions use strong-positive/positive/neutral/negative/strong-negative at
  +60/+25/−25/−60. Higher/rising VIX, INR weakening (rising USD/INR), and rising Brent generally
  contribute negatively; outputs are contextual inputs rather than predictions.
- **Safe source posture/fallback**: no common documented unauthenticated automated contract was
  established for these exact datasets, so dedicated providers perform zero HTTP and accept only
  explicit validated local data. Evaluation follows legitimate primary → legitimate secondary →
  exact stored observation → stale stored observation → unavailable, records every provider attempt,
  catches provider exceptions, never converts missing to zero, and never labels stale data live.
- **Replay/performance**: every evaluation requires an aware timestamp and applies conservative
  completed-session/date-only cutoffs. Provider and stored histories are de-duplicated by trade date
  with provider precedence. Malformed or future-only source rows cannot contaminate historical
  evaluation. Each service performs one bounded bulk DB query and one call per supporting provider
  and requested range, with no date/factor N+1 and no service-owned commit or rollback.
- **Limitations**: stored market-factor provenance is date-granular. Existing
  `InstitutionalFlow` financial columns are legacy Float; intelligence converts through
  `Decimal(str(value))`, while validated provider/import calculations remain Decimal. No final
  market regime or composite score is produced; Task 18.9 owns that integration.
- **Tests**: 31 Task 18.5 cases (13 institutional + 18 VIX/FX/Brent), all without live HTTP,
  secrets, or placeholder data. They cover exact identity/unit validation, formulas and boundaries,
  primary/secondary/DB fallback, partial provider data, exceptions, overlap de-duplication,
  exact-vs-stale-vs-unavailable outcomes, and mandatory future-invariance for FII/DII, India VIX,
  USD/INR, and Brent.
- **Verification**: focused Task 18.5 suite 51 passed; Tasks 18.1–18.5 preservation suite passed;
  exact collection **810 tests**; full suite **810 passed ×3**; `ruff check .` clean; Alembic current
  = heads = `25e6f159a646` and `alembic check` reports no new operations; `app.main` imports;
  `git diff --check` clean apart from benign line-ending notices. Migration created: **NONE**.

## Task 18.6 — Market Breadth Engine — **COMPLETE & VERIFIED**

Implement: advances, declines, unchanged, advance/decline ratio, percentage advancing, new highs/lows where data exists, moving-average participation where possible. Generate deterministic breadth score.

### Task 18.6 Verification Summary

- **Centralized config**: `breadth_config.py` declares SMA windows, high/low windows (20d/52w),
  momentum session windows, participation/high-low/momentum/score weights, divergence
  component scores, and momentum-impulse scales as constants. Canonical breadth state and
  direction thresholds are `STRONG/POSITIVE/WEAK/VERY_WEAK` and
  `strong_bullish/bullish/bearish/strong_bearish` at **+60/+25/−25/−60** (not 70/25/−25/−70).
- **Engine**: `app/services/market_breadth.py` computes advance/decline and momentum participation,
  SMA20/50/200 participation, new-high/low balance, momentum, and divergence; derives a
  deterministic breadth score bounded −100..+100, five direction labels, completeness (0–100),
  confidence (0–100), and a single bounded bulk DB query with no date×factor N+1.
- **Tests**: 11 dedicated cases in `tests/test_market_breadth.py`; exact collection moved from
  810 to 821. Full suite passed.
- **Migration**: none (reuses existing `MarketIndexPrice` and a read-only breadth repository).

## Task 18.7 — Sector Strength & Rotation — **COMPLETE & VERIFIED**

Implement sector index tracking. Calculate: 1D/5D/20D return, relative performance vs NIFTY, trend, rank. Generate: leaders, laggards, sector participation, rotation state.

### Task 18.7 Verification Summary

- **Universe/config**: `sector_universe.py` defines the canonical sector set; `sector_intelligence.py`
  computes sector 1D/5D/20D returns, relative performance versus NIFTY, trend, rank, leaders,
  laggards, sector participation, and rotation state deterministically.
- **Scoring**: deterministic sector score bounded −100..+100 with the canonical direction
  thresholds, completeness, confidence, provenance, and a single bounded bulk query.
- **Tests**: 9 dedicated cases in `tests/test_sector_intelligence.py`; exact collection moved from
  821 to 830. Full suite passed.
- **Migration**: none (reuses existing price and index tables).

## Task 18.8 — Macro & Event Intelligence

Implement structured event layer: RBI, Fed, CPI, GDP, payrolls, major scheduled events. Provide: event calendar, importance, time-to-event, actual/forecast/previous, event risk state. Do not scrape unreliable news indiscriminately.

### Task 18.8 Verification Summary

- **Declarative catalog**: `app/market_intelligence/macro_event_catalog.py` declares exactly **34**
  tracked events (18 core + 16 sector), derived from constants — never a hardcoded number. Core
  covers RBI/Fed monetary policy, India CPI/WPI/GDP/IIP/PMI/PMI-services/trade, and US
  CPI/PPI/payrolls/unemployment/ISM/retail. Each entry states category, importance, frequency,
  direction (`normal` vs `inverted`), consensus-forecast availability, timezone, unit, and a
  declarative `surprise_full_scale`. The catalog self-validates (unique codes, disjoint core/sector,
  valid importance/direction, positive scale).
- **Persistence**: focused `EconEvent` model + **one Alembic migration** `5f7b2c9d4e3a`
  (down_revision `25e6f159a646`) creating `econ_events` with a unique
  `(event_code, reference_time)`, Numeric actual/forecast/previous, aware timestamps, and
  provenance/source fields. `alembic check` reports no new operations; head is single.
- **Provider**: `MacroEventProvider` is a validated no-HTTP adapter, **unavailable by default** and
  accepting only explicit local CSV with event code against the catalog, matching unit, aware
  reference/observation/availability timestamps, finite nullable values, and a required source
  reference. No real HTTP client and no hidden network path.
- **Engine**: `MacroIntelligence.evaluate(aware_timestamp)` classifies each event lifecycle as
  `upcoming` / `known` / `scheduled_not_knowable` / `missing`. Surprise = actual − forecast
  (else actual − previous), normalized by the declarative scale to ±100, sign-flipped for
  inverted-direction events (inflation, unemployment). Upcoming events contribute **no directional
  signal** but raise a deterministic event-risk dampener that never reverses score direction.
- **Domains & score**: monetary, inflation, growth, employment, and an `other` (external/fiscal/
  sector) domain are importance-weighted; `macro_score` is a renormalized weighted blend bounded
  −100..+100 with five directions at exact **+60/+25/−25/−60**
  (`strong_supportive/supportive/neutral/restrictive/strongly_restrictive`).
- **Completeness/confidence**: completeness (0–100) is the fraction of core events that are rowed;
  confidence (0–100) combines completeness, known-event freshness, and signal agreement
  (contradictory domains lower confidence but do not reverse the score direction).
- **No look-ahead & performance**: provider and persisted rows are only eligible when observed_at
  and available_at are on or before evaluation_at; future rows cannot change historical replay
  (mandatory future-invariance regression passes). One bounded bulk DB query
  (`get_range_for_codes`) plus one provider call; no N+1, no service-owned commit/rollback, and no
  `datetime.now()` without the explicit injected clock (evaluation_at is always required).
- **Tests**: 24 dedicated cases across `tests/test_macro_event_provider.py` (8) and
  `tests/test_macro_intelligence.py` (16), all without live HTTP or secrets. They cover catalog
  derivation (34 = 18+16), exact thresholds, provider unavailable-by-default/no-HTTP, unit/identity
  validation, aware-timestamp enforcement, surprise + inverted semantics, growth support,
  monetary/inflation domains, contradiction→confidence, upcoming no-direction + risk, scheduled-
  not-knowable, forecast→previous fallback, future-row invariance, future-availability exclusion,
  provider/stored merge, one-query efficiency, empty-DB missing preservation, and bounds.
- **Verification**: exact pytest collection **854 tests**; full suite **854 passed ×3**; `ruff check .`
  clean; Alembic current = heads = `5f7b2c9d4e3a` with `alembic check` clean; `app.main` imports;
  `git diff --check` clean apart from benign line-ending notices. Migration created: **YES**
  (`5f7b2c9d4e3a`, one focused table).

## Task 18.9 — Market Regime & Composite Intelligence Engine

Produce: regime, composite_score, confidence_score, bullish_factors, bearish_factors, risk_factors, missing_factors. Weights must be centralized/configurable. Tests must cover: strongly bullish, bullish, mixed, bearish, strongly bearish, high volatility, missing data, contradictory factors, stale data.

### Task 18.9 Verification Summary

- **Orchestrator**: `app/services/composite_market_intelligence.py` defines `CompositeMarketIntelligence`,
  which consumes every upstream factor engine (Tasks 18.2–18.8) evaluated at the SAME evaluation
  boundary and never re-implements a factor formula; the pure `compute_composite_factor_result()`
  core emits the full Task 18.9 contract on `CompositeMarketIntelligenceResult`: `regime`,
  `composite_score`, `confidence_score`, `bullish_factors`, `bearish_factors`, `risk_factors`,
  `missing_factors`, plus `contradictions`, `stale_factors`, `agreement_percent`, and provenance.
- **Composite/confidence**: deterministic weighted composite bounded −100..+100 with five regime
  bands at exact **+60/+25/−25/−60**; weighted completeness; a confidence blend of completeness +
  factor-confidence + freshness + agreement with a low-confidence reliability dampener so agreement
  alone can never inflate confidence; direction-weighted agreement; high-`volatility` flag;
  deterministic contradiction pairs; and centralized risk-factor rules (HIGH_VIX,
  STALE_MAJOR_DATA, LOW_COMPLETENESS, EXTREME_NEGATIVE_*). Missing factors remain `None` — never
  fabricated as zero.
- **Weights** are centralized/configurable via `MarketIntelligenceScoreConfig` (10 factors, sum 1.0).
- **Persistence**: `evaluate(..., persistence=True)` upserts one `MarketIntelligenceSnapshot` row per
  `market_date` (idempotent), persisting per-factor scores, composite, confidence, regime,
  completeness, and bullish/bearish/risk/missing JSON, and returns `snapshot_id`.
- **No-look-ahead**: factor engines and the price-action bulk read are bounded by the evaluation
  `market_date` (never wall clock); a future-dated price/snapshot row cannot alter a historical
  evaluation. Mandatory future-invariance regressions pass at both the repository and orchestrator.
- **Tests**: 38 pure composite tests (`tests/test_composite_market_intelligence.py`) covering all
  nine mandatory Task 18.9 scenarios — strongly bullish, bullish, mixed/neutral, bearish, strongly
  bearish, high volatility, missing data, contradictory factors, stale data — plus regime exact
  boundaries, weight/contribution accounting, determinism, and factor-count/name validation; 8
  DB-backed integration tests (`tests/test_market_intelligence_composite_integration.py`) for
  snapshot persistence/upsert idempotency and no-look-ahead future-invariance. Task 18.1 foundation
  suite (72) preserved. Focused 18.9 suite: **46 passed**.
- **Migration**: NONE (reuses existing `MarketIntelligenceScoreConfig`, `MarketIndexPrice`, and
  `market_intelligence_snapshots` domain).

## Task 18.10 — AI Market Explanation

**COMPLETE & VERIFIED.**

Input: structured `CompositeMarketIntelligenceResult` (from Task 18.9). Output: `MarketExplanation` — concise market view, supporting factors, opposing factors, risks, what to watch.

Implementation (`app/services/ai_explanation_provider.py`, `app/services/ai_market_explanation.py`, `tests/test_1810_ai_market_explanation.py`):
- Provider abstraction (`AIExplanationProvider`) with result contract `AIExplanationResult`.
- Centralized prompt builder `build_ai_prompt` (compact JSON, 10 model-instruction rules per 18.10E).
- `sanitize_provider_error` — masks `sk-***` keys and `password=***` secrets.
- `validate_ai_output` anti-hallucination — AI can NEVER override deterministic `regime`, `composite_score`, or `confidence_score`.
- `AIMarketExplanationService` — mandatory deterministic fallback (`MarketExplanation`), always available, zero cost, zero external HTTP.
- `market_date` bounded evaluation for historical reproducibility (no look-ahead).
- Provider fallback order: primary AI → deterministic (guaranteed availability).
- 61 tests covering all 28 mandatory categories per 18.10M (structured input/output, deterministic fallback, anti-hallucination, historical reproducibility, token/cost control, security, provider fallback, no-look-ahead).
- Exact pytest collection: 61; full suite 61 ×3 passed; ruff clean; alembic current==heads==check; git diff --check clean.
- No external AI calls in tests (injected fake providers only).
- Migration: NONE.

## Task 18.11 - Market Intelligence REST APIs & Dashboard - **COMPLETE & VERIFIED**

REST API examples:
- GET /api/v1/market-intelligence/latest
- GET /api/v1/market-intelligence/{date}
- GET /api/v1/market-intelligence/global
- GET /api/v1/market-intelligence/institutional
- GET /api/v1/market-intelligence/breadth
- GET /api/v1/market-intelligence/sectors
- GET /api/v1/market-intelligence/events

Browser: /market-intelligence. Build dashboard using existing: FastAPI, Jinja2, HTMX, Bootstrap, existing chart system. Do NOT introduce another frontend framework.

### Task 18.11 Verification Summary

- REST API: 14 endpoints under `/api/v1/market-intelligence/` with Bearer-auth + `market_intelligence.read/refresh` permissions enforced via router dependencies; refresh endpoints gated on `market_intelligence.refresh`.
- Browser: `/market-intelligence` dashboard + HTMX partials; GET never persists or calls an AI provider (deterministic path); POST `/refresh` requires `market_intelligence.refresh` + CSRF token.
- Pydantic schemas validate all responses; missing values render "Unavailable" (never zero/fabricated).
- Timezone safety: all `utcnow()` replaced with `datetime.now(UTC)` in reachable 18.11 paths.
- Gates: `ruff check .` -> All checks passed; `alembic current`/`heads` -> `5f7b2c9d4e3a (head)` single head; `alembic check` -> No new upgrade operations; `app.main` import OK.
- Tests: 36 focused 18.11 tests pass (`tests/test_market_intelligence_api.py` 22 + `tests/test_market_intelligence_ui.py` 14); full repository suite 998 passed x3 consecutive runs.

## Task 18.12 — Scheduling, Notifications, Final Verification & Release — **COMPLETE & VERIFIED**

### Task 18.12 Verification Summary

- **Scheduler**: 3 market intelligence jobs integrated into existing APScheduler (`EODScheduler`):
  - Pre-market refresh: 08:30 IST, Mon-Fri, `market_intelligence_premarket` job
  - Post-market refresh: 16:15 IST, Mon-Fri, `market_intelligence_postmarket` job
  - Daily snapshot: 17:00 IST, Mon-Fri, `market_intelligence_snapshot` job
  - All jobs: timezone-aware (`Asia/Kolkata`), `max_instances=1`, `coalesce=True`, idempotent evaluation with optional persistence, failure isolation, last-good-data preservation
- **Notifications**: 4 deterministic triggers reusing Sprint 15 notification infrastructure:
  - Regime change: detects transition between regimes (neutral→bullish, bullish→bearish, etc.)
  - Composite score transition: absolute change ≥ 15 points between snapshots
  - Extreme VIX: India VIX factor score ≤ −60
  - High-risk macro event: upcoming Task 18.8 macro event with top-tier `high` importance (RBI/Fed policy, India/US CPI, GDP, NFP, etc.); deterministic, no direction prediction, no AI; dedup by real event identity (event_code + scheduled reference_time) + market date
  - Deduplication by event_type + market_date + state transition; cooldowns (24h/12h/6h/24h)
  - Generic composite risks (HIGH_VIX, EXTREME_NEGATIVE_*, STRONG_CONTRADICTION) remain deterministic risk info but are NOT macro-event notifications
  - AI explanation optional, only adds prose after deterministic trigger
  - Historical APIs never emit live notifications
- **Config**: Centralized in `app/core/config.py` with sensible defaults (all disabled by default)
- **Gates**: `ruff check .` → All checks passed; `alembic current/heads` → `5f7b2c9d4e3a (head)` single head; `alembic check` → No new upgrade operations; `app.main` import OK; `git diff --check` clean
- **Tests**: 28 new focused tests (`tests/test_market_intelligence_scheduler.py`); full repository suite 1026 passed ×3 consecutive runs; all Sprint 15/18.9/18.10/18.11 preservation suites pass
- **Version**: Bumped to `v0.14.0` in `app/main.py`
- **Migration**: NONE required (reuses existing scheduler/notification/snapshot infrastructure)

Target release: **v0.14.0** — **RELEASED**.

---

# 34. Permission Architecture

Do not invent a new auth system. Use Sprint 16 conventions. Potential new permissions: market_intelligence.read, market_intelligence.refresh, market_intelligence.write. Exact permissions should be finalized in Task 18.1 after inspecting existing permission conventions.

Likely role intent:
- super_admin: read / refresh / write
- admin: read / refresh / write
- analyst: read / refresh
- viewer: read

---

# 35. Testing Philosophy

Every Sprint 18 task must include tests.

### Data
- parser correctness, missing fields, stale data, invalid data, provider failure

### Historical correctness
- no future data, date-scoped calculation, deterministic replay

### Scoring
- positive factors, negative factors, contradictory factors, missing factors, weight normalization, score bounds

### Confidence
- full data, partial data, stale data, disagreement

### Security
- auth, permissions, ownership where relevant, sanitized errors, no secrets

No automated tests should require live external services.

---

# 36. External Provider Test Rule

Never make production tests depend on: NSE availability, Yahoo availability, GIFT provider availability, macro API availability, AI provider availability. Use fixtures, mock transports, saved representative payloads where legally appropriate, dependency injection. Live provider verification is separate from unit/integration testing.

---

# 37. Data Provenance

Where practical, persist: source_name, source_url_identifier, observed_at, fetched_at. Do not necessarily persist raw full webpages. Goal: auditability: "Where did this number come from and when was it observed?"

---

# 38. Time Zones

Primary application market timezone: Asia/Kolkata. External market timestamps must be normalized carefully. Do not compare US and Indian dates naively. Example: A US session closing on Aug 11 may be the relevant overnight input for India's Aug 12 session. Create explicit tests for date/session mapping where needed.

---

# 39. Numerical Precision

Use Decimal/database numeric types for financial values where consistent with existing project architecture. Avoid floating-point equality assumptions. Scoring can use floats where appropriate but: clamp bounds, round only for presentation, test tolerances.

---

# 40. Performance

Avoid: date × factor × database query, N+1 patterns. Prefer: bulk loading, grouped calculations, cached daily snapshots, indexed dates/codes. Do not prematurely introduce Redis solely for intelligence calculations.

---

# 41. Scheduler Philosophy

Providers should be callable independently from scheduler. Architecture: Provider → Service → Repository. Scheduler merely invokes services. Allows: manual refresh, API refresh, CLI refresh, scheduled refresh, without duplicating logic.

---

# 42. CLI

Useful future CLI pattern:
- uv run python -m app.cli.market_intelligence refresh
- uv run python -m app.cli.market_intelligence generate --date 2026-08-12
- uv run python -m app.cli.market_intelligence show --date 2026-08-12

Exact CLI should follow existing project CLI conventions.

---

# 43. Observability

Provider operations should record useful status: success, cached, stale, unavailable, blocked, failed. Never log secrets. Provider failure should identify the provider/factor without exposing sensitive configuration.

---

# 44. Important Product Rule

Never present the intelligence engine as guaranteed prediction. Preferred terminology: Market Bias, Market Regime, Market Context, Market Intelligence, Risk Environment, Confidence. Avoid: NIFTY WILL rise, Guaranteed bullish, Sure-shot trade. The system analyzes conditions rather than claiming certainty.

---

# 45. What Sprint 18 Must NOT Become

Do not turn Sprint 18 into an uncontrolled collection of web scrapers. Avoid: 20 websites, 20 parsers, 20 different schemas. Architecture first. Normalized data second. Intelligence third. UI last.

---

# 46. Cross-AI Development Rule

Every AI provider must begin by reading: docs/AI_HANDOFF.md, docs/CURRENT_TASK.md, docs/05_Development/PROJECT_STATE.md, docs/05_Development/Sprint_18.md. Then inspect actual code. The repository wins over old AI conversation summaries.

---

# 47. Git Rule for Tasks 18.1–18.11

DO NOT COMMIT. DO NOT PUSH. DO NOT TAG. DO NOT RESET. DO NOT CLEAN. DO NOT STASH. Complete and verify each task first. Release Git operations belong to Task 18.12 unless the project owner explicitly changes this workflow.

---

# 48. Standard Verification Per Task

Every implementation task should finish with:
- uv run alembic current
- uv run alembic heads
- uv run ruff check .
- uv run pytest -q
- git diff --check
- git status --short

Tasks with significant core logic should run targeted tests first and then the full suite. Release task should run the full suite three consecutive times.

---

# 49. Required AI Completion Report

Every task must return:
```
Sprint 18 Task 18.X — <Name>

Baseline:
Files created:
Files modified:

Models:
Repositories:
Schemas:
Services:
Providers:

Routes:
UI:
Scheduler:

Migration:
Tests added: Total tests:

Provider behavior:
Failure/fallback behavior:
Freshness behavior:

No-look-ahead verification:

Alembic: Ruff: Pytest:

Documentation:

CURRENT_TASK:

Known blockers: Recommended next task:

DO NOT COMMIT.
```

Fields irrelevant to a particular task may be marked: N/A rather than invented.

---

# 50. Sprint 18 Definition of Done

Sprint 18 is complete only when the project has:
- normalized Indian index intelligence
- global market context
- Asian market context
- GIFT Nifty context
- FII/DII flows
- India VIX
- USD/INR
- crude context
- breadth
- sector strength
- macro/event context
- deterministic market regime
- composite intelligence score
- confidence score
- stale/missing-data handling
- historical reproducibility
- AI explanation
- deterministic explanation fallback
- REST APIs
- browser dashboard
- scheduling
- appropriate notifications
- authorization
- comprehensive tests
- documentation
- three consecutive release test passes
- release v0.14.0

---

# 51. Final Product Vision

After Sprint 18, AIStockScanner should be capable of presenting market intelligence with regime, composite score, confidence, domestic trend, breadth, global markets, GIFT Nifty, FII/DII, India VIX, USD/INR, crude, sector participation, major risks, and AI market view explaining what the market is doing, what is influencing it, how strong the evidence is, what disagrees with the dominant view, and what risks matter — while keeping underlying calculations deterministic, testable, historically reproducible and separate from AI-generated prose.

The system should explain what the market is doing, what is influencing it, how strong the evidence is, what disagrees with the dominant view, and what risks matter — while keeping the underlying calculations deterministic, testable, historically reproducible and separate from AI-generated prose.