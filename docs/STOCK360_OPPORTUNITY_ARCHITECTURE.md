# Stock 360° + Opportunity Intelligence Architecture — Sprint 19.1G

## 1. Overview & Architectural Goals

The Stock 360° and Opportunity Intelligence engine is an **orchestration layer** that unifies 10 domain dimensions into a point-in-time (PIT) research snapshot and evaluates stocks across 5 specialized opportunity profiles.

It does **NOT** duplicate technical indicators, fundamental models, news classifiers, or derivatives analytics—it orchestrates and consumes them.

```
Raw Database Tables (DailyPrice, FinancialStatement, NewsEvent, etc.)
                   ↓
Existing Domain Services (FundamentalCorporateService, NewsPolicyService, HistoricalOptionsAnalytics, etc.)
                   ↓
Stock 360 Snapshot (10 Dimensions)
                   ↓
Opportunity Engine (5 Profiles: Investment, Pullback, Breakout, Swing, BTST)
                   ↓
Evidence / Contradiction / Veto Analysis
                   ↓
Deterministic Classification (STRONG_RESEARCH_CANDIDATE, RESEARCH_CANDIDATE, WATCH, WAIT, REJECTED, NO_TRADE)
```

---

## 2. Point-in-Time (PIT) Safety Invariant

Every call to `Stock360Service` and `OpportunityRankingService` accepts `evaluation_date` and `as_of_date` cutoff parameters.
- **Lookahead Protection**: All underlying queries enforce `trade_date <= evaluation_date`, `available_at <= evaluation_datetime`, and `as_of_date` cutoff.
- Future financial statements, restatements, corporate filings, news headlines, government policies, or price action are strictly hidden.

---

## 3. The 5 Opportunity Profiles

1. **`INVESTMENT_QUALITY`**: Long-term fundamentals, revenue & profit growth, ROE/ROCE, low debt-to-equity (<0.5), P/E valuation context, and 50D/200D EMA trend structure.
2. **`QUALITY_PULLBACK` (Fallen Leader Detection)**: Finds previously strong high-quality stocks undergoing a temporary, controlled drawdown (-8% to -35% from 52w high) near structural support, with moderating selling volume, intact fundamental quality, and no material adverse news/events.
3. **`BREAKOUT`**: Price location near 52-week High (within 3%), relative volume expansion (Rvol ≥ 1.8x), delivery breakout confirmation, and outperforming benchmark relative strength.
4. **`SWING`**: Trend structure alignment (UPTREND), moving average alignment (EMA20 > EMA50), volume participation, and low risk flags for multi-day swing setups.
5. **`BTST_READINESS`**: End-of-day continuation characteristics (relative volume ≥ 1.5x, delivery % ≥ 40%, closing near high) with an **Overnight Event Risk VETO**. Scheduled earnings or board meeting disclosures scheduled for tomorrow immediately trigger an overnight risk VETO.

---

## 4. Classifications & Non-Guaranteed Disclaimer

The engine outputs research-oriented classifications:
- `STRONG_RESEARCH_CANDIDATE` (Score ≥ 80.0)
- `RESEARCH_CANDIDATE` (Score 65.0 - 79.9)
- `WATCH` (Score 50.0 - 64.9)
- `WAIT` (Score 35.0 - 49.9)
- `REJECTED` (Veto triggered or score < 35.0)
- `NO_TRADE` (High contradiction/risk load)
- `INSUFFICIENT_DATA` (Data completeness < 50%)

> [!IMPORTANT]
> The engine produces deterministic research scores and evidence breakdowns. It does **NOT** generate guaranteed BUY/SELL signals, price targets, or option strike recommendations.

---

## 5. API & UI Endpoints

- `GET /api/v1/stocks/{symbol}/360`: Unified 10-dimensional Stock 360° snapshot.
- `GET /api/v1/opportunities`: Opportunity candidate ranking.
- `GET /api/v1/opportunities/{symbol}`: Single stock opportunity assessment.
- `GET /market-data/stock360/cards`: HTMX UI partial for Stock 360°.
- `GET /market-data/opportunities/board`: HTMX UI Opportunity Board with 5 profile tabs.
