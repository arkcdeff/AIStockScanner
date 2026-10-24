# OPT Data Reconciliation — M1

**Date:** 2026-10-23  
**Milestone:** M1 — Environment and Import Reconciliation  
**Mode:** Read-only investigation; no fixes, imports, migrations, provider calls, capture, scheduler, broker calls, or tests.  
**Deliverable:** This report only.

---

## 1. Executive verdict

1. **No currently accessible environment contains the intended derivatives dataset.** The local development database `ai_stock_scanner` has **0** `derivative_daily_prices` and **0** futures contracts. Its 2,781 option contracts were created on 2026-09-25 and 2026-10-07; no derivative contract row predates 2026-09-25.
2. **The intended dataset did exist locally.** The 2026-09-03 browser inspection recorded, on `127.0.0.1:8001`, **75,962 contract masters**, **28,508 futures EOD rows**, **1,703,830 option EOD rows**, and **576 participant OI rows**. The 2026-09-07 handoff certified **75,962** contracts and **1,732,338** daily prices.
3. **The 25-date claim is a subset, not the whole dataset.** The 25 `ALREADY_IMPORTED` derivatives dates are `2026-07-06` through `2026-08-10`. They are exactly the **last 25 sessions** of a 45-session window from `2026-06-08` through `2026-08-10`. Raw FO bhavcopy rows for those 45 sessions total **1,731,456**, which is only **882 rows (0.05%)** below the claimed **1,732,338**. The 25 dates alone contain only **887,557** raw rows.
4. **The data was lost or removed from the local dev database between 2026-09-07 and 2026-09-26.** The 2026-09-26 G.11-OPT-3 discovery recorded only **4** derivative contracts and **0** daily prices in `ai_stock_scanner`. No reset command, log, or backup was found in the repository.
5. **The archive can reconstruct the EOD dataset, but not 1m OHLC.** All 25 manifest dates are present in the FO archive, along with the earlier 20 sessions. The archive is NSE FO bhavcopy/EOD data. It cannot supply 1m candles.
6. **No authoritative 1m OHLC source is currently established.** There is no 1m bar schema. The Upstox historical provider accepts an `interval` argument but hardcodes the `day` endpoint and truncates timestamps to dates. Live quotes and option snapshots are point observations, not OHLC bars. Provider 1m entitlement and exchange timestamps remain unverified.

---

## 2. Evidence boundary

- All database access was to the configured local development database only, over `::1:5432`, PostgreSQL 18.4, with `default_transaction_read_only=on` and `pg_is_in_recovery=false`.
- No other database's contents were queried. `pg_database` was read as server metadata to inventory local candidates.
- No production or remote environment was contacted.
- No files were imported, written, migrated, deleted, or truncated.
- No provider API was called.
- No pytest or other test suite was run.
- No Git mutation was performed.
- Secrets and DSNs were not printed.

---

## 3. Environment inventory

| Candidate | Evidence | Status / access | Assessment |
|---|---|---|---|
| Local dev `ai_stock_scanner` | `.env`, `run_aistockscanner.bat`; `::1:5432`; database size 40 MB | Inspected read-only | Current configured DB. Contains 0 daily derivative prices and 0 futures. Not the intended dataset. |
| Local `test` | `tests/conftest.py`; database size 34 MB | Not connected | Too small to hold 1.7M daily rows; test-only. |
| Local `college_cert_sys_staging` | Server catalog only; 20 MB | Not connected | Unrelated project database; no evidence of AIStockScanner derivatives data. |
| Local `hubdb`, `postgres`, templates | Server catalog only; 7.8–8.1 MB each | Not connected | No evidence of application derivatives data. |
| Docker Compose `ai_stock_scanner` | `docker-compose.yml`; port 5432; local-development stack | Docker not installed | Not operational on this host. |
| Production `aistock` | `docs/07_Deployment/Production.md`; PostgreSQL 18; remote deployment shape | Not authorized / not contacted | A production candidate exists by name only. Local catalog has no `aistock` database. Contents unknown. |
| Repository backups | Bounded search for `*.dump`, `*.sql`, `*.backup`, `*.bak`, `*.tar` | None found | No recovery artifact found in the repository. |
| Prior local dev state | 2026-09-03 browser report; 2026-09-07 handoff | Historical | This is the only environment with direct evidence of the intended 75,962 / 1,732,338 dataset. |

The local cluster runs one PostgreSQL service, `postgresql-x64-18`. The postmaster started at `2026-10-22 10:47:13 +05:30`; this is not evidence of when the database state changed.

---

## 4. Current configured database: measured state

| Metric | Current value |
|---|---:|
| Database revision | `d4e5f6a7b8c1` |
| Repository Alembic head | `e5f6a7b8c9d0` |
| `derivative_contracts` | 2,781 |
| — options | 2,781 |
| — futures | 0 |
| `derivative_daily_prices` | 0 |
| `derivative_participant_positions` | 0 |
| `fii_derivative_statistics` | 0 |
| `market_index_prices` | 0 |
| `option_chain_snapshots` | 2 (`NSE_TEST`, 2026-09-24) |
| `option_chain_quotes` | 8 |
| `stocks` | 3,574 |
| `daily_prices` | 102,064 |
| `daily_prices` date range | 2026-09-01 through 2026-10-22 |
| `daily_prices` distinct dates | 35 |
| `import_jobs` | 31, all NSE cash-market; none derivative-like |
| Database size | 40 MB |

Derivative contract creation dates:

| Instrument | Created date | Count |
|---|---|---:|
| OPTION | 2026-09-25 | 4 |
| OPTION | 2026-10-07 | 2,777 |

No derivative contract row predates 2026-09-25. The two `option_chain_snapshots` were created on 2026-09-25 and carry `source_name='NSE_TEST'`; they are test fixtures, not provider observations.

The 2026-09-03 browser report recorded 3,639 stocks and 1,006,907 daily price rows; the current DB has 3,574 and 102,064. `daily_prices` alone is 22 MB for 102k rows. A 1.7M-row derivative table plus indexes cannot fit in the current 40 MB database; the largest local database is 40 MB.

The current DB is therefore a later, reduced state. It is not the database state described in the 2026-09-03/07 reports.

---

## 5. Reconciliation of the 25-date claim

Source: `data/audit/session_manifest_20250808_20260810.json`.

| Item | Value |
|---|---|
| Window | 2025-08-08 through 2026-08-10 |
| Sessions | 250 |
| Derivatives `ALREADY_IMPORTED` | 25 |
| Derivatives `OFFICIAL_REMOTE_NOT_FOUND` | 225 |
| First imported derivative date | 2026-07-06 |
| Last imported derivative date | 2026-08-10 |

The 25 dates are:

`2026-07-06`, `2026-07-07`, `2026-07-08`, `2026-07-09`, `2026-07-10`, `2026-07-13`, `2026-07-14`, `2026-07-15`, `2026-07-16`, `2026-07-17`, `2026-07-20`, `2026-07-21`, `2026-07-22`, `2026-07-23`, `2026-07-24`, `2026-07-27`, `2026-07-28`, `2026-07-29`, `2026-07-30`, `2026-07-31`, `2026-08-03`, `2026-08-04`, `2026-08-05`, `2026-08-06`, `2026-08-10`.

Findings:

- The manifest is a static snapshot committed on 2026-08-22. It records statuses, not the database identity, connection, query, or import job IDs.
- The manifest does not define `ALREADY_IMPORTED`; it may mean local source availability rather than confirmed DB rows. The current DB has no derivative-like import jobs to corroborate it.
- It does **not** prove current coverage. The current DB has 0 derivative daily rows.
- All 25 dates are present in `data/reference/derivatives/raw`.
- The 25 dates are exactly the last 25 sessions of the 45-session window `2026-06-08` through `2026-08-10`.
- Raw FO bhavcopy rows for the 25 dates total **887,557**. That is far below the claimed **1,732,338**, so the 25-date claim cannot account for the full historical count by itself.

---

## 6. Reconciliation of 75,962 contracts and 1,732,338 daily prices

### 6.1 Claimed composition

From the 2026-09-03 browser report and 2026-09-07 handoff. Neither document records the database name; the local application configuration targets `ai_stock_scanner`.

| Metric | Claimed |
|---|---:|
| Contract masters | 75,962 |
| Futures EOD rows | 28,508 |
| Option EOD rows | 1,703,830 |
| Total derivative daily rows | 1,732,338 |
| Participant OI rows | 576 |

`28,508 + 1,703,830 = 1,732,338`, so the browser report and handoff describe the same dataset composition.

### 6.2 Archive reconciliation

Using the FO UDiff CSVs in `data/reference/derivatives/raw`, classifying `STO`/`IDO` as options and `STF`/`IDF` as futures:

| Window | Sessions | Raw rows | Raw options | Raw futures |
|---|---:|---:|---:|---:|
| 2026-07-06 to 2026-08-10 (manifest dates) | 25 | 887,557 | — | — |
| 2026-06-08 to 2026-08-10 | 45 | 1,731,456 | 1,702,619 | 28,837 |
| Claimed database | — | 1,732,338 | 1,703,830 | 28,508 |
| Delta (DB minus 45-session raw) | — | +882 | +1,211 | −329 |

The 45-session raw total is within **882 rows (0.05%)** of the claimed total. The option and futures splits are also within approximately 0.1%. The 20 earlier sessions (`2026-06-08` through `2026-07-03`) contain 843,899 raw rows; the 25 manifest dates contain 887,557. The claimed 28,508 futures rows average 633 per session, closely matching the raw 28,837 / 45 = 641. The residual is not explained by the current evidence; likely causes include import filtering, contract-master mapping, duplicate suppression, or a slightly different session boundary.

The 25 manifest dates are the last 25 of these 45 sessions. Therefore:

- The 25-date claim is **incomplete, not false**.
- The 1.7M dataset most likely covers approximately **45 sessions**, not 25.
- The 25 dates alone sum to 887,557 raw rows and cannot explain 1.7M rows.

Contract masters are not an exact raw-ID count: the 45 sessions contain 71,709 distinct `FinInstrmId` values, while the claimed master count is 75,962. The master count can include listed contracts not traded in every session; this is not a contradiction by itself.

### 6.3 Data-loss bracket

| Date | Evidence | Derivative state |
|---|---|---|
| 2026-09-03 | `BROWSER_INSPECTION_REPORT.md` | 75,962 masters; 28,508 futures; 1,703,830 options; 576 participant OI |
| 2026-09-07 | `docs/AI_HANDOFF.md`, `docs/05_Development/PROJECT_STATE.md` | 75,962 contracts; 1,732,338 daily prices |
| 2026-09-26 | `G11OPT3_DISCOVERY_REPORT.md` | 4 contracts; 0 daily prices; 0 futures |
| 2026-09-29 | `docs/G11OPT9_DISCOVERY_REPORT.md` | 2 `NSE_TEST` snapshots; no persisted options history |
| 2026-10-23 | Current M1 probe | 2,781 option contracts; 0 futures; 0 daily prices; 2 test snapshots |

The loss occurred between **2026-09-07 and 2026-09-26**. The current DB contains only post-2026-09-25 derivative rows. No reset command, database log, or recovery artifact was found in the repository.

Possible causes not proven:

- database recreation or replacement;
- a destructive maintenance step outside the repository;
- a restore from an older or partial backup;
- a test/QA process that bypassed the current test-DB guard.

The repository's current safety controls (`tests/conftest.py`, `_seed_opt8_qa_db.py`, `qa_opt10_browser_audit.py`) explicitly refuse non-test database names. No evidence was found that these paths truncated the dev DB. `scripts/g8_pre_repair.py` deletes duplicate contracts after remapping their prices; it does not contain a full-table truncate and does not explain a complete 1.7M-row loss.

---

## 7. Archive and recoverability

| Item | Value |
|---|---|
| FO ZIPs scanned | 255 |
| FO ZIP bytes | 318,843,946 |
| Unique FO dates | 234 |
| FO date range | 2025-10-01 through 2026-09-10 |
| Primary archive root | `data/reference/derivatives/raw` (234 FO dates) |
| Duplicate/June root | `data/raw/nse/2026/06_udiff` (21 FO duplicates + 21 non-derivative CM ZIPs) |
| Archive format | Valid single-CSV FO UDiff ZIPs |
| Largest calendar gap | No gap greater than 4 calendar days |
| Imported into current DB | 0 of 234 dates |
| 2026 manifest | 172 valid sessions; 7,105,914 total rows |
| Q4-2025 manifest | 62 sessions |

Recoverability conclusions:

- The 45-session EOD dataset is reconstructible from the on-disk FO bhavcopy archive, subject to explicit import authorization.
- The archive is EOD only. It does not contain 1m OHLC.
- Q4-2025 FO files exist on disk, while `AI_HANDOFF.md` and `CURRENT_TASK.md` say Q4-2025 F&O was “NOT ACQUIRED” / forbidden. This is a provenance contradiction that must be resolved before any import decision.
- Archive presence is not coverage proof. The current DB has 0 archived derivatives dates loaded.

---

## 8. Authoritative source assessment for future 1m OHLC

| Candidate | Evidence | Verdict |
|---|---|---|
| NSE FO bhavcopy archive | 234 EOD dates, no intraday columns | **Not 1m.** Can supply EOD only. |
| `DerivativeDailyPrice` | `trade_date` is a date; unique `(contract_id, trade_date)` | **Not 1m.** EOD model. |
| `OptionChainSnapshot` / `OptionChainQuote` | Point-in-time chain observations; LTP/bid/ask/OI; no OHLC columns | **Not OHLC bars.** Cannot be relabeled. |
| Futures JSONL observations | Point samples; default directory absent; 0 repo files; capture disabled | **Not 1m OHLC.** No active dataset. |
| Upstox live quotes | Day OHLC and OI; timestamped point observations | **Not 1m bars.** Could feed an authorized aggregation loop, but no such loop exists. |
| Upstox historical candles | `app/providers/upstox_historical_provider.py:96-137` accepts `interval`, but request hardcodes `/day/` (`:126`); parser truncates to `.date()` (`:174`) | **Only plausible provider path, currently day-only.** 1m entitlement and exchange timestamps not verified. |
| NSE historical provider | Reads local daily `DailyPrice` | **Not 1m.** |
| Other providers | No configured 1m historical path found | **No candidate established.** |

Assessment:

- There is **no authoritative, verified 1m OHLC source** in the current system.
- The only plausible future path is the Upstox historical-candle endpoint with an intraday interval, if the account is entitled and the exchange timestamp is preserved. The current code cannot request it and would discard the time-of-day.
- A live-quote aggregation path would require an explicitly authorized capture loop and a new bar schema; it does not exist today.
- The existing “1 minute” UI analytics are interval OI/last-price measurements over snapshots, not 1m OHLC bars.

---

## 9. Conclusions

1. The intended derivatives dataset was a local development dataset, evidenced on 2026-09-03/07. It is not present in the current local database.
2. The 25 `ALREADY_IMPORTED` dates are the final 25 sessions of a 45-session dataset. The 1.7M count matches the 45-session archive within 882 rows; the 25-date subset alone explains only 887,557 rows.
3. The current `ai_stock_scanner` DB is a later, reduced state: 2,781 option contracts, 0 futures, 0 derivative daily prices, 2 test snapshots, 40 MB total. No derivative row predates 2026-09-25.
4. No accessible local environment currently contains the intended dataset. The local cluster has no database with a plausible footprint for 1.7M derivative rows. No repository backup or dump was found.
5. The production `aistock` environment is documented but remote and unverified. It was not contacted. Its contents remain UNKNOWN.
6. The archive is the recoverable EOD source for the 45-session window, not a 1m source. Any re-import must be separately authorized and reconciled, and the Q4-2025 provenance conflict must be resolved first.
7. No 1m OHLC source is authoritative today. Upstox historical intraday candles are the leading candidate only if entitlement, interval support, and exchange timestamps are verified; that is an M2 prerequisite, not an M1 implementation.

---

## 10. Unknowns and work not performed

- Whether the remote production `aistock` database contains the 75,962 / 1,732,338 dataset.
- The exact cause and timestamp of the local derivative-data loss.
- Whether any external backup exists outside the repository.
- Whether the 882-row residual reflects filtering, date boundaries, or a different import source.
- Whether Upstox 1m historical candles are entitled for the configured account.
- Whether the Q4-2025 FO files were acquired under a later authorization despite the handoff restriction.
- No imports, migrations, schema changes, provider calls, capture starts, scheduler changes, broker calls, tests, or Git mutations were performed.
- No OHLC design or M2 implementation was started.
