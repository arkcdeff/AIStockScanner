# AISTOCKSCANNER
# STAGE 2 — G.11-C M-1 FINAL DATA REPAIR & IMPORTER HARDENING
## Production Data Repair & Importer Hardening Completion Report

**Date:** 2026-09-12  
**Baseline Commit:** `60b1c46e6ed0555e1a5b8651b02285b09701178f` (`v0.14.0-g10`)  
**Status:** **REPAIRED, HARDENED & FULLY VERIFIED**  
**Execution Constraints:** COMMIT: NO | TAG: NO | PUSH: NO | VERSION BUMP: NO  

---

## 1. EXECUTIVE SUMMARY

Following the discovery by Muse Spark 1.3 and GLM-5.3 of institutional-flow stale-content contamination in the G.11-C Phase 2 ingestion, this mission successfully executed the **complete data repair**, **fail-closed importer hardening**, and **full regression verification** for `institutional_flows` in production database `ai_stock_scanner`:

1. **Production Database Repaired (`scripts/repair_g11c_m1_institutional.py`)**:
   - **76 overwritten genuine rows** (38 trading sessions × 2 rows: FII + DII) restored from authoritative captures (`nse_fii_dii_cash_20260601_20260831.json`, SHA-256 `c048a96c7eca7e810ff351e9f175c40f41c4c73bcb47ede6d91f9d54d13272b2` and 62 matching BIFF8 raw XLS files).
   - **110 contaminated stale rows** (55 sessions × 2 rows: FII + DII) deleted and quarantined from `institutional_flows`.
   - **55 `DataQualityIssue` records** logged with `issue_code="SOURCE_STALE_CONTENT"` and `severity="error"` explaining the exact January-echo anomaly.
   - **164 rolling cumulatives (1D, 3D, 5D, 20D)** recomputed chronologically across all 82 genuine sessions for FII and DII.
   - **Zero cross-domain disruption**: Exactly **0 row modifications** across all 10 non-institutional tables (`stocks`, `daily_prices`, `indicator_results`, `bulk_block_deals`, `market_index_prices`, `derivative_participant_positions`, `corporate_actions`, `corporate_announcements`, `derivative_contracts`, `derivative_daily_prices`).
   - **Strict Zero-Fill / Synthetic Data Discipline**: Exactly **0 synthetic rows**, **0 interpolated rows**, and **0 placeholder zeroes** inserted.
   - **Budget Day 2026-02-01 Semantics**: Institutional data quarantined (0 rows, marked `SOURCE_STALE_CONTENT`), while other domain data (market index prices, stock prices, participant positions) remain fully active and valid.

2. **Importer Hardened (`NseInstitutionalImportService`)**:
   - Generic fail-closed validator `validate_session_entry()` prevents stale-echo or mismatched data from ever entering production:
     - **Inner Filename Report Date Validation**: Rejects any file where inner filename report date (`cat_turnover_DDMMYY.xls` or `category_turnover_YYYYMMDD.xls`) differs from the requested session trade date.
     - **Duplicate Inner CRC32 Detection**: Tracks and rejects repeated CRC32 content across different trading dates.
     - **Duplicate Turnover Payload Signature Detection**: Detects and rejects repeated category turnover tuples across dates.
     - **Explicit Status Checking**: Rejects entries marked `SOURCE_STALE_CONTENT`, `SOURCE_UNAVAILABLE`, or unapproved status codes.

3. **Coverage State & Manifest Hardened**:
   - Added `CoverageState.SOURCE_STALE_CONTENT` to `app/data/coverage_state.py`.
   - Manifest `data/reference/institutional/manifest_20260101_20260831.json` updated with exact audit classifications for all 164 sessions (82 genuine, 55 stale, 27 unavailable) and verified inner metadata.

4. **Testing & Verification**:
   - **Test M-2 Fixed**: `tests/test_alerts.py::test_pipeline_runs_alert_stage` fixed and green (all 18/18 alert tests pass).
   - **New Hardening Suite**: `tests/test_institutional_hardening_m1.py` created with 14 comprehensive tests (14/14 pass).
   - **Full Regression Passed**: `test_g11c_historical_expansion.py` (27/27), `test_broad_market_indicator_g11b.py` (15/15), `test_bulk_block_g10.py` (30/30), `test_g8_derivative_expansion.py` (6/6).
   - **Full Suite 100% Pass**: Entire project test suite passes with **0 failures and 0 errors**.
   - **UI Rendering Verified**: Missing/stale institutional flow dates render `"FII/DII data unavailable"` or `"--"`, never misleading zeroes or 0%.

---

## 2. ROOT CAUSE OF M-1 CONTAMINATION

NSE's live `"CM - Category-wise Turnover"` reporting endpoint (`https://www.nseindia.com/api/reports?archives=...`) exhibited a silent, deterministic archive defect:
- When queried for dates in Feb–Aug 2026, the endpoint returned **HTTP 200**, but served the **January 2026 file corresponding to the same day-of-month** (e.g. 2026-06-01 returned `cat_turnover_010126.xls` with CRC `c68bef9d`).
- When no January counterpart existed (weekends/holidays in January), the endpoint returned HTTP 404.
- Because naive ingest scripts checked only `http_status == 200` without validating the inner filename date or content signature, 113 sessions were marked `VALID`, overwriting 38 previously genuine dates and injecting 55 dates of stale January data.

---

## 3. QUANTIFIED REPAIR METRICS

| Metric | Pre-Repair Contaminated State | Post-Repair Clean State | Delta / Action |
|---|---|---|---|
| **Total Nominal Sessions** | 164 | 164 | 100% accounted for |
| **Total Institutional Rows in DB** | 274 (137 dates × 2) | **164 (82 dates × 2)** | -110 stale rows deleted |
| **Genuine Sessions in DB** | 44 (20 Jan + 24 survivors) | **82 (20 Jan + 62 Jun-Aug)** | +38 dates restored |
| **Overwritten Rows Restored** | 76 (corrupted) | **76 (100% restored)** | Verified against JSON & XLS |
| **Stale Contaminated Rows** | 110 (55 dates × 2) | **0 (quarantined/deleted)** | Quarantined with DQ issues |
| **Data Quality Issues Logged** | 0 | **55 (`SOURCE_STALE_CONTENT`)** | Error-severity audit trail |
| **Genuine Acquired Sessions** | 20 (January 2026) | **20 (January 2026)** | Retained, byte-verified |
| **Genuine Prior Trusted Sessions**| 24 (survivors only) | **62 (all June-August 2026)** | Fully restored |
| **Unavailable Sessions** | 27 | **27 (0 fact rows)** | Deterministic 404 preserved |
| **Stale Sessions** | 0 (falsely ingested) | **55 (0 fact rows)** | Fail-closed quarantine |
| **Rolling Cumulatives Recomputed**| Inaccurate / mixed | **164 (82 FII + 82 DII)** | 1D, 3D, 5D, 20D valid |
| **Idempotency Drift** | N/A | **0 rows (100% idempotent)** | Confirmed via 2-pass test |

---

## 4. SPOT-CHECK VERIFICATION TABLE

Representative verification dates comparing contaminated DB values against restored genuine values:

| Trade Date | Classification | Category | Contaminated Stale Value (Crore) | Repaired Production DB Value (Crore) | Verified Source Reference |
|---|---|---|---|---|---|
| **2026-01-01** | `GENUINE_ACQUIRED` | FII Buy / Sell | 1,323.19 / 4,364.01 | **1,323.19 / 4,364.01** | `cat_turnover_010126.xls` (CRC `c68bef9d`) |
| **2026-01-02** | `GENUINE_ACQUIRED` | DII Buy / Sell | 14,704.31 / 14,542.50 | **14,704.31 / 14,542.50** | `cat_turnover_020126.xls` (CRC `d379317a`) |
| **2026-06-01** | `GENUINE_PRIOR_TRUSTED` | FII Buy / Sell | 1,323.19 / 4,364.01 *(Jan-01 echo)* | **18,424.46 / 22,267.85** | `category_turnover_20260601.xls` + JSON |
| **2026-06-01** | `GENUINE_PRIOR_TRUSTED` | DII Buy / Sell | 13,192.30 / 11,958.27 *(Jan-01 echo)* | **14,946.90 / 10,918.89** | `category_turnover_20260601.xls` + JSON |
| **2026-06-15** | `GENUINE_PRIOR_TRUSTED` | FII Buy / Sell | 16,442.29 / 15,438.17 *(survivor)* | **16,442.29 / 15,438.17** | `category_turnover_20260615.xls` + JSON |
| **2026-08-06** | `GENUINE_PRIOR_TRUSTED` | FII Buy / Sell | 15,472.11 / 15,521.37 *(Jan-06 echo)* | **16,581.55 / 16,163.63** | `category_turnover_20260806.xls` + JSON |
| **2026-08-28** | `GENUINE_PRIOR_TRUSTED` | FII Buy / Sell | 25,520.68 / 21,123.98 *(Jan-28 echo)* | **14,228.85 / 15,653.17** | `category_turnover_20260828.xls` + JSON |
| **2026-08-28** | `GENUINE_PRIOR_TRUSTED` | DII Buy / Sell | 18,741.72 / 16,344.68 *(Jan-28 echo)* | **15,094.39 / 10,861.59** | `category_turnover_20260828.xls` + JSON |
| **2026-02-01** | `SOURCE_STALE_CONTENT` | FII & DII | 1,323.19 / 4,364.01 *(Jan-01 echo)* | **0 ROWS IN DB (QUARANTINED)** | Stale echo removed; DQ issue logged |
| **2026-03-02** | `SOURCE_STALE_CONTENT` | FII & DII | Stale Jan-02 echo values | **0 ROWS IN DB (QUARANTINED)** | Stale echo removed; DQ issue logged |
| **2026-07-03** | `SOURCE_UNAVAILABLE` | FII & DII | Absent | **0 ROWS IN DB (UNAVAILABLE)** | HTTP 404 preserved; 0 fact rows |

---

## 5. CROSS-DOMAIN INTEGRITY AUDIT

During the execution of `scripts/repair_g11c_m1_institutional.py`, strict before-and-after count snapshots verified that **no non-institutional table was modified**:

| Domain Table | Baseline Row Count | Post-Repair Row Count | Delta |
|---|---|---|---|
| `stocks` | 2,130 | 2,130 | **0** |
| `daily_prices` | 227,330 | 227,330 | **0** |
| `indicator_results` | 2,866,358 | 2,866,358 | **0** |
| `bulk_block_deals` | 2,050 | 2,050 | **0** |
| `market_index_prices` | 3,116 | 3,116 | **0** |
| `derivative_participant_positions` | 1,312 | 1,312 | **0** |
| `corporate_actions` | 1,180 | 1,180 | **0** |
| `corporate_announcements` | 4,210 | 4,210 | **0** |
| `derivative_contracts` | 3,747 | 3,747 | **0** |
| `derivative_daily_prices` | 100,564 | 100,564 | **0** |

---

## 6. IMPORTER HARDENING ARCHITECTURE

The `NseInstitutionalImportService` has been fortified with multi-layered validation logic in `validate_session_entry()`:
- Inner filename report date validation (`cat_turnover_DDMMYY.xls` and `category_turnover_YYYYMMDD.xls`).
- Duplicate inner CRC32 cross-date detection.
- Duplicate turnover payload signature repeat detection.
- Fail-closed status checks against stale-echo or unavailable states.

---

## 7. AUTOMATED TEST SUITES & VERIFICATION RESULTS

| Test Suite | Scope | Result | Execution Time |
|---|---|---|---|
| `tests/test_institutional_hardening_m1.py` | 14 hardening, fail-closed, restore, and integrity checks | **14 / 14 PASSED (100%)** | 10.32s |
| `tests/test_alerts.py` | 18 alert engine tests (incl. M-2 regression fix) | **18 / 18 PASSED (100%)** | 18.25s |
| `tests/test_g11c_historical_expansion.py` | 27 G.11-C historical expansion & PIT tests | **27 / 27 PASSED (100%)** | 25.60s |
| `tests/test_broad_market_indicator_g11b.py` | 15 G.11-B broad-market indicator expansion tests | **15 / 15 PASSED (100%)** | 20.08s |
| `tests/test_bulk_block_g10.py` | 30 G.10 bulk and block deal tests | **30 / 30 PASSED (100%)** | 33.14s |
| `tests/test_g8_derivative_expansion.py` | 6 G.8 derivative expansion tests | **6 / 6 PASSED (100%)** | 15.72s |
| **Full Pytest Suite (`pytest -q`)** | **Entire project test suite** | **ALL PASSED (0 failures, 0 errors)** | ~30m |

---

## 8. REPAIR SCRIPT CAPABILITY

The production repair script `scripts/repair_g11c_m1_institutional.py` provides standalone verification and execution options:

```bash
# Verify integrity, counts, and spot checks without writing
uv run python scripts/repair_g11c_m1_institutional.py --verify-only

# Execute the complete repair transactionally
uv run python scripts/repair_g11c_m1_institutional.py --repair

# Verify two-pass idempotency (0 drift)
uv run python scripts/repair_g11c_m1_institutional.py --idempotency-test
```

---

## 9. CONCLUSION & CERTIFICATION RECOMMENDATION

- The institutional-flow stale-content defect (M-1) has been **100% repaired** in the production database.
- The institutional importer has been **fortified with fail-closed safeguards** against any future upstream archive drift.
- All alert and pipeline tests (M-2) are green and robust.
- The codebase and database adhere to all certification baselines.

**Strict Governance Constraints Maintained:**
- Git Commit: **NO**
- Git Tag: **NO**
- Git Push: **NO**
- Version Bump: **NO**
- Baseline Commit Preserved: `60b1c46e6ed0555e1a5b8651b02285b09701178f`
