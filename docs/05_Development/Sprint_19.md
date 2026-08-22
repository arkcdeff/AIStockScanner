# Sprint 19 — Trading Foundation & Paper Trading

**Status:** IN PROGRESS
**Target release:** v0.15.0 (do NOT bump version until the sprint completes)
**Started:** Task 19.1 (Trading Foundation — PAPER ONLY) — COMPLETE & VERIFIED;
Task 19.1A (NSE Market Data Ingestion + Backfill + EOD Automation) — COMPLETE & VERIFIED
**Version at start:** 0.14.0 (released end of Sprint 18)

---

## Objective

Build the foundation for **simulated / paper trading** — the persistence, domain models, order
lifecycle, repositories, schemas, and a foundational service. This sprint is explicitly
**PAPER-ONLY** and must not introduce any live brokerage, execution engine, risk engine,
signal-to-order automation, or user-facing API/UI. Live trading is deferred to a future sprint
behind a `BrokerAdapter` abstraction (Task 19.2+).

---

## In scope (Sprint 19)

- Paper trading accounts, orders, executions, and positions (persistence + lifecycle).
- Order state machine and idempotent creation.
- Broker abstraction interface (paper mock only) — Task 19.2.
- (Deferred, explicit): cash/position mutation on fills, P&L, execution engine internals, risk,
  live brokerage, API/UI.

## Out of scope (Sprint 19)

- Any `execution_mode = "live"` path (hard-rejected at account + order creation).
- Real broker APIs, credential handling, order routing.
- Signal → order automation; strategy execution; risk limits.
- REST/UI surface for trading (reuse existing auth/permission patterns only when added later).

---

## Tasks

| Task | Title | Status |
|------|-------|--------|
| 19.1 | Trading Foundation (PAPER ONLY) | **COMPLETE & VERIFIED** |
| 19.1A | NSE Market Data Ingestion + Historical Backfill + EOD Automation | **COMPLETE & VERIFIED** |
| 19.1B | UI Shell + Navigation + Dashboard Redesign | **COMPLETE & VERIFIED** |
| 19.1C | Point-in-Time Historical Data Core Expansion | **COMPLETE & VERIFIED** |
| 19.1D-1 | Derivative Contract Master + Source Registry | **COMPLETE & VERIFIED** |
| 19.1D-2 | Historical NSE Futures EOD Import | **COMPLETE & VERIFIED** |
| 19.1D-3 | Historical NSE Options EOD Import | **COMPLETE & VERIFIED** |
| 19.1D-4 | Historical Option Chain Reconstruction | **COMPLETE & VERIFIED** |
| 19.1D-5 | Futures OI + Buildup + Basis + Rollover Analytics | **COMPLETE & VERIFIED** |
| 19.1D-6 | PCR + Max Pain + OI Walls + Expiry Analytics | **COMPLETE & VERIFIED** |
| 19.1D-7 | Participant-wise OI / Volume + FII Derivatives | **COMPLETE & VERIFIED** |
| 19.1D-8 | Coverage + Data Quality + Historical Replay Hardening | **COMPLETE & VERIFIED** |
| 19.1E | Fundamental + Corporate Intelligence | **COMPLETE & VERIFIED** |
| 19.1F | News + Government Policy Intelligence | **COMPLETE & VERIFIED** |
| 19.1G | Stock 360° Research + Opportunity Intelligence | **COMPLETE & VERIFIED** |
| 19.1G-A1 | Advanced Options Foundation | **COMPLETE & VERIFIED** |
| 19.1G-A2 | Estimated GEX + Gamma Flip + Advanced Exposure Analytics | **COMPLETE & VERIFIED** |
| 19.1G-A3 | Options Opportunity + Expiry + Moneyness + Strike Selection | **COMPLETE & VERIFIED** |
| 19.1I | Historical / Walk-Forward Validation | **COMPLETE & VERIFIED** |
| 19.1J | Historical Data Expansion & Coverage Certification | **COMPLETE & VERIFIED** |
| 19.1H | Forecasting Engine | PLANNED |
| 19.2 | Broker Abstraction (paper-only `BrokerAdapter` + paper broker mock) | POSTPONED (owner priority change) |

---

## Task 19.1 — Trading Foundation (PAPER ONLY) — COMPLETE & VERIFIED

### Objective
Establish the data model and lifecycle for paper trading: accounts that own cash and positions,
orders with a strict status machine, executions (fills) attached to orders, and positions stored
for later reconciliation. No broker, no execution, no risk, no API/UI.

### What was built
- **Models** (`app/models/`): `TradingAccount`, `TradingOrder`, `TradingExecution`,
  `TradingPosition` (SQLAlchemy 2.0). Enums are `str` tuples + `CheckConstraint` (project
  convention — no Python `Enum`). Money = `Decimal`/`Numeric(18,4)`; quantity = `Integer`;
  timestamps = timezone-aware `DateTime(timezone=True)`.
- **Account**: `execution_mode` ∈ {`paper`, `live`}; default `paper`; **live is hard-rejected**
  (`UnsupportedExecutionModeError`) at `create_account` and at `create_order`.
- **Order lifecycle**: statuses `NEW`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCELLED`,
  `CANCEL_PENDING`, `REPLACED`, governed by `ORDER_STATUS_TRANSITIONS`. `transition_order`
  enforces valid transitions and terminal-state immutability; `reject_order` is terminal;
  `create_order` is idempotent on `(account_id, client_order_id)` (DB unique + race test).
- **Execution**: fill store (qty / price / source) attached to an order; no P&L math.
- **Position**: persistence-only (qty / direction / avg price); **no avg-price recompute or
  realized/unrealized P&L** (explicitly deferred to Task 19.4).
- **Repositories** (`app/repositories/trading.py`): `TradingAccountRepository`,
  `TradingOrderRepository`, `TradingExecutionRepository`, `TradingPositionRepository` — bounded,
  account-scoped, `get_by_client_order_id`, `list_by_account`, `list_open_orders`.
- **Schemas** (`app/schemas/trading.py`): three-layer `Base`/`Create`/`Read`; `model_validator`
  enforces market ⇒ no `limit_price`, limit ⇒ positive `limit_price`; `_validate_enum` mirrors
  model constraints.
- **Service** (`app/services/trading_foundation.py`): `TradingAccountService`,
  `TradingOrderService`, `ORDER_STATUS_TRANSITIONS`, `validate_order_transition`,
  `is_terminal_order_status`; errors `TradingFoundationError`, `TradingAccountNotFoundError`,
  `TradingAccountInactiveError`, `DuplicateClientOrderIdError`, `UnsupportedExecutionModeError`,
  `InvalidOrderTransitionError`, `InvalidOrderError`.
- **Migration** `95a0be65d4a7` (down_revision `5f7b2c9d4e3a`): single head, creates
  `trading_accounts`, `trading_orders`, `trading_executions`, `trading_positions`. Applied to dev
  + test DBs; `alembic check` clean.

### Files
- `app/models/trading_account.py`, `app/models/trading_order.py`, `app/models/trading_execution.py`,
  `app/models/trading_position.py` (created; registered in `app/models/__init__.py`)
- `app/repositories/trading.py` (created)
- `app/schemas/trading.py` (created)
- `app/services/trading_foundation.py` (created)
- `migrations/versions/95a0be65d4a7_add_trading_foundation_tables.py` (created)
- `tests/test_trading_foundation.py` (created, 34 tests)
- `tests/conftest.py` (added 4 trading tables to the truncate list)

### Verification (all gates GREEN)
- `alembic current` = `alembic heads` = `95a0be65d4a7 (head)`; `alembic check` clean.
- `ruff check .` → All checks passed! (including new files).
- `python -c "import app.main"` → IMPORT OK.
- `git diff --check` → clean (only benign LF→CRLF EOL-normalization warning).
- `pytest --collect-only` → 995 test functions / 1066 tests (with parametrization).
- Full suite **×3** → **1066 passed** each run (0 failed, 0 error); only unrelated
  Starlette/httpx deprecation warnings.

### Acceptance criteria — met
- [x] Paper-only foundation; no live broker/execution/risk/API/UI.
- [x] `live` execution mode rejected at account + order creation.
- [x] Order lifecycle state machine with terminal immutability.
- [x] Idempotent order creation on `(account_id, client_order_id)`.
- [x] Decimal money, integer quantity, aware timestamps.
- [x] Single Alembic migration, single head, applied to dev + test DBs.
- [x] Repositories + schemas + foundational service.
- [x] 34 focused tests; full suite green ×3; ruff + alembic + import + diff clean.
- [x] Docs updated (this file, `CURRENT_TASK.md`, `PROJECT_STATE.md`, `AI_HANDOFF.md`).

### Known limitations (scope boundaries, by design)
- No cash/position mutation on fills (Task 19.4).
- No execution engine (Task 19.3).
- No risk guards (Task 19.5).
- No live trading (Task 19.2+ behind `BrokerAdapter`).
- No REST/UI (Task 19.6).
- Positions are stored only — no avg-price recompute or realized/unrealized P&L yet.

---

## Task 19.1A — NSE Market Data Ingestion + Historical Backfill + EOD Automation — COMPLETE & VERIFIED

### Objective
Fix the owner's market-data experience: the UI previously hardcoded `nse_download_blocked`,
had no CSRF/size guards, and offered no download/backfill/coverage UX. Root cause was the UI
layer — the backend parser+importer already handled the owner's format. 19.1A unified UI, REST
API, CLI, and scheduler onto one code path and added backfill, coverage, readiness, upload
archival, and EOD retry/catch-up.

### What was built
- **Parser** (`app/data/nse_parser.py`): `detect_format` → `FORMAT_LEGACY` / `FORMAT_UDIFF` /
  `FORMAT_CM_UDIFF` (discriminated by `LASTPRIC`) / `FORMAT_DELIVERY` / `FORMAT_UNKNOWN`;
  `parse_bhavcopy_detailed` → `BhavcopyParseResult` (records, skipped series, errors,
  delivery_missing, format); same-day delivery merge by symbol (`DELIV_QTY`/`DELIV_PER`);
  `parse_bhavcopy` kept backward-compatible. Legacy `27-Jul-2026` dates parse cleanly.
- **Importer** (`app/services/nse_import.py`): mode constants (`upload` / `date_download` /
  `latest_download` / `range_backfill` / `auto_eod` / `auto_catchup`); `import_bytes` now
  records mode/format/rows_discovered/rows_skipped_series/delivery_rows/delivery_missing/
  has_delivery_columns/duration_ms/checksum/parse_errors/valid_rows; failed jobs also record
  mode + checksum.
- **Orchestrator** (`app/services/market_data_service.py`, new): `archive_bytes` (sanitized
  filename → `data/raw/nse/YYYY/MM` + `.sha256` sidecar), `import_upload`,
  `download_and_import_date`, `download_and_import_latest`, `backfill` (missing weekdays
  only), `missing_dates`, `coverage` (per-day status complete/partial/missing/weekend),
  `scanner_readiness` (20/50/100/200-session thresholds), `data_status`.
- **REST API** (`app/api/routes/market_data.py`): `POST /backfill` (validates
  start ≤ end and range ≤ `MAX_RANGE_DAYS`=366), `GET /coverage`, `GET /readiness`,
  `GET /status`.
- **Page routes** (`app/api/routes/market_data_pages.py`, rewritten): full-page GET with
  stage CSRF + flash alerts from query params; CSRF-verified POSTs for import (with empty
  guard + `max_upload_size`→413 + archival), download-latest, download-date, backfill,
  run-indicators, run-scanners; HTMX partials for prices/imports/coverage.
- **Templates**: `market_data.html` reworked (status cards, readiness, forms, flash alerts,
  imports + coverage + prices), new `components/market_coverage_table.html`, enhanced
  `components/market_imports_table.html` (mode/format/delivery/duration).
- **EOD pipeline** (`app/services/eod_pipeline.py`): `run_scanners` parameter (defaults to
  config) gates stage 3.
- **EOD scheduler** (`app/scheduler/eod_scheduler.py`): bounded retry loop — retries **only**
  when `failed_stage == "import"`; `run_market_data_catchup`; `status()` now returns
  `eod_retry_enabled` / `eod_max_retries` / `eod_run_scanners` / `eod_auto_catchup` /
  `last_catchup`.
- **Config** (`app/core/config.py`): `eod_scheduler_retry_enabled=True`,
  `eod_scheduler_max_retries=3`, `eod_scheduler_retry_minutes=15`,
  `eod_scheduler_run_scanners=True`, `eod_scheduler_auto_catchup=False`,
  `eod_scheduler_catchup_max_days=7`.
- **Startup** (`app/main.py`): catch-up wired into lifespan after `scheduler.start()`.
- **CLI** (`app/cli/import_nse.py`): new `range`, `missing`, `status` subcommands; `date` and
  `latest` pass explicit modes.
- **Tests**: `tests/test_market_data_19a.py` — 34 tests, zero live network (MockTransport),
  covering format detection, parser, delivery merge, importer audit fields, upload archival,
  coverage, readiness, status, API validation (backfill range/semantics), CSRF/security,
  page actions, pipeline scanner-skip, and scheduler retry/catch-up.

### Real-file validation
Owner's actual files were validated through the parser (read-only, no DB writes):
`20260727_NSE.csv` → legacy, 3453 rows → 3186 records (EQ 2413 / SM 324 / BE 285 / ST 125 /
BZ 39), 267 skipped series, 0 errors; `20260728_NSE.csv` → legacy, 3413 → 3163 records,
250 skipped, 0 errors. No delivery columns in either file. BSE files (`20260727_BSE.csv`,
`20260728_BSE.csv`) are a different format and out of scope (NSE task).

### Files
- `app/data/nse_parser.py` (modified)
- `app/services/nse_import.py` (modified)
- `app/services/market_data_service.py` (created)
- `app/api/routes/market_data.py` (modified), `app/api/routes/market_data_pages.py` (rewritten)
- `app/schemas/market_data.py` (modified)
- `app/services/eod_pipeline.py`, `app/scheduler/eod_scheduler.py`, `app/main.py`,
  `app/core/config.py` (modified)
- `app/templates/market_data.html`, `app/templates/components/market_imports_table.html`
  (modified), `app/templates/components/market_coverage_table.html` (created)
- `app/cli/import_nse.py` (modified)
- `tests/test_market_data_19a.py` (created, 34 tests)
- `docs/MARKET_DATA_GUIDE.md` (created)

### Verification (all gates GREEN)
- `alembic current` = `alembic heads` = `95a0be65d4a7 (head)`; `alembic check` → **no
  migration needed**.
- `ruff check .` → All checks passed!
- `python -c "import app.main"` → IMPORT OK.
- `pytest --collect-only` → 1100 tests (1066 + 34 new).
- Full suite **×3** → **1100 passed** each run (0 failed, 0 error); only unrelated
  Starlette/httpx deprecation warnings.
- `git diff --check` → clean (only benign LF→CRLF EOL-normalization warning).
- Real owner NSE files parse with 0 errors (see above).

### Known limitations (scope boundaries, by design)
- NSE blocks plain automation → downloads may report `blocked_403`; manual upload remains the
  reliable fallback (honest, no anti-bot bypass).
- BSE-format files are out of scope.
- Delivery enrichment requires a same-day delivery file; `delivery_missing` stays None (never
  fabricated zero).
- No migration created — reuses existing `import_jobs`/`stocks`/`daily_prices` tables.

---

## Task 19.1B — UI Shell + Navigation + Dashboard Redesign — COMPLETE & VERIFIED

### Objective
Modernize the AIStockScanner web UI: replace the manual sidebar+navbar duplication with a
DRY shell layout, redesign the dashboard with KPI cards and data-driven sections, standardize
components, implement dark/light mode, add responsive mobile support, and ensure all pages
use a consistent layout pattern.

### What was built
- **Design System** (`app/static/css/design-system.css`): 342-line CSS custom properties
  library covering layout, typography, spacing, radius, shadows, colors (light + dark),
  status colors, and base component styles (cards, tables, badges, forms, KPI, empty state,
  loading, alerts, page header, status dots, utilities).
- **Shell Layout** (`app/templates/layouts/shell.html`): extends `base.html`; provides
  sidebar (desktop), mobile offcanvas, topbar, flash messages, and `{% block content %}`.
- **Sidebar** (`app/templates/components/sidebar.html`): grouped navigation (Main/Market/
  Analytics/Monitoring/System/Admin), permission-aware (market_intelligence, backtests,
  admin sections gated), active state via `request.url.path`, collapsed/tooltip support,
  user footer with avatar/role.
- **Topbar** (`app/templates/components/topbar.html`): hamburger (mobile), sidebar toggle
  (desktop), page title block, status block, theme toggle, user dropdown (profile/settings/
  sessions/logout).
- **Shell JS** (`app/static/js/shell.js`): sidebar collapse with `localStorage`, theme
  toggle (`light`/`dark`/`system`), tooltip init/reinit on HTMX swaps, system theme
  media listener.
- **Dashboard CSS** (`app/static/css/dashboard.css`): rewritten — `.app-sidebar` (fixed,
  260px/72px collapsed, transition), `.app-topbar` (sticky, 56px), `.app-main` (flex column,
  margin-left offset), `.app-content`, `.app-offcanvas` (mobile), sidebar/topbar
  sub-components, responsive breakpoints.
- **Dashboard redesigned** (`app/templates/dashboard.html`): KPI row (Market Regime/Latest
  NSE Data/Scanner Matches/Total Stocks), Market Intelligence + Data Health cards, Scanner
  Results + Notifications, Top Rated Stocks + Market Summary, Recent Imports + Pipeline
  Status, Stock Search.
- **Login page** (`app/templates/auth/login.html`): redesigned with design system CSS
  (centered card, brand logo, auth-footer).
- **Auth CSS** (`app/static/css/auth.css`): rewritten using design system variables, dark
  mode support via `data-bs-theme`.
- **Pages migrated** (16+ templates): all migrated to `extends "layouts/shell.html"`:
  dashboard, stocks, scanner_run, scanner_library, scanner_results, analytics, watchlists,
  portfolio, pipeline, scheduler, alerts, ai, indicators, market_data, market_intelligence,
  notifications (index/settings/history), admin (users/roles/permissions/user_detail/
  role_detail), backtests (workspace/runs/run_detail/strategies), stock_detail, auth
  (profile/settings/sessions).
- **Tests** (`tests/test_ui_shell.py`): 26 UI tests covering login page design system,
  shell layout, sidebar navigation, theme toggle, sidebar toggle, admin/permission gating,
  CSRF preservation, HTMX routes, dead sidebar links, no trading links, dark mode attribute,
  page header standardization.

### Files
- `app/static/css/design-system.css` (created)
- `app/static/css/dashboard.css` (rewritten)
- `app/static/css/auth.css` (rewritten)
- `app/templates/layouts/shell.html` (created)
- `app/templates/components/sidebar.html` (rewritten)
- `app/templates/components/topbar.html` (rewritten)
- `app/templates/components/flash.html` (modified)
- `app/templates/base.html` (modified — added design-system.css + shell.js)
- `app/static/js/shell.js` (created)
- `app/static/js/dashboard.js` (simplified)
- `app/templates/dashboard.html` (rewritten)
- `app/templates/auth/login.html` (rewritten)
- 16+ page templates migrated to shell.html
- `tests/test_ui_shell.py` (created, 26 tests)
- `docs/UI_GUIDE.md` (created)

### Verification — FINAL GATE (all GREEN)

#### Regression history (formally recorded)
| Gate | Result |
|------|--------|
| First full suite after 19.1B implementation | **1091 passed, 23 FAILED** (UI/template regressions) |
| After resolving all 23 regressions — focused UI + core check | **104 passed, 0 failed, 0 errors** |
| First full repository suite after UI reconciliation | **1114 passed, 0 failed, 0 errors** |
| Full repository suite after security/hygiene fixes (final) | **1120 passed, 0 failed, 0 errors** |

#### 23 UI/template regression failures — RESOLVED
All 23 failures were template/route context regressions introduced during the shell migration.
Root causes: missing template context keys (`scanners_by_category`, `pipeline_status`, `summary`,
`total_pages`) and renamed UI labels (`Recent runs`, `Run history`, `Total Value`,
`Add to Watchlist`, `Recent Alert Events`, `Portfolio Value`, `Alert Rules`).
All 23 were corrected and verified before the full 1114-pass suite.

#### Lightweight gate verification (current workspace — live confirmed)
- `ruff check .` → **All checks passed!**
- `alembic current` → **`95a0be65d4a7 (head)`** (single head; `alembic heads` agrees)
- `alembic check` → **No new upgrade operations detected.** (no migration for 19.1B — UI-only)
- `python -c "import app.main"` → **IMPORT OK**
- `git diff --check` → **clean** (exit 0; only benign LF→CRLF EOL normalization warnings)
- `pytest --collect-only -q` → **1120 tests collected** (confirmed live)
- All sidebar links return 200 (no dead links).
- HTMX routes return 200.
- Dark mode `data-bs-theme` attribute applied on all shell pages.
- CSRF tokens present on all protected pages.
- Permission-gated links hidden from unauthorized users.

#### No migration for 19.1B (UI-only changes)
#### No version bump for 19.1B
#### No commit/push/tag

### Known limitations (scope boundaries, by design)
- No new migration (UI-only changes).
- Login page does not use shell.html (intentional — no sidebar/topbar on auth pages).
- Old `layouts/app.html` still exists (backward-compatible; superseded by `layouts/shell.html`).
- `stock_detail.css` is a duplicate of old dashboard.css (to be cleaned up in future sprint).

---

## Next phase (Owner Roadmap Change)

The owner has prioritized building the comprehensive historical research platform before broker abstraction:
1. **19.1C** Historical Data Core Expansion
2. **19.1D** NSE Historical Futures & Options
3. **19.1E** Fundamental + Corporate Intelligence
4. **19.1F** News + Government Policy Intelligence
5. **19.1G** Stock 360° Research
6. **19.1H** Stock/Index/F&O Probability Forecasting
7. **19.1I** Historical Validation / Walk-Forward Evaluation

Broker Abstraction (paper-only `BrokerAdapter`) and execution will resume following the historical platform expansion.

---

## Task 19.1D-4 — Historical Option Chain Reconstruction — COMPLETE & VERIFIED

### Objective
Reconstruct point-in-time safe EOD historical option chains on demand for any underlying symbol and trade date without raw database table duplication.

### What was built
- **Service**: `HistoricalOptionChainService` (`app/services/historical_option_chain.py`).
- **Exact Decimal Strike Pairing**: Pairs Call and Put sides per strike price with `moneyness` classification (`ITM`, `OTM`, `ATM`).
- **Spot Fallback Hierarchy**: `DerivativeDailyPrice.underlying_value` -> `MarketIndexPrice.close` (indices) / `DailyPrice.close` (stocks).
- **REST API & Web UI**: `GET /api/v1/market-data/derivatives/options/chain`, `GET /api/v1/market-data/derivatives/options/expiries`, and HTMX partial `GET /market-data/derivatives/options/chain/table`.

---

## Task 19.1D-5 — Futures OI, Buildup, Basis & Rollover Analytics — COMPLETE & VERIFIED

### Objective
Provide point-in-time EOD futures analytics across contract curves without mutating existing schemas.

### What was built
- **Service**: `HistoricalFuturesAnalyticsService` (`app/services/historical_futures_analytics.py`).
- **Buildup Classification**: Deterministic classification (`LONG_BUILDUP`, `SHORT_BUILDUP`, `SHORT_COVERING`, `LONG_UNWINDING`, neutral states) with noise filter thresholds.
- **Basis & Curve Shape**: Spot basis, annualized basis pct, term structure slope classification (`UPWARD_SLOPING`, `DOWNWARD_SLOPING`, `FLAT_CURVE`), and adjacent calendar spreads.
- **Rollover Analytics**: Near/Next/Far OI share percentages, `rollover_next_pct`, and expiry window flags.
- **REST API & Web UI**: `GET /api/v1/market-data/derivatives/futures/analytics`, `GET /api/v1/market-data/derivatives/futures/rollover`, and HTMX partial `GET /market-data/derivatives/futures/analytics/table`.

---

## Task 19.1D-6 — Point-in-Time Options Analytics — COMPLETE & VERIFIED

### Objective
Implement options analytics (PCR, Max Pain, OI Walls, Term Structure) on top of reconstructed historical option chains.

### What was built
- **Service**: `HistoricalOptionsAnalyticsService` (`app/services/historical_options_analytics.py`).
- **PCR & Max Pain**: $\text{PCR}_{OI}$ and $\text{PCR}_{Volume}$ with observation coverage tracking. Payout-minimizing Max Pain strike with deterministic tie-breaking.
- **OI Walls & Unwinding**: Static Call/Put Walls, Fresh Call/Put Walls ($\Delta OI > 0$), and Call/Put Unwinding levels ($\Delta OI < 0$).
- **REST API & Web UI**: `GET /api/v1/market-data/derivatives/options/analytics`, `GET /api/v1/market-data/derivatives/options/expiry-analytics`, and HTMX partial `GET /market-data/derivatives/options/analytics/cards`.

---

## Task 19.1D-7 — Participant-wise Derivatives Positioning — COMPLETE & VERIFIED

### Objective
Ingest and analyze participant-wise open interest, volume, and FII derivatives statistics.

### What was built
- **Models**: `DerivativeParticipantPosition` (`derivative_participant_positions`) and `FiiDerivativeStatistic` (`fii_derivative_statistics`) (Alembic `e91f4a5678ab`, ORM models count: 51).
- **Parsers & Service**: `ParticipantOpenInterestParser`, `ParticipantTradingVolumeParser`, `FiiDerivativesStatisticsParser`, `DerivativeParticipantImportService`, and `HistoricalParticipantAnalyticsService`.
- **FII Metrics**: FII Index Futures Net (`Long - Short`), Long/Short Ratios, Day-over-Day changes, and category concentration shares.
- **Monetary Units**: Explicitly documented `INR_CRORE` monetary units for FII turnover and OI amounts.
- **REST API & Web UI**: `GET /api/v1/market-data/derivatives/participants`, `GET /api/v1/market-data/derivatives/fii`, and HTMX partial `GET /market-data/derivatives/participants/cards` on `/market-data`.

---

## Task 19.1D-8 — Derivatives Coverage, Quality & Point-in-Time Safe Historical Replay — COMPLETE & VERIFIED

### Objective
Hardening task for parent Sprint 19.1D providing 7-dimension platform derivatives coverage, data quality reporting, and point-in-time safe historical derivatives knowledge state reconstruction.

### What was built
- **Coverage Service**: `DerivativeCoverageService` (`app/services/derivative_coverage_service.py`) assessing 7 dataset dimensions (`FO_CONTRACT_MASTER`, `FO_FUTURES_EOD`, `FO_OPTIONS_EOD`, `FO_PARTICIPANT_OI`, `FO_PARTICIPANT_VOLUME`, `FO_FII_DERIVATIVES`, and Option Chains). Scope descriptions explicitly demarcate imported scope from exchange-wide scope. `missing_session_count` returned as `"UNKNOWN"`.
- **Replay Service**: `HistoricalDerivativesReplayService` (`app/services/historical_derivatives_replay.py`) orchestrating on-demand historical knowledge state reconstruction for symbol $U$ on date $T$ as of evaluation cutoff $A$. Enforces `available_at <= as_of_date` filtering with zero lookahead bias and returns provenance audit trail (`ReplayProvenanceItem`). Reuses existing D-4, D-5, D-6, and D-7 services without table additions (ORM model count remains at **51**).
- **REST API & Web UI**: REST endpoints `GET /api/v1/market-data/derivatives/coverage`, `GET /api/v1/market-data/derivatives/quality`, `GET /api/v1/market-data/derivatives/replay`, and HTMX UI partial `GET /market-data/derivatives/replay/cards` rendering the **Historical Replay** tab on `/market-data`.
- **Verification**: Focused tests (`tests/test_derivatives_replay_and_coverage.py`: 4 passed), preservation suite (110 passed), full repo suite (**1230 passed, 0 failed, 0 errors**), ruff clean, alembic clean (`e91f4a5678ab`), git diff clean.

---

## Parent Sprint 19.1D Final Status Matrix

| Sub-Sprint | Module / Feature Area | ORM Models | API / UI Endpoints | Unit Tests | Verification Status |
|---|---|---|---|---|---|
| **19.1D-1** | Contract Master & Lot-Size History | `DerivativeContract`, `DerivativeLotSizeHistory` | `/derivatives/contracts` | 13 | **COMPLETE & VERIFIED** |
| **19.1D-2** | Historical NSE Futures EOD Ingestion | `DerivativeDailyPrice` | `/derivatives/futures/coverage` | 6 | **COMPLETE & VERIFIED** |
| **19.1D-3** | Historical NSE Options EOD Ingestion | Reused `DerivativeDailyPrice` | `/derivatives/options/coverage` | 6 | **COMPLETE & VERIFIED** |
| **19.1D-4** | Historical Option Chain Reconstruction | Reused Services & Models | `/derivatives/options/chain` | 5 | **COMPLETE & VERIFIED** |
| **19.1D-5** | Futures OI, Buildup, Basis & Rollover Analytics | Reused Services & Models | `/derivatives/futures/analytics` | 5 | **COMPLETE & VERIFIED** |
| **19.1D-6** | Options Analytics (PCR, Max Pain, OI Walls) | Reused Services & Models | `/derivatives/options/analytics` | 6 | **COMPLETE & VERIFIED** |
| **19.1D-7** | Participant OI / Volume & FII Derivatives | `DerivativeParticipantPosition`, `FiiDerivativeStatistic` | `/derivatives/participants` | 6 | **COMPLETE & VERIFIED** |
| **19.1D-8** | Coverage, Quality & Historical Replay Hardening | Dataclass Schemas (`HistoricalDerivativesReplay`) | `/derivatives/coverage`, `/replay` | 4 | **COMPLETE & VERIFIED** |
| **Parent 19.1D** | **NSE Historical Futures & Options Platform** | **51 Models (Alembic `e91f4a5678ab`)** | **Complete F&O Suite** | **1230 Total Suite** | **100% COMPLETE & VERIFIED** |

