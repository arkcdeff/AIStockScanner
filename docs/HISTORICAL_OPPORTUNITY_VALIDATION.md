# HISTORICAL OPPORTUNITY VALIDATION & WALK-FORWARD CALIBRATION ARCHITECTURE

**Sprint:** 19.1I  
**Architecture Layer:** Out-of-Sample Historical Validation & Empirical Calibration Layer  
**ORM Models Added:** `0` (Strictly derived, 56 total)  
**Alembic Head:** `g7h8i9j0k1l2 (head)`  

---

## 1. Executive Summary & Core Validation Principles

Sprint 19.1I implements an out-of-sample historical validation and walk-forward calibration engine (`HistoricalOpportunityValidationService` & `HistoricalOptionsValidationService`) for AIStockScanner.

### Core Principles
1. **PIT Signal Freeze**: Features are computed strictly using data legitimately available at cutoff $A$ (as of date $T$). Signal facts (`ValidationSignalSnapshot`) are frozen before future outcome prices (`ValidationOutcome` / `OptionValidationOutcome`) are loaded.
2. **Outcome Isolation**: Future outcomes never feed feature construction.
3. **Execution Reference Methodologies**:
   - `NEXT_SESSION_OPEN`: Realistic entry at next available trading session open price.
   - `SAME_CLOSE_RESEARCH_RETURN`: Research mark using trade date close price.
4. **Profile-Specific Horizons**:
   - **BTST**: Signal-day close $\to$ next-session open / next-session close.
   - **BREAKOUT**: 1D, 3D, 5D, 10D (MFE, MAE, continuation rate, failed breakout rate).
   - **SWING**: 3D, 5D, 10D, 20D.
   - **QUALITY_PULLBACK**: 5D, 10D, 20D, 60D (compares `WAITING_FOR_CONFIRMATION` vs `CONFIRMED_RECOVERY_STRUCTURE`).
   - **INVESTMENT_QUALITY**: 20D, 60D, 120D (benchmark-relative return against NIFTY broad index).
   - **OPTIONS**: Profile-dependent, min-bounded by contract expiry date ($\min(\text{horizon}, \text{DTE})$).

```
[PIT FEATURE EXTRACTION (Cutoff A / Date T)]
                      ↓
  [SIGNAL FREEZE (ValidationSignalSnapshot)]
                      ↓
 [OUTCOME EVALUATION (ValidationOutcome / OptionValidationOutcome)]
                      ↓
 [CALIBRATION & METRICS AGGREGATION (ValidationSummary)]
```

---

## 2. Options Validation & Strike Comparators

- **Exact Strike Evaluation**: Evaluates the ACTUAL historical selected contract. Never substitutes a better strike later.
- **Strike Comparators**: Compares Preferred Strike vs ATM Benchmark comparator.
- **Directional vs Premium Separation**:
  - **Directional Accuracy**: True if underlying moved in predicted direction (CE $\to \Delta S > 0$, PE $\to \Delta S < 0$).
  - **Option Premium Return**: Actual P&L return % of option contract.
  - Distinguishes correct direction but poor strike/theta/IV from wrong direction.

---

## 3. Score Buckets, Confidence Intervals, & Calibration Output

- **Score Buckets**: 90-100, 80-89, 70-79, 60-69, <60.
- **Wilson Score Interval**: Computes 95% confidence intervals for positive outcome rates to prevent small-sample misinterpretation:
  $$p \pm z \sqrt{\frac{p(1-p)}{n}}$$
- **Sample Quality Flags**:
  - $n \ge 30$: `SUFFICIENT`
  - $10 \le n < 30$: `LIMITED`
  - $n < 10$: `INSUFFICIENT`
- **19.1H Forecasting Handoff**: Produces empirical calibration tables ready for Sprint 19.1H probability mapping.

---

## 4. Zero Schema & Zero Auto-Trading Directives

- **Database Migrations**: `0` (ORM count remains strictly 56, Alembic head remains `g7h8i9j0k1l2`).
- **Auto-Trading Execution**: `0` (analytics and validation only, no broker calls or order placement).
