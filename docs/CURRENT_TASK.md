# CURRENT TASK STATE

## Authoritative Current Task — Stage 2 Pre-G.8 Final Hardening (N1 + N2 + N3 ONLY)

- **Task**: Pre-G.8 Final Hardening — Muse Audit Notes Repairs (N1 + N2 + N3)
- **Phase**: Implementation & Verification Complete — 2026-09-07
- **Status**: `N1-N3 COMPLETE — READY FOR FINAL NEMOTRON AUDIT`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **HEAD Commit**: `58a9549`
- **Version**: `0.14.0` (strictly preserved)
- **Alembic head**: `478d723971bc` (no new migrations)
- **G.8 Status**: NOT STARTED — all work strictly restricted to Pre-G.8 final hardening (N1-N3)
- **Hardening Scope (N1, N2, N3)**:
  - **N1 DONE**: Gamma Profile Lot Quality
    - `OptionExposureRow`: added `@property def lot_source` and `@property def lot_quality` mapping to underlying source and quality fields.
    - `AdvancedExposureService.get_contract_exposures`: missing historical lot or `source_name is None` sets `hist_lot = None` and `lot_size = None` (no placeholder 1).
    - `AdvancedExposureService.get_gamma_profile`: enforces that if any contract has `lot_size_source == "UNAVAILABLE"`, `lot_size_quality == "UNAVAILABLE"`, `lot_source == "UNAVAILABLE"`, `lot_quality == "UNAVAILABLE"`, `lot_size is None`, or `lot_size <= 0`, gamma profile returns `None` (gamma flip returns `status="UNAVAILABLE"`).
    - Valid source-backed calculations remain intact.
  - **N2 DONE**: Strike Table None -> Zero Preservation
    - `AdvancedExposureService.get_strike_exposure_table`: preserves `None` for missing lot-scaled exposures rather than converting to `Decimal("0.00")`. Real calculated zero is preserved as `Decimal("0.00")`.
    - `app/templates/components/advanced_exposure_tab.html`: properly renders `—` when exposure is `None` using `is not none` Jinja test.
  - **N3 DONE**: Suppressed Max-Pain Candidate Payouts
    - `HistoricalOptionsAnalyticsService.get_option_chain_analytics`: when `LOT_UNAVAILABLE_MAX_PAIN_SUPPRESSED` is active, candidate strikes are preserved with `call_payout=None`, `put_payout=None`, and `total_payout=None` (no 1-scaled payouts).
- **Quality Gates (All PASS)**:
  - Focused repair suite (`tests/test_g8_pre_repair.py`): 53/53 passed in 26.69s (exit 0)
  - Regression suite (G4-G7 + repair): 161/161 passed (exit 0)
  - Full test suite: 1609/1609 passed (0 failed, 0 errors, 0 skipped, exit 0)
  - `uv run ruff check .`: All checks passed!
  - `uv run alembic current`: `478d723971bc (head)`
  - `uv run alembic heads`: `478d723971bc (head)`
  - `uv run alembic check`: No new upgrade operations detected.
  - `import app.main`: IMPORT OK
  - `git diff --check`: Clean (exit code 0)
  - `scripts/g8_pre_repair.py --verify`: All 10 assertions PASSED (exit code 0)
- **Database State (Read-Only Certified)**:
  - DerivativeContract: 75,962 | DerivativeDailyPrice: 1,732,338
  - FiiDerivativeStatistic: 0 | GIFT_NIFTY contracts: 0
  - Option lot=1: 74,902 | Option lot!=1: 0
  - Orphans: 0 | Duplicates: 0 | Semantic duplicates: 0 | Weekend DDP: 0
  - Stock: 3,652 | DailyPrice: 1,013,721 | IndicatorResult: 16,834
- **Strict Scope Boundaries**:
  - G.8 NOT STARTED.
  - No Q4-2025 F&O data acquired.
  - No new Alembic migrations.
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
