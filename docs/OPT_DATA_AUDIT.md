# OPT-DATA-AUDIT: One-Minute Futures/Options Data Readiness

**Audit ID:** OPT-DATA-AUDIT  
**Audit date:** 2026-10-23  
**Repository:** `D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`  
**Branch:** `checkpoint/sprint19-20-20260822`  
**HEAD:** `b2df61f1d56c5a5032787c96678326d0a73f6173`  
**Working-tree state at audit start:** 0 staged, 21 modified tracked files, 282 untracked status lines, Alembic head `e5f6a7b8c9d0` (28 revisions).  
**Method:** read-only source inspection. No database connection, migration, provider call, capture session, scheduler, test, or Git mutation was performed.

> **Verdict:** There is **no native one-minute OHLC candle infrastructure** for futures or options.  
> What exists is (a) mature **EOD** derivatives storage/analytics, (b) a gated, operator-started **live option-chain snapshot** path into PostgreSQL, and (c) a gated **futures observation** path into append-only JSONL files.  
> Existing "1 minute" analytics are computed from repeated **last-price/OI snapshots** (and futures JSONL observations), not from one-minute OHLC bars. A true 1m candle/backfill capability is **MISSING**.

---

## 1. Scope and evidence standard

This audit answers four questions:

1. What futures/options data models and tables already exist?
2. What ingestion/capture paths actually write them?
3. What analytics and UI already consume them?
4. What is required for a genuine one-minute futures/options pipeline, and which parts can be reused?

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| **SOURCE-VERIFIED** | Directly visible in the current working tree with an exact file:line reference. |
| **SOURCE-PARTIAL** | Code path exists, but a required capability is incomplete, gated, unwired, or only partially implemented. |
| **MISSING** | No schema, code path, or wiring was found in the audited areas. |
| **UNKNOWN** | Cannot be established without data/runtime access that this audit was not authorized to use. |

All line numbers refer to the **current working tree**, which contains 21 pre-existing modifications relative to HEAD. They are not guaranteed to match HEAD.

---

## 2. Current derivatives data architecture

### 2.1 Canonical contract identity

`DerivativeContract` is the canonical futures/options identity table.

- Class/table: `app/models/derivative_contract.py:37`, `:40`
- Identity fields: exchange/segment `:76-81`, instrument type `:82`, underlying stock/index `:84-90`, trading symbol `:92`, provider contract id `:93`, expiry `:96`, strike/option type `:97-98`, lot/tick `:99-100`, lifecycle `:105-112`, idempotency hash `:112`
- Point-in-time field is **date-granular**: `available_at: date` at `:113`
- Migration: `migrations/versions/b71d9e2a3456_add_derivative_contract_master_tables.py:22-79`

This is reusable as the contract dimension for any intraday bar table.

### 2.2 EOD futures/options prices

`DerivativeDailyPrice` is the only OHLC table for derivatives.

- Class/table: `app/models/derivative_daily_price.py:34`, `:37`
- Unique key `(contract_id, trade_date)`: `:39`
- OHLC `:70-73`; last/settlement/previous close `:75-77`; volume/turnover/OI/change in OI `:80-84`; underlying value `:87`
- `available_at: date` at `:90`, source `:91-93`
- Migration: `migrations/versions/c82e3f4a5678_add_derivative_daily_prices_table.py:24-66`

This is **daily only**. `trade_date` is a `Date`, not a timestamp. It cannot represent a 1m bar.

### 2.3 Option-chain snapshots and quotes

`OptionChainSnapshot` is a timestamped cross-sectional chain header; `OptionChainQuote` stores per-contract quote observations belonging to a snapshot.

- Header class/table: `app/models/option_chain.py:33`, `:36`
- Chain scope and expiry scope: `:86-87`
- Timestamp model: `market_timestamp` `:91`, `source_timestamp` `:92`, `ingestion_timestamp` `:93`, `available_at` `:94`
- Provenance: `:97-101`; revision `:104`; quality/completeness `:107-117`; futures reference `:120-123`; Greek reproducibility context `:126-131`
- Partial unique indexes: `:56-78`
- Quote class/table: `:156`, `:159`
- Quote unique key `(snapshot_id, contract_id, trade_date)`: `:161-166`
- Quote fields: LTP `:210`, bid/ask and sizes `:211-214`, volume `:216`, OI `:217`, source/interval/session OI changes `:220-226`, source Greeks `:229-233`, calculated Greeks `:237-242`
- Range partitioning is PostgreSQL-specific: migration `migrations/versions/b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py:220-236`
- Partition helper: `app/services/option_chain_partition_service.py:4`, `:74`, `:111`

Critical exclusions: **there is no high, low, open, or close column for the option premium** in `OptionChainQuote`. LTP is a point-in-time last traded price. A snapshot row is a quote observation, not an OHLC bar.

Migration: `migrations/versions/b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py:24-26`.

### 2.4 Underlying/index prices

`MarketIndexPrice` is daily OHLC only.

- Class/table: `app/models/market_index_price.py:15`, `:16`
- Daily unique key: `:19`
- OHLC `:29-32`; previous close/change `:33-37`; volume `:40`; `observed_at`/`fetched_at` are **dates** `:41-42`
- Migration: `migrations/versions/25e6f159a646_add_market_intelligence_foundation_.py:49-82`

`DailyPrice` is also daily OHLC only: `app/models/daily_price.py:16-36`.

There is **no intraday index/underlying price table**.

### 2.5 EOD-only supporting data

- Participant positions: `app/models/derivative_participant.py:34-57`; table `derivative_participant_positions`; report/available dates are `Date`.
- FII derivative statistics: `app/models/derivative_participant.py:80-102`.
- Market intelligence snapshots: `app/models/market_intelligence_snapshot.py:12-13`, daily.
- Migrations: participant `e91f4a5678ab_add_derivative_participant_tables.py:24-107`; market intelligence `25e6f159a646:114-160`.

These are EOD context sources, not intraday market-data sources.

### 2.6 No intraday bar/candle schema found

No model or migration in the audited schema declares a minute/intraday/candle/tick OHLC table. The time-series tables found are:

- `derivative_daily_prices` — daily
- `option_chain_snapshots` / `option_chain_quotes` — timestamped snapshots/quotes
- `market_index_prices` — daily
- `daily_prices` — daily
- participant/FII/market-intelligence tables — EOD

`NormalizedBar` exists as a provider-neutral object and explicitly supports "daily or intraday" (`app/providers/models.py:64-81`), but no persistence model consumes intraday bars.

**Conclusion: 1m OHLC storage is MISSING.**

---

## 3. Ingestion and capture paths

### 3.1 Live option-chain capture (PostgreSQL)

The only intraday options persistence path is the operator-controlled capture service.

- Default interval: 60 seconds (`app/services/derivatives_capture_service.py:100`)
- Minimum interval: 10 seconds (`:116-117`)
- Session bounds: 09:15–15:30 IST on weekdays (`:267-281`)
- Global gate: `derivatives_observation_capture_enabled`, default **False** (`app/core/config.py:52`; check `app/services/derivatives_capture_service.py:71-81`)
- Disabled start is refused (`derivatives_capture_service.py:241-246`)
- API start requires `market_data.write` and returns 503 when disabled (`app/api/routes/options.py:601-631`)
- Each cycle resolves scope/spot, fetches a **live** chain, rejects non-real chains, and persists it (`derivatives_capture_service.py:466-523`)
- Persistence also independently refuses live observations when capture is disabled (`app/services/option_chain_persistence_service.py:777-786`)
- The persistence layer stores `market_timestamp` from the normalized chain and defaults `available_at` to ingestion time (`option_chain_persistence_service.py:973-987`)

Important timestamp limitation: the live chain parser sets only `as_of_timestamp=datetime.now(UTC)` and per-quote `timestamp=datetime.now(UTC)`; it does **not** preserve a provider exchange timestamp. The adapter then copies that local `as_of_timestamp` into both `source_timestamp` and `market_timestamp` (`app/services/option_chain_adapter.py:165-170`; parser `app/providers/upstox_live_provider.py:745-786`, `:803-864`). The snapshot's market timestamp is therefore local observation/fetch time, not an exchange timestamp. Minute bucketing will be approximate.

Deduplication is based on the logical snapshot key and canonical payload hash, with revision allocation (`option_chain_persistence_service.py:837-906`). Repeated identical snapshots are ignored rather than appended as new data.

**What this path produces:** per-contract LTP, bid/ask, volume, OI, source OI change and Greeks at a repeating observation instant.  
**What it does not produce:** premium open/high/low/close for a minute.

### 3.2 Live futures observations (JSONL, not PostgreSQL)

Futures observations are appended to JSONL, not written to a database table.

- Observation fields: `app/services/derivatives_observation_service.py:981-1006`
- JSONL store: `:1045-1069`; file name `UNDERLYING_YYYY-MM-DD.jsonl` at `:1068-1069`
- Capture writes with `append_with_result` only after an authorized session (`derivatives_capture_service.py:525-590`)
- `FuturesObservation` carries `last_price`, `previous_close`, **day** open/high/low, volume, OI, OI day high/low, buy/sell quantity, spot, and timestamps (`derivatives_observation_service.py:984-1005`)
- `day_open/day_high/day_low` are session-to-date provider values, not interval OHLC
- The futures ledger reads this store and a live quote (`app/services/derivatives_futures_ledger.py:801-927`; `app/services/derivatives_observation_service.py:1304`)

At audit time, no JSONL files existed in the repository and the default observation directory `data/derivatives_observations` did not exist (`Test-Path` = False). This proves there is no **repository-local** futures observation history. It does not prove the production/development database or another deployment path is empty.

### 3.3 Historical provider bars (daily only in practice)

`UpstoxHistoricalProvider.fetch_historical_bars` accepts an `interval` argument (`app/providers/upstox_historical_provider.py:96-102`) but hardcodes the `day` endpoint (`:126`), so intraday candles are never requested. The parser derives `trade_date` via `.date()` (`:174`) while retaining a full `timestamp` (`:187`); the `interval` value is unused for both the request and the resulting bar series.

The NSE historical provider reads only the daily `DailyPrice` table (`app/providers/nse_historical_provider.py:67-108`).

**Conclusion: no intraday historical backfill is implemented, even if the provider entitlement exists.**

### 3.4 Tick stream (code exists, not wired to storage)

`UpstoxLiveProvider.subscribe_ticks` implements a V3 WebSocket stream (`app/providers/upstox_live_provider.py:513-620`). Its only consumer found is the CLI demo (`app/cli/upstox_cli.py:89`). It is not used by the capture service, scheduler, analytics, or persistence.

The stream yields `NormalizedQuote` objects (LTP/close/volume/OI/bid/ask), not OHLC bars, and there is no tick-to-bar aggregator.

### 3.5 EOD import paths (manual, existing)

Existing EOD imports are CLI-driven and separate from live capture:

- Contract master: `app/services/derivative_import.py:49-136`; CLI `app/cli/import_nse.py:204-228`
- Futures bhavcopy: `app/services/derivative_futures_import.py:56`, fields `:182-200`; CLI `app/cli/import_nse.py:247-284`
- Options bhavcopy: `app/services/derivative_options_import.py:63`, fields `:219-237`; CLI `app/cli/import_nse.py:288-326`
- Participant OI/volume/FII: CLI `app/cli/import_nse.py:330-432`
- Contract sync is separately gated and disabled by default (`app/services/derivative_contract_sync_service.py:11`, `:143-160`; CLI `app/cli/import_nse.py:608-687`)

No derivative ingestion was found in `app/scheduler/*.py`. The scheduler does not start capture or import derivatives.

### 3.6 Historical EOD archive present on disk

A read-only filename inventory (not content parsing, not DB rows) found:

- 255 NSE F&O bhavcopy ZIP files
- 234 unique trade dates
- First date: **2025-10-01**
- Last date: **2026-09-10**
- No gap greater than 4 calendar days between consecutive unique dates
- Locations: `data/reference/derivatives/raw` (234 files) and `data/raw/nse/2026/06_udiff` (21 files)

This is evidence that **raw EOD archive files exist locally**. It is not evidence that those files have been imported, that rows are complete, or that the database contains matching dates. Those remain **UNKNOWN**.

---

## 4. Analytics and UI: what exists and what it actually reads

### 4.1 Service surface

`IntradayOptionsService` owns the live/snapshot-based analytics:

| Capability | Method | Location |
|---|---|---|
| Underlying scope and spot | `get_scope` | `app/services/intraday_options_service.py:827` |
| Provider-listed expiries | `get_available_expiries` | `:934` |
| Live strike ladder | `get_option_chain_ladder` | `:1043` |
| Dashboard summary | `get_option_dashboard` | `:1312` |
| Live PCR | `get_live_pcr` | `:1385` |
| Call vs put OI | `get_call_vs_put_oi` | `:1434` |
| Strike OI change | `get_oi_change_analysis` | `:1508` |
| OI statistics / max pain | `get_oi_statistics` | `:1609` |
| Interval OI change | `get_interval_oi_change` | `:1758` |
| Multi-strike trajectories | `get_multistrike_oi` | `:2054` |
| Options OI vs time | `get_options_oi_vs_time` | `:2154` |
| Futures OI vs time | `get_futures_oi_vs_time` | `:2286` |
| Session replay timeline | `get_session_replay_timeline` | `:2340` |
| Replay ladder | `get_replay_ladder` | `:2441` |

The ladder fetches a **live** provider chain through `_fetch_normalized_chain` (`:783-803`) and `get_option_chain_ladder` (`:1069-1074`). It is not a historical candle chart.

The interval/OI-vs-time analytics read persisted snapshots through `_load_real_series` (`:1987-2052`) and `OptionObservationRepository` (`app/services/derivatives_observation_service.py:672`, `:775-810`, `:845`). The repository applies point-in-time visibility with `OptionChainSnapshot.available_at <= evaluation_at` (`:800-802`) and a session bound (`:807-810`).

The "1 minute" interval option in the UI is therefore an **OI/last-price interval measurement** over stored observations, not a 1m OHLC chart. The service explicitly refuses to pro-rate a longer interval into a shorter one (`intraday_options_service.py:1782-1841`).

### 4.2 EOD historical analytics

`HistoricalOptionChainService` explicitly reconstructs an **EOD** option chain from `DerivativeDailyPrice` + `DerivativeContract`:

- Intent: `app/services/historical_option_chain.py:1-19`
- Query: `:134-150`
- Expiries: `:337-349`
- Available dates: `:371-385`
- Independent spot: `:446-490`

Consumers include advanced options/exposure/OI intelligence/opportunity services:

- `app/services/advanced_options_service.py:55`
- `app/services/advanced_exposure_service.py:85`
- `app/services/historical_oi_intelligence_service.py:47`
- `app/services/historical_options_analytics.py:54`
- `app/services/options_opportunity_service.py:65`
- `app/services/historical_derivatives_replay.py:42-59`

This is a strong **EOD** analytics foundation and should be kept separate from 1m logic.

### 4.3 Routes and pages (production-wired)

Derivatives workspace pages:

- `/derivatives` — `app/api/routes/market_data_pages.py:209-210`
- `/derivatives/option-chain` and `/options` — `:228-230`
- `/derivatives/oi-statistics` — `:318-319`
- `/derivatives/oi-change` — `:377-378`
- `/derivatives/multistrike-oi` — `:444-445`
- `/derivatives/options-oi-time` — `:506-507`
- `/derivatives/futures-oi-time` — `:568-569`
- `/derivatives/oi-replay` — `:636-637`

JSON API surface for the same capabilities: `app/api/routes/options.py:144-579` (underlyings, scope, expiries, contracts, chain, dashboard, PCR, OI comparison, OI change, futures ledger, export, replay, replay ladder, OI statistics, multistrike, options/futures OI time, replay timeline).

Capture control: `app/api/routes/options.py:593-705` (`/capture/status`, `/capture/start`, `/capture/stop`, `/capture/cleanup`).

Templates already render ladder/PCR/OI/replay/futures views, e.g.:

- `app/templates/components/intraday_option_chain_table.html:1-40`
- `app/templates/components/derivatives_filter_panel.html:26`, `:111` (1m–60m interval choices)
- `app/templates/components/intraday_option_pcr_view.html:16-17`
- `app/templates/components/intraday_option_oi_change_view.html:14-16`
- `app/templates/components/intraday_option_oi_comparison.html:12`
- `app/templates/components/derivatives_nav_tabs.html:5`, `:17`, `:24`, `:35`
- `app/templates/components/derivatives_replay_cards.html:102-103`
- `app/templates/components/futures_analytics_table.html:23`

### 4.4 Reuse implication

- **Direct reuse:** contract identity, EOD chain reconstruction, snapshot quote/OI persistence, PIT visibility, PCR/OI/max-pain/summary logic, page/API/template patterns.
- **Adaptation required:** the snapshot repository and OI interval engine can be generalized to read bars, but their data contract is observation-oriented; they do not compute OHLC.
- **Not reusable for 1m OHLC:** `DerivativeDailyPrice`, `MarketIndexPrice`, `HistoryOptionChainService`, and the daily import pipeline, because all are date-granular.

---

## 5. Build readiness matrix

| Capability | Status | Evidence summary | Runtime/coverage |
|---|---|---|---|
| Canonical futures/options contract identity | **READY** | `DerivativeContract`, gated sync, lot/metadata history | Schema/source verified; current row counts UNKNOWN |
| EOD futures/options OHLC + OI | **READY** | `DerivativeDailyPrice`, CLI bhavcopy imports | Source verified; imported date coverage UNKNOWN |
| EOD option chain reconstruction | **READY** | `HistoricalOptionChainService` over daily tables | Source verified; loaded coverage UNKNOWN |
| EOD participant/FII context | **READY** | Participant/FII models and imports | Source verified; loaded coverage UNKNOWN |
| Live option-chain snapshot capture | **READY BUT GATED** | 60s default, 10s minimum, 09:15–15:30 IST, capture flag False | Code path verified; no capture run during audit |
| Snapshot-based OI/PCR/max-pain analytics | **READY BUT DATA-DEPENDENT** | Repositories/services/templates wired | Requires stored snapshots; coverage UNKNOWN |
| Live futures quote/ledger | **READY BUT GATED** | JSONL store + live quote + ledger | Code path verified; no JSONL files present locally |
| Tick WebSocket ingestion | **PARTIAL / UNWIRED** | `subscribe_ticks` exists; only CLI uses it | Not connected to storage or analytics |
| Intraday historical backfill | **MISSING** | Upstox historical endpoint hardcodes `day`; NSE provider reads daily table | No 1m history path |
| 1m futures OHLC bars | **MISSING** | No schema; JSONL observations are point samples | Not available |
| 1m options OHLC bars | **MISSING** | `OptionChainQuote` has LTP/bid/ask/OI, no OHLC | Not available |
| 1m underlying/index bars | **MISSING** | `MarketIndexPrice`/`DailyPrice` are daily only | Not available |
| 1m bar point-in-time semantics | **MISSING** | EOD uses `available_at: date`; snapshots use ingestion timestamps | Not designed for bars |
| Production scheduler for derivatives capture | **MISSING** | No derivative references in `app/scheduler/*.py` | Capture is operator-started only |
| Test isolation for option-chain tables | **RISK** | `_TABLES_TO_TRUNCATE` omits `option_chain_snapshots`/`option_chain_quotes` (`tests/conftest.py:115-133`) | Cross-test contamination risk |

---

## 6. Recommended dependency-ordered milestones

These are recommendations only; no implementation was performed.

### M0 — Close the data unknowns (read-only, authorized)
- Query row counts, min/max timestamps, distinct session dates, and partition inventory for `option_chain_snapshots`, `option_chain_quotes`, `derivative_daily_prices`, `derivative_contracts`, `market_index_prices`.
- Query the actual JSONL observation directory in the deployment environment.
- Confirm whether the 255 raw F&O bhavcopy files were imported and for which dates.
- **Exit criterion:** every "UNKNOWN" coverage claim in this audit is replaced by a measured value or explicitly declared out of scope.

### M1 — Freeze the 1m data contract
- Define a canonical bar schema keyed by `(contract_id, interval, bar_start)` with `trade_date`, OHLC, volume, OI, source, `available_at`, and quality flags.
- Decide whether options bars are per-contract only or also aggregated per strike/expiry.
- Define session/bar alignment in IST and how exchange timestamps are obtained.
- **Exit criterion:** approved schema and PIT semantics, including late-arrival and correction rules.

### M2 — Establish a real intraday source
- Verify provider entitlement for 1m historical candles and exchange timestamps.
- If entitled, implement a provider interval path that preserves timestamps and does not collapse to a date (the current Upstox path hardcodes `day` at `upstox_historical_provider.py:126` and truncates at `:174`).
- If not entitled, use the existing tick stream or 10–60s snapshots and explicitly label derived bars as **observed-sample-derived**, with no claim of true intra-minute high/low.
- **Exit criterion:** an evidenced source that can produce, or honestly approximate, OHLC per minute.

### M3 — Build the 1m writer and backfill
- Add the schema/migration, partitioning/retention, idempotent upsert, and source/provenance fields.
- Backfill from provider candles where available; do not fabricate bars from EOD data.
- Reuse `DerivativeContract` identity and existing quality conventions.
- **Exit criterion:** reproducible backfill with row-count and checksum reconciliation.

### M4 — Wire capture/aggregation
- If using ticks: aggregate into 1m buckets with explicit handling of missing minutes, late ticks, and session boundaries.
- If using snapshots: persist observations as today and derive 1m close/OI series only; do not label them OHLC.
- Keep the existing capture gate, bounds, rate limit, storage cap, and dedupe behavior.
- **Exit criterion:** one authorized session produces complete, auditable 1m records with no synthetic substitution.

### M5 — Analytics on bars
- Add bar-based OHLC/volume/VWAP/OI-delta analytics alongside, not inside, the existing EOD services.
- Reuse PCR/max-pain/OI summary logic only where the input contract is compatible.
- Preserve PIT: bars must be visible only at/after their `available_at`.
- **Exit criterion:** analytics tests prove no look-ahead and no pro-rating across gaps.

### M6 — UI integration
- Add a timeframe control to the existing derivatives pages and reuse the current table/chart components.
- Clearly distinguish "1m bars" from "1m interval OI change" and from EOD analytics.
- Show coverage, source, and unavailable states explicitly.
- **Exit criterion:** browser acceptance verifies labels, provenance, and empty/partial states.

### M7 — Operations and certification
- Add coverage APIs, freshness/staleness checks, partition maintenance, backfill retry, and disk/DB retention.
- Add test-DB isolation for the new tables and fix the existing option-chain truncate omission if those tables are used in tests.
- Produce a coverage certificate with measured counts and date ranges.
- **Exit criterion:** measured coverage, reproducible operations, and passing isolation tests.

---

## 7. Risks and blockers

1. **No 1m schema exists.** Any implementation starts with a migration; there is no table to extend.
2. **No intraday backfill exists.** The historical provider is day-only in practice. Backfill cannot be assumed.
3. **Snapshot timestamps are local fetch times.** The live chain parser does not preserve an exchange timestamp, so minute alignment is approximate.
4. **Futures history is JSONL-only.** It is not queryable in SQL, not partitioned, and not covered by DB backup/retention.
5. **Capture is disabled by default and operator-started.** There is no scheduler; historical coverage depends on manual sessions.
6. **Coverage is UNKNOWN.** No DB access was authorized. The presence of raw EOD archives does not prove loaded rows.
7. **Partitioning is PostgreSQL-specific.** A 1m design must account for partition creation/retention and migrations.
8. **Test isolation gap.** `_TABLES_TO_TRUNCATE` omits `option_chain_snapshots` and `option_chain_quotes`, so tests touching them can leak state across cases.
9. **Do not conflate capabilities.** The existing "1 minute" UI interval is an OI/last-price interval measurement; presenting it as 1m OHLC would be inaccurate.

---

## 8. Safety and verification statement

- No database connection or query was made.
- No migration was run.
- No provider/API call was made.
- No capture, scheduler, broker, or test was started.
- No files inside the repository were created, edited, or deleted.
- No Git state was changed.
- No secrets or `.env` values were read or written.
- All deliverables are outside the repository, under the audit workspace.

Supporting evidence: `EVIDENCE_INDEX.md` and `REUSE_INVENTORY.md` in this workspace. Raw baseline: `raw/git_baseline.txt`.

---

## 9. Bottom line

The repository already has a credible **EOD derivatives platform** and a gated **intraday observation** platform. It does **not** have one-minute OHLC candles, intraday historical backfill, a futures time-series database, or measured 1m coverage. Building genuine 1m futures/options analytics requires a new bar schema, a real intraday source, an aggregation/backfill path, and measured coverage—not a relabeling of the existing snapshot/OI features.
