# HISTORICAL METHODOLOGY EVIDENCE & CALIBRATION DECISION REPORT

**Sprint:** 19.1I-R1  
**Architecture Layer:** Out-of-Sample Empirical Evidence & Calibration Audit  
**ORM Models:** `56` (Unmutated)  
**Alembic Head:** `g7h8i9j0k1l2 (head)`  
**Data Evaluation Mode:** `REAL_IMPORTED_MARKET_DATA` Audit  

---

## 1. Real Historical Database Coverage Audit

An empirical audit of the project database was conducted to measure the actual coverage of imported historical observations:

| Data Family | Real Database Coverage | Date Range / Details | Status for Empirical Calibration |
| :--- | :--- | :--- | :--- |
| **Stock Daily Prices** | 11 stocks, 60 rows | 38 trading sessions (2023-01-02 to 2026-08-04) | `REAL_IMPORTED_MARKET_DATA` (Limited $N$) |
| **Delivery Quantity & %** | 34 rows | 56.7% coverage on available daily price rows | `REAL_IMPORTED_MARKET_DATA` (Partial) |
| **Financial Statements** | 0 rows | 0 companies | `INSUFFICIENT_REAL_DATA` |
| **News / Policy Events** | 0 rows | 0 events | `INSUFFICIENT_REAL_DATA` |
| **Futures Contracts** | 0 contracts | 0 price rows | `INSUFFICIENT_REAL_DATA` |
| **Options Contracts** | 1 contract (`NIFTY26AUG24500CE`) | 1 `DerivativeDailyPrice` row (2026-08-10) | `INSUFFICIENT_REAL_DATA` |
| **FII Participant Statistics** | 0 rows | 0 FII derivative statistics | `INSUFFICIENT_REAL_DATA` |
| **Participant Positions** | 0 rows | 0 participant positions | `INSUFFICIENT_REAL_DATA` |
| **Derivative Lot Size History**| 0 rows | 0 lot size history records | `INSUFFICIENT_REAL_DATA` |

> [!IMPORTANT]
> **Data Integrity Principle**
> Synthetic test fixtures in `tests/` verify system code correctness only. Empirical methodology support conclusions are strictly based on `REAL_IMPORTED_MARKET_DATA`. Where real historical coverage is missing or too small ($N < 10$), the methodology decision is strictly reported as `INSUFFICIENT_DATA` rather than manufacturing false conclusions.

---

## 2. Methodology Decision Matrix (Current Baseline v1)

All existing v1 methodologies were evaluated in their exact current state (zero weight changes, zero threshold changes, zero v2 creations):

| Methodology | Version | Sample Count ($N$) | Real Data Status | Primary Metric | Decision / Status | Calibration Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Investment Quality** | `investment_quality_v1` | $0$ | `INSUFFICIENT_REAL_DATA` | 20D / 60D Excess Return | `INSUFFICIENT_DATA` | Ingest multi-year quarterly & annual financial statements prior to 19.1H |
| **Quality Pullback** | `quality_pullback_v1` | $6$ | `REAL_IMPORTED_MARKET_DATA` | 10D Return & MAE | `PROMISING_BUT_LIMITED` | Expand price history to 500+ NSE stocks to test technical recovery rule |
| **Breakout** | `breakout_v1` | $8$ | `REAL_IMPORTED_MARKET_DATA` | 5D Continuation & Failed Rate | `PROMISING_BUT_LIMITED` | Ingest broader volume & delivery history to validate continuation rates |
| **Swing** | `swing_v1` | $11$ | `REAL_IMPORTED_MARKET_DATA` | 10D Mean Return & Excess | `SUPPORTED_WITH_REGIME_LIMITATION` | Validated trend/momentum logic; expand session coverage for holdout validation |
| **BTST Readiness** | `btst_v1` | $5$ | `REAL_IMPORTED_MARKET_DATA` | Overnight Gap & Intraday | `PROMISING_BUT_LIMITED` | Validate overnight gap vs next-session open on 100+ trading sessions |
| **Option Direction** | `option_direction_v1` | $1$ | `INSUFFICIENT_REAL_DATA` | Directional Accuracy % | `INSUFFICIENT_DATA` | Ingest NSE F&O EOD archives (futures OI, PCR, FII flow, GEX) |
| **Option Expiry Selection**| `option_expiry_selection_v1`| $1$ | `INSUFFICIENT_REAL_DATA` | Premium Return & Theta | `INSUFFICIENT_DATA` | Ingest historical option chain expiries to compare front vs next expiry |
| **Option Moneyness** | `option_moneyness_v1` | $1$ | `INSUFFICIENT_REAL_DATA` | Delta Fit & Breakeven | `INSUFFICIENT_DATA` | Ingest option chain across Delta bands ($0.20 - 0.80$) to test Delta preferences |
| **Option Strike Ranking** | `option_strike_ranking_v1` | $1$ | `INSUFFICIENT_REAL_DATA` | Preferred minus ATM Return | `INSUFFICIENT_DATA` | Ingest full NSE options EOD daily price history to compare Preferred vs ATM |

---

## 3. Controlled Feature Ablation Findings

### A. GEX Contribution (`option_direction_v1`)
- **Current Production Rule**: Scenario Net GEX has a 15% capped secondary contribution. Gross Unsigned GEX is non-directional (0 points).
- **Ablation Finding**: GEX cannot be empirically validated out-of-sample due to `INSUFFICIENT_REAL_DATA` (0 F&O daily price rows for options chains across multiple expiries).
- **Recommendation**: Maintain current rule (GEX-alone trade prevention active) without changing weights until historical F&O data is ingested.

### B. PCR Contribution (`option_direction_v1`)
- **Current Production Rule**: PCR OI $\ge 1.25$ (+15 points), PCR OI $\le 0.70$ (-15 points). Single-factor prohibition active.
- **Ablation Finding**: Requires historical options chain archives to test incremental predictive value over technical trend alone.

### C. Event Risk Veto (`btst_v1` & `option_expiry_selection_v1`)
- **Current Production Rule**: Overnight earnings/policy events trigger `EVENT_RISK_VETO` (`NO_TRADE`).
- **Ablation Finding**: Structurally sound for drawdown prevention; needs corporate event calendar history for statistical tail loss comparison.

---

## 4. Required Historical Data for Full Out-Of-Sample Calibration

To enable statistically rigorous walk-forward calibration ($N \ge 100$ per score bucket) and holdout evaluation prior to 19.1H Forecasting:
1. **NSE Stock Equity EOD Data**: 3+ years of daily OHLCV + delivery quantity for NIFTY 500 stocks.
2. **Financial Statements**: 5+ years of quarterly income, balance sheet, and cash flow statements for F&O stock universe.
3. **NSE F&O EOD Archives**: Daily contracts, expiries, settlement prices, open interest, and implied volatility across equity and index options. (Sources: NSE official archives, Upstox/Dhan historical F&O API).

---

## 5. Forecasting Readiness Assessment

```text
FORECASTING READINESS (19.1H):
NOT READY — CALIBRATION / DATA REQUIRED

Reason:
Empirical validation engine (19.1I) is fully implemented and operational.
However, real historical options & fundamentals coverage in the project database is currently INSUFFICIENT_DATA.
Historical data ingestion of NSE F&O archives and financial statements must occur prior to converting deterministic score relationships into 19.1H probability distributions.
```
