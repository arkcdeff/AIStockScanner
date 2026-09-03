# CURRENT TASK STATE

## Authoritative Current Task — Stage 2 Phase G.5 Official Corporate Events & Action Acquisition for Decision Context Completion

- **Task**: Official Corporate Events & Action Acquisition for Decision Context Completion (Stage 2 Phase G.5)
- **Phase**: Implementation Complete & Verified — 2026-09-03
- **Status**: `STAGE 2 — PHASE G.5 COMPLETE & VERIFIED`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **HEAD Commit**: `b316d38`
- **Version**: `0.14.0` (preserved)
- **Alembic head**: `478d723971bc` (no new migrations, existing schema strictly reused)
- **Official NSE Corporate Sourcing**:
  - Source Endpoints:
    - Actions: `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=01-06-2026&to_date=31-08-2026`
    - Calendar: `https://www.nseindia.com/api/event-calendar?index=equities&from_date=01-06-2026&to_date=31-08-2026`
    - Announcements: `https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=01-06-2026&to_date=31-08-2026`
  - Target Historical Window: `2026-06-01` to `2026-08-31`.
  - Acquired Files:
    - `data/reference/corporate/nse_corporate_actions_20260601_20260831.json` (871 rows, SHA256 `4f41b207e0a5b9999a7e0712e9e75170bb2db75f76a01170845731323875fa4e`)
    - `data/reference/corporate/nse_corporate_announcements_20260601_20260831.json` (52,500 rows, SHA256 `533198e464f620785d7b2d6b9c1eed92994b383ac1d5c1a5efa6f8bda5e2e4e6`)
    - `data/reference/corporate/nse_event_calendar_20260601_20260831.json` (2,783 rows, SHA256 `70bda00385b19f23b10b6d45005cf58039fe88c00201051f8c86607bfcfc3608`)
    - `data/reference/corporate/manifest.json`: Full provenance manifest.
- **Parser & Ingestion Implementation**:
  - `app/data/fundamental_parser.py`: Extended `CorporateAnnouncementParser` with `normalize_category(cls, desc: str | None) -> str` mapping exchange purposes to canonical 18 categories.
  - `app/services/nse_corporate_import_service.py`: Bulk-oriented, transactional, and idempotent ingestion service:
    - Resolves securities truthfully using `Stock.isin` then `Stock.symbol` (unmatched securities outside product universe cleanly skipped; 0 phantom stocks created).
    - Ingested 829 official corporate actions (`dividend`: 783, `split`: 12, `bonus`: 11, `rights`: 11, `buyback`: 10, `merger`: 3).
    - Ingested 52,063 official corporate announcements across 18 canonical categories with official broadcast dates (`an_dt`) and PDF archive URLs (`nsearchives.nseindia.com/corporate/...`).
    - Links corporate announcements (`DIVIDEND`, `STOCK_SPLIT`, `BONUS`) with `CorporateAction.id` via `ca_lookup`.
    - Idempotency Proven: Run 1 inserted 829 actions & 52,063 announcements; Run 2 had 0 inserted, 829 unchanged actions, 52,063 unchanged announcements.
- **Stock Intelligence Decision Context Unblocking**:
  - Verified on representative pilot stocks (`RELIANCE`, `HDFCBANK`, `TCS`, `INFY`, `SBIN`):
    - `corporate_events` domain transitioned from `UNAVAILABLE` to `AVAILABLE`.
    - Decision context completeness jumped from **75.0% to 87.5%** (7 of 8 domains available: technical, fundamental, valuation, delivery, sector, market_context, corporate_events; news remains unavailable).
    - Missing inputs: strictly `['news']`.
    - Real corporate evidence generated: e.g. TCS detected positive `CORPORATE_ORDER_WIN` factor from official contract disclosure.
- **Point-in-Time (PIT) Safety**:
  - Evaluated at `cutoff = 2026-08-20`: Disclosures from August 21+ are strictly excluded; zero future event leakage.
- **Quality Gates & Test Results**:
  - Dedicated G.5 test suite: 22 / 22 tests passing in `tests/test_corporate_events_g5.py`.
  - Regression test suite: 22 / 22 tests passing in `tests/test_market_context_g4.py` (44 total across G.4 + G.5).
  - Ruff: clean (`All checks passed!`).
  - Alembic: clean (`No new upgrade operations detected.`, head `478d723971bc`).
  - Authenticated browser rendering: HTTP 200 on `/stocks/{symbol}` for all pilot stocks with Corporate Events table and "PIT Safe" badge.
- **Strict Scope Boundaries**:
  - G.5 COMPLETE.
  - News remains UNAVAILABLE (no fake news created).
  - No synthetic corporate events; only official exchange disclosures.
  - No new Alembic migrations; existing models `CorporateAnnouncement` and `CorporateAction` strictly reused.
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
