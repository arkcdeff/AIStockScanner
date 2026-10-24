# Reuse Inventory — OPT-DATA-AUDIT

Scope: identify which existing futures/options components can be reused for a future **one-minute OHLC** pipeline, which need adaptation, and which must be newly built.

Legend:

- **DIRECT** — reuse as-is with no semantic change.
- **ADAPT** — reuse the component/pattern, but change its data contract or add a bar-specific path.
- **DO NOT REUSE** — daily/EOD or snapshot semantics make it unsuitable for 1m OHLC.
- **MISSING** — no component exists.

---

## 1. Schema and persistence

| Component | Path | Verdict | Reason / adaptation |
|---|---|---|---|
| Contract identity | `app/models/derivative_contract.py:37-118` | **DIRECT** | Canonical `contract_id`, expiry, strike, option type, lot size, PIT `available_at`. Use as the dimension for 1m bars. |
| Contract master migration | `migrations/versions/b71d9e2a3456_add_derivative_contract_master_tables.py:22-79` | **DIRECT** | Existing base for foreign keys. |
| EOD derivative prices | `app/models/derivative_daily_price.py:34-93` | **DO NOT REUSE** | `trade_date: date` and unique `(contract_id, trade_date)` cannot represent intraday bars. Keep for EOD only. |
| EOD migration | `migrations/versions/c82e3f4a5678_add_derivative_daily_prices_table.py:24-66` | **DO NOT REUSE** | Same date-granular semantics. |
| Option-chain snapshot header | `app/models/option_chain.py:33-147` | **ADAPT** | Excellent pattern for snapshot provenance/PIT/quality, but its row is a cross-sectional chain observation, not a per-contract bar. Do not overload it for 1m candles. |
| Option-chain quote observation | `app/models/option_chain.py:156-255` | **ADAPT** | Reuse quote fields/provenance ideas. Add a separate bar table with open/high/low/close/volume/OI; current table has LTP/bid/ask only. |
| Option-chain migration/partitioning | `migrations/versions/b3a4c5d6e7f8_add_option_chain_snapshots_and_quotes.py:220-236`; `app/services/option_chain_partition_service.py:4-111` | **ADAPT** | Reuse the PostgreSQL partition + advisory-lock strategy for a bar table, with a bar-appropriate partition key (e.g. trade date/bar start). |
| Underlying daily prices | `app/models/market_index_price.py:15-45`; `app/models/daily_price.py:16-36` | **DO NOT REUSE** for 1m | Daily only. Keep for daily context/spot fallback. |
| Participant/FII EOD | `app/models/derivative_participant.py:34-102` | **DO NOT REUSE** for 1m | EOD report data, not market ticks. |
| 1m bar table | — | **MISSING** | No model/migration exists. Must be designed and added. |
| Tick capture table | — | **MISSING** | No tick persistence. The WebSocket stream is not stored. |

**Recommended new schema shape (for design discussion, not implemented):**

- `derivative_intraday_bars`
  - `contract_id` FK to `derivative_contracts`
  - `interval` (e.g. `1m`)
  - `bar_start` (`timestamptz`, UTC)
  - `trade_date` (IST-derived `date`, partition key)
  - `open`, `high`, `low`, `close`, `volume`, `open_interest`
  - `source_name`, `source_reference`, `ingestion_timestamp`, `available_at`
  - `quality_status`, `quality_flags`
  - unique `(contract_id, interval, bar_start)`
- Optional `underlying_intraday_bars` for index spot, reusing the same shape.

---

## 2. Ingestion and capture

| Component | Path | Verdict | Reason / adaptation |
|---|---|---|---|
| Live option-chain capture loop | `app/services/derivatives_capture_service.py:163-600` | **ADAPT** | Reuse gating, IST session bounds, rate limit, dedupe, storage caps, and operator controls. Add a 1m aggregation/writer step; do not treat snapshots as OHLC bars. |
| Capture config | `app/services/derivatives_capture_service.py:94-120` | **DIRECT** | 10s minimum / 60s default is compatible with 1m sampling. |
| Capture API | `app/api/routes/options.py:593-705` | **DIRECT** | Existing authenticated operator surface. |
| Capture gate | `app/core/config.py:52`; `derivatives_capture_service.py:71-81`, `:241-246` | **DIRECT** | Preserve fail-closed behavior for any new 1m writer. |
| Snapshot persistence | `app/services/option_chain_persistence_service.py:695-1738` | **ADAPT** | Reuse revision/dedupe/provenance/PIT patterns. Its `market_timestamp` is local fetch time because the parser does not preserve an exchange timestamp and the adapter copies the local `as_of_timestamp` (`app/services/option_chain_adapter.py:165-170`). For 1m bars, require an explicit exchange/source timestamp. |
| Futures JSONL store | `app/services/derivatives_observation_service.py:1045-1300` | **ADAPT** | Good for real point observations and auditability, but not SQL-queryable, not partitioned, not backed up with the DB. Consider dual-write to a bar table or a migration path. |
| Futures observation model | `app/services/derivatives_observation_service.py:981-1006` | **ADAPT** | Fields include day O/H/L, not interval O/H/L. Only `last_price`/OI/volume are true point samples. |
| Futures ledger | `app/services/derivatives_futures_ledger.py:801-927` | **ADAPT** | Reuse interval classification/positioning/basis logic once bars exist; currently consumes JSONL observations + live quote. |
| Upstox historical bars | `app/providers/upstox_historical_provider.py:96-137` | **ADAPT / currently unusable for 1m** | Signature accepts `interval`, but request hardcodes `day` (`:126`) and parser truncates to date (`:174`). Must be extended and verified against provider entitlement. |
| NSE historical provider | `app/providers/nse_historical_provider.py:67-108` | **DO NOT REUSE** for 1m | Reads daily `DailyPrice` only. |
| Upstox live chain | `app/providers/upstox_live_provider.py:432-511`, `:725-864` | **ADAPT** | Reuse normalization and quote fields. Add exchange/source timestamp capture and do not claim OHLC from LTP. |
| Upstox futures quote | `app/providers/upstox_live_provider.py:319-366` | **DIRECT / ADAPT** | Good point observation source; add timestamp normalization and aggregation for 1m bars. |
| Upstox tick stream | `app/providers/upstox_live_provider.py:513-620` | **ADAPT** | Best source for true intra-minute high/low if wired. Currently only CLI uses it (`app/cli/upstox_cli.py:89`). Needs reconnect/backpressure/persistence and a bar aggregator. |
| EOD bhavcopy futures import | `app/services/derivative_futures_import.py:56-200` | **DIRECT for EOD** | Keep for daily context; cannot create 1m bars. |
| EOD bhavcopy options import | `app/services/derivative_options_import.py:63-237` | **DIRECT for EOD** | Same. |
| Contract master sync | `app/services/derivative_contract_sync_service.py:534-760` | **DIRECT** | Reuse provider contract identity mapping; separately gated (`:143-160`). |
| Scheduler | `app/scheduler/eod_scheduler.py` | **ADAPT** | No derivative capture job exists. Add an explicitly gated intraday job only if operations approves; do not silently enable capture. |

---

## 3. Analytics

| Component | Path | Verdict | Reason / adaptation |
|---|---|---|---|
| `IntradayOptionsService` live ladder/scope/expiries | `app/services/intraday_options_service.py:827-1074` | **DIRECT** | Live chain UI and contract selection remain valid. |
| PCR / call-vs-put / OI statistics / max pain | `app/services/intraday_options_service.py:1385-1756` | **ADAPT** | Reuse formulas and presentation; feed them from bars when bar-based PCR is required. Current inputs are live chain/snapshot OI. |
| Interval OI change engine | `app/services/intraday_options_service.py:1758-1985`; `app/services/derivatives_observation_service.py:426-657` | **ADAPT** | Strong real-observation/tolerance model. Extend to bars only if bars carry OI at each minute; do not pro-rate. |
| Options OI vs time | `app/services/intraday_options_service.py:2154-2285` | **ADAPT** | Snapshot-based trajectory. A bar-based version should return OHLC/volume in addition to OI. |
| Futures OI vs time / ledger | `app/services/intraday_options_service.py:2286-2338`; `app/services/derivatives_futures_ledger.py:111-801` | **ADAPT** | Reuse interval classification and session positioning; replace JSONL point reads with bar reads when available. |
| Session replay | `app/services/intraday_options_service.py:2340-2540` | **ADAPT** | Reuse session-bound replay and PIT patterns; add bar timeline. |
| Historical EOD option chain | `app/services/historical_option_chain.py:108-490` | **DO NOT REUSE** for 1m | Explicitly EOD and daily-table based. Keep for EOD research. |
| Advanced EOD analytics | `advanced_options_service.py:55`; `advanced_exposure_service.py:85`; `historical_oi_intelligence_service.py:47`; `historical_options_analytics.py:54`; `options_opportunity_service.py:65` | **DO NOT REUSE** for 1m | Built on EOD chain/daily prices. Do not feed 1m bars into EOD semantics without a separate design. |
| CSV export | `app/services/derivatives_export.py:88-187` | **ADAPT** | Reuse provenance header and allow-list approach; add bar export columns/types. |

---

## 4. UI / API

| Component | Path | Verdict | Reason / adaptation |
|---|---|---|---|
| Derivatives pages/routes | `app/api/routes/market_data_pages.py:209-637` | **DIRECT** | Existing navigation and page structure. |
| Options JSON API | `app/api/routes/options.py:144-579` | **ADAPT** | Add timeframe/bar endpoints; keep existing snapshot endpoints for compatibility. |
| Capture API | `app/api/routes/options.py:593-705` | **DIRECT** | Reuse operator controls; add bar-specific status/coverage fields. |
| Filter panel | `app/templates/components/derivatives_filter_panel.html:26-111` | **DIRECT** | Already exposes 1–60 minute intervals; add a clear "bar vs OI interval" label. |
| Ladder table | `app/templates/components/intraday_option_chain_table.html:1-40` | **DIRECT** | Live ladder remains; optionally add per-contract 1m mini-chart. |
| PCR/OI change/comparison/replay/futures templates | `intraday_option_pcr_view.html:16-17`; `intraday_option_oi_change_view.html:14-16`; `intraday_option_oi_comparison.html:12`; `derivatives_replay_cards.html:102-103`; `futures_analytics_table.html:23` | **ADAPT** | Reuse layout/components; add explicit "1m bar" provenance labels and coverage states. |
| Nav tabs | `app/templates/components/derivatives_nav_tabs.html:5-35` | **DIRECT** | Add a 1m view only if backed by real bar data. |

---

## 5. Operations, testing, and data quality

| Component | Path | Verdict | Reason / adaptation |
|---|---|---|---|
| Capture status/telemetry | `app/services/derivatives_capture_service.py:130-222` | **ADAPT** | Extend with bar coverage/freshness; current metrics are observation-centric. |
| Storage cap/cleanup | `derivatives_capture_service.py:192-222`, `:353-364` | **ADAPT** | JSONL cleanup exists; DB bar retention/partition maintenance is missing. |
| PIT/quality conventions | `option_chain_persistence_service.py:275-321`, `:936-1076`; `option_chain.py:107-117` | **DIRECT** | Reuse quality status/flags, source hashes, normalization version, `available_at`. |
| Test isolation | `tests/conftest.py:115-133`, `:197-201` | **ADAPT / FIX REQUIRED** | `option_chain_snapshots`/`option_chain_quotes` are missing from the truncate list. Add new bar tables and the missing option-chain tables when tests use them. |
| Coverage certification | No measured DB coverage available | **MISSING** | Needs authorized read-only count/min/max/date coverage queries. |
| Raw archive | `data/reference/derivatives/raw` (234 files); `data/raw/nse/2026/06_udiff` (21 files) | **DIRECT for EOD backfill source** | 255 files / 234 unique dates / 2025-10-01..2026-09-10, no >4-day gap. Archive presence only; import status UNKNOWN. |

---

## 6. Reuse summary

**Directly reusable foundation:** contract identity; capture gating and operator controls; snapshot provenance/PIT patterns; PCR/OI summary logic; page/API/template structure; EOD imports and EOD chain reconstruction for non-1m features.

**Requires new engineering:** 1m bar schema and migration; exchange-timestamp-aware bar construction; tick-to-bar aggregation or provider 1m candles; futures bar persistence; backfill/reconciliation; partition/retention; bar coverage APIs; test isolation for new and existing option-chain tables.

**Must not be relabeled:** repeated option-chain snapshots are not 1m OHLC candles; daily `DerivativeDailyPrice`/`MarketIndexPrice` rows are not intraday bars; the existing "1 minute" UI interval is an OI/last-price interval measurement.
