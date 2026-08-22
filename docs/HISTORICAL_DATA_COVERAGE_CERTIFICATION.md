# HISTORICAL DATA COVERAGE & VALIDATION READINESS CERTIFICATION ARCHITECTURE

**Sprint:** 19.1J-R1
**Architecture Layer:** Out-of-Sample Coverage Certification & Session Continuity Engine
**ORM Models Added:** `0` (Strictly derived, 56 total)
**Alembic Head:** `g7h8i9j0k1l2 (head)`

---

## 1. Executive Summary & Core Architectural Principles

Sprint 19.1J-R1 establishes a point-in-time (PIT) safe historical data coverage certification engine (`HistoricalCoverageCertificationService`), a deterministic exchange session calendar (`ExchangeCalendarService`), and session continuity validators for AIStockScanner.

### Core Architectural Principles
1. **True Exchange Session Model (`ExchangeCalendarService`)**:
   - Accounts for weekends (Saturdays, Sundays) and official NSE trading holidays across 2023–2026.
   - Resolves next trading sessions deterministically (`next_exchange_session(date)`, `nth_exchange_session_after(date, n)`).
   - Friday signal date $\to$ Monday is valid next session.
   - Friday signal date $\to$ Monday holiday $\to$ Tuesday is valid next session.
2. **Session Continuity Guard**: Validation algorithms must never define `next trading session = next available DB row` across missing exchange trading session gaps.
   - If expected next exchange session has no observation in DB (e.g. Tuesday observation missing after Monday holiday), the case is tagged `INSUFFICIENT_SESSION_COVERAGE` and rejected from outcome scoring.
3. **Full-Period vs Import-Window Session Metrics**:
   - **`full_period_expected_exchange_sessions`**: Total expected trading sessions across full date span (min date to max date in database) calculated via `ExchangeCalendarService`.
   - **`observed_distinct_sessions`**: Count of distinct trading dates with stored observations.
   - **`full_period_coverage_pct`**: `observed_distinct_sessions / full_period_expected_exchange_sessions * 100.0`.
4. **Equity vs Index Classification**:
   - `INDEX_SYMBOLS`: `NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, `SENSEX`, `NIFTY50`, `NIFTY_50`.
   - `EQUITY_SYMBOLS`: All individual stock symbols (`RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, etc.).
5. **Domain-Specific Readiness Gates**: Readiness is certified independently per domain (`READY`, `PARTIAL`, `INSUFFICIENT`):
   - **`INVESTMENT`**: Requires multi-year stock prices + quarterly/annual financial statements.
   - **`PULLBACK`**: Requires equity price history + session continuity.
   - **`BREAKOUT`**: Requires equity price history + volume + delivery quantity coverage.
   - **`SWING`**: Requires equity price history + session continuity.
   - **`BTST`**: Requires equity price history + next-session gap validity.
   - **`FUTURES`**: Requires futures contracts + price history + open interest.
   - **`OPTIONS_DIRECTION`**: Requires options contracts + daily prices + OI + PCR.
   - **`EXPIRY_SELECTION`**: Requires multiple option expiries.
   - **`MONEYNESS`**: Requires option chain strikes across Delta bands ($0.20 - 0.80$).
   - **`EXACT_STRIKE`**: Requires multi-strike option chains, CE/PE pairing, multiple expiries, lot size history, and spot values.

---

## 2. Data Provenance & Idempotency Rules

- **Source Provenance**: Every imported historical dataset captures `source`, `mode`, `checksum`, `download_date`, and `file_name`.
- **Idempotency**: Re-importing identical historical data files produces `0` duplicate rows.
- **Malformed File Safety**: Corrupted or schema-mismatched files trigger a `DataQualityIssue` and abort transaction without DB corruption.

---

## 3. UI & CLI Integration

- **CLI Certification Command**: `python -m app.cli.import_nse certify` outputs formatted per-domain readiness tables.
- **REST API Endpoint**: `GET /api/v1/market-data/coverage/certification`.
- **Validation Lab UI**: Displays domain-specific validation readiness badges (`READY`, `PARTIAL`, `INSUFFICIENT`) in `validation_lab_tab.html`.
