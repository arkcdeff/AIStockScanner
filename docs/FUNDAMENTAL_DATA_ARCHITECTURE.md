# FUNDAMENTAL & CORPORATE INTELLIGENCE DATA ARCHITECTURE — SPRINT 19.1E

## 1. Executive Summary

Sprint 19.1E establishes a trustworthy, point-in-time (PIT) safe fundamental and corporate intelligence layer for the AI Stock Scanner platform.

The primary objective is to build an uncompromised historical financial statement, corporate announcement, and shareholding pattern foundation suitable for:
1. Long-term stock research & Quality Pullback / Fallen Leader detection
2. Stock 360° deep research integration
3. Future Quality Stock ranking and Opportunity Engine scoring
4. Historical walk-forward strategy backtesting

---

## 2. Invariants & Source Policy

### Point-in-Time Publication Safety
- **Core Requirement**: Queries specify `as_of_date` and strictly enforce `available_at <= as_of_date`.
- **Lookahead Bias Prevention**: A financial quarter ending `2026-06-30` announced on `2026-08-05` is strictly **invisible** to any replay evaluated prior to `2026-08-05`.

### Explicit Facts & Period Semantics
- **MISSING != ZERO**: `None` represents missing or unreported fields. Missing values are never converted to zero.
- **Period Types**: Explicitly `"QUARTERLY"` or `"ANNUAL"`.
- **Statement Bases**: Explicitly `"CONSOLIDATED"` or `"STANDALONE"`. Defaults to `CONSOLIDATED` with automatic fallback to `STANDALONE` if unavailable.
- **Units**: All financial statement monetary values are stored normalized in **`INR_CRORE`** (`currency="INR"`).

### Official Source Policy
- No raw AI or LLM is permitted as a source of raw facts. All facts originate from official disclosures.
- Idempotency is enforced using SHA-256 fingerprint hashing.

### Action Model Reuse
- Existing `CorporateAction` model (from 19.1C) is reused for economic actions (dividends, splits, bonuses, rights). No duplicate corporate action tables are created.

---

## 3. Data Models

1. **`FinancialStatement` (`financial_statements`)**:
   - `stock_id`, `period_type`, `statement_basis`, `period_end`, `reported_at`, `available_at`, `revision_type`.
   - Facts: `revenue`, `operating_income`, `ebit`, `pbt`, `pat`, `eps`, `total_assets`, `total_liabilities`, `total_equity`, `total_debt`, `cash_and_equivalents`, `operating_cash_flow`, `investing_cash_flow`, `financing_cash_flow`, `free_cash_flow`, `shares_outstanding`.
   - Unit: `INR_CRORE`.

2. **`CorporateAnnouncement` (`corporate_announcements`)**:
   - `stock_id`, `category`, `announcement_date`, `available_at`, `title`, `summary`, `corporate_action_id`.

3. **`ShareholdingPattern` (`shareholding_patterns`)**:
   - `stock_id`, `period_end`, `reported_at`, `available_at`, `promoter_holding_pct`, `fii_holding_pct`, `dii_holding_pct`, `public_holding_pct`, `promoter_pledged_pct`, `promoter_holding_change`.

---

## 4. Deterministic Analytics & Quality Score Inputs

The service layer computes point-in-time safe deterministic metrics:
- **Growth**: Revenue YoY/QoQ %, Profit YoY/QoQ %, EPS YoY %.
- **Profitability**: Operating Margin %, Net Margin %, ROE %, ROCE %.
- **Solvency & Cash Flow**: Debt/Equity, Interest Coverage, OCF/PAT, Free Cash Flow.
- **Valuation**: PIT P/E, PIT P/B, PIT P/S, Dividend Yield % (using PIT price and PIT statement denominator).
- **Quality Score Inputs**: Deterministic component scores (`fundamental_quality_score`, `growth_quality_score`, `cashflow_quality_score`, `balance_sheet_quality_score`) feeding future Stock 360° and Quality Pullback scanners.

---

## 5. REST & UI Interfaces

- `GET /api/v1/fundamentals/{symbol}` — Point-in-time company snapshot.
- `GET /api/v1/fundamentals/{symbol}/history` — Historical financial statements.
- `GET /api/v1/corporate/{symbol}/announcements` — Corporate announcements history.
- `GET /api/v1/corporate/{symbol}/shareholding` — Shareholding pattern history.
- `GET /api/v1/fundamentals/coverage` — Platform fundamental coverage summary.
- `GET /market-data/fundamentals/cards` — HTMX UI cards template.
