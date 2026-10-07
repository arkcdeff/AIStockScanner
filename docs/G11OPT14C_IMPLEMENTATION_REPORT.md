# G.11-OPT-14C — IMPLEMENTATION + CERTIFICATION REPORT

**Milestone:** G.11-OPT-14C — Capture-Flag Enforcement + MarketIndex Reconciliation
**Project:** AIStockScanner (Never touched RGUKT CMS / port 8011)
**Branch:** `checkpoint/sprint19-20-20260822`
**HEAD at start and end:** `8f38221ac75ac5a0922a84f937b4c4d6b4c2991d`
**Date:** 2026-10-06
**Commits created:** 0 · **Pushes:** 0 · **Destructive git operations:** 0

---

## 1. Executive Verdict

**PASS.**

Both authorized deliverables are implemented and certified:

* **(A) Capture-flag enforcement** — `settings.derivatives_observation_capture_enabled`
  is now a real safety gate, enforced at three independent layers (HTTP entrypoint,
  capture service/worker, persistence). Default remains `False`. Nothing in the
  codebase can enable it automatically.
* **(B) MarketIndex reconciliation** — the catalog/DB identity for NIFTY is
  reconciled without creating a duplicate row, and BANKNIFTY is established so
  `OptionChainPersistenceService._resolve_underlying_index("BANKNIFTY")` succeeds.

Evidence: 575 tests pass with 0 failures across 23 files; runtime validation against
a live uvicorn process on the **test** database confirms `globally_enabled=false`,
HTTP 503 on authenticated capture start and 401 unauthenticated; browser validation of
the four mandated routes shows HTTP errors = 0, console errors = 0, navigation intact
and no horizontal overflow at 1440 px and 390 px widths.

No contract synchronization, no live capture, no production ingestion, no fake data.

---

## 2. Pre-Implementation Discovery

Read-only tracing was performed before any edit (Parts 1–11).

| # | Traced | Finding |
|---|---|---|
| 1 | `derivatives_observation_capture_enabled` | Declared `app/core/config.py:52`, default `False`. Read by exactly ONE consumer: `FuturesObservationStore.enabled` (`app/services/derivatives_observation_service.py:1066`). |
| 2 | Capture start route | `POST /market-data/options/capture/start` → `start_capture_session` (`app/api/routes/options.py:606`). **Two** RBAC closures: router-level `stocks.read` and route-level `market_data.write`. Router prefix `/market-data/options`. |
| 3 | Capture service | `DerivativesCaptureService.start_capture` (`derivatives_capture_service.py:218`) → `asyncio.create_task(self._run_capture_loop(...))` (:283). |
| 4 | Capture worker | `_run_capture_loop` (:341) → sole caller of `_capture_cycle` (:425 → def :466). |
| 5 | Persistence service | `OptionChainPersistenceService.persist_snapshot` (:705). |
| 6 | `persist_snapshot` callers | In `app/`: **only** `derivatives_capture_service.py:477`. In tests: `test_option_chain_persistence_g11opt1.py` (fixtures, `provider_id="NSE_TEST_PROVIDER"`) and the quality suite. |
| 7 | Other persistence entrypoints | None that bypass `persist_snapshot`. |
| 8 | Futures observation path | `FuturesObservationStore.append` / `append_with_result` (:1150, :1190) enforce `self.enabled` only on the `force_enabled=False` branch. |
| 9 | MarketIndex model | `app/models/market_index.py:15` — `code`, `name`, `country`, `exchange`, `category`, `currency`, `timezone`, `provider_symbol`, `is_active`; `uq_market_index_code_country`, `uq_market_index_provider_symbol`. |
| 10 | MarketIndex repository | `app/repositories/market_index.py` — `get_by_code`, `list_active`, `list_by_category`, `list_by_country`. |
| 11 | MarketIndex catalog | `INDEX_CATALOG = CORE_INDEX_CATALOG + SECTOR_INDEX_CATALOG`; `seed_market_index_catalog` (`index_catalog.py:290`) referenced only by `tests/test_global_index_provider.py` — no app/CLI/lifespan/migration caller. |
| 12 | Migrations | Head was `b3a4c5d6e7f8`; no BANKNIFTY migration existed. |
| 13 | `_resolve_underlying_index` | `option_chain_persistence_service.py:1352`. Matches `UPPER(code)` / `UPPER(provider_symbol)` against `{raw, normalized}`; `normalize_index_symbol` does NOT map `BANKNIFTY→NIFTY_BANK`. |
| 14 | Derivative contract persistence | `_load_index_map` (`derivative_import.py:162`) maps `code`/`name`/`provider_symbol` (upper) → id. |
| 15 | Tests covering MarketIndex/capture | `test_derivatives_capture_g11opt4.py` (13), `test_derivatives_quality_g11opt5.py` (38), `test_derivatives_scope_g11opt3.py` (66), `test_global_index_provider.py`, `test_option_chain_persistence_g11opt1.py` (16). |

**Root cause (A):** the flag gated *nothing* on the capture path. `start_capture`
never consulted it, the worker never consulted it, and `persist_snapshot` never
consulted it. Capture passed `force_enabled=True` to the futures store (deliberately
commented "explicitly operator-authorized"), so the flag's only enforcement point was
bypassed by the only production caller.

**Root cause (B):** the catalog authoring code is `NIFTY_50` / `NIFTY_BANK`, while the
DB row created by root QA seed scripts is `NIFTY`, and no BANKNIFTY row existed at all.
`seed_market_index_catalog` matched on code only, so running it would have inserted a
duplicate `NIFTY_50` row and a `NIFTY_BANK` row that neither the resolver nor the
derivative importers look up.

---

## 3. Capture Flag Root Cause

The flag was documentation, not a control:

* No read at `start_capture`, `_run_capture_loop`, `_capture_cycle` or `persist_snapshot`.
* The single existing read (`FuturesObservationStore.enabled`) was neutralised by the
  capture path's own `force_enabled=True`.
* Therefore an operator reaching `POST /capture/start` with valid RBAC could start a
  worker and persist real option-chain observations with the flag `False`.

---

## 4. Capture Entry Gate

Three layers now enforce the flag.

**Layer 1 — HTTP route** (`app/api/routes/options.py`, `start_capture_session`):

```python
if not settings.derivatives_observation_capture_enabled:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Derivatives observation capture is disabled; set "
            "DERIVATIVES_OBSERVATION_CAPTURE_ENABLED=true to enable it"
        ),
    )
```

Placed **after** the RBAC dependencies (which are FastAPI route dependencies, so they
run first) and **before** payload parsing. HTTP **503** was chosen because it is the
project's established "feature disabled" convention (`app/api/routes/ai.py:23`,
`app/api/dependencies.py:46`, `app/api/routes/admin.py:335`), not a new authorization
model. Existing behaviours preserved: unauthenticated → 401, insufficient permission →
403, service-level refusal → 400.

**Layer 2 — service** (`derivatives_capture_service.py`): new module-level
`observation_capture_enabled()` is the single source of truth (`is_globally_enabled`
now delegates to it). `start_capture` returns before `config.validate()`:

```
CAPTURE_DISABLED: global observation capture is disabled
(derivatives_observation_capture_enabled=False); no capture worker was started and
no observation was fetched or persisted
```

No worker, no provider call, no persistence.

**Layer 3 — worker** (`_run_capture_loop`): a per-iteration re-check halts a running
session if the flag is withdrawn mid-session, setting `last_error="CAPTURE_DISABLED_MID_SESSION"`.

---

## 5. Persistence Defense Gate

`persist_snapshot` now performs the check immediately before
`overrides = _PersistOverrides(...)`:

* new exception `OptionChainCaptureDisabledError(OptionChainPersistenceError)`;
* `_LIVE_PROVIDER_IDENTITIES = frozenset({"UPSTOX_LIVE", "UPSTOX"})` and
  `_is_live_provider_observation(chain)`;
* when the chain is a live provider read **and** the flag is `False`, raise — nothing
  is planned, written or committed (the exception is raised before any DB work).

**Deliberate exemption (Part 4 requirement):** the project intentionally permits
historical/test persistence. Fixture chains carry `provider_id="NSE_TEST_PROVIDER"`
(and the model default is `"UNKNOWN"`), so they do not match the live identity set and
remain fully functional. This is proven by the untouched 16-test
`test_option_chain_persistence_g11opt1.py` suite passing, and by explicit test
`test_d_fixture_persistence_path_is_not_blocked` (the fixture path still fails only on
its own `UnderlyingResolutionError`, never on the capture gate).

This is true defense in depth: it does not rely on any caller, so a future caller
cannot bypass the flag.

---

## 6. force_enabled Analysis

`force_enabled` exists only in `FuturesObservationStore.append` /
`append_with_result` (`derivatives_observation_service.py:1150`, `:1190`). Complete
caller classification:

| Caller | Class | Decision |
|---|---|---|
| `derivatives_capture_service.py:587` (`_capture_cycle`) | **production** capture session | Not changed. It is now unreachable while the flag is `False`, because Layer 2 blocks session start and Layer 3 halts the loop. |
| `test_derivatives_capture_g11opt4.py` (:256, :260, :307, :308) | test fixture | Not changed |
| `test_derivatives_quality_g11opt5.py` (:426, :429, :447, :448, :454, :455, :470, :471, :487) | test fixture | Not changed |
| `test_derivatives_capture_g11opt4.py:89` | negative test (`force_enabled=False`) | Not changed |

`_capture_cycle` has exactly **one** application caller (`_run_capture_loop:425`).
Conclusion: **no unintended production bypass remains**, and no legitimate internal or
test usage was broken (all suites pass unchanged). No edit to
`derivatives_observation_service.py` was required, which keeps the change surface
minimal and avoids breaking the store's deliberate operator-authorization contract.

---

## 7. MarketIndex Root Cause

* Catalog authoring codes: `NIFTY_50` (provider symbol `NIFTY50`) and `NIFTY_BANK`
  (provider symbol `BANKNIFTY`).
* DB row before this milestone: `code='NIFTY', name='Nifty 50'`,
  `provider_symbol=NULL`, created by root QA seed scripts (`_seed_opt12b_phase2a_qa.py:90`,
  `_seed_opt8_qa_db.py:95`, `_seed_opt9_qa_db.py:37`, `qa_opt10_browser_audit.py:84`)
  and `tests/conftest.py:269` — not a production bootstrap.
* `seed_market_index_catalog` matched on `code` only → running it would have inserted a
  duplicate `NIFTY_50` row (two rows for the same index) plus an unusable `NIFTY_BANK`
  row.
* No BANKNIFTY row existed anywhere, so BANKNIFTY observations could never resolve an
  underlying.

---

## 8. NIFTY Identity Reconciliation

The authoritative identity is the **existing DB row `code='NIFTY'`**, not the catalog
authoring code `NIFTY_50`. Reasons, in order of weight:

1. `NIFTY` is what `_resolve_underlying_index` and the derivative importers already
   look up in production.
2. The existing primary key may be referenced by `derivative_contracts.underlying_index_id`
   and `option_chain_snapshots.underlying_index_id`; renaming the row is therefore
   riskier than reconciling the catalog to it.
3. There is no data in the repo that requires the `NIFTY_50` spelling at runtime.

**Least destructive solution chosen: the catalog reconciles onto the DB row, no rename,
no insert, no delete.** `CATALOG_CODE_ALIASES = {"NIFTY_50": ("NIFTY",), "NIFTY_BANK": ("BANKNIFTY",)}`
is added; `seed_market_index_catalog` selects `codes + alias_codes` and, when the
catalog code has no row but an alias row exists, updates that existing row **keeping its
code**, instead of inserting a duplicate.

**One authoritative NIFTY MarketIndex. No dual identity. No historical reference was
touched.**

---

## 9. BANKNIFTY MarketIndex

The canonical identifier is **`BANKNIFTY`**, decided from actual repository usage (not
from the Upstox symbol alone):

* the derivative import index map (`derivative_import.py`) keys on short uppercase codes;
* `get_strike_step("BANKNIFTY")` is already configured (100) and "NIFTY_BANK" would
  only work by alias;
* `normalize_index_symbol` does not translate `NIFTY_BANK → BANKNIFTY`;
* G.11-OPT-14A/14B establish the provider symbol `BANKNIFTY`.

`provider_symbol = 'BANKNIFTY'` is set so the bank index resolves both by code and by
provider symbol. No derivative contracts were inserted.

---

## 10. Files Modified

### A. Intentionally modified by G.11-OPT-14C (5 tracked files)

| # | Path | Reason | Exact change |
|---|---|---|---|
| 1 | `app/api/routes/options.py` (+15) | Layer-1 capture entrypoint gate | `start_capture_session` raises HTTP 503 with the "capture is disabled" detail when the flag is off, before payload parsing; RBAC dependencies unchanged. |
| 2 | `app/services/derivatives_capture_service.py` (+38/−1) | Layers 2–3 + single source of truth | Added module-level `observation_capture_enabled()`; `is_globally_enabled` delegates to it; `start_capture` returns `CAPTURE_DISABLED` before `config.validate()`; `_run_capture_loop` halts mid-session with `last_error="CAPTURE_DISABLED_MID_SESSION"`. |
| 3 | `app/services/option_chain_persistence_service.py` (+39) | Layer-3 defense in depth | Added `OptionChainCaptureDisabledError`, `_LIVE_PROVIDER_IDENTITIES`, `_is_live_provider_observation`; `persist_snapshot` refuses a live-provider observation while the flag is off (raised before `_PersistOverrides`). Fixture/historical chains exempt. |
| 4 | `app/market_intelligence/index_catalog.py` (+26/−? ) | NIFTY reconciliation | Added `CATALOG_CODE_ALIASES`; `seed_market_index_catalog` also selects alias codes, reconciles onto an existing alias row instead of inserting a duplicate, and registers newly created rows in the idempotency map. |
| 5 | `tests/test_derivatives_capture_g11opt4.py` (+30/−?) | Tests encoded the old (ungated) behaviour | `test_capture_lifecycle_start_and_stop` now monkeypatches the flag `True`; `test_database_schema_unchanged` expects head `c4d5e6f7a8b9` and additionally asserts the new revision is data-only. |

### B. New files added by G.11-OPT-14C (untracked)

| Path | Purpose |
|---|---|
| `migrations/versions/c4d5e6f7a8b9_add_banknifty_market_index.py` | Data-only idempotent BANKNIFTY migration (68 lines) |
| `tests/test_capture_flag_enforcement_g11opt14c.py` | 11 capture-flag enforcement tests |
| `tests/test_market_index_reconciliation_g11opt14c.py` | 9 MarketIndex reconciliation/idempotency tests |
| `docs/G11OPT14C_IMPLEMENTATION_REPORT.md` | This report |

### C. Pre-existing tracked modifications — NOT touched by this milestone (8)

`app/templates/components/derivatives_nav_tabs.html`,
`app/templates/components/intraday_option_pcr_view.html`,
`app/templates/derivatives.html`,
`app/templates/derivatives_multistrike_oi.html`,
`app/templates/derivatives_oi_change.html`,
`app/templates/derivatives_oi_statistics.html`,
`app/templates/derivatives_oi_time.html` (the G.11-OPT-13 Phase B restoration set) and
`run_aistockscanner.bat`.

No file outside the list above was modified. `.env`, `.freebuff/`, `tests/conftest.py`,
`app/core/config.py`, `app/services/derivatives_observation_service.py`,
`app/services/derivatives_scope.py`, auth/RBAC modules and all templates were left
byte-identical. `app/core/config.py:52` still reads `derivatives_observation_capture_enabled: bool = False`.

---

## 11. Migration Changes

**One migration, data-only, single head.**

```python
revision      = "c4d5e6f7a8b9"
down_revision = "b3a4c5d6e7f8"
```

* `upgrade()` — one guarded `INSERT ... SELECT ... WHERE NOT EXISTS (code) AND NOT
  EXISTS (provider_symbol)`. Inserts `('BANKNIFTY','Nifty Bank','INDIA','NSE',
  'indian_index','INR','Asia/Kolkata','BANKNIFTY',TRUE)`.
* `downgrade()` — data-preserving: deletes the row **only** when no
  `derivative_contracts.underlying_index_id` and no
  `option_chain_snapshots.underlying_index_id` reference it, so a downgrade can never
  cascade into history.
* No `op.create_table` / `add_column` / `drop_column` / `alter_column` — asserted by
  test.
* Idempotency, duplicate-NIFTY and duplicate-BANKNIFTY safety are all enforced by the
  `NOT EXISTS` guards; an already-migrated DB is a no-op.

**Applied only to the isolated local test database `test`** (never to production
`ai_stock_scanner`):

```
alembic upgrade head      → b3a4c5d6e7f8 -> c4d5e6f7a8b9   (market_indices: NIFTY, BANKNIFTY)
alembic upgrade head      → no-op                          (still exactly 2 rows)
alembic downgrade -1      → c4d5e6f7a8b9 -> b3a4c5d6e7f8   (BANKNIFTY removed; NIFTY intact)
alembic upgrade head      → re-applied cleanly
```

`alembic heads` reports exactly one head: `c4d5e6f7a8b9`.

**The production database was not migrated and still has no BANKNIFTY row.** Applying
this migration to production is a deployment decision outside this milestone.

Production `ai_stock_scanner` re-inspected after all 14C work, and is byte-for-byte
unchanged from the G.11-OPT-14A/14B baseline:

```
alembic_version      = ['b3a4c5d6e7f8']            (NOT c4d5e6f7a8b9)
market_indices       = [(7, 'NIFTY', 'Nifty 50', provider_symbol=NULL)]   (1 row, unchanged)
derivative_contracts = 4
option_chain_snapshots = 2
option_chain_quotes    = 8
```

---

## 12. Tests Added/Changed

**New — `tests/test_capture_flag_enforcement_g11opt14c.py` (11 tests):**

| Requirement | Test |
|---|---|
| A. flag FALSE → start rejected | `test_a_capture_start_rejected_when_disabled` |
| B. flag TRUE → proceeds through existing validation | `test_b_capture_start_proceeds_when_enabled` (also asserts `CAPTURE_ALREADY_RUNNING`) |
| C. flag FALSE → production persistence blocked | `test_c_disabled_flag_blocks_live_observation_persistence` |
| D. fixture/historical path not blocked | `test_d_fixture_persistence_path_is_not_blocked` |
| E. RBAC preserved + HTTP gate | `test_e_unauthenticated_capture_start_is_still_401_or_403`, `test_e_authorized_capture_start_is_503_when_disabled`, `test_e_authorized_capture_start_passes_gate_when_enabled` (400, not 503, once enabled) |
| F. no worker created | `test_f_disabled_flag_creates_no_worker` |
| G/H. no snapshot, no quotes | `test_g_h_disabled_flag_creates_no_snapshot_or_quotes` (also asserts `_capture_cycle` never called) |
| I. enabling does not bypass validation | `test_i_enabled_flag_does_not_bypass_underlying_validation` (`UnderlyingResolutionError` still raised) |
| J. nothing enables the flag | `test_j_catalog_seeding_does_not_enable_capture` |

**New — `tests/test_market_index_reconciliation_g11opt14c.py` (9 tests):** NIFTY and
BANKNIFTY resolution with independent ids; provider-symbol-only resolution;
idempotent double seed; no `NIFTY_50` / `NIFTY_BANK` duplicates and alias rows keep
their authoring code (Part 13 C/D/K); historical references survive re-seeding (H/I);
a non-catalog row is never deleted; and a migration guard test that loads the migration
by path and asserts it is data-only with a `NOT EXISTS` guard.

**Changed — `tests/test_derivatives_capture_g11opt4.py`:** two tests encoded the old
ungated behaviour and were updated (`test_capture_lifecycle_start_and_stop` now enables
the flag explicitly; `test_database_schema_unchanged` expects the new head and asserts
the revision adds no schema). **No assertion was weakened, no test skipped, no
suppression added.**

---

## 13. Test Results

Exact commands (test database `test`; `-o addopts=` clears project default options):

| # | Command | Result |
|---|---|---|
| 1 | `pytest tests/test_capture_flag_enforcement_g11opt14c.py -o addopts= -p no:cacheprovider -q` | **11 passed**, exit 0 |
| 2 | `pytest tests/test_capture_flag_enforcement_g11opt14c.py tests/test_market_index_reconciliation_g11opt14c.py tests/test_derivatives_capture_g11opt4.py -o addopts= -p no:cacheprovider -q` | **33 passed** (11+9+13), 27.09 s, exit 0 |
| 3 | `pytest tests/test_derivatives_scope_g11opt3.py tests/test_derivatives_capture_g11opt4.py tests/test_derivatives_quality_g11opt5.py tests/test_option_chain_persistence_g11opt1.py tests/test_derivative_contract_master.py tests/test_multi_provider.py tests/test_upstox_provider.py -o addopts= -p no:cacheprovider -q` | **213 passed**, 186.90 s, exit 0 |
| 4 | `pytest tests/test_derivatives_analytics_g11opt2.py tests/test_derivatives_observations_g11opt3.py tests/test_derivatives_workbench_g11opt3.py tests/test_options_workspace_state_propagation_g11opt13.py tests/test_global_index_provider.py tests/test_option_chain_ladder_g11opt1.py tests/test_option_chain_models_g11opt1.py tests/test_option_chain_validation_g11opt1.py tests/test_option_chain_provider_adapter_g11opt1.py tests/test_intraday_options_g11opt1.py tests/test_historical_option_chain.py tests/test_derivatives_acceptance_g11opt9.py tests/test_derivatives_replay_and_coverage.py -o addopts= -p no:cacheprovider -q` | **329 passed**, 395.76 s, exit 0 |

**Totals: 575 passed, 0 failed, 0 errors, 0 skipped (23 files).** Run 3 reproduces the
G.11-OPT-14A baseline exactly (213) — no regression. Exit status was captured with
`${PIPESTATUS[0]}` and was 0 for every run.

---

## 14. Runtime Validation

A real application process was started against the **isolated test database** (never
production), with the scheduler explicitly disabled:

```
DATABASE_URL_ASYNC/SYNC -> .../localhost:5432/test
EOD_SCHEDULER_ENABLED=false DAILY_SUMMARY_ENABLED=false EOD_SCHEDULER_AUTO_CATCHUP=false
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015
```

Log: `Started AI Stock Scanner in development mode` · `Application scheduler disabled;
not starting` · `Application startup complete.`

| Check | Result |
|---|---|
| 1. Application starts | PASS (`/api/v1/health` → 200) |
| 2. Capture disabled by default | PASS — `GET /api/v1/market-data/options/capture/status` (authenticated) → `state=IDLE, is_running=False, globally_enabled=false, active_task_running=False, session_id=''` |
| 3. Capture-start rejects while disabled | PASS — authenticated `POST /api/v1/market-data/options/capture/start` → **HTTP 503** `{"detail":"Derivatives observation capture is disabled; set DERIVATIVES_OBSERVATION_CAPTURE_ENABLED=true to enable it"}` |
| 4. RBAC preserved | PASS — unauthenticated POST → **401** `Invalid authentication credentials`; server log shows `503` for authenticated and `401` unauthenticated |
| 5. No worker starts | PASS — `active_task_running=false` before and after |
| 6. No snapshot created | PASS — `option_chain_snapshots = 0` after the rejected attempt |
| 7. No quote created | PASS — `option_chain_quotes = 0` after the rejected attempt |
| 8. NIFTY resolution works | PASS — `_resolve_underlying_index(None,"NIFTY")` → `id=1 code=NIFTY provider_symbol=NIFTY50` |
| 9. BANKNIFTY resolution works | PASS — `_resolve_underlying_index(None,"BANKNIFTY")` → `id=3 code=BANKNIFTY provider_symbol=BANKNIFTY` |
| 10. Catalog seeding is identity-safe | PASS — seeded twice: 35 rows, **0 duplicate codes**, `NIFTY_50` absent, `NIFTY_BANK` absent, NIFTY row preserved |
| 11. Downstream strike-grid config | PASS — `get_strike_step('NIFTY')=50`, `get_strike_step('BANKNIFTY')=100` (matches the live 14B step) |
| 12. Historical derivatives pages | PASS — all four mandated routes return 200 (see §15) |

**Test-only setting documented:** a superuser (`opt14c_runtime_admin`, test database
only, created with the project's own `RoleService.seed_builtins` + `hash_password`) was
used solely to obtain a real bearer token/cookie so the authenticated path could be
exercised. Production observation capture was never enabled; the flag was `False` in
every runtime step.

---

## 15. Browser Validation

Chromium session over the live process at `http://127.0.0.1:8015`, authenticated
through the real `/login` form (using the test-only superuser above).

| Route | HTTP | Console errors | Horizontal overflow (1440 px) | Overflow (390 px) | Structure |
|---|---|---|---|---|---|
| `/market-data/derivatives` | 200 | **0** | 0 | 0 (measured at 367 px too) | `h1 = Derivatives Workbench`, 60 nav links, workbench tabs intact |
| `/market-data/options` | 200 | **0** | 0 | — | `h1 = Options / Intraday Workbench`, Chart.js loaded 200 |
| `/market-data/derivatives/option-chain` | 200 | **0** | 0 | — | 3 tables rendered, workbench nav present |
| `/market-data/derivatives/options-oi-time` | 200 | **0** | 0 | 0 | `h1 = Options OI vs Time`, 3 chart canvases, nav intact |

* HTTP errors = **0**; every document and asset request returned 200.
* Console errors = **0** on every route.
* Navigation intact: the derivatives tab set (`/market-data/derivatives`,
  `/oi-statistics`, `/oi-change`, `/multistrike-oi`, `/options-oi-time`,
  `/futures-oi-time`, `/oi-replay`) is present and correct.
* No horizontal overflow on desktop or mobile widths.
* No live capture was attempted from the UI and the UI exposes no control that could
  start one while the flag is `False`.

**Limitation (honest reporting):** `preview_screenshot` returned
`capturePage returned an empty image` / "preview webview is not being composited", so
pixel-level visual comparison could not be captured in this environment. Certification
above is from DOM/geometry measurements (scrollWidth vs clientWidth, table/canvas
counts, nav presence) plus HTTP status and console/network logs, not from screenshots.

---

## 16. Historical Workbench Safety

* No template was modified (the seven Phase B template files in the working tree are
  pre-existing modifications from an earlier milestone and were not touched).
* No OI Change, OI-vs-Time, multistrike-OI or OI-statistics code path was edited.
* All four workbench routes render, with zero console errors and zero overflow.
* `derivative_contracts`, `option_chain_snapshots` and `option_chain_quotes` were not
  deleted, rewritten or re-lotted. Existing (stale) historical rows reported in
  G.11-OPT-14A/14B were left exactly as they were — that cleanup is explicitly out of
  scope for 14C.
* Migration `downgrade()` is written so it can never cascade into historical
  derivatives.

---

## 17. Contract-Synchronization Boundary

**CONTRACT SYNCHRONIZATION = NOT IMPLEMENTED.**

Explicitly confirmed absent from this milestone:

* no `DerivativesContractSyncService` created;
* `derivative_contracts` not populated from Upstox (the 1,918 NIFTY and 941 BANKNIFTY
  contracts were **not** inserted);
* `_parse_option_contract_row` (`app/services/derivatives_scope.py:1386`) unmodified —
  `exchange`, `segment`, `exchange_token`, `freeze_quantity`, `minimum_lot`, `name`,
  `underlying_key`, `underlying_symbol`, `underlying_type` are still discarded exactly
  as before;
* `source_contract_id` handling unchanged;
* the `segment="NSE_FO"` vs model default `"NFO"` mismatch is documented as an input for
  G.11-OPT-14D and was **not** fixed;
* **no Upstox API call was made in this milestone.**

---

## 18. OI Change Boundary

**OI CHANGE = NOT MODIFIED.**

OI Change calculations, interval pairing, the OI Change chart, the OI-vs-Time chart,
ΔOI display and historical trajectory logic were not touched. No file under the OI
Change / OI-vs-Time feature surface appears in `git diff`. The OI-vs-Time page was
browser-validated as a regression check only.

---

## 19. Git State

```
branch : checkpoint/sprint19-20-20260822
HEAD   : 8f38221ac75ac5a0922a84f937b4c4d6b4c2991d  (unchanged)
tracking: ahead 1 of origin/checkpoint/sprint19-20-20260822 (HEAD was already unpushed before this milestone)
commits created by G.11-OPT-14C : 0
pushes by G.11-OPT-14C          : 0
git reset/restore/checkout/clean/stash/rebase/amend : 0
```

`git diff --stat`: 13 files changed, 1404 insertions(+), 9 deletions(-).

**A. Files intentionally modified by G.11-OPT-14C (5):**
`app/api/routes/options.py` (+15) · `app/market_intelligence/index_catalog.py` (+26) ·
`app/services/derivatives_capture_service.py` (+38/−1) ·
`app/services/option_chain_persistence_service.py` (+39) ·
`tests/test_derivatives_capture_g11opt4.py` (+30).

**B. Pre-existing modified files, preserved untouched (8):**
`app/templates/components/derivatives_nav_tabs.html`,
`app/templates/components/intraday_option_pcr_view.html`,
`app/templates/derivatives.html`, `app/templates/derivatives_multistrike_oi.html`,
`app/templates/derivatives_oi_change.html`, `app/templates/derivatives_oi_statistics.html`,
`app/templates/derivatives_oi_time.html`, `run_aistockscanner.bat`.

**C. Untracked:** 224 entries at milestone start-of-validation (221 baseline + 3 new),
becoming **225** once this report is written. New entries added by G.11-OPT-14C:
`migrations/versions/c4d5e6f7a8b9_add_banknifty_market_index.py`,
`tests/test_capture_flag_enforcement_g11opt14c.py`,
`tests/test_market_index_reconciliation_g11opt14c.py`,
`docs/G11OPT14C_IMPLEMENTATION_REPORT.md`.
Nothing was deleted, stashed or absorbed. The G.11-OPT-14A and G.11-OPT-14B reports and
the seven Phase B templates remain uncommitted, as they were.

*(A temporary local log directory `.tmp_opt14c/` holds the uvicorn log for the runtime
validation above; its `.log` file is covered by the repository's `*.log` ignore rule.)*

---

## 20. Certification Gates

| Gate | Status |
|---|---|
| Capture disabled by default | **PASS** — `config.py:52` still `False`; runtime `globally_enabled=false` |
| Capture-start blocked when disabled | **PASS** — route 503, service `CAPTURE_DISABLED`, worker re-check |
| Persistence blocked when disabled | **PASS** — `OptionChainCaptureDisabledError` before any write for live providers |
| Existing RBAC preserved | **PASS** — unauthenticated 401; dependencies unchanged; 403 path intact |
| No unintended force-enable bypass | **PASS** — sole production `force_enabled=True` caller unreachable while disabled; classified in §6 |
| One canonical NIFTY MarketIndex | **PASS** — reconciliation onto row `id=1 code='NIFTY'`; no rename, no insert |
| BANKNIFTY MarketIndex resolves | **PASS** — `_resolve_underlying_index("BANKNIFTY")` → row `code='BANKNIFTY'`, `provider_symbol='BANKNIFTY'` |
| No duplicate MarketIndex rows | **PASS** — double seed: 0 duplicates, no `NIFTY_50`, no `NIFTY_BANK` |
| Historical references preserved | **PASS** — NIFTY row and all derivative history untouched; downgrade cannot cascade |
| No derivative contracts synchronized | **PASS** — 0 contracts imported, parser unmodified, no Upstox call |
| No live capture | **PASS** — 0 snapshots, 0 quotes created; no worker started |
| No fake data | **PASS** — no synthetic observations or contracts created |
| Regression tests pass | **PASS** — 575 passed / 0 failed across 23 files |
| Runtime validation pass | **PASS** — live process, 12/12 checks (§14) |
| Browser validation pass | **PASS** — 4/4 routes, 0 HTTP errors, 0 console errors, 0 overflow (screenshot capture unavailable; see §15) |

---

## 21. Remaining Risks

1. **Production DB still lacks the BANKNIFTY row.** The migration is written and
   validated but not applied to `ai_stock_scanner`. Until a deployment applies it,
   BANKNIFTY observations cannot resolve in production. This is intentional — applying
   a production migration was not authorized by this milestone.
2. **The provider-identity heuristic is the capture gate's discriminator.** A future
   caller that invents a *different* live-looking `provider_id` for real observations
   while the flag is `False` would not be blocked by Layer 3. Mitigation: Layer 2 still
   blocks the only production capture entrypoint, and `_LIVE_PROVIDER_IDENTITIES`
   should be extended when a new live provider identity is introduced.
3. **`config.validate()` runs after the flag gate** in `start_capture`, so a disabled
   flag reports `CAPTURE_DISABLED` rather than `INVALID_CONFIG`. This is the intended
   precedence (safety first) but changes the previously returned reason for
   misconfigured requests while disabled.
4. **Production NIFTY row has `currency = NULL`** while the migration's BANKNIFTY row
   sets `INR`. Cosmetic inconsistency only; the catalog does not backfill `currency` on
   existing rows. Not corrected here to avoid touching the existing row.
5. **Fixture exemption is identity-based.** Persistence of fixtures/historical imports
   relies on `provider_id` not being in the live set; the 16 persistence tests and the
   explicit exemption test cover this today, and any new fixture writer must follow the
   same convention.
6. **Screenshot-based visual certification was not possible** in this environment
   (webview not composited), so visual regressions were excluded by geometry/DOM and
   HTTP/console evidence rather than pixels.

---

## 22. Recommendation for G.11-OPT-14D

Recommended as the next milestone, now that the safety and identity foundations exist:

1. **Contract-master synchronization** — implement `DerivativesContractSyncService`
   behind an explicit operator gate, ingesting the verified 14B payload (NIFTY 1,918 /
   BANKNIFTY 941).
2. **Fix the segment mismatch first** — decide how `segment="NSE_FO"` from the provider
   maps onto the `DerivativeContract` model default `"NFO"`, because `segment` is part of
   the `_resolve_contract_ids` key. This must be settled before bulk import, and it
   requires extending `_parse_option_contract_row` to stop discarding
   `exchange`/`segment`/`exchange_token`/`freeze_quantity`/`minimum_lot`/`underlying_*`.
3. **Persist `source_contract_id`** (`instrument_key`, e.g. `NSE_FO|<numeric>`) as the
   idempotency key for the 2,859 contracts, so re-sync is upsert-safe.
4. **BANKNIFTY weeklies must not be assumed** — 14B proved every BANKNIFTY row has
   `weekly=false`; expiry handling must tolerate a monthly-only index while NIFTY keeps
   weeklies.
5. **Apply the BANKNIFTY migration to production** (`c4d5e6f7a8b9`) as part of that
   deployment, then verify `_resolve_underlying_index` for both indices against the real
   database.
6. Keep the capture flag `False`; capture enablement should be its own separately
   authorized milestone after synchronization is verified.
7. Defer SecretStr / authtoken redaction, OI Change and OI-vs-Time work to their own
   milestones — they remain untouched and unblocked by 14C.

---

G.11-OPT-14C IMPLEMENTATION IS LIMITED TO CAPTURE-SAFETY ENFORCEMENT AND MARKETINDEX RECONCILIATION. G.11-OPT-14D CONTRACT-MASTER SYNCHRONIZATION IS NOT AUTHORIZED BY THIS MILESTONE.
