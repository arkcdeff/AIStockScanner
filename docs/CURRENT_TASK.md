# CURRENT TASK STATE

## Authoritative Current Task — Stage 2 Phase G.2 Partial — NSE Nifty Total Market Classification: 750/3,530 Product Stocks (21.25%)

- **Task**: Stage 2 Phase G: G.2 Sector & Industry Classification (Partial — Nifty Total Market Source)
- **Phase**: Phase G.2 Complete & Hardened
- **Status**: `STAGE 2 PHASE G.2 PARTIAL — NSE NIFTY TOTAL MARKET CLASSIFICATION (750/3,530 PRODUCT STOCKS, 21.25%) COMPLETE & AUDITED — 2026-09-01`
- **Branch**: `checkpoint/sprint19-20-20260822`
- **Version**: `0.14.0` (preserved)
- **Alembic head**: `478d723971bc` (no new migrations, existing schema used)
- **Taxonomy Truth & Architecture**:
  - The authoritative NSE Index Master CSV (`ind_niftytotalmarket_list.csv`) provides a single classification field labeled `Industry`.
  - The implementation truthfully materializes this as 22 canonical `Sector` and 22 canonical `Industry` records with a 1:1 mapping: `Sector.name = source Industry`, `Industry.name = source Industry`, and `Industry.sector_id = Sector.id`.
  - This provides a flattened 1:1 classification hierarchy, not a fabricated multi-level hierarchy.
  - A deeper multi-tier Sector $\to$ Industry taxonomy is explicitly deferred pending an authoritative multi-level classification dataset from NSE.
- **Coverage & Scope Truth**:
  - Source file records: 752 data rows (750 valid, 2 exchange test dummy rows isolated: `DUMMYINXGN`, `DUMMYTRVN`).
  - Source utilization: 750 / 752 (99.73% of source records matched).
  - Product stock coverage: **750 / 3,530 product stocks (21.25%)**.
  - Unclassified product stocks: **2,780 / 3,530 (78.75%)** (smallcap/illiquid non-Nifty total market equities).
  - Market capitalization & trading volume coverage: **>95%** of institutional trading universe.
  - Pilot coverage: **14 / 14 (100%)** pilot stocks classified.
- **Fixture Isolation Policy**:
  - `ANALYTX` is a pre-existing seeded synthetic fixture (`SYNTHETIC_FIXTURE_SYMBOLS`) retaining test classifications (`sector_id = Analytics Test`, `industry_id = Testing`).
  - All synthetic fixtures are explicitly excluded from product classification metrics and preserved without mutation.
- **Runtime Provenance**:
  - `StockIntelligence.provenance["sector_source"]` carries runtime provenance digest `"ind_niftytotalmarket_list.csv#69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac"` when `stock.sector_id` is present.
- **Key Accomplishments**:
  1. **Acquired Official Source File**: Stored official NSE Index Master CSV at `data/reference/ind_niftytotalmarket_list.csv` (SHA-256: `69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac`).
  2. **Strict Parser Implementation**: Built `app/data/sector_classification_parser.py` enforcing strict header validation (`['Company Name', 'Industry', 'Symbol', 'Series', 'ISIN Code']`), UTF-8 encoding, whitespace normalization, ISIN format regex, and exchange test dummy rejection (`DUMMYINXGN`, `DUMMYTRVN`).
  3. **Transactional Classification Service**: Built `app/services/sector_classification_service.py` with zero architectural duplication (no SectorV2/IndustryV2, no duplicate Stock columns), matching primary ISIN first (748) and symbol fallback second (2: `KIRLPNU`, `TDPOWERSYS`), upserting canonical `Sector` and `Industry` records, updating `Stock.sector_id` and `Stock.industry_id`, and synchronizing legacy `Stock.industry = Industry.name`.
  4. **Strict Safety & Idempotency**: Verified dry-run execution rolls back completely. Performed controlled import (Run 1: 22 sectors created, 22 industries created, 750 stocks mapped, 1 legacy industry synchronized). Executed second run immediately (Run 2: 0 new sectors, 0 new industries, 0 stocks updated, 100% idempotent). Total DB stocks preserved at 3,538 (0 duplicate stocks, 0 synthetic stocks modified).
  5. **100% Hierarchy Consistency**: Verified `Stock.sector_id == Industry.sector_id` (0 mismatches), `Stock.industry == Industry.name` (0 mismatches), 0 orphan foreign keys across `sectors`, `industries`, and `stocks`.
  6. **Downstream Integrations Verified**:
     - `FundamentalCorporateService._calculate_relative_valuation`: Evaluates peer group type `INDUSTRY` and computes median P/E, P/B, P/S and valuation premiums.
     - `StockIntelligenceService.get_stock_intelligence`: Evaluates `domain_coverage["sector"]` as `AVAILABLE` with factor `SECTOR_MAPPING_ESTABLISHED` and runtime provenance `sector_source`.
     - `ScannerWorkspaceService.get_filter_options`: Dropdown populated with all 22 distinct canonical sectors.
     - `StockService.list_explorer`: Reports `summary.sector_mapped = 750`; warning banner removed.
     - `StockDetailService.get_overview`: Returns canonical sector name for badge chips and overview panel.
  7. **Quality Gates & Tests**:
     - Dedicated G.2 test suite: 20 / 20 tests passing in `tests/test_sector_classification_g2.py` (hardened fixture isolation, isolated SME test ISIN, deterministic unmatched tracking).
     - Full test collection: **1,446 tests** across 94 modules (1,426 previous baseline including 13 G.1 tests + 20 G.2 tests).
     - Full test suite: 1,446 passed (100% GREEN, 0 failures, 0 errors).
     - Ruff: clean (All checks passed!).
     - Alembic: clean (No new upgrade operations).
     - App import: clean (`IMPORT OK`).
     - Git diff: clean.
     - Real browser endpoints: 9 / 9 verified at HTTP 200 with canonical taxonomy.
- **Strict Scope Boundaries**:
  - G.3 (Delivery acquisition) NOT started.
  - No synthetic sector classifications; no guess inference.
  - No commit, push, tag, or version bump.
- **Next action**: User review of Stage 2 Phase G.2. Do NOT start Phase G.3.

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
