# Evidence Index — OPT-DATA-AUDIT

All paths are relative to the repository root:

`D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`

Line numbers refer to the current working tree (21 pre-existing tracked modifications). They are not guaranteed to match HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`.

**Baseline file:** `C:\Users\RK\AppData\Local\Temp\opencode\OPT_DATA_AUDIT_20261023_154401\raw\git_baseline.txt`

---

## A. Repository / audit baseline

| ID | Claim | Evidence | Type |
|---|---|---|---|
| A1 | Audit ran on branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173` | `raw/git_baseline.txt` | measured |
| A2 | Working tree at baseline: 0 staged, 21 modified tracked, 282 untracked status lines | `raw/git_baseline.txt` | measured |
| A3 | Alembic single head `e5f6a7b8c9d0`, 28 migration revisions | `raw/git_baseline.txt`; count of `migrations/versions/*.py` | measured |
| A4 | No DB, provider, capture, scheduler, test, or Git mutation was performed | Audit method; no commands of those types were run | procedural |

---

## B. Schema evidence

| ID | Claim | Evidence | Type |
|---|---|---|---|
| B1 | `DerivativeContract` is the canonical futures/options identity table | `app/models/derivative_contract.py:37`, `:40` | source-verified |
| B2 | Contract identity includes underlying stock/index, instrument type, trading symbol, provider id, expiry, strike, option type, lot/tick, lifecycle, idempotency hash | `app/models/derivative_contract.py:76-118` | source-verified |
| B3 | `DerivativeContract.available_at` is `date`-granular | `app/models/derivative_contract.py:113` | source-verified |
| B4 | Contract master migration creates the table and indexes | `migrations/versions/b71d9e2a3456_add_derivative_contract_master_tables.py:22-79` | source-verified |
| B5 | `DerivativeDailyPrice` is the only derivatives OHLC table | `app/models/derivative_daily_price.py:34`, `:37` | source-verified |
| B6 | `DerivativeDailyPrice` is daily: unique `(contract_id, trade_date)`, OHLC, settlement, volume/OI, date `available_at` | `app/models/derivative_daily_price.py:39`, `:62-93` | source-verified |
| B7 | Daily derivatives migration and indexes | `migrations/versions/c82e3f4a5678_add_derivative_daily_prices_table.py:24-66` | source-verified |
| B8 | `OptionChainSnapshot` is a timestamped cross-sectional chain header | `app/models/option_chain.py:33`, `:36` | source-verified |
| B9 | Snapshot has `market_timestamp`, `source_timestamp`, `ingestion_timestamp`, `available_at` | `app/models/option_chain.py:91-94` | source-verified |
| B10 | Snapshot has provenance, revision, quality/completeness, futures reference, Greek context | `app/models/option_chain.py:97-131` | source-verified |
| B11 | Snapshot uniqueness is partial-index based | `app/models/option_chain.py:56-78` | source-verified |
| B12 | `OptionChainQuote` is a per-snapshot quote observation, not OHLC | `app/models/option_chain.py:156`, `:159` | source-verified |
| B13 | Quote unique key is `(snapshot_id, contract_id, trade_date)` | `app/models/option_chain.py:161-166` | source-verified |
| B14 | Quote fields are LTP, bid/ask/size, volume, OI, OI changes, source/calculated Greeks; no open/high/low/close premium columns | `app/models/option_chain.py:210-242` | source-verified |
| B15 | Option-chain tables are PostgreSQL range-partitioned | `migrations/versions/b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py:220-236` | source-verified |
| B16 | Partition maintenance helper exists | `app/services/option_chain_partition_service.py:4`, `:74`, `:111` | source-verified |
| B17 | `MarketIndexPrice` is daily OHLC only; `observed_at`/`fetched_at` are dates | `app/models/market_index_price.py:15-45` | source-verified |
| B18 | `DailyPrice` is daily OHLC only | `app/models/daily_price.py:16-36` | source-verified |
| B19 | Participant/FII tables store EOD report dates and available dates | `app/models/derivative_participant.py:34-57`, `:80-102` | source-verified |
| B20 | Participant/FII migration | `migrations/versions/e91f4a5678ab_add_derivative_participant_tables.py:24-107` | source-verified |
| B21 | Market intelligence snapshots are daily | `app/models/market_intelligence_snapshot.py:12-22` | source-verified |
| B22 | Market index/intelligence migration | `migrations/versions/25e6f159a646_add_market_intelligence_foundation_.py:49-82`, `:114-160` | source-verified |
| B23 | No model/migration declares minute/intraday/candle/OHLC/tick storage | Search over `app/models` and `migrations/versions` for `minute|intraday|candle|ohlc|tick`; only unrelated cooldown/quality/config names matched | negative search |

---

## C. Capture and ingestion evidence

| ID | Claim | Evidence | Type |
|---|---|---|---|
| C1 | Capture default interval is 60 seconds; minimum is 10 seconds | `app/services/derivatives_capture_service.py:100`, `:116-117` | source-verified |
| C2 | Capture is bounded to 09:15–15:30 IST on weekdays unless explicitly overridden | `app/services/derivatives_capture_service.py:267-281` | source-verified |
| C3 | Capture is globally disabled by default | `app/core/config.py:52`; `app/services/derivatives_capture_service.py:71-81` | source-verified |
| C4 | Disabled capture start is refused before a worker is created | `app/services/derivatives_capture_service.py:241-246` | source-verified |
| C5 | API start requires `market_data.write` and returns 503 when disabled | `app/api/routes/options.py:601-631` | source-verified |
| C6 | Capture cycle resolves scope/spot, fetches a live chain, rejects non-real chains, and persists the chain | `app/services/derivatives_capture_service.py:466-523` | source-verified |
| C7 | Options are persisted to the snapshot tables; futures are appended to JSONL | `app/services/derivatives_capture_service.py:497-523`, `:525-590` | source-verified |
| C8 | Persistence refuses live observations while capture is disabled | `app/services/option_chain_persistence_service.py:777-786` | source-verified |
| C9 | Snapshot `available_at` defaults to ingestion time; `market_timestamp` comes from the normalized chain | `app/services/option_chain_persistence_service.py:973-987` | source-verified |
| C10 | Duplicate detection and revision allocation are based on logical snapshot key + canonical payload hash | `app/services/option_chain_persistence_service.py:837-906` | source-verified |
| C11 | Live chain parser does not preserve a provider exchange timestamp; it uses `datetime.now(UTC)`, and the adapter copies that local `as_of_timestamp` into both `source_timestamp` and `market_timestamp` | `app/providers/upstox_live_provider.py:745-786`, `:803-864`; `app/services/option_chain_adapter.py:165-170` | source-verified |
| C12 | Live chain parser preserves LTP, close, volume, OI, OI change, bid/ask, provider Greeks | `app/providers/upstox_live_provider.py:745-786`, `:803-844` | source-verified |
| C13 | Futures quote returns last price, previous close, day O/H/L, volume, OI, OI day high/low, buy/sell quantity, provider timestamp | `app/providers/upstox_live_provider.py:319-366` | source-verified |
| C14 | Futures observations are stored in `UNDERLYING_YYYY-MM-DD.jsonl`, one document per observation, append-only | `app/services/derivatives_observation_service.py:1045-1069` | source-verified |
| C15 | Futures observation fields include last price, previous close, day O/H/L, volume, OI, OI day high/low, spot, quality flags | `app/services/derivatives_observation_service.py:981-1006` | source-verified |
| C16 | Futures ledger reads the JSONL store and a live quote | `app/services/derivatives_futures_ledger.py:801-927`; `app/services/derivatives_observation_service.py:1304` | source-verified |
| C17 | No JSONL observation files were present in the repository; default `data/derivatives_observations` did not exist | Filesystem inventory (`Get-ChildItem -Recurse -Filter *.jsonl` returned none; `Test-Path` False) | measured local state |
| C18 | No derivative references were found in `app/scheduler/*.py` | Search of scheduler modules for `derivative|capture|option|futures` returned no matches | negative search |
| C19 | Upstox historical bars ignore the `interval` argument and hardcode the `day` endpoint, so intraday candles are never requested | `app/providers/upstox_historical_provider.py:96-102`, `:126` | source-verified |
| C20 | Upstox historical parser derives `trade_date` via `.date()` while retaining a full `timestamp`; the request remains daily | `app/providers/upstox_historical_provider.py:174`, `:187` | source-verified |
| C21 | NSE historical bars read the daily `DailyPrice` table only | `app/providers/nse_historical_provider.py:67-108` | source-verified |
| C22 | Tick WebSocket stream exists | `app/providers/upstox_live_provider.py:513-620` | source-verified |
| C23 | Tick WebSocket stream is only used by the CLI demo, not capture/analytics/persistence | `app/cli/upstox_cli.py:89`; no other consumer found | source-verified / negative search |
| C24 | Source registry granularity field allows `INTRADAY`, but all 10 registered datasets are EOD | `app/data/derivative_source_registry.py:44`, `:58`, `:69`, `:80`, `:91`, `:102`, `:113`, `:124`, `:135`, `:146`, `:152` | source-verified |
| C25 | EOD futures import fields map bhavcopy values into `DerivativeDailyPrice` | `app/services/derivative_futures_import.py:56`, `:182-200` | source-verified |
| C26 | EOD options import fields map bhavcopy values into `DerivativeDailyPrice` | `app/services/derivative_options_import.py:63`, `:219-237` | source-verified |
| C27 | EOD contract master import exists | `app/services/derivative_import.py:49-136`; CLI `app/cli/import_nse.py:204-228` | source-verified |
| C28 | Contract sync is separately gated and disabled by default | `app/services/derivative_contract_sync_service.py:11`, `:143-160`; CLI `app/cli/import_nse.py:608-687` | source-verified |
| C29 | Raw F&O bhavcopy archive inventory: 255 files, 234 unique dates, 2025-10-01 to 2026-09-10, no >4-day gap | Filename inventory of `data/reference/derivatives/raw` (234) + `data/raw/nse/2026/06_udiff` (21) | measured local state |
| C30 | Archive presence is not proof of imported DB rows or coverage | No DB access authorized | scope limitation |

---

## D. Analytics and UI evidence

| ID | Claim | Evidence | Type |
|---|---|---|---|
| D1 | `IntradayOptionsService.get_scope` exists | `app/services/intraday_options_service.py:827` | source-verified |
| D2 | `get_available_expiries` exists | `app/services/intraday_options_service.py:934` | source-verified |
| D3 | `get_option_chain_ladder` fetches a live normalized chain | `app/services/intraday_options_service.py:1043-1074`; boundary `:783-803` | source-verified |
| D4 | `get_option_dashboard` exists | `app/services/intraday_options_service.py:1312` | source-verified |
| D5 | `get_live_pcr` exists | `app/services/intraday_options_service.py:1385` | source-verified |
| D6 | `get_call_vs_put_oi` exists | `app/services/intraday_options_service.py:1434` | source-verified |
| D7 | `get_oi_change_analysis` exists | `app/services/intraday_options_service.py:1508` | source-verified |
| D8 | `get_oi_statistics` exists | `app/services/intraday_options_service.py:1609` | source-verified |
| D9 | `get_interval_oi_change` exists and refuses pro-rata substitution | `app/services/intraday_options_service.py:1758-1841` | source-verified |
| D10 | `get_multistrike_oi` exists | `app/services/intraday_options_service.py:2054` | source-verified |
| D11 | `get_options_oi_vs_time` builds a session-bound series from stored observations | `app/services/intraday_options_service.py:2154-2228` | source-verified |
| D12 | `get_futures_oi_vs_time` delegates to `build_futures_workbench` | `app/services/intraday_options_service.py:2286-2338` | source-verified |
| D13 | `get_session_replay_timeline` and `get_replay_ladder` exist | `app/services/intraday_options_service.py:2340`, `:2441` | source-verified |
| D14 | Snapshot reads are point-in-time visible via `available_at <= evaluation_at` | `app/services/derivatives_observation_service.py:775-810` | source-verified |
| D15 | Snapshot reads can be bounded to one IST session | `app/services/derivatives_observation_service.py:807-810` | source-verified |
| D16 | `_load_real_series` reads `OptionObservationRepository` and returns explicit unavailable reasons | `app/services/intraday_options_service.py:1987-2052` | source-verified |
| D17 | Historical option chain is explicitly EOD and reads daily tables | `app/services/historical_option_chain.py:1-19`, `:134-150` | source-verified |
| D18 | Historical chain expiry/date discovery is daily-table based | `app/services/historical_option_chain.py:337-349`, `:371-385` | source-verified |
| D19 | Historical chain spot fallback uses daily index/stock prices | `app/services/historical_option_chain.py:446-490` | source-verified |
| D20 | EOD analytics consumers are wired | `app/services/advanced_options_service.py:55`; `advanced_exposure_service.py:85`; `historical_oi_intelligence_service.py:47`; `historical_options_analytics.py:54`; `options_opportunity_service.py:65`; `historical_derivatives_replay.py:42-59` | source-verified |
| D21 | Derivatives workspace pages are production routes | `app/api/routes/market_data_pages.py:209-210`, `:228-230`, `:318-319`, `:377-378`, `:444-445`, `:506-507`, `:568-569`, `:636-637` | source-verified |
| D22 | Derivatives JSON API is production-wired | `app/api/routes/options.py:144-579` | source-verified |
| D23 | Capture control API is production-wired | `app/api/routes/options.py:593-705` | source-verified |
| D24 | UI filter offers 1–60 minute intervals | `app/templates/components/derivatives_filter_panel.html:26`, `:111` | source-verified |
| D25 | UI renders ladder and PCR from chain data | `app/templates/components/intraday_option_chain_table.html:20`, `:40`; `intraday_option_pcr_view.html:16-17` | source-verified |
| D26 | UI renders OI change/comparison/replay/futures views | `intraday_option_oi_change_view.html:14-16`; `intraday_option_oi_comparison.html:12`; `derivatives_replay_cards.html:102-103`; `futures_analytics_table.html:23` | source-verified |
| D27 | Derivatives navigation exposes the workbench tabs | `app/templates/components/derivatives_nav_tabs.html:5`, `:17`, `:24`, `:35` | source-verified |
| D28 | Existing "1 minute" analytics are OI/last-price interval measurements, not OHLC candles | `app/services/intraday_options_service.py:1758-1841`, `:2154-2228`; schema B14 | source-verified |

---

## E. Risk / isolation evidence

| ID | Claim | Evidence | Type |
|---|---|---|---|
| E1 | Test truncate list includes derivative daily/participant tables but omits `option_chain_snapshots` and `option_chain_quotes` | `tests/conftest.py:115-133` | source-verified |
| E2 | Truncate execution uses that list | `tests/conftest.py:197-201` | source-verified |
| E3 | `option_chain_quotes` is PostgreSQL range-partitioned (with PostgreSQL partial unique indexes on the header), so a 1m design must handle partitions | migration `b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py:220-236`; `app/models/option_chain.py:56-78` | source-verified |
| E4 | Capture is operator-started, not scheduler-driven | `app/api/routes/options.py:601-661`; no derivative scheduler references (C18) | source-verified / negative search |
| E5 | Volume/date coverage of loaded DB rows cannot be established in this audit | DB access prohibited | scope limitation |

---

## F. Commands used for measured local facts

These were non-mutating and did not touch the database or network:

1. Git baseline capture: `git rev-parse`, `git status --short`, `git diff --stat`, `alembic heads` (stored in `raw/git_baseline.txt`).
2. Migration count: `(Get-ChildItem 'migrations/versions' -Filter '*.py').Count` -> 28.
3. JSONL inventory: `Get-ChildItem -Path . -Recurse -Filter '*.jsonl'` -> no files.
4. Observation directory existence: `Test-Path 'data/derivatives_observations'` -> False.
5. Raw F&O archive inventory: filename regex over `data` for `BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip` -> 255 files, 234 unique dates, 2025-10-01..2026-09-10, no >4-day gap.
6. Source/value-machine timestamp search: `Select-String` over `app/**/*.py` for `source_timestamp`, `market_timestamp`, `available_at`, `interval_change_in_oi`, `subscribe_ticks`.
7. Negative schema search: `Select-String` over `app/models` and `migrations/versions` for minute/intraday/candle/OHLC/tick naming; no relevant storage match.

---

## G. Evidence limitations

- Source inspection proves **code intent and wiring**, not execution. No test or runtime call was made.
- Working-tree line numbers may differ from HEAD because 21 tracked files are modified.
- Local filesystem state may differ from the deployment environment.
- DB row counts, date coverage, null rates, duplicate rates, partition availability, and actual capture history remain **UNKNOWN** until read-only DB authorization is granted.
