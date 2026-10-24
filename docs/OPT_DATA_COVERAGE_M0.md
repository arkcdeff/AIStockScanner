# M0 — Data Coverage Verification

**Status:** Complete for read-only coverage verification
**Date:** 2026-10-23
**Scope:** Configured local development database and repository/configured-path evidence
**Report type:** Read-only audit; no implementation

**Evidence labels**

- **MEASURED** — direct result from the configured database, repository, or filesystem during the completed M0 investigation.
- **INFERRED** — conclusion drawn from measured evidence plus the prior source audit. It is not a new runtime measurement.
- **UNKNOWN** — not established by the retained evidence.

> This report intentionally does not treat local execution, archive presence, or test fixture rows as proof of production coverage.

---

## 1. Executive summary and M0 outcome

**M0 outcome:** The configured local development database does **not** demonstrate production coverage for derivatives or market-index data. It contains only a small option-chain test fixture and option-contract metadata. The 234-date FO bhavcopy archive is present on disk but has **0** corresponding rows in `derivative_daily_prices`. No genuine one-minute OHLC dataset or table was found.

**MEASURED summary:**

- `option_chain_snapshots`: **2 rows**
- `option_chain_quotes`: **8 rows**
- `derivative_daily_prices`: **0 rows**
- `market_index_prices`: **0 rows**
- `derivative_contracts`: **2,781 rows**, all options, **0 futures**
- Derivative daily coverage from the FO archive: **0 of 234 unique archived dates**
- Derivative-like import jobs: **0**
- Local futures JSONL files: **0**; default observation directory absent
- Genuine one-minute OHLC table/source: **not established**

**INFERRED:** The current evidence is sufficient to reject the proposition that this configured database already contains a usable historical derivatives/market-data corpus for one-minute OHLC construction. It is not sufficient to make any claim about another environment or deployment.

**Preserved conclusions:**

- No native one-minute OHLC infrastructure has been established.
- Option-chain snapshots are gated point observations, not OHLC bars.
- Futures JSONL capture is gated; deployed coverage remains unknown.
- Historical provider bars are daily-only according to the prior audit.
- Archived bhavcopy files do not, by themselves, prove database import completeness.

**M0 disposition:** Audit complete; production data readiness **not demonstrated**. M1 has not been started.

---

## 2. Repository and Git baseline

**MEASURED:**

- Repository root: `D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`
- Branch: `checkpoint/sprint19-20-20260822`
- HEAD: `b2df61f1d56c5a5032787c96678326d0a73f6173`
- Git status immediately before creation of this report:
  - `git status --porcelain=v1` lines: **306**
  - staged files: **0**
  - modified tracked files: **21**
  - untracked status entries: **285**
  - `docs/OPT_DATA_COVERAGE_M0.md` present: **no**
- Pre-existing untracked documentation includes the three prior OPT-DATA-AUDIT files:
  - `docs/OPT_DATA_AUDIT.md`
  - `docs/OPT_DATA_AUDIT_EVIDENCE_INDEX.md`
  - `docs/OPT_DATA_AUDIT_REUSE_INVENTORY.md`
- `docs/G11OPT14EF_EVIDENCE_RECONCILIATION.md` is also present as an untracked prior-audit document.

**MEASURED:** No staging, commit, push, reset, clean, stash, or modification of existing files was performed as part of this report task.

**Expected repository delta:** one new untracked file, this report. No existing tracked or untracked file is to be changed.

---

## 3. Database identity and safety decision

**MEASURED preflight result:**

| Check | Value |
|---|---|
| Application environment | `development` |
| Database | `ai_stock_scanner` |
| Server address | `::1` |
| Server port | `5432` |
| Server version | PostgreSQL 18.4 on x86_64-windows |
| Session role | `postgres` |
| `transaction_read_only` | `on` |
| `pg_is_in_recovery()` | `false` |
| Server timezone | `Asia/Calcutta` |
| Database revision | `d4e5f6a7b8c1` |
| Repository migration head | `e5f6a7b8c9d0` |
| `derivative_contract_sync_runs` | absent |

**Safety decision:**

- The target was explicitly confirmed by the operator as the local development database, not a test database.
- The connection was opened through the project virtual environment with `default_transaction_read_only=on`, bounded `statement_timeout`, bounded `lock_timeout`, and bounded `connect_timeout`.
- `current_database()`, `inet_server_addr()`, `inet_server_port()`, server version, and `transaction_read_only` were checked before coverage queries.
- The abort conditions were not triggered: the server address was loopback, the environment was `development`, and read-only status was verified as `on`.
- No credentials were printed or included in this report.

**MEASURED schema discrepancy:** The database is one migration behind the repository head. The missing revision is `e5f6a7b8c9d0`, which adds `derivative_contract_sync_runs`. As a result, contract-sync run history **could not be inspected** in this environment.

**INFERRED:** The database schema is sufficient for the principal coverage tables inspected, but the missing migration means the database is not schema-current. This report does not treat the missing table as evidence that no sync has ever occurred elsewhere.

**UNKNOWN:** Whether this development database is representative of any deployed environment, and whether another database contains the intended derivative imports.

---

## 4. Coverage table for each requested table and data source

### 4.1 Core database coverage

| Table / source | Rows | Date, session, or range | Coverage classification | Label |
|---|---:|---|---|---|
| `option_chain_snapshots` | 2 | One session: `2026-09-24`; source `NSE_TEST`; quality `WARNING`; chain scope `SINGLE_EXPIRY`; expiry `2026-09-24` | Test observation only; not production coverage | **MEASURED** |
| `option_chain_quotes` | 8 | `2026-09-24`; 4 contracts; 2 strikes; 4 CE / 4 PE; one expiry | Test observation only; not production coverage | **MEASURED** |
| `option_chain_quotes_2026_09` | 8 | 2026-09 partition | Only populated quote partition | **MEASURED** |
| `option_chain_quotes_2026_10` | 0 | 2026-10 partition | Empty | **MEASURED** |
| `option_chain_quotes_2026_11` | 0 | 2026-11 partition | Empty | **MEASURED** |
| `option_chain_quotes_2026_12` | 0 | 2026-12 partition | Empty | **MEASURED** |
| `derivative_daily_prices` | 0 | No rows; no date range | No daily derivative coverage | **MEASURED** |
| `derivative_contracts` | 2,781 | Options only; NIFTY/BANKNIFTY; expiry `2026-09-24` to `2031-06-24` overall | Contract metadata only; not price coverage | **MEASURED** |
| `derivative_contract_sync_runs` | n/a | Table absent | Cannot inspect sync history | **MEASURED** |
| `market_index_prices` | 0 | No rows; no date range | No index price coverage | **MEASURED** |
| `market_indices` | 2 | NIFTY and BANKNIFTY definitions | Reference/definition data only | **MEASURED** |
| `import_jobs` | 31 | All `NSE`, all `completed`; trade dates `2026-09-01` to `2026-10-22`; created `2026-09-25` to `2026-10-23` | Cash-market bhavcopy jobs; **0** derivative-like jobs | **MEASURED** |
| `daily_prices` | 102,064 | 35 distinct dates; `2026-09-01` to `2026-10-22` | Equity cash-market context only | **MEASURED** |
| `derivative_contract_metadata_histories` | 0 | No rows | Empty | **MEASURED** |
| `derivative_lot_size_histories` | 0 | No rows | Empty | **MEASURED** |
| `derivative_participant_positions` | 0 | No rows | Empty | **MEASURED** |
| `fii_derivative_statistics` | 0 | No rows | Empty | **MEASURED** |
| `eod_pipeline_runs` | 0 | No rows | Empty | **MEASURED** |
| `data_quality_issues` | 0 | No rows | Empty; snapshot warnings are not registered here | **MEASURED** |
| `market_intelligence_snapshots` | 0 | No rows | Empty | **MEASURED** |

### 4.2 Contract composition

**MEASURED:**

| Source | Instrument type | Rows | Underlyings | Expiry range | Available-at range |
|---|---|---:|---|---|---|
| `UPSTOX_OPTION_CONTRACT_MASTER` | OPTION | 2,777 | NIFTY, BANKNIFTY | `2026-10-13` to `2031-06-24` | `2026-10-07` |
| `NSE` (test fixture) | OPTION | 4 | NIFTY | `2026-09-24` | `2020-01-01` (sentinel/test value) |
| **Total** | **OPTION** | **2,781** | **2 index underlyings** | **`2026-09-24` to `2031-06-24`** | — |

- Underlying counts: NIFTY **1,836**; BANKNIFTY **945**.
- Futures contracts: **0**.
- Stock-underlying derivative contracts: **0**.
- All contracts are `ACTIVE` in the retained result.
- The four NSE test contracts have an anomalous `available_at` of `2020-01-01`; this is a data-quality issue and should not be treated as market availability evidence.

### 4.3 Option-chain session detail

**MEASURED:**

- Two snapshots share the same `market_timestamp` of `2026-09-24 09:45:00+05:30`.
- They are revisions **1** and **2** of the same test batch, with the same `source_reference` and raw payload hash.
- Snapshot 1: `available_at = 2026-09-24 09:45:02+05:30`, `ingestion_timestamp = 2026-09-25 14:28:07.765443+05:30`.
- Snapshot 2: `available_at = 2026-09-24 09:50:02+05:30`, `ingestion_timestamp = 2026-09-25 14:28:07.796841+05:30`.
- Both are `WARNING` quality and carry an `AVAILABLE_AT_INVERSION` flag.
- Quote prices differ between revisions for the same contracts (for example, LTP `150` versus `155`, and `200` versus `205`).

**INFERRED:** The two snapshots are a revision test, not two independent intraday observations. The `available_at` inversion makes naive point-in-time use of these rows unsafe.

---

## 5. Timestamp semantics and data-quality findings

### 5.1 Measured issues

**MEASURED:**

1. **Point-in-time inversion:** For both retained snapshots, `available_at` precedes `ingestion_timestamp` by approximately **2 days 19 hours**. The retained differences were approximately `-2 days 19:16:54` and `-2 days 19:21:54`.
2. **Ingestion lag:** `ingestion_timestamp` is roughly one day and 4 hours 43 minutes after `market_timestamp`.
3. **Source lag:** `source_timestamp` is one second after `market_timestamp` in both test rows.
4. **Duplicate market timestamp:** Two revisions share the same market timestamp and batch identity.
5. **Quality warning not in issue table:** `option_chain_snapshots.quality_status` is `WARNING` and contains `AVAILABLE_AT_INVERSION`, while `data_quality_issues` has **0** rows.
6. **Missing interval analytics:** `interval_seconds`, `previous_snapshot_id`, `interval_change_in_oi`, `session_change_in_oi`, and `calculated_iv` are null in all eight quote rows.
7. **Greeks:** The retained M0 result records the Greek fields as null for the test quote rows. No production Greek population was observed.
8. **No OHLC columns:** Neither `option_chain_snapshots` nor `option_chain_quotes` contains open, high, low, or close columns.
9. **Daily tables:** `derivative_daily_prices` and `market_index_prices` have daily `trade_date` keys and OHLC columns, but both have **0 rows**.
10. **Contract metadata anomaly:** The NSE test contracts use `available_at = 2020-01-01`; the Upstox master uses `available_at = 2026-10-07`.

### 5.2 Prior audit context

**INFERRED (prior source audit; not re-measured in M0):**

- The live chain parser used local fetch time (`datetime.now(UTC)`), and the adapter copied local `as_of_timestamp` into both `source_timestamp` and `market_timestamp`.
- Snapshot persistence defaulted `ingestion_timestamp` to now UTC and `available_at` to ingestion time.
- This behavior would not by itself provide an exchange timestamp or an exchange-time-aligned one-minute bar series.

### 5.3 Distinctions that must be preserved

- **Daily bars:** One open/high/low/close record per instrument per trading date. `derivative_daily_prices` and `market_index_prices` have this shape, but no derivative/index rows are present.
- **Point-in-time option snapshots:** Observations of an option chain at a captured moment. The two test snapshots are not a complete minute series.
- **Interval OI analytics:** Derived changes over intervals using `interval_seconds`, previous snapshot linkage, and OI fields. These are not OHLC bars; the retained test rows have null interval fields.
- **Genuine one-minute OHLC bars:** Timestamped per-minute open, high, low, and close per instrument, with usable point-in-time semantics. No such source or table was established.

---

## 6. Futures JSONL filesystem results

**MEASURED:**

- Configured default observation directory: `data/derivatives_observations`.
- Directory exists: **no**.
- Repository `.jsonl` files found: **0**.
- Configured override for the observation directory: **none** in `.env`.
- Configuration default for observation capture: disabled.
- Configuration default interval: 60 seconds, with gating/limits defined by the application configuration.

**INFERRED:** There is no local futures observation corpus available for coverage analysis in this repository. Any deployed JSONL corpus would need an independently verified path and inventory.

**UNKNOWN:**

- Whether futures observation capture has ever run in a deployed environment.
- The deployed observation directory, file count, date range, underlyings, and interval density.
- Whether deployed JSONL records are suitable for any bar construction; observation cadence and gating are not OHLC guarantees.

**Coverage result:** local futures JSONL coverage is **absent**; deployed coverage is **UNKNOWN**.

---

## 7. Bhavcopy archive coverage and verified import status

### 7.1 Archive inventory

**MEASURED:**

- Derivative FO ZIP files: **255**
- Total FO ZIP bytes: **318,843,946**
- Unique FO dates: **234**
- Date range: **`2025-10-01` to `2026-09-10`**
- Duplicate-date groups: **21**, all in June 2026
- Extra duplicate FO files: **21**
- ZIP integrity: **255 valid ZIPs**, each with a single CSV entry
- Gaps greater than four calendar days: **none**
- Archive locations:
  - `data/reference/derivatives/raw` — 234 FO ZIPs
  - `data/raw/nse/2026/06_udiff` — 21 FO ZIPs plus 21 cash-market CM ZIPs

**MEASURED reconciliation note:** The two scanned roots contain **276 ZIP files** in total: 255 FO files and 21 CM files. The 21 CM files are not derivatives and are excluded from the 255 FO count.

**MEASURED manifest metadata:**

- `manifest_2026.json`: 172 date entries, `2026-01-01` to `2026-09-10`.
- `manifest_q4_2025.json`: 62 date entries, `2025-10-01` to `2025-12-31`.
- 2026 manifest metadata reports `final_total_rows = 7,105,914`.
- Manifest dates and row counts are acquisition metadata; they are not database import proof.

### 7.2 Import verification against the configured database

**MEASURED:**

- `derivative_daily_prices`: **0 rows**
- Archived FO dates represented in `derivative_daily_prices`: **0 of 234**
- Derivative-like import jobs in `import_jobs`: **0**
- All 31 import jobs are `NSE` cash-market bhavcopy jobs and are `completed`.
- `daily_prices` contains **102,064** equity rows across **35** dates, confirming cash-market import activity elsewhere in the database.

**INFERRED:** The FO archive was acquired successfully, but it has **not** been imported into this configured database. The 234 archived dates are **not** evidence of complete database imports.

**MEASURED prior-status claim:**

- `data/audit/session_manifest_20250808_20260810.json` reports derivatives `already_imported = 25` and `official_remote_not_found = 225` for its `2025-08-08` to `2026-08-10` window.

**UNKNOWN:** Whether those 25 prior imports occurred in a different database, were removed, or were never represented in this development database. The current configured database has zero derivative daily rows, so the prior status claim cannot be treated as current coverage proof.

---

## 8. Intraday OHLC readiness matrix

| Source | Native granularity | Measured availability | OHLC? | M0 readiness |
|---|---|---|---|---|
| `option_chain_snapshots` / `option_chain_quotes` | Captured point observations | 2 snapshots / 8 quotes, test only, one session | **No OHLC columns** | **Not usable** for historical 1m OHLC; test-only |
| Futures JSONL observations | Gated observation records | Local directory absent; 0 repo files | Not established | **Not available locally**; deployed **UNKNOWN** |
| FO bhavcopy archive | Daily | 255 ZIPs / 234 dates on disk | Daily bar source; file contents not re-parsed in M0 | **Daily only**; not intraday |
| `derivative_daily_prices` | Daily | 0 rows | Daily OHLC schema | **Not available** |
| `market_index_prices` | Daily | 0 rows | Daily OHLC schema | **Not available** |
| Upstox historical provider bars | Daily | Not tested in M0 | Prior audit: daily-only | **Daily only** (**INFERRED**) |
| Live tick/WebSocket feed | Tick | Not tested for persistence in M0 | Not a persisted bar source | **No persisted intraday evidence** (**INFERRED**) |
| Genuine one-minute OHLC | One-minute bars | No table or data source established | Required | **UNKNOWN / not established** |

**MEASURED:** The option-chain schema contains OI, LTP, bid/ask, volume, interval fields, and Greek fields, but no OHLC fields.

**INFERRED:** Option-chain snapshots, interval OI analytics, and daily bhavcopy records cannot be reclassified as genuine one-minute OHLC bars. Constructing one-minute OHLC would require a continuously captured, time-aligned price series (or a verified minute-bar provider), plus a defined bar-construction and point-in-time policy.

**Conclusion:** **No native one-minute OHLC infrastructure has been established.**

---

## 9. Remaining UNKNOWN items and evidence needed

| UNKNOWN | Why it remains unresolved | Evidence needed |
|---|---|---|
| Other environment/database import status | Only the configured development database was inspected | Authoritative deployment DB identity, revision, and derivative coverage counts |
| Deployed JSONL coverage | No local observation directory or files; no override found | Deployed path, file inventory, date/session coverage, parse results, capture logs |
| File-level bhavcopy import completeness | Archive presence is not import proof; DB has zero derivative daily rows | Per-file row counts and provenance reconciled against DB rows, or an authorized staging import |
| Production option-chain capture coverage | Only two `NSE_TEST` rows were found | Production source observations, session dates, interval density, quality status, and PIT evidence |
| Genuine one-minute OHLC source | No minute table/source was found | Vendor/API contract, sample minute bars, schema, retention, timestamp policy, and licensing |
| Database revision completeness | DB is at `d4e5f6a7b8c1`, repository head is `e5f6a7b8c9d0` | Authorized migration/revision verification; inspect `derivative_contract_sync_runs` after migration |
| Prior 25-import claim | Prior manifest conflicts with current zero rows | Audit trail tying the prior imports to a specific database/environment |
| Full raw query/command output | Summary results were retained; raw result sets were not saved as separate artifacts | Persisted evidence bundle if reproducible audit output is required |

**UNKNOWN:** Whether any deployed environment has the required data. This report does not infer deployment coverage from the local development database or from archive presence.

---

## 10. Recommended next milestone

**Recommended:** M1 — Environment and Import Reconciliation; not started.

Suggested M1 objectives, subject to separate authorization:

1. Identify the authoritative deployment database and observation root; repeat the identity/revision/safety preflight there.
2. Reconcile the FO archive against actual database provenance on a per-file or per-date basis, rather than using archive counts as import proof.
3. Use an authorized staging environment to perform a controlled one-file import dry run, then verify row counts and idempotency before any full backfill.
4. Resolve the database revision gap (`d4e5f6a7b8c1` to `e5f6a7b8c9d0`) through the approved migration process and then inspect contract-sync history.
5. Establish a verified deployment path and inventory for futures JSONL observations.
6. Define one-minute OHLC requirements separately: instrument coverage, session calendar, bar timestamp policy, PIT/availability policy, retention, and vendor/source validation.

**M1 exit criteria should include:** authenticated environment identity, schema at the approved revision, non-zero and reconciled derivative daily coverage, documented JSONL deployment coverage, and an evidence-backed one-minute OHLC source decision. Until then, no one-minute OHLC implementation should be inferred as supported.

---

## 11. Read-only commands and SQL evidence available from the completed investigation

**Evidence limitation:** The M0 investigation retained summarized results and representative SQL/command patterns. Full raw result sets were not saved as a separate evidence bundle. The SQL below is a sanitized representation of read-only statements used in the investigation; it is not a claim that every query was preserved verbatim as an execution log.

### 11.1 Connection safety

The investigation used the project virtual environment and a bounded, read-only PostgreSQL connection:

```text
.venv\Scripts\python.exe
psycopg.connect(
  <sanitized DSN>,
  connect_timeout=5,
  application_name="opt_m0_audit",
  options="-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000"
)
```

No DSN, username, password, or token is reproduced in this report.

### 11.2 Identity and revision checks

```sql
SELECT current_database(),
       current_user,
       inet_server_addr()::text,
       inet_server_port(),
       version(),
       current_setting('transaction_read_only'),
       current_setting('TimeZone'),
       pg_is_in_recovery();

SELECT version_num FROM alembic_version;

SELECT to_regclass('public.derivative_contract_sync_runs');
```

Observed summary: database `ai_stock_scanner`; server `::1:5432`; PostgreSQL 18.4; `transaction_read_only=on`; `pg_is_in_recovery=false`; revision `d4e5f6a7b8c1`; `derivative_contract_sync_runs` null.

### 11.3 Coverage counts

```sql
SELECT count(*) FROM option_chain_snapshots;
SELECT count(*) FROM option_chain_quotes;
SELECT count(*) FROM derivative_daily_prices;
SELECT count(*) FROM derivative_contracts;
SELECT count(*) FROM market_index_prices;
SELECT count(*) FROM import_jobs;
SELECT count(*) FROM data_quality_issues;
SELECT count(*) FROM eod_pipeline_runs;
```

### 11.4 Partition inventory

```sql
SELECT child.relname AS partition,
       pg_get_expr(child.relpartbound, child.oid) AS bound
FROM pg_inherits i
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_class child ON child.oid = i.inhrelid
JOIN pg_namespace n ON n.oid = child.relnamespace
WHERE parent.relname = 'option_chain_quotes'
  AND n.nspname = 'public'
ORDER BY child.relname;
```

Returned children: `option_chain_quotes_2026_09` through `option_chain_quotes_2026_12`; only the 2026-09 child contained rows.

### 11.5 Snapshot timing and quote null checks

```sql
SELECT id, revision, batch_id, normalization_version, raw_payload_hash,
       quality_flags, market_timestamp, source_timestamp,
       ingestion_timestamp, available_at,
       available_at - ingestion_timestamp AS available_minus_ingestion,
       ingestion_timestamp - market_timestamp AS ingestion_lag,
       source_timestamp - market_timestamp AS source_lag
FROM option_chain_snapshots
ORDER BY id;

SELECT count(*) AS total,
       count(*) FILTER (WHERE interval_seconds IS NULL) AS null_interval,
       count(*) FILTER (WHERE previous_snapshot_id IS NULL) AS null_prev_snapshot,
       count(*) FILTER (WHERE calculated_iv IS NULL) AS null_calculated_iv
FROM option_chain_quotes;
```

### 11.6 Import and contract checks

```sql
SELECT source, status, count(*) AS rows,
       min(trade_date), max(trade_date)
FROM import_jobs
GROUP BY source, status
ORDER BY source, status;

SELECT instrument_type, source_name, lifecycle_status,
       count(*) AS row_count,
       min(expiry_date), max(expiry_date),
       min(available_at), max(available_at)
FROM derivative_contracts
GROUP BY instrument_type, source_name, lifecycle_status
ORDER BY instrument_type, source_name, lifecycle_status;
```

### 11.7 Filesystem and Git checks

```text
Python pathlib scan of:
  data/reference/derivatives/raw
  data/raw/nse/2026/06_udiff

Filter:
  BhavCopy_NSE_FO_*.zip

ZIP validation:
  zipfile.ZipFile(...).namelist()
  require exactly one CSV entry

JSONL scan:
  repository rglob("*.jsonl")
  Test-Path data/derivatives_observations
```

```text
git status --porcelain=v1
git diff --cached --name-only
git diff --name-only
git ls-files --others --exclude-standard
```

No runtime evidence is reconstructed beyond the summarized results above.

---

## 12. Actions not performed

**Not performed in M0:**

- No database writes, INSERT/UPDATE/DELETE, DDL, migrations, imports, backfills, or data repair.
- No test suite execution.
- No provider/API calls or downloads.
- No capture jobs, scheduler startup, broker execution, or trading actions.
- No extraction of bhavcopy ZIPs; archives were inspected read-only and validated as ZIP containers.
- No disk-wide search; filesystem inspection was limited to the repository and the configured observation path.
- No changes to existing repository files.
- No `git add`, commit, push, reset, clean, stash, or branch changes.
- No M1 work and no one-minute OHLC implementation.

**Database safety:** Every inspection connection used `default_transaction_read_only=on`; read-only status was verified as `on`; transactions were rolled back. The database remains at revision `d4e5f6a7b8c1`.

**Final position:** M0 is documented. The loaded development database does not establish production derivatives or market-data coverage, and it does not support a claim that one-minute OHLC can currently be constructed. Further work requires separate authorization.
