# G.11-OPT-14 — UPSTOX TOKEN + CONTRACT MASTER
# SYNCHRONIZATION DESIGN AUDIT

## 1. Audit Identity

| Field | Value |
|---|---|
| Repository | `arkcdeff/AIStockScanner` |
| Workspace | `AIStockScanner_Milestone2_3/AIStockScanner` |
| Branch | `checkpoint/sprint19-20-20260822` |
| HEAD | `4fa354d3ddfeb8f6c36bbaf05815d23b4ab75897` (`4fa354d`) |
| HEAD subject | `feat(options): complete cumulative derivatives and option analytics checkpoint` |
| Parent | `e43856e` — `G.11-D7: certify multi-session signal trajectory engine` |
| Audit mode | STRICT READ-ONLY (no provider call, no token use, no DB write) |
| Implementation | NOT AUTHORIZED |

Working-tree state observed (`git status --short`, 227 lines total):

* Tracked modifications: **8**
  * `app/templates/components/derivatives_nav_tabs.html` (+54)
  * `app/templates/components/intraday_option_pcr_view.html` (+84)
  * `app/templates/derivatives.html` (+1)
  * `app/templates/derivatives_multistrike_oi.html` (+190)
  * `app/templates/derivatives_oi_change.html` (+248)
  * `app/templates/derivatives_oi_statistics.html` (+398)
  * `app/templates/derivatives_oi_time.html` (+284)
  * `run_aistockscanner.bat` (pre-existing, untouched by this audit)
* `git diff --stat` → `8 files changed, 1262 insertions(+), 3 deletions(-)`
* Staged (`git diff --cached --name-only | wc -l`) → **0**
* Untracked entries → 219

The six 0-byte Historical Derivatives templates damaged by commit `4fa354d` remain restored on disk
(Phase B) and uncommitted. `derivatives.html` (+1 nav item linking to
`/market-data/derivatives/options-oi-time`) is also a working-tree modification and is **not** one of the
six restored files.

## 2. Executive Verdict

**HOLD — MORE DISCOVERY REQUIRED**

The *architecture* is discoverable and the safe design is now explicit (sections 9–14). Three concrete,
first-hand-verified discovery gaps remain before implementation can be considered safe:

1. **Provider payload for `/option/contract` is unverified first-hand.** This audit is forbidden from
   activating the provider or the token, so the exact option-contract payload shape
   (`DerivativesContractMasterClient.get_option_contracts`, `app/services/derivatives_scope.py:1133`) is known
   only from source code plus repository reports, not from a live read.
2. **BANKNIFTY has no `MarketIndex` row.** Read-only DB inspection shows `market_indices` = **1** row
   (`NIFTY`, id=7). `OptionChainPersistenceService._resolve_underlying_index`
   (`app/services/option_chain_persistence_service.py:1326`) raises `UnderlyingResolutionError` when no
   authoritative index row exists, so **BANKNIFTY observations cannot be persisted at all** today — a
   prerequisite independent of the contract master.
3. **The capture *start* endpoint does not honour the global capture flag.** `POST /market-data/options/capture/start`
   (`app/api/routes/options.py:601`) calls `DerivativesCaptureService.start_capture(..., authorized=True)`
   (`app/services/derivatives_capture_service.py:211`) which gates only on operator permission
   (`market_data.write`) and session bounds — it never reads
   `settings.derivatives_observation_capture_enabled`. The flag is enforced lower down (futures append store,
   `app/services/derivatives_observation_service.py:1066,1152`). The intended invariant
   "LIVE PROVIDER = AVAILABLE while CAPTURE = DISABLED" is therefore enforced at the append layer, not at the
   API entrypoint; operator intent should be confirmed before relying on it.

## 3. Upstox Authentication Architecture

Single static bearer token. There is **no OAuth dance, no refresh, no expiry tracking** anywhere in the code
path. The chain, followed by real imports:

| Step | Location | Symbol | Purpose |
|---|---|---|---|
| A. Declaration | `app/core/config.py:39` | `upstox_analytics_token: str = Field(default="", validation_alias="UPSTOX_ANALYTICS_TOKEN")` | Env-backed setting, **plain `str`** (not `SecretStr`) |
| B. Env loading | `app/core/config.py` `model_config` | `SettingsConfigDict(env_file=…/".env")` | `.env` → `Settings` |
| C. Settings object | `app/core/config.py:get_settings()` | `@lru_cache settings = get_settings()` | Process-wide singleton |
| D. Provider init | `app/providers/upstox_live_provider.py:167` | `UpstoxLiveProvider.__init__(token=None, …, mock_mode=False)` → `self.token = token or settings.upstox_analytics_token` | Token captured at construction |
| E. Client construction | `app/providers/upstox_live_provider.py:208` | `_get_headers()` → `{"Authorization": f"Bearer {self.token}"}` | Per-request `httpx.AsyncClient(timeout=5)`; **no persistent client** |
| F. Token "validation" | `app/providers/upstox_live_provider.py:202` | `is_available()` → `True` if `mock_mode` else `bool(token.strip())` | **Non-empty string only** — no signature/expiry/scope check |
| G. Error handling | `:38` `_provider_unavailable()`, `:285/:401/:432` | Exception → log via `redact_secrets` | Chain path returns `source_name="UNAVAILABLE"` |
| H. Token refresh | — | **absent** | No refresh code exists |
| I. Token expiry | — | **absent** | No `exp`/expiry parsing exists |
| J. Read-path fallback | `:285, :401, :513` | `if self.mock_mode or not self.token:` → mock/calendar | Empty token ⇒ mock, on the *display* path |

Alternate constructors: `app/services/option_chain_adapter.py:116`
(`UpstoxLiveProvider(mock_mode=mock_mode)`), `app/services/derivatives_capture_service.py:350`
(`UpstoxLiveProvider()`), `app/cli/upstox_cli.py:62`
(`UpstoxLiveProvider(mock_mode=not token_configured)`). The runtime workspace path
(`app/services/intraday_options_service.py:657`) reads the token directly and constructs
`UpstoxOptionChainAdapter(mock_mode=False)` when a token is present, else `MockOptionChainAdapter()`.

## 4. Token Configuration

| Field | Value |
|---|---|
| `TOKEN_PRESENT` | **YES** |
| `TOKEN_CONFIGURED` | **YES** (non-empty after strip) |
| `TOKEN_SOURCE` | Environment variable `UPSTOX_ANALYTICS_TOKEN`, loaded through the repository `.env` file (873 B, key present) |
| `TOKEN_FORMAT_VALID` | **YES (structural only)** — the value has the 3 dot-separated segments of a JWT; length bucket 101–500 chars. No signature, issuer, audience or expiry validation exists in code |
| `TOKEN_USED_BY_PROVIDER` | **YES** — three consumers: `UpstoxLiveProvider` (`:174`), `UpstoxHistoricalProvider` (`:58`), `DerivativesContractMasterClient` (`app/services/derivatives_scope.py:1104`) |
| No actual value printed | ✔ access/refresh tokens, client secret and API key were never printed, logged, screenshotted or written |

* **Expiry:** no token-lifetime concept in code.
* **Refresh:** none. A rotated token requires an operator edit of `.env` + process restart.
* **Security:** the setting is `str`, so the token can be serialised by any `model_dump()`;
  `jwt_secret_key`/`csrf_secret_key` are `SecretStr` by contrast. Mitigated in practice by
  `redact_secrets` (`app/providers/upstox_historical_provider.py:30`) on log/telemetry paths.
* **Mock fallback:** when the token is empty, `UpstoxLiveProvider.get_live_quote`, `get_listed_expiries` and
  `subscribe_ticks` return mock/calendar data; `get_live_option_chain` returns a mock chain
  (`_generate_mock_live_option_chain`, `:893`, `source_name="UPSTOX_MOCK"`) and `get_index_spot`
  returns `(None, None, "UNAVAILABLE")`.

## 5. Live Provider Architecture

* **Instantiation:** per-service; each service instance constructs or receives a provider
  (`option_chain_adapter.py:116`, `derivatives_capture_service.py:350`,
  `derivatives_observation_service.py:1360`).
* **Authentication timing:** **lazy**. Nothing authenticates at startup; every REST method builds its own
  `httpx.AsyncClient` and attaches the bearer header per call.
* **Persistent client:** none. Each call is a fresh short-lived client (`timeout=5` for REST quotes/chain;
  `self._timeout` default 60 s for contract-master reads).
* **Retry:** only `get_live_option_chain` (`:432`) retries — `_BACKOFF_BASE_SECONDS = 0.5`,
  `_BACKOFF_MAX_ATTEMPTS = 3`, exponential, `Retry-After` respected on HTTP 429. All other methods are single-shot.
* **Rate limiting:** none in the provider; capture-level `rate_limit_rps` (`config.py:55`, default 5.0) est
  per-session only.
* **HTTP error handling:** 429/500/502/503/504 → retry, then `_provider_unavailable(...)`
  (`source_name="UNAVAILABLE"`, `spot_provenance="PROVIDER_ERROR"`). Permanent 4xx and auth failure →
  immediate `UNAVAILABLE` for the chain. **401/403 are not special-cased** — they fall through the generic
  non-transient branch.
* **Mock fallback on the chain path:** *closed* (G.11-OPT-5 D1) — a failed live read never substitutes the
  mock chain. **Still open on other paths:** `get_live_quote` (`:285`) returns `_generate_mock_quote` on
  exception, and `get_listed_expiries` (`:401`) returns `calculate_dynamic_index_expiries()` on exception.
* **`DerivativesContractMasterClient` fallback:** returns `ContractMasterResult(rows=[], source="UNAVAILABLE",
  reason="PROVIDER_TOKEN_NOT_CONFIGURED" | "PROVIDER_HTTP_…" | "PROVIDER_READ_FAILED:…")` — never a fabricated
  contract list. The calendar expiry schedule is explicitly *not* returned as
  `PROVIDER_LISTED_CONTRACT_MASTER`.

**Can `UPSTOX_MOCK` accidentally be used when the real provider is unavailable?**
On the **display** path: yes — any code constructing `UpstoxLiveProvider()` with an empty token gets mock
quotes/chain. On the **capture** path: no (section 9). Note also that
`UpstoxOptionChainAdapter.fetch_chain_snapshot` (`app/services/option_chain_adapter.py:~150`) **overwrites**
`chain.source_name = self.source_name` → `"UPSTOX"`, so a mock chain routed through the adapter is relabelled
`UPSTOX` in the UI. The capture service bypasses the adapter and calls the provider directly, so the true
`UPSTOX_MOCK`/`UNAVAILABLE` label reaches the guard.

## 6. Contract Master Architecture

| Layer | Location | Role |
|---|---|---|
| Read-only client | `app/services/derivatives_scope.py:1079` `DerivativesContractMasterClient` | Provider-listed options + futures; **no DB writes** |
| Result envelope | `:1062` `ContractMasterResult(rows, source, reason, fetched_at)` | `source ∈ {PROVIDER_LISTED_CONTRACT_MASTER, UNAVAILABLE}` (`:77,:82`) |
| Row shapes | `OptionContractRow` (instrument_key, trading_symbol, expiry_date, strike_price, option_type, lot_size, tick_size, is_weekly); `FutureContractRow` (instrument_key, trading_symbol, expiry_date, lot_size, name) | Dataclasses |
| Options source | `get_option_contracts` (`:1133`) → `GET {base_url}/option/contract?instrument_key=<index key>` | 900 s TTL in-memory cache |
| Expiries / strikes / lot | `get_listed_expiries` (`:1190`), `get_strike_grid`, `resolve_lot_size` | Provider's own `weekly` flag preserved |
| Futures source | `get_futures_contracts` (`:1266`) → `_read_futures` (`:1289`) → published instrument master `.json.gz` (`derivatives_instrument_master_url`, `config.py:142`) | Disk cache `data/reference/derivatives_contract_master/<exchange>_futures.json` — **present, 491 805 B, `nse_futures.json`, mtime 2026-10-06 15:54** |
| Underlying catalog | `UNDERLYING_CATALOG` (`:129`) — NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX | `index_instrument_key` + `instrument_master_exchange`; `SUPPORTED_UNDERLYINGS` = sorted keys |

**Persistence into `derivative_contracts` does not exist for Upstox.** The only writer is
`DerivativeContractImportService.import_contract_master_file` (`app/services/derivative_import.py:57`),
fed by the NSE CSV parser (`app/data/derivative_parser.py`) via
`app/cli/import_nse.py:225`. Seed scripts under the repo root (`_seed_opt*_qa_db.py`,
`qa_opt10_browser_audit.py`) create rows in QA/test databases only.

Answering section 9's ten questions: (1) source = Upstox `/option/contract` + public instrument master;
(2) yes, provided directly by Upstox over API; (3) futures additionally via a downloadable file; (4) yes, API;
(5) a parser exists for futures + options rows (`_parse_option_contract_row`); (6) normalization exists
(`normalize_underlying`, `infer_strike_grid`, `resolve_lot_size`); (7) **synchronization into the DB does not
exist**; (8) n/a — nothing to disable; (9) the client is production code, not test-only; (10) the *importer* is
historical-EOD-only.

## 7. DerivativeContract Database

Model `app/models/derivative_contract.py:35`. Migrations: `migrations/versions/b71d9e2a3456_add_derivative_contract_master_tables.py`
(creates the table) and `migrations/versions/b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py`
(adds `option_chain_snapshots`/`option_chain_quotes`).
Alembic head applied to the live DB: **`b3a4c5d6e7f8`**.

Key columns: `exchange`, `segment`, `instrument_type ∈ {FUTURE, OPTION}`, `underlying_stock_id` XOR
`underlying_index_id` (CHECK `ck_derivative_contracts_exactly_one_underlying`), `trading_symbol`,
`source_contract_id` (nullable), `expiry_date`, `strike_price`, `option_type ∈ {CE,PE}`, `lot_size > 0`,
`tick_size`, `lifecycle_status ∈ {ACTIVE,EXPIRED,INACTIVE,UNKNOWN}`, `idempotency_hash` **UNIQUE**,
`available_at`, `source_name`, `source_reference`, `fetched_at`.
Constraints: PK `id`; `uq_derivative_contracts_idempotency_hash`; 7 indexes.

Read-only DB state (database `ai_stock_scanner`, SELECT only):

| Metric | Value |
|---|---|
| `derivative_contracts` rows | **4** |
| by `instrument_type` | OPTION 4 (FUTURE **0**) |
| by `option_type` | CE 2 / PE 2 |
| distinct `expiry_date` | **2026-09-24 only** (min = max) |
| distinct `source_name` | `NSE` only |
| by index | `NIFTY` 4 (id 21–24: `NIFTY24SEP2624000CE/PE`, `NIFTY24SEP2624050CE/PE`), `lot_size=25` |
| `available_at` | 2020-01-01 (all 4) |
| `source_contract_id` populated | 0 |
| `lifecycle_status` | `ACTIVE` for all 4 (stale — expiry already passed) |
| `option_chain_snapshots` | **2** (id 5,6; `source_name='NSE_TEST'`; `expiry_scope=2026-09-24`; `quality_status='WARNING'` + `AVAILABLE_AT_INVERSION`; revisions 1,2; `batch_id=OPT1-20260924T041500Z-c7db4efd`) |
| `option_chain_quotes` | **8** (FOREIGN KEY to the 4 contracts; `UNIQUE (snapshot_id, contract_id, trade_date)`; `PRIMARY KEY (id, trade_date)` — partitioned) |
| `market_indices` | **1** — `NIFTY` (id 7, active, `provider_symbol` NULL). **No BANKNIFTY / FINNIFTY / MIDCPNIFTY / SENSEX row** |
| `stocks` | 3541 |

There is **no synchronization metadata** (no `synced_at`, no source-version column); contract-master reads are
cached in memory/disk only and never mirrored into the table.

## 8. Current Contract Gap

Runtime date is 2026-10-06. Required contracts for the live chain:

| Underlying | Required expiry (current) | Contracts on hand | CE | PE | Result |
|---|---|---|---|---|---|
| NIFTY | live listed weekly (e.g. 2026-10-08/2026-10-15) + monthly | 0 (table holds only expired `2026-09-24`) | 0 | 0 | **Every quote skipped → observation rejected** |
| BANKNIFTY | live listed weekly | 0 | 0 | 0 | **Underlying cannot even resolve** — no `market_indices` row |

Two independent blockers therefore exist:

1. **No matching `DerivativeContract`** for any currently listed NIFTY expiry/strike → the persistence gate
   (section 9) skips every quote.
2. **No `MarketIndex` row for BANKNIFTY** → `_resolve_underlying_index` (`:1326`) raises
   `UnderlyingResolutionError("No authoritative MarketIndex mapping …")` before any quote is considered.

Secondary observation: the calendar fallback `calculate_dynamic_index_expiries`
(`app/providers/upstox_live_provider.py:123`) computes **Thursday** weeklies (weekday 3). NSE's real index
weekly expiry day has since moved; the fallback is explicitly labelled a fallback and is never returned as
`PROVIDER_LISTED_CONTRACT_MASTER`, but any code path that trusts it while the provider read fails would show a
wrong expiry grid. The authoritative path (`/option/contract`) is unaffected.

## 9. Persistence Gate

Exact path that rejects an observation when the contract is missing or the chain is not real:

1. **Capture entry** — `DerivativesCaptureService._capture_cycle`
   (`app/services/derivatives_capture_service.py:462`):
   `opt_chain = await provider.get_live_option_chain(...)` then
   `_chain_is_real_observation(opt_chain)` (`:50`).
   `_FORBIDDEN_CHAIN_SOURCES = {"UPSTOX_MOCK","UNAVAILABLE","MOCK","TEST","UNKNOWN"}` (`:47`); additionally
   rejects any `source_name` containing `MOCK`/`TEST`/`UNAVAILABLE`, any `spot_provenance ∈
   {PROVIDER_ERROR, UNAVAILABLE}`, and any empty-quote chain. Failure ⇒ `return False`, counted as a failed
   request, **nothing persisted**. Persistence is then called with the chain's own `source_name` preserved
   (no `source_name=` override).
2. **Planning** — `OptionChainPersistenceService._plan_snapshot`
   (`app/services/option_chain_persistence_service.py:897`) → `_resolve_quote_plans` (`:1057`):
   * `_quote_identity_problem` rejects quotes missing strike / expiry / valid `option_type`.
   * `_resolve_contract_ids` (`:1122`) resolves each remaining quote to an **existing** `DerivativeContract`:
     * if `quote.contract_id` is set → `SELECT id, instrument_type FROM derivative_contracts WHERE id IN (…)`;
       unknown id or non-`OPTION` ⇒ skip;
     * else → `_preload_option_contract_cache` matches on
       `(exchange, segment, underlying_index_id, instrument_type='OPTION', expiry_date, strike_price, option_type)`;
       no match ⇒ skip with reason *"no canonical DerivativeContract matches (exchange, segment, underlying,
       OPTION, expiry, strike, option_type)"*.
   * It **never fabricates a contract or an identifier**.
3. **Flag / rejection** — skipped quotes are recorded as `DQ_FLAG_MISSING_CONTRACT` detail (`:1002`); if
   `quote_plans` is empty (or validation is invalid/rejected) and `allow_rejected` is false,
   `OptionChainValidationRejectedError` is raised and **no snapshot row and no quote row** are written.

Test coverage exists: `tests/test_option_chain_persistence_g11opt1.py::test_16_quotes_without_contract_are_flagged_not_invented`
and `tests/test_option_chain_persistence_g11opt1.py::test_14_unresolvable_underlying_is_refused_without_fabrication`.

**Answer to "Can mock data currently be persisted as real intraday observations?" → NO**, on the capture path:
the mock/unavailable chain is rejected before persistence (`_chain_is_real_observation`), and even if it
reached planning it would be excluded because mock quotes carry no matching `DerivativeContract`.
Residual risks (not mock-as-real, but worth recording): (a) the read path can display mock data as `UPSTOX`
after the adapter relabel; (b) `get_live_quote` still returns a mock quote on transport failure; (c) persisted
observations are **not filtered by `source_name`** on read (`derivatives_observation_service.py` reads all
`OptionChainSnapshot` rows for the underlying/expiry), so the existing `NSE_TEST` snapshots can serve as
interval baselines for display analytics.

## 10. Contract Identity

* **Authoritative database identity:** `DerivativeContract.id` (surrogate PK), used as
  `option_chain_quotes.contract_id` (FK, `ON DELETE CASCADE`).
* **Economic (natural) identity:** `(exchange, segment, underlying_index_id|underlying_stock_id,
  instrument_type, expiry_date, strike_price, option_type)` — exactly the tuple
  `compute_fingerprint()` (`app/models/derivative_contract.py`) hashes into `idempotency_hash`.
* **Provider identity:** `instrument_key` (e.g. `NSE_FO|…`, `NSE_INDEX|Nifty 50`) and `trading_symbol` are
  stored on the provider row (`OptionContractRow`) and, for futures observations, in the JSONL store — but
  `source_contract_id` in `derivative_contracts` is currently **unused/empty** (0 rows).
* **Observation identity:** `option_chain_quotes.contract_id` + `trade_date`, unique by
  `uq_option_chain_quotes_snapshot_contract_date (snapshot_id, contract_id, trade_date)`; snapshot revision
  uniqueness is enforced in application code under the advisory lock
  `_REVISION_LOCK_NAMESPACE = "option_chain_snapshot_revision_lock"`.
* **Consumers:** OI Change / OI vs Time group persisted quotes by
  `_contract_key = "<expiry>:<strike>:<option_type>"` (`derivatives_observation_service.py`).

**Join-break risk:** a contract-master sync that inserts *new* `DerivativeContract` rows is safe — existing
`(snapshot_id, contract_id, trade_date)` joins keep pointing at the same ids. A sync that **replaces or
deletes** rows would cascade-delete quote history (`ON DELETE CASCADE`) and break OI Change/OI Time joins and
the `idempotency_hash` uniqueness of history. Therefore any sync must be **INSERT-only (upsert by
`idempotency_hash`)**, never destructive, and must not change `compute_fingerprint()` inputs.

## 11. Synchronization Design Options

| Option | Code impact | DB impact | Provider impact | Operational risk | Failure behaviour | Test complexity | Production safety |
|---|---|---|---|---|---|---|---|
| **A. Manual operator sync** | New CLI/route; reuse `DerivativeContractImportService` shape | INSERT-only rows | One `/option/contract` read per underlying | Operator can forget; stale master | Explicit no-op, no rows | Low | High (explicit operator act) |
| **B. Startup sync** | ASGI lifespan hook | INSERT rows at boot | 1 read per underlying per boot | Adds boot latency; noisy on restart loops | Boot must not fail hard | Medium | Medium |
| **C. Pre-capture validate + sync** | Hook inside `start_capture` / `_capture_cycle` | INSERT rows immediately before capture | 1 read per underlying per capture start | Bounded; only runs when capture is operator-start | Capture refuses (already does for missing spot) | Medium | **Highest** |
| **D. Scheduled daily sync** | Scheduler task (repo already has scheduler infrastructure, all flags default `False`) | INSERT rows daily | 1 read/underlying/day | Needs a running scheduler; intraday rollovers missed until next run | Silent skip until next tick | Medium-High | Medium-High |
| **E. Lazy sync when expiry missing** | Inside persistence `_resolve_contract_ids` | INSERT rows on demand | Read inside the write path | **Blurs the persistence gate**; DB write during validation; hard to audit | Must fail closed | High | **Lowest** |

## 12. Recommended Synchronization Design

**Option C — pre-capture contract-master validation + sync** (with D as an out-of-scope future enhancement).

* It runs only when an operator explicitly starts a capture session, which is exactly the moment the required
  contracts must exist — so it cannot waste provider calls, cannot run unattended, and cannot change state
  while nobody is watching.
* It keeps synchronization **out of the persistence path** (unlike E), so `_resolve_contract_ids`
  (`option_chain_persistence_service.py:1122`) remains a pure, side-effect-free reader — the property that
  makes `test_16_quotes_without_contract_are_flagged_not_invented` meaningful.
* It is strictly read-then-INSERT-ONLY using the existing `idempotency_hash` unique constraint, so it cannot
  damage history (section 10).
* It fails closed: if the contract-master read is `UNAVAILABLE` (no token / HTTP error), capture refuses with
  an explicit reason instead of persisting partial history.

Why the alternatives are weaker: **A** depends on human memory; **B** couples boot success to provider
availability and adds start-up latency for a rarely needed operation; **D** cannot guarantee an intraday
weekly-expiry rollover because it runs once a day; **E** is the only option that puts a DB write inside the
validation gate, which is precisely the architectural boundary this audit exists to protect.

## 13. Token → Contract Master → Capture Order

The observed architecture matches the proposed order, with the explicit requirement that step 6 does **not**
imply step 7:

1. Validate configuration (`settings` loads; `USE` consumers resolve).
2. Validate Upstox token **presence/format only** (`is_available()`); no refresh, no rotation.
3. Read the contract master (`DerivativeContractMasterClient.get_option_contracts`).
4. Validate the read (`ContractMasterResult.is_provider_listed` and `is_available`; reject any non-`PROVIDER_LISTED`).
5. Synchronize rows — INSERT-only, idempotent (section 15).
6. Verify the required current contracts exist for the requested underlying + expiry (+ underlying
   `MarketIndex` row).
7. **Only then** permit a future observation capture — as a *separate, explicitly-enabled* act.

## 14. Capture Boundary

* Enablement flag: `settings.derivatives_observation_capture_enabled` (`app/core/config.py:52`, default
  `False`), surfaced by `DerivativesCaptureService.is_globally_enabled`
  (`derivatives_capture_service.py:177`) and `FuturesObservationStore.enabled`
  (`derivatives_observation_service.py:1066`).
* Entry point: `POST /market-data/options/capture/start` (`app/api/routes/options.py:601`) requiring the
  `market_data.write` permission → `start_capture(config, authorized=True)` (`:211`).
* Enforcement: the flag gates the **futures append store** (`:1152`, `:1200`) and is reported in
  `GET /capture/status`, but is **not** checked in `start_capture` (section 2, gap 3).
* Contract synchronization is a *different* action with a different function and no shared control; it must
  never set `derivatives_observation_capture_enabled`. Nothing in the current code couples them.

## 15. Idempotency / Rollover

* **Repeated sync is safe by construction.** `uq_derivative_contracts_idempotency_hash` plus
  `DerivativeContractRepository.get_by_fingerprint` (`app/repositories/derivative.py:33,44`) means a second
  sync of the same 1000 contracts yields 1000 rows, not 2000 — provided the fingerprint inputs are unchanged
  (`compute_fingerprint` covers exchange, underlying ids, instrument_type, expiry, strike, option_type).
  A sync implementation must therefore read-before-insert or use
  `INSERT … ON CONFLICT (idempotency_hash) DO NOTHING`.
* **`strike_price` normalisation** matters: the fingerprint stringifies `Decimal.normalize()`, so
  `24000.0000` and `24000` collapse to the same value; `_canonical_number` is used on the read side for the
  same reason.
* **Rollover:** the provider lists a new weekly contract each expiry cycle. Sync naturally INSERTs the new
  `(expiry, strike, option_type)` rows; **nothing should be deleted or rewritten**. Expired rows stay
  (`lifecycle_status` may be transitioned to `EXPIRED` only via an explicit, reversible policy — note the
  current 4 rows are still `ACTIVE` although their expiry passed, so lifecycle maintenance is already
  inconsistent and must not be assumed correct).
* **Weekly / monthly / weekends / holidays:** all resolved by the provider (`is_weekly` flag). No new
  contract appears on Saturdays/Sundays/holidays, so a sync run then is a no-op.
* **During market hours:** a sync adds new rows while observations are being written; because it never
  updates or deletes existing rows, in-flight observation joins are unaffected.

## 16. Security Audit

| Surface | Finding |
|---|---|
| Logs / exceptions | Covered by `redact_secrets` (`upstox_historical_provider.py:30`): exact-token replace (if >4 chars), `Bearer …` regex, and JSON `access_token|refresh_token|api_key|api_secret|client_secret|password|secret` regex. All provider error logs call it |
| Capture telemetry | Covered — `test_secret_redaction_in_capture_telemetry` (`tests/test_derivatives_capture_g11opt4.py`) |
| HTML / templates | **No exposure** — grep for `upstox_analytics_token` / `authtoken` / `access_token` across `app/templates/` and `app/static/` returns nothing |
| API responses | **No exposure** — no route serialises `settings` or the token; `/capture/status` returns telemetry only |
| Browser JS | No token surface found |
| Database | Token is never persisted (not a column anywhere) |
| Screenshots / reports | This audit produced no screenshots and no secret values |

Real residual risks:

1. **`upstox_analytics_token` is a plain `str`**, not `SecretStr` — any future `settings.model_dump()` or
   debug dump would expose it. `jwt_secret_key` / `csrf_secret_key` already use `SecretStr`; the token should
   match.
2. **`subscribe_ticks` embeds the token in a URI query string** —
   `ws_uri = authorized_ws_uri or f"{self.ws_url}?authtoken={self.token}"`
   (`upstox_live_provider.py:~518`). It is not logged today, but `redact_secrets` has **no `authtoken=`
   pattern**, so a future log/exception of that URI would leak the token.
3. `_get_headers()` output is never logged (httpx errors do not include headers), so the bearer header is safe
   in the current code.

## 17. Test Coverage

| Test | Purpose | Status | Gap |
|---|---|---|---|
| `tests/test_upstox_provider.py` (8) | capabilities, **token redaction & security**, historical candle parsing, live quote parsing, freshness, mock tick stream, source/Greek preservation, market-closed suppression | Present | No token-expiry/refresh test (feature absent) |
| `tests/test_multi_provider.py` | Provider registry / mock live provider wiring | Present | — |
| `tests/test_derivative_contract_master.py` (14) | Future/option invariants, exactly-one-underlying, **idempotency dedup**, `available_at` PIT gate, lot-size history, parser formats/malformed/header/omission, importer underlying resolution, coverage | Present | Nothing exercises Upstox → DB sync (feature absent) |
| `tests/test_option_chain_persistence_g11opt1.py` (16) | Atomic persist, rollback, revision immutability, PIT, provenance separation, partitions, concurrency, **unresolvable underlying refused**, **off-grid strikes rejected**, **quotes without contract flagged not invented** | Present | No "after sync, observation accepted" case |
| `tests/test_derivatives_capture_g11opt4.py` (13) | **Capture disabled by default**, start/stop authorization, API permissions, config validation, no fabricated data on provider error, synthetic futures prohibition, dedup, storage/cleanup, replay no-lookahead, secret redaction, schema unchanged | Present | Does not test that `capture/start` honours the global flag |
| `tests/test_derivatives_quality_g11opt5.py` (38) | Transient HTTP never returns mock, 429 `Retry-After`, permanent 4xx → UNAVAILABLE, **chain rejection classifier**, capture rejects mock/unavailable, accepts real, duplicates count zero, source_name preserved, interval windows use real baselines only | Present | No contract-master-unavailable capture test |
| `tests/test_derivatives_observations_g11opt3.py`, `test_derivatives_scope_g11opt3.py`, `test_historical_acceptance_g11opt6.py` | Scope/expiries/ladder, real-observation acceptance | Present | — |

No test file was modified.

## 18. Required Future Tests

Design only (none implemented):

| ID | Test |
|---|---|
| A | Token missing → provider `is_available()` False; contract master returns `PROVIDER_TOKEN_NOT_CONFIGURED`; capture refuses |
| B | Token invalid/shape-wrong → provider read raises/returns `UNAVAILABLE`; no mock persisted |
| C | Token present → contract master read attempts `PROVIDER_LISTED`; header carries `Bearer` (asserted via transport stub, never the value) |
| D | Provider unavailable (timeout/5xx) → `UNAVAILABLE`, capture refuses, zero rows |
| E | Contract master unavailable → sync is a no-op and capture refuses with an explicit reason |
| F | Malformed contract-master payload → parse yields 0 rows, `PROVIDER_LISTED` not claimed, no rows written |
| G | Duplicate contract in payload → one row after sync (idempotency hash) |
| H | Current expiry missing → persistence skips quotes; `DQ_FLAG_MISSING_CONTRACT` set |
| I | Current expiry synchronized → quotes resolve; snapshot persisted |
| J | NIFTY sync → rows for NIFTY `underlying_index_id` |
| K | BANKNIFTY sync → requires a `MarketIndex` row first; assert refusal when absent |
| L | CE rows created with correct `option_type`/`strike_price` |
| M | PE rows created likewise |
| N | Expired contract → retained, never deleted |
| O | Repeated sync → row count unchanged |
| P | Observation after sync → accepted and readable |
| Q | Mock observation rejected → no snapshot row |
| R | Real observation accepted → snapshot + quotes rows |
| S | OI Change after two real observations → non-null interval ΔOI |
| T | OI Time after multiple observations → ordered series |

## 19. Exact File Impact

**LIST A — existing files that already provide the required capability**

* `app/services/derivatives_scope.py` — `DerivativesContractMasterClient` (read-only provider master; reuse as-is).
* `app/providers/upstox_live_provider.py` — token consumption, bearer headers, `is_available` (presence check).
* `app/models/derivative_contract.py` — model, invariants, `compute_fingerprint`, `idempotency_hash`.
* `app/repositories/derivative.py` — `get_by_fingerprint` (idempotency primitive).
* `app/services/derivative_import.py` — INSERT-only, underlying-resolution, DQ-issue pattern to mirror.
* `app/services/option_chain_persistence_service.py` — the contract-resolution gate (unchanged).
* `app/services/derivatives_capture_service.py` — `_chain_is_real_observation`, capture lifecycle.
* `app/providers/upstox_historical_provider.py` — `redact_secrets`.
* `migrations/versions/b71d9e2a3456_…`, `migrations/versions/b3a4c5d6e7f8_…` — schema + uniqueness (no new migration required for INSERT-only sync).

**LIST B — existing files that would probably need modification**

* `app/services/derivatives_scope.py` *(or a sibling)* — a *new* sync function is required; placing it here keeps it next to the client it wraps and avoids a new module. Reason: the sync must consume `ContractMasterResult` and emit `DerivativeContract` rows.
* `app/services/derivatives_capture_service.py` — add the pre-capture validation hook (Option C) and (recommended) honour `is_globally_enabled` in `start_capture`. Reason: this is the only place the "validate before capture" order can be enforced.
* `app/core/config.py` — add a sync-related setting (e.g. a per-session sync toggle). Reason: it must be independently controllable and default-safe.
* `app/api/routes/options.py` — surface sync outcome in `capture/status` / the start response. Reason: operator visibility; no new permission.
* `app/providers/upstox_historical_provider.py` — extend `redact_secrets` with an `authtoken=` pattern (section 16, risk 2). Reason: latent leak surface.

**LIST C — new files that would probably need to be created**

* `app/services/derivatives_contract_sync_service.py` — the INSERT-only, idempotent sync orchestrator. Reason: keeps `derivatives_scope.py` (a pure reader) free of writes and gives the sync a single auditable home.
* `tests/test_derivatives_contract_sync_g11opt14.py` — the matrix in section 18 (A–T). Reason: no existing test covers sync.
* `docs/G11OPT14_IMPLEMENTATION_REPORT.md` — post-implementation report (future).

**LIST D — files that must remain untouched**

* `app/services/option_chain_persistence_service.py` — `_resolve_contract_ids`/`_plan_snapshot` must stay side-effect-free (this is what keeps Option E rejected).
* `app/services/derivatives_observation_service.py` — read-only analytics; no write path belongs here.
* `app/models/option_chain.py` and all migrations — no schema change is required or permitted.
* `app/models/derivative_contract.py` — the fingerprint inputs are a compatibility contract for history.
* `app/templates/**` — no UI change in this scope (`options_workspace.html` and the Historical Derivatives
  Workbench are explicitly out of scope).
* `run_aistockscanner.bat`, `.env`, `tests/conftest.py` isolation guard, auth/RBAC, `.freebuff/`.

## 20. Proposed G.11-OPT-14 Architecture

High-level only:

```
        UPSTOX  (/option/contract  +  public instrument master .json.gz)
           │
           ▼
   Token validation            (is_available: presence/format only — no refresh)
           │
           ▼
   Contract Master             (DerivativesContractMasterClient — read-only, TTL cache)
           │
           ▼
   Contract normalization      (normalize_underlying, parse rows, is_weekly, lot/tick)
           │
           ▼
   derivative_contracts        (INSERT ONLY, ON CONFLICT (idempotency_hash) DO NOTHING)
           │
        VALIDATED
           │
           ▼
   [future capture boundary]   (derivatives_observation_capture_enabled + operator permission)
           │
           ▼
   option observations         (option_chain_snapshots + option_chain_quotes)
           │
     ┌─────┴─────┐
     ▼           ▼
 OI Change   OI vs Time
```

## 21. Implementation Sequence

No code. Ordered plan:

1. Confirm the open questions in section 2 (provider payload shape; BANKNIFTY `MarketIndex` seeding; intended
   capture-flag enforcement point).
2. Change `upstox_analytics_token` to `SecretStr` and extend `redact_secrets` with an `authtoken=` pattern.
3. Build `DerivativesContractSyncService` on top of `DerivativesContractMasterClient`, writing through
   `DerivativeContractRepository` with INSERT-only idempotency; refuse when `source != PROVIDER_LISTED`.
4. Ensure/prepare the required `MarketIndex` rows for each underlying to be captured (data task, not schema).
5. Wire the pre-capture validation hook into `DerivativesCaptureService.start_capture`, returning an explicit
   refusal reason when the sync or verification fails.
6. Add sync outcome reporting to `GET /capture/status`.
7. Add the section 18 tests (A–T) including a transport stub for the provider (no live token use in CI).
8. Re-run the existing regression suites (capture G.11-OPT-4, quality G.11-OPT-5, persistence G.11-OPT-1,
   contract master, options workspace) and confirm 0 failures.
9. Only after 1–8 are certified, consider a separate authorization to enable capture.

## 22. Certification Gates

Capture must remain disabled until **all** of the following hold:

1. `DerivativesContractSyncService` exists and is INSERT-only + idempotent; a repeated sync test proves no
   duplicate rows (section 18 G/O).
2. Contract master is `PROVIDER_LISTED_CONTRACT_MASTER` for the target underlying; non-`PROVIDER_LISTED`
   sources are refused.
3. Required current contracts (CE **and** PE, all strikes) exist for the target underlying+expiry.
4. A `MarketIndex` row exists for each underlying to be captured (BANKNIFTY currently fails this).
5. `_chain_is_real_observation` rejects mock/unavailable chains (already certified in G.11-OPT-5).
6. Persisted observations are joined only to existing contracts, and no destructive write path exists.
7. `settings.derivatives_observation_capture_enabled` defaults `False` and is the only capture switch.
8. `start_capture` behaviour with the global flag is decided and tested (section 2, gap 3).
9. Token handling is `SecretStr` and the `authtoken=` leak surface is closed.
10. OI Change produces a non-null interval ΔOI from two *real* persisted observations (section 18 S).
11. Full regression suite green (0 failures) with no weakened assertions.
12. Explicit written operator authorization (this audit does not provide it).

## 23. Final Decision

**HOLD — MORE DISCOVERY REQUIRED**

The design is established and the safety boundary is intact and proven (mock cannot be persisted as a real
observation; missing contracts are skipped, never invented; contract sync and capture are independent). The
hold is solely because three prerequisites must be resolved — and, for the first, verified first-hand with an
authorized provider read — before implementation: the unverified `/option/contract` payload, the absent
BANKNIFTY `MarketIndex` row, and the capture-flag enforcement point.

## 24. Git / Database Safety Confirmation

| Item | Value |
|---|---|
| Files modified (source/template/test/migration/schema) | **0** |
| Tests modified | **0** |
| Database rows inserted/updated/deleted | **0** (SELECT-only sessions) |
| Contract master synchronized | **NO** |
| Upstox token activated/refreshed/rotated | **NO** (presence/format inspected only; value never printed) |
| Provider capture enabled/started | **NO** |
| Production ingestion enabled | **NO** |
| Broker orders placed | **NO** |
| Git staged changes | **0** |
| Commits | **0** |
| Pushes | **0** |
| Destructive Git operations (reset/restore/checkout/clean/stash/rebase/amend) | **0** |

HEAD remains `4fa354d3ddfeb8f6c36bbaf05815d23b4ab75897` on
`checkpoint/sprint19-20-20260822`; the working tree is unchanged from section 1. The only artifact created by
this audit is this report document (`docs/G11OPT14_TOKEN_CONTRACT_MASTER_DESIGN_AUDIT.md`); no existing file
was altered.

"NO IMPLEMENTATION, CONTRACT-MASTER SYNCHRONIZATION,
DATABASE WRITE, TOKEN ACTIVATION, PROVIDER CAPTURE,
COMMIT, OR PUSH IS AUTHORIZED BY THIS AUDIT."
