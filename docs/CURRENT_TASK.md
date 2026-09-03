# CURRENT TASK STATE

## Authoritative Current Task — Stage 2 Phase G.6 Official Institutional Flow Acquisition & Market Context Completion

- **Task**: Official Institutional Flow Acquisition & Market Context Completion (Stage 2 Phase G.6)
- **Phase**: Implementation Complete & Verified — 2026-09-03
- **Status**: `STAGE 2 — PHASE G.6 COMPLETE & VERIFIED`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **HEAD Commit**: `97ebf38`
- **Version**: `0.14.0` (preserved)
- **Alembic head**: `478d723971bc` (no new migrations, existing schema strictly reused)
- **Official NSE Institutional Sourcing**:
  - Source Endpoint: `https://www.nseindia.com/api/reports?archives=[{"name":"CM - Category-wise Turnover","type":"archives","category":"capital-market","section":"equities"}]&date=DD-Mon-YYYY&type=equities&mode=single`
  - Target Historical Window: `2026-06-01` to `2026-08-31` (62 certified trading sessions).
  - Acquired Files:
    - `data/reference/institutional/raw/category_turnover_YYYYMMDD.xls` (62 official raw workbooks)
    - `data/reference/institutional/nse_fii_dii_cash_20260601_20260831.json` (62 sessions, SHA256 `c048a96c7eca7e810ff351e9f175c40f41c4c73bcb47ede6d91f9d54d13272b2`)
    - `data/reference/institutional/nse_fii_dii_cash_20260601_20260831.csv` (124 rows, SHA256 `53e026640997a0e68bfd80170ed038eea22112389172e552b74cfa0fd863323b`)
    - `data/reference/institutional/manifest.json`: Full provenance metadata.
- **Parser & Ingestion Implementation**:
  - `app/services/nse_institutional_import_service.py` (`NseInstitutionalImportService`):
    - Parses canonical CSV / JSON and computes rolling cumulatives (1D, 3D, 5D, 20D) chronologically per investor category.
    - Idempotently upserts into PostgreSQL `institutional_flows`.
    - Exactly 124 records ingested (62 FII, 62 DII).
    - Idempotency Proven: Run 1 inserted 124, updated 0; Run 2 inserted 0, updated 124.
- **Market Intelligence & Completeness Unblocking**:
  - `institutional_score` successfully populated across all 62 sessions in `MarketIntelligenceSnapshot`.
  - Institutional factor transitioned from `UNAVAILABLE` to `AVAILABLE`.
  - Composite Market Intelligence data completeness reached **70.0%** (`data_completeness_percent = 70.0`).
  - Missing factors reduced from 6 to 5 (`['global', 'gift_nifty', 'currency', 'crude', 'macro']`).
  - Stock Intelligence completeness on pilot stocks confirmed at **87.5% completeness** (7 of 8 domains available; news remains unavailable).
- **Quality Gates & Test Results**:
  - Dedicated G.6 test suite: 8 / 8 tests passing in `tests/test_institutional_flow_g6.py`.
  - G.5 test suite: 22 / 22 tests passing in `tests/test_corporate_events_g5.py`.
  - G.4 test suite: 22 / 22 tests passing in `tests/test_market_context_g4.py`.
  - Ruff: clean (`All checks passed!`).
  - Alembic: clean (`No new upgrade operations detected.`, head `478d723971bc`).
  - Authenticated browser rendering: HTTP 200 on `/market-intelligence` with rendered FII/DII cards, net values, and combined score.
- **Strict Scope Boundaries**:
  - G.6 COMPLETE.
  - No scanner formulas modified.
  - No Stock Intelligence scoring methodology modified.
  - No new Alembic migrations; existing `InstitutionalFlow` table reused.
  - No commit, push, tag, or version bump.

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
