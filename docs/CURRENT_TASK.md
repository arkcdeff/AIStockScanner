# CURRENT TASK STATE

## Authoritative Current Task — Stage 2 Phase G.7 Critical Data-Integrity Repair

- **Task**: G.7 200-Session NSE Bhavcopy Expansion — Critical Data-Integrity Repair (Muse Review FAIL → REPAIRED)
- **Phase**: Repair Complete & Verified — 2026-09-03
- **Status**: `STAGE 2 — PHASE G.7 REPAIRED — PENDING CHECKPOINT`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **HEAD Commit**: `b60bdcc`
- **Version**: `0.14.0` (preserved)
- **Alembic head**: `478d723971bc` (no new migrations, existing schema strictly reused)
- **Official NSE Historical Price Sourcing**:
  - Source Endpoints: `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` and companion `https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`
  - Target Historical Window: `2025-10-01` to `2025-12-31` (62 certified trading sessions).
  - Acquired Files:
    - `data/reference/bhavcopy/200session/raw/` (124 official files: 62 bhavcopy zips + 62 companion delivery csvs)
    - `data/reference/bhavcopy/200session/manifest.json`: Full provenance metadata with SHA-256 hashes, byte sizes, and row counts.
- **Parser & Ingestion Implementation**:
  - `app/services/nse_historical_extension_service.py` (`NseHistoricalExtensionService`):
    - Uses `parse_bhavcopy_detailed` and companion `parse_delivery` from `app.data.nse_parser`.
    - Matches entities via ISIN primary (`Stock.isin == record.isin`) with symbol fallback (`Stock.symbol == record.symbol` and `Stock.exchange == "NSE"`).
    - Preserves existing `Stock` count strictly at 3,647 (0 new stocks created).
    - Preserves existing delivery values using PostgreSQL `func.coalesce(stmt.excluded.delivery_quantity, DailyPrice.delivery_quantity)`.
    - Batch upserts into `daily_prices` with chunking and conflict handling.
    - Exactly 179,736 rows processed across 62 sessions (+176,886 inserted, 0 updated, 2,850 unchanged).
    - Idempotency Proven: Run 1 inserted 176,886, unchanged 2,850; Run 2 inserted 0, updated 0, unchanged 179,736.
- **Database & Coverage Expansion**:
  - Total `DailyPrice` rows grew from **827,235 to 1,006,971** (+179,736 records).
  - Total distinct trading dates grew from **305 to 367**.
  - Consecutive trading session coverage ending 2026-08-31 reached **222 consecutive sessions** (Oct 1, 2025 to Aug 31, 2026), comfortably exceeding the 200-session target.
  - Universe stocks with $\ge 200$ sessions increased from 2,668 to **2,900 stocks**.
  - Classified product-stock universe (751 stocks) coverage $\ge 200$ sessions increased from 701 to **733 stocks (97.6%)**. Remaining 18 stocks are late-2025/2026 recent IPO listings (e.g. GROWW, MEESHO).
- **Indicator & Scanner Readiness**:
  - `IndicatorEngine` computes `ema_200`, `ema_50`, `sma_20` across 343+ sessions for pilot stocks (`RELIANCE`, `HDFCBANK`, `TCS`, `INFY`, `SBIN`).
  - `golden_cross` and `death_cross` scanners evaluate cleanly with valid `ema_50` and `ema_200` metadata.
  - `Stock360Service` snapshot provides non-null `ema200`, `% from 52w High`, and `% from 52w Low`.
  - Point-in-Time (PIT) isolation verified: evaluation at `2026-08-20` strictly uses prices $\le 2026-08-20$.
- **Quality Gates & Test Results (Post-Repair)**:
  - G.7 test suite: **28 / 28 passing** in `tests/test_historical_price_extension_g7.py` (was 22/22 pre-repair; +6 new weekend guard tests).
  - G.6 test suite: 8 / 8 passing in `tests/test_institutional_flow_g6.py`.
  - G.5 test suite: 22 / 22 passing in `tests/test_corporate_events_g5.py`.
  - NSE parser test suite: 5 / 5 passing in `tests/test_nse_parser.py`.
  - Full test suite: **ALL PASS** (exit code 0).
  - Ruff: clean (`All checks passed!`).
  - Alembic: clean (`No new upgrade operations detected.`, head `478d723971bc`).
  - `app.main` import: `IMPORT OK`.
  - git diff --check: clean.
  - G.1–G.6 regression: ALL PASS (Stock=3647, Sector=24, MIP=1199, Snapshots=62, Announcements=52063, Actions=829, Flows=124).
- **Strict Scope Boundaries**:
  - G.7 REPAIR COMPLETE. No G.8 work started.
  - No scanner formulas modified.
  - No Stock Intelligence scoring methodology modified.
  - No new Alembic migrations; existing schema strictly reused.
  - No commit, push, tag, or version bump.

---

## Prior Phase State — Stage 2 Phase G.6 Official Institutional Flow Acquisition & Market Context Completion

- **Task**: Official Institutional Flow Acquisition & Market Context Completion (Stage 2 Phase G.6)
- **Phase**: Implementation Complete & Verified — 2026-09-03
- **Status**: `STAGE 2 — PHASE G.6 COMPLETE & VERIFIED`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **HEAD Commit**: `b60bdcc`

---

## Sprint 20.0 UI/UX 2.0 Audit

- **Task**: Feature Inventory, Information Architecture, Page Specifications, and Implementation Plan
- **Status**: `COMPLETE - DOCUMENTATION ONLY`
- **Completed**: 2026-08-22
- **CODE_CHANGED**: `NO` for production code; documentation files only
- **Deliverables**:
  - `docs/UI_UX_2_0_FEATURE_INVENTORY.md`
  - `docs/UI_UX_2_0_INFORMATION_ARCHITECTURE.md`
  - `docs/UI_UX_2_0_PAGE_SPECIFICATIONS.md`
  - `docs/UI_UX_2_0_IMPLEMENTATION_PLAN.md`
- **Decision**: Existing capabilities are sufficient for a broad research platform, but P0 features are obscured by generic pages, unattached partials, and misleading navigation. Implementation must begin with truthful navigation and complete page wrappers; no production implementation was authorized in Sprint 20.0.

---

- **Sprint**: `19.1J-R5-R2`
- **Task**: Official Historical Source Discovery + Real Missing-Session Acquisition Pilot
- **Status**: `19.1J-R5-R2 COMPLETE & AUDITED`
- **ORM Version**: 56
- **Alembic Revision**: `g7h8i9j0k1l2`
- **Pytest Suite**: 1310 / 1310 tests passing (100% PASS)
- **CODE_CHANGED**: `NO`
- **Remote Discovery Pilot**:
  - Tested 4 Target Dates: `2025-09-15`, `2025-12-15`, `2026-03-16`, `2026-06-15`
  - Equity & F&O Historical Direct ZIP Archives: `OFFICIAL_REMOTE_NOT_FOUND` (404 on `nseindia.com` historical directories)
  - Participant OI (`fao_participant_oi_DDMMYYYY.csv`): `OFFICIAL_REMOTE_FOUND` (200 OK)
  - FII Statistics (`fii_derivatives_DDMMYYYY.csv`): `OFFICIAL_REMOTE_NOT_FOUND` (404)
- **Real Import Pilot (June 2026)**:
  - Acquired & Ingested 21 missing sessions of Participant OI data (`2026-06-01` to `2026-06-30`)
  - DB Size Before: 455.51 MB | DB Size After: 455.79 MB | Delta: +0.28 MB
  - Participant OI Sessions Ingested: +21 sessions (Total in DB: 22 sessions in 1-year window)
- **NIFTY Index Series**: Official source identified (YES), Existing model suitable (`MarketIndexPrice`), Schema change required (NO).
- **Readiness State**: `PARTIAL` / `INSUFFICIENT` across all strategy domains.
- **Pending Live Audit**: `R1D` Market-Hours Upstox V3 WebSocket Live Certification (Pending Monday `NORMAL_OPEN`)

---

## Task Audit & Completed Deliverables

1. **Real Application Execution**:
   - Running live Uvicorn app at `http://127.0.0.1:8004` against PostgreSQL.
2. **Playwright Chromium E2E Automation**:
   - Engine: Chromium `151.0.7922.34`.
   - Authenticated login flow verified: Form POST `/login` $\to$ redirect `303` $\to$ `/dashboard` $\to$ cookies set & retained.
3. **Real Page Rendering & Navigation**:
   - 6/6 pages rendered at HTTP `200`: `/dashboard`, `/market-data`, `/market-data/coverage`, `/analytics`, `/stocks`, `/backtests`.
4. **DOM Real Data & NO_TRADE UI Contract**:
   - Verified presentation of `NO_TRADE` decision, reference ATM strike (`24550`), and `NONE` preferred actionable contract.
   - Badge labels verified: `SOURCE: NSE OFFICIAL`, `MODE: REPLAY`, `MARKET_CLOSED`. No fake `LIVE` labels.
5. **Responsive Real Viewports**:
   - Desktop (1440x900), Tablet (768x1024), Mobile (390x844) verified with zero page-wide layout overflow.
6. **Error Capture & Role Security**:
   - 0 page errors, 0 500 errors, 0 404 errors, 0 HTMX failures.
   - Unauthenticated browser access to `/dashboard` redirects to `/login`.
7. **R1D Live Certification Status**:
   - `PENDING — DO NOT CLOSE`. Software test suite baseline preserved: **1309 / 1309 passed** (`CODE_CHANGED = NO`).
