# OPT Data Reconciliation — M1-B Controlled Read-Only Database Report

**Investigation ID:** `M1B-DB-RECON-20261024`
**Milestone:** M1-B (controlled, read-only database reconciliation)
**Investigation date:** 2026-10-24, 15:25–15:35 IST (10:01–10:05 UTC)
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Target:** local development PostgreSQL `ai_stock_scanner` on `localhost:5432`
**Mode:** strictly read-only. No writes, no DDL, no migrations, no imports.

---

## 1. Verdict

# `PARTIALLY RECONCILED`

The database identity, current schema, current row counts, and migration gap were established
against verified evidence. The historical derivative counts **cannot** be reproduced in the
current database, and this non-reproduction is now supported by structural evidence rather than
by absence alone. The composition of the 882-row residual, the 576-row participant-OI claim, and
all production state remain **unresolved**.

**M2 remains on HOLD.**

---

## 2. Executive summary

**[OBSERVED]** The authorized local development database `ai_stock_scanner` (PostgreSQL 18.4,
`localhost:5432`) contains **zero** rows in `derivative_daily_prices`. It contains **2,781**
contract masters, all classified `OPTION`, and **zero** futures contracts and **zero**
participant-position rows.

**[OBSERVED]** This is not a case of rows being present but differently classified. The
`derivative_daily_prices` heap occupies **0 pages**, and its `id` sequence
(`derivative_daily_prices_id_seq`) has **never been used** (`last_value IS NULL`). As a control,
every table that demonstrably received data in this database has an advanced sequence
(`daily_prices_id_seq` = 105,459 for 102,064 rows; `derivative_contracts_id_seq` = 2,801 for
2,781 rows; `option_chain_snapshots_id_seq` = 6 for 2 rows). The differential is clean: this
database instance has never held derivative daily-price rows.

**[CALCULATED]** Consequently the reported "882-row discrepancy" is not a measurable delta
between the current database and the archive. Against the current database the gap is
**1,731,456 rows** over the 45-session window — the entire population. The +882 / +1,211 / −329
figures are a difference between **two documents** (a UI-page reading and the archive recount),
not a difference between a database and the archive.

**[OBSERVED]** The migration gap is explained without inferring any data loss. The repository has
a single Alembic head `e5f6a7b8c9d0`; the database is at `d4e5f6a7b8c1`. The one unapplied
revision is literally `e5f6a7b8c9d0_add_derivative_contract_sync_runs.py`, whose parent is
`d4e5f6a7b8c1`. The previously reported "missing table" `derivative_contract_sync_runs` is absent
**because that migration has not been run** — that is a schema-version fact, not evidence of loss.

**[CLAIM]** The historical figures (1,732,338 total / 1,703,830 options / 28,508 futures / 75,962
contract masters / 576 participant rows) originate from a browser UI page captured on 2026-09-03
and a count-check table in a handoff document on 2026-09-07. Neither records a database identity,
a SQL statement, a server, or a reproducible query. They remain **historical claims without
provenance**, and they cannot be validated as a prior state of *this* database.

**[HYPOTHESIS]** Corroborating context from the same authorized database suggests the current
instance was initialised around 2026-09-24 rather than having derivative data selectively removed.
The equity table `daily_prices` contains only 35 trading dates (2026-09-01 … 2026-10-22) and every
row in it and in `stocks` was created on or after `2026-09-24 14:57:57`; the 2026-09-03 UI page had
reported 1,006,907 equity price rows. A whole-database reset/reload fits the evidence better than a
derivatives-only event. This remains a **hypothesis**: distinguishing a drop-and-recreate from a
restore-from-backup requires server logs, which are outside read-only database scope.

---

## 3. Scope and authorization

**Authorized by the user:** connect to the identified local development database, execute
read-only SQL, inspect schema and migration metadata, reconcile derivative records by trading date
and instrument type, and save the report plus supporting evidence under `docs/`.

**Explicitly not authorized, and not performed:** production connections; reads or writes against
any database other than the identified local development database; migrations; DML/DDL/TRUNCATE;
imports, backfills, syncs, schedulers, capture jobs, provider calls; broker operations; changes to
application code, configuration, schema, or archive data; any Git mutation; installing packages.

**Method containment.** All database access went through one runner,
[docs/data-audit/m1b/run_m1b_readonly.py](data-audit/m1b/run_m1b_readonly.py), which:

- resolves the target from the repository's own `.env` `DATABASE_URL_SYNC` (no hard-coded host);
- opens every connection with `psycopg2 >= conn.set_session(readonly=True, autocommit=False)`;
- issues `SET TRANSACTION READ ONLY` on every stage;
- calls `SHOW transaction_read_only` and **aborts** if the result is not `on`;
- contains only `SELECT`, `SHOW`, and the read-only transaction statements;
- never prints, logs, or persists a password or connection secret.

`transaction_read_only = on` was verified on every stage. The server default is `off`, so the
read-only guarantee came from the explicit session/transaction settings, not from the role.

---

## 4. Database identity receipt (Gate 1)

Evidence: [docs/data-audit/m1b/database_identity_receipt.json](data-audit/m1b/database_identity_receipt.json)

| Field | Value | Source |
|---|---|---|
| Environment classification | `development` | `.env` `APP_ENV` |
| Configured target | `postgresql+psycopg://postgres@localhost:5432/ai_stock_scanner` | `.env` `DATABASE_URL_SYNC` (password present, value never read into output) |
| Async target | `postgresql+asyncpg://…@localhost:5432/ai_stock_scanner` (same host/port/db) | `.env` `DATABASE_URL_ASYNC` |
| `current_database()` | `ai_stock_scanner` | server response |
| Server version | `PostgreSQL 18.4 on x86_64-windows, compiled by msvc-19.44.35227, 64-bit` | `version()` |
| `server_version_num` | `180004` | `SHOW` |
| `inet_server_addr()` | `::1/128` (IPv6 loopback) | server response |
| `inet_server_port()` | `5432` | server response |
| `listen_addresses` | `*` | `SHOW` |
| `data_directory` | `C:/Program Files/PostgreSQL/18/data` | `SHOW` (local Windows installation) |
| `postmaster_start_time` | `2026-10-24 11:50:30.914561+05:30` | server response |
| `now()` at receipt | `2026-10-24 15:31:22.74013+05:30` | server response |
| Timezone | `Asia/Calcutta` | `SHOW timezone` |
| Server encoding | `UTF8` | `SHOW` |
| `current_user` / `session_user` | `postgres` (superuser) | server response |
| Database size | `40 MB` | `pg_database_size` |
| Read-only enforcement | `transaction_read_only = on` (verified) | `SHOW` |
| Database Alembic revision | `d4e5f6a7b8c1` | `public.alembic_version` |

### Databases present on this server

| Database | Owner | Size | Connections allowed |
|---|---|---|---|
| `ai_stock_scanner` | `ai_scanner` | 40 MB | yes — **the authorized target** |
| `college_cert_sys_staging` | `college_cert_staging_user` | 20 MB | yes |
| `hubdb` | `postgres` | 7.9 MB | yes |
| `postgres` | `postgres` | 7.9 MB | yes |
| `template0` | `postgres` | 7.8 MB | no |
| `template1` | `postgres` | 7.9 MB | yes |
| `test` | `postgres` | 34 MB | yes |

Only `ai_stock_scanner` was connected to and queried. The other databases were **listed** (a
catalogue read on the authorized server) but never opened. No database named `aistock` — the name
used in earlier reports for remote production — exists on this server.

### Why this is accepted as local development

1. `.env` sets `APP_ENV=development`, and `.env` lives in the working copy.
2. The configured host is `localhost` and the server reports the client address as `::1/128`.
3. `data_directory` is a local Windows installation path, not a managed/remote endpoint.
4. The process was started locally at `2026-10-24 11:50:30` and the database is 40 MB — consistent
   with a developer machine, inconsistent with a populated production warehouse.
5. No production database name is reachable on this server.

**Gate 1 result: PASSED.** Identity is unambiguous, production is excluded, and read-only
execution is verified.

> Residual risk noted: `current_user` is `postgres`, a superuser. Read-only was therefore
> enforced by the session and transaction, not by privilege separation. This is acceptable for a
> read-only investigation but is not a substitute for a least-privilege role in future work.

---

## 5. Repository state before and after

**Before** (captured before any file in this task existed):

| Item | Value |
|---|---|
| Branch | `checkpoint/sprint19-20-20260822` |
| HEAD | `b2df61f1d56c5a5032787c96678326d0a73f6173` |
| Commits ahead of `origin` | 2 |
| `git status --short` lines | 332 (21 ` M`, 311 `??`) |
| Staged / index entries | 0 |

The 21 pre-existing modified tracked files (`app/cli/import_nse.py`, `app/core/config.py`,
`app/models/*`, `app/repositories/derivative.py`, `app/services/*`, `app/templates/*`,
`run_aistockscanner.bat`, `tests/conftest.py`, `tests/test_derivatives_capture_g11opt4.py`) were
present before this task and were not touched. The 311 pre-existing untracked entries include the
M1-A audit artifacts. Nothing was staged, committed, pushed, reset, cleaned, stashed, checked out,
or branch-switched.

**After:** see §13.

---

## 6. Schema findings (Gate 2)

Evidence: [docs/data-audit/m1b/schema_inventory.json](data-audit/m1b/schema_inventory.json)

**[OBSERVED]** 66 base tables exist, all in schema `public`; `public` is the only non-system
schema, and no other schema anywhere on the server holds a derivative or daily-price relation.

The schema does **not** match the table names assumed by the historical reports in two ways:

| Historical report wording | Actual object | Note |
|---|---|---|
| `participant_oi` | `derivative_participant_positions` | 15 columns; keyed by `report_date`, `dataset_type`, `participant_type`, `instrument_bucket` |
| `derivative_contract_sync_runs` (reported missing) | **does not exist** | created by the unapplied migration `e5f6a7b8c9d0` |

### Tables relevant to this reconciliation

| Table | Rows | Notes |
|---|---:|---|
| `derivative_contracts` | 2,781 | instrument master; `instrument_type` lives here |
| `derivative_daily_prices` | **0** | EOD prices; **no** `instrument_type` column |
| `derivative_participant_positions` | **0** | participant OI dataset |
| `derivative_contract_metadata_histories` | 0 | created by `d4e5f6a7b8c1` (applied) |
| `derivative_lot_size_histories` | 0 | |
| `fii_derivative_statistics` | 0 | |
| `option_chain_snapshots` | 2 | ids 5 and 6, created 2026-09-25 |
| `option_chain_quotes_2026_09` | 8 | monthly partition |
| `option_chain_quotes_2026_10` … `_12` | 0 | empty monthly partitions |
| `alembic_version` | 1 | `d4e5f6a7b8c1` |

### Structural constraints that shape the counting rules

- `derivative_daily_prices` has **no** `instrument_type` column. Any option/futures split requires
  `derivative_daily_prices p JOIN derivative_contracts c ON c.id = p.contract_id` and must read
  `c.instrument_type`. The archive counting rule (`FinInstrmTp` ∈ {`STO`,`IDO`} / {`STF`,`IDF`}) and
  the database rule (`instrument_type` ∈ {`OPTION`,`FUTURE`}) are therefore **not** the same
  vocabulary, and this is the first place a split could diverge.
- `UNIQUE (contract_id, trade_date)` on `derivative_daily_prices` — duplicate `(contract, date)`
  rows are structurally impossible; duplicate analysis of that shape is closed by the schema.
- `FOREIGN KEY contract_id REFERENCES derivative_contracts(id) ON DELETE CASCADE` — deleting a
  contract master would silently cascade-delete its price rows. This is the only in-schema
  mechanism by which populated price rows could vanish, and it is recorded here as a structural
  risk rather than as a finding (see §8).
- `derivative_daily_prices.trade_date` is `NOT NULL`; a null-date bucket can only be zero, and was
  measured rather than assumed.

---

## 7. Baseline counts (Gate 2)

Evidence: [docs/data-audit/m1b/baseline_results.json](data-audit/m1b/baseline_results.json)

| Measurement | Value |
|---|---:|
| `derivative_contracts` total | 2,781 |
| `derivative_daily_prices` total | **0** |
| Distinct `trade_date` in prices | **0** |
| Distinct `contract_id` in prices | **0** |
| Price date range | none (`min`/`max` = NULL) |
| Prices in window 2026-06-08 … 2026-08-10 | **0** |
| Null `trade_date` rows | 0 |
| Orphan price rows (no parent contract) | 0 |
| `derivative_participant_positions` | **0** |
| `option_chain_snapshots` | 2 |
| `fii_derivative_statistics` | 0 |
| `derivative_contract_metadata_histories` | 0 |
| `derivative_lot_size_histories` | 0 |

### Contract-master composition

| Dimension | Value |
|---|---|
| `instrument_type` | `OPTION` = 2,781; `FUTURE` = **0** |
| `exchange` / `segment` | `NSE` / `NFO` (all 2,781) |
| `lifecycle_status` | `ACTIVE` (all 2,781) |
| `option_type` | `PE` = 1,399; `CE` = 1,382; null = 0 |
| Distinct `trading_symbol` | 2,781 (no repeated symbols) |
| Distinct `expiry_date` | 19 |
| Distinct underlyings | 2 (`underlying_stock_id` / `underlying_index_id` pairs) |
| Duplicate `idempotency_hash` groups | 0 |
| Duplicate `source_contract_id` groups | 0 |
| Duplicate `(symbol, expiry, strike, option_type)` groups | 0 |
| `created_at` range | `2026-09-25 14:28:07.749792+05:30` … `2026-10-07 16:33:34.244109+05:30` |
| `created_at` by day | 2026-09-25 → 4 rows; 2026-10-07 → 2,777 rows |

**No derivative contract row predates 2026-09-25.**

Note the shape mismatch: 2,781 option masters distributed over 19 expiries and only 2 underlyings
is a narrow slice, not a full market master. The historical claim of 75,962 masters over a broad
universe is not structurally comparable to what exists now.

---

## 8. Gate 4 — the 882-row difference and the forensics of absence

Evidence: [docs/data-audit/m1b/forensics_results.json](data-audit/m1b/forensics_results.json),
[docs/data-audit/m1b/context_results.json](data-audit/m1b/context_results.json)

### 8.1 The two arithmetic claims, as stated

**[CALCULATED]** The historical claims and the independently reproduced archive counts are:

| Identity | Arithmetic | Status |
|---|---|---|
| Total | 1,732,338 − 1,731,456 = **+882** | arithmetic correct; comparison valid |
| Options | 1,703,830 − 1,702,619 = **+1,211** | arithmetic correct; comparison valid |
| Futures | 28,508 − 28,837 = **−329** | arithmetic correct; comparison valid |
| Net | +1,211 + (−329) = **+882** | identities are consistent |

The arithmetic was never in doubt. What was in doubt is whether either side describes the current
database.

### 8.2 Structural test: has this database ever held derivative price rows?

Counts of zero cannot distinguish "never populated" from "populated then deleted". Four
independent structural indicators were measured:

| Indicator | `derivative_daily_prices` | Control: `daily_prices` (equity) | Control: `derivative_contracts` |
|---|---|---|---|
| `count(*)` | 0 | 102,064 | 2,781 |
| Heap pages (`relpages`) | **0** | 1,625 | 103 |
| `pg_relation_size` (heap bytes) | **0** | 13,312,000 | 843,776 |
| `pg_class.reltuples` | `-1` (never analyzed) | 102,064 (analyzed) | 2,781 (analyzed) |
| `id` sequence `last_value` | **NULL — never used** | 105,459 | 2,801 |
| Earliest `created_at` | none | 2026-09-24 14:57:57 | 2026-09-25 14:28:07 |

**Control logic.** Sequences in this database demonstrably record usage: `daily_prices_id_seq` is
at 105,459 for 102,064 live rows, `derivative_contracts_id_seq` is at 2,801 for 2,781 rows,
`option_chain_snapshots_id_seq` is at 6 for 2 rows, and `option_chain_quotes_id_seq` is at 24 for
8 rows. Against that baseline, `derivative_daily_prices_id_seq` and
`derivative_participant_positions_id_seq` having `last_value IS NULL` is a meaningful negative
signal, not an artefact of an unused cluster.

**Conclusion [OBSERVED + CALCULATED]:** in this database instance, no row has ever been inserted
into `derivative_daily_prices` through its default sequence, and its heap has never been
allocated. Combined with 0 rows and never-analyzed statistics, the strongest supportable statement
is that **this database instance has never been populated with derivative daily prices.**

**Caveats, stated explicitly:**

- A `TRUNCATE ... RESTART IDENTITY` would reset `relpages` *and* the sequence, reproducing this
  exact signature. Such an operation cannot be excluded from database metadata alone.
- Inserts that supplied explicit `id` values (bypassing `nextval`) would leave the sequence at
  NULL. Application code was not traced for this in M1-B; it is a residual alternative explanation.
- WAL, `pg_stat` progress views, and server logs would settle the question. They are outside the
  authorized read-only scope (log files are not database objects) and were not consulted.

### 8.3 Statistics counters were found unusable and are excluded

`track_counts = on`, yet `pg_stat_user_tables` reports `n_live_tup = 0`, `n_tup_ins = 0` for
**every** table, including `stocks` (3,574 rows) and `daily_prices` (102,064 rows), and
`pg_stat_database` reports `tup_inserted = 3` for the whole database with `stats_reset = NULL`.

These counters are internally inconsistent with the physical row counts, so **no conclusion in
this report relies on them.** They are recorded only to document that a plausible-looking
forensic avenue was tested and rejected. (Had they been trusted, `tup_inserted = 3` would have
"proved" that almost nothing was ever written to this database — a false conclusion contradicted
by 102,064 equity rows.)

### 8.4 What the evidence proves, and what it does not

**Proved by evidence:**

1. The current database contains no derivative daily-price rows in the 45-session window, or in
   any other period.
2. The current database cannot reproduce 1,732,338 / 1,703,830 / 28,508, and does not reproduce
   them under any date filter.
3. The current database contains 0 futures contracts, so a −329 futures difference cannot be
   observed here at all.
4. Duplicates are excluded as a cause of a +882 surplus *in the current database*: the
   `(contract_id, trade_date)` uniqueness constraint makes that shape impossible, and there are no
   price rows to duplicate. This does **not** retract the possibility that the historical 1,732,338
   figure itself included duplicates.

**Still hypothetical:**

5. That the historical figures described a *different* database instance, or a state of this
   database before a reset around 2026-09-24.
6. That the +882 / +1,211 / −329 composition is caused by import filtering, contract-master
   mapping, reclassification (FUTURE→OPTION), or session-boundary definition. No database evidence
   can discriminate between these, because no database rows survive to test them against.
7. That a whole-database reset, rather than a derivatives-only deletion, occurred. Supported by
   the equity-table evidence in §8.5 but not proven.

**Still unresolved:**

8. The composition of the 882 rows. This cannot be resolved from the current database under any
   read-only query, because the population required to explain it does not exist.
9. Whether the historical figures were ever an accurate readout of any real database. Their
   provenance is a rendered UI counter and a document table, not a query result.

### 8.5 Corroborating context from the same authorized database

These reads are **outside the derivative scope** and are recorded solely because they bear on
whether a derivatives-only event is a sufficient explanation. They were executed on the same
authorized read-only connection, in the same schema, with no additional authorization implied.

| Measurement | 2026-09-03 UI page [CLAIM] | Current database [OBSERVED] |
|---|---:|---:|
| `stocks` rows | 3,639 | 3,574 |
| `daily_prices` (equity EOD) rows | 1,006,907 | 102,064 |
| Latest equity trade date | 2026-09-02 | 2026-10-22 |

- `daily_prices` covers 35 distinct trade dates: `2026-09-01` … `2026-10-22`.
- `min(created_at)` for `daily_prices` and for `stocks` is **identical to the microsecond**:
  `2026-09-24 14:57:57.788974+05:30`. `max(created_at)` for both is `2026-10-23 16:21:13`.

**[HYPOTHESIS]** A database-wide initialisation on 2026-09-24, followed by incremental loads,
explains: (a) why no row anywhere predates 2026-09-24; (b) why equity history before 2026-09-01 is
absent although the UI reported 1,006,907 rows on 2026-09-03; (c) why derivative daily prices were
never populated in the rebuilt instance; and (d) why the containers (`derivative_contracts` with
2,781 option masters over 19 expiries) look like a partial reload rather than a deleted full set.

This is a hypothesis. It is consistent with the evidence and is *not* contradicted by it, but
distinguishing a drop-and-recreate from a restore-from-backup requires artefacts (logs, backup
manifests, shell history) that this investigation is not authorized to read.

---

## 9. Participant OI (576 rows)

Evidence: [docs/data-audit/m1b/baseline_results.json](data-audit/m1b/baseline_results.json)

| Item | Result |
|---|---|
| Table named in prior reports (`participant_oi`) | does not exist |
| Actual table | `derivative_participant_positions` (exists) |
| Rows | **0** |
| `dataset_type` / `instrument_bucket` groups | none (empty table) |
| `id` sequence `last_value` | **NULL — never used** |
| Originating producer query documented anywhere | **not found** |

The 576-row claim cannot be verified, contradicted, or partially reproduced. The table exists and
is structurally capable of holding the dataset, but has never received a row in this database.
The originating query remains unidentified. **[UNVERIFIED]**

---

## 10. Migration and synchronization history

Evidence: [docs/data-audit/m1b/migration_state.json](data-audit/m1b/migration_state.json)

| Item | Previously reported | Verified 2026-10-24 | Status |
|---|---|---|---|
| Repository Alembic head | `e5f6a7b8c9d0` | `e5f6a7b8c9d0` — the single head across 28 revision files | **CONFIRMED** |
| Database Alembic revision | `d4e5f6a7b8c1` | `d4e5f6a7b8c1` | **CONFIRMED** |
| `derivative_contract_sync_runs` table | reported missing | **ABSENT** (`to_regclass` → NULL) | **CONFIRMED** |
| `derivative_contract_metadata_histories` | — | PRESENT (0 rows) | observed |
| `derivative_lot_size_histories` | — | PRESENT (0 rows) | observed |

**[OBSERVED]** The revision graph was reconstructed from the repository files: the chain from
`d4e5f6a7b8c1` runs backwards through `d5e6f7a8b9c0` → `c4d5e6f7a8b9` → … → `0001`, and
`e5f6a7b8c9d0` declares `down_revision = "d4e5f6a7b8c1"`. The file that creates
`derivative_contract_sync_runs` **is** `e5f6a7b8c9d0_add_derivative_contract_sync_runs.py`.

**Therefore the "missing table" is fully explained by the unapplied revision.** The database is
exactly one revision behind the repository head, and that one revision is the one that would create
the table. This is a deployment-version fact.

**Per instruction, no data loss is inferred from this mismatch, and no migration was run.**

Note also: the absence of `derivative_contract_sync_runs` means this database has **no
synchronization-history record at all** for derivative contracts. Even if the table were migrated
now, it would be empty and could not retroactively document past loads. Any future import-provenance
requirement must be satisfied prospectively.

---

## 11. Reconciliation table

Evidence: [docs/data-audit/m1b/reconciliation_results.json](data-audit/m1b/reconciliation_results.json)

Archive reference: [docs/data-audit/recount_result.json](data-audit/recount_result.json),
SHA-256 `425b02264bdc19423f48e452496072b093bd600710b6a8f52fbadf2b0f9ed14e` (unchanged from M1-A).

| Metric | Historical claim (no DB identity) | Archive, 45 sessions 2026-06-08…2026-08-10 | Current DB, same window | Claim − Archive | Claim − Current DB | Reproducible in current DB? |
|---|---:|---:|---:|---:|---:|---|
| Total derivative daily rows | 1,732,338 | 1,731,456 | **0** | **+882** | **+1,732,338** | **NO** |
| Option rows | 1,703,830 | 1,702,619 | **0** | +1,211 | +1,703,830 | **NO** |
| Futures rows | 28,508 | 28,837 | **0** | −329 | +28,508 | **NO** |
| Contract masters | 75,962 | n/a (archive reports 71,709 distinct `FinInstrmId`; not the same definition) | 2,781 | n/a | −73,181 | **NO** |
| Participant OI rows | 576 | n/a (no archive source) | 0 | n/a | +576 | **NO** |

Unavailable or non-comparable values are marked `n/a` rather than estimated.

**Counting-rule comparison.** The two sides of the only comparison that is actually valid (archive
vs claim) are not established as using identical definitions:

| Aspect | Archive (M1-A recount) | Historical claim |
|---|---|---|
| Source | `BhavCopy_NSE_FO_*` ZIPs, 234 dates, 2025-10-01 … 2026-09-10 | rendered UI counters; unattributed |
| Classification | `FinInstrmTp` ∈ {`STO`,`IDO`} = option; {`STF`,`IDF`} = future | `instrument_type` ∈ {`OPTION`,`FUTURE`} as stored |
| Session definition | calendar dates present in the archive; 45 dates confirmed present | unknown |
| Duplicate handling | none applied; raw row counts | unknown |
| Reproducibility | scripted and hash-verified | none recorded |

Because the claim side records neither its query nor its deduplication, the +882 cannot be
attributed even in principle from document evidence alone. M1-A's window search independently
found **0** exact matches for the claimed total, options, or futures figures across all contiguous
archive windows — so the archive cannot be tuned to reproduce the claim either.

---

## 12. Resolved, partially resolved, unresolved

### Resolved

| Question | Answer |
|---|---|
| Is the target a verified local development database? | **Yes** — §4. Gate 1 passed. |
| Was read-only access actually enforced? | **Yes** — session + transaction, verified per stage. |
| What is the current derivative data state? | 2,781 option masters, **0** price rows, **0** futures, **0** participant rows. |
| Can the historical counts be reproduced in this database? | **No**, under the exact 45-session window or any filter. |
| Does the migration gap imply loss? | **No** — one unapplied revision creating one table. |
| Is `derivative_contract_sync_runs` genuinely absent? | **Yes**, and its absence is fully explained by the unapplied revision. |
| Is the archive evidence intact? | **Yes** — hash matches the M1-A record. |
| Could duplicates explain a +882 surplus here? | **No** — structural uniqueness constraint, and no rows to duplicate. |

### Partially resolved

| Question | What is known | What is not |
|---|---|---|
| The 882-row discrepancy | It is a document-to-document delta, not a database delta. The current DB contributes an additional, much larger 1,731,456-row gap. | Its composition (+1,211 / −329) cannot be decomposed without the source population. |
| Why the dataset is absent | Structural evidence (0-page heap, unused sequence, no row before 2026-09-25) shows this instance was never populated with derivative prices. | Whether the instance was recreated, restored, or truncated, and when. |

### Unresolved

| Question | Blocker |
|---|---|
| The 576 participant-OI rows | 0 rows, never-used sequence, no producer query identified. |
| Whether the historical figures were ever a true database readout | No identity, SQL, timestamp, or artefact ties them to any database. |
| Production (`aistock`) contents | Out of scope by explicit prohibition. No claim is made about production data. |
| Whether other local databases (`test`, `college_cert_sys_staging`) hold a copy | Not authorized to connect; see §14. |

---

## 13. Limitations and remaining blockers

1. **The comparison population is absent.** No read-only query against this database can explain
   the 882 rows, because the rows required to explain them do not exist. Further database work in
   this repository has no path to an answer.
2. **Truncation cannot be excluded from metadata alone.** `TRUNCATE … RESTART IDENTITY` would
   mimic a never-populated table. Ruling it out needs server logs, not database queries.
3. **Explicit-`id` inserts were not ruled out.** The application's import path was not traced in
   M1-B to confirm it always uses the sequence. This is a residual alternative explanation for a
   NULL sequence.
4. **`pg_stat` counters are unusable** in this cluster (§8.3) and were excluded rather than used.
5. **Only one database was queried.** `test` (34 MB) and the other local databases were catalogued
   but never opened; a copy of the historical dataset could exist there. This is the single most
   valuable remaining lead and it requires new authorization.
6. **Production is untouched and unknown.** Nothing here supports or refutes any production claim.
7. **The historical claims have no provenance.** No database identity, SQL text, execution
   timestamp, or raw output accompanies the 1,732,338 / 1,703,830 / 28,508 figures. They must
   remain labelled as claims.
8. **A superuser role was used.** Read-only was enforced in-session, which is sound for this
   investigation but weaker than privilege-based enforcement.

---

## 14. Recommendation concerning M2

**M2 must remain on HOLD.** Nothing in M1-B clears a blocker; one blocker is materially sharpened.

| Blocker | Status after M1-B |
|---|---|
| Historical counts lack verified database identity | **Still open.** Confirmed: they cannot be tied to the current database at all. |
| 882-row discrepancy unresolved | **Still open**, and now known to be unresolvable from this database. |
| Q4-2025 per-file integrity | **Still open** (M1-A carry-over; not in M1-B scope). |
| Historical dataset absent from dev | **Sharper.** Structural evidence that the current instance never held it. |
| Migration gap | **Resolved** as a version fact; not a blocker. |
| Production scope unknown | **Still open**; unchanged. |

**Why M2 should not proceed:** M2 would consume or extend historical derivative data. The origin,
provenance, and completeness of the historical figures are unverified, the dataset itself is absent
from development, and there is no synchronization-history table to establish import lineage. Any
M2 work would build on a population that cannot be shown to exist or to have existed.

**Suggested next authorizations, in priority order:**

1. Query the `test` database read-only for `derivative_daily_prices` / contract counts — the only
   remaining local candidate for the historical dataset.
2. Read PostgreSQL server logs (`pg_log`) for `TRUNCATE`/`DROP`/restore activity around
   2026-09-07 … 2026-09-26 — settles the truncation-vs-recreation question.
3. Trace the import path (`app/cli/import_nse.py` and the derivative importers) to confirm whether
   `derivative_daily_prices` inserts always use the sequence.
4. Decide whether the historical figures are to be treated as **retired claims** and re-derived
   from the archive (which is verified) rather than pursued further.

---

## 15. Evidence file inventory

All new; none pre-existing; all untracked.

| File | Purpose |
|---|---|
| [docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md](OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md) | This report |
| [docs/data-audit/m1b/run_m1b_readonly.py](data-audit/m1b/run_m1b_readonly.py) | The single read-only runner implementing every stage; enforces and asserts read-only |
| [docs/data-audit/m1b/baseline_queries.sql](data-audit/m1b/baseline_queries.sql) | Verbatim SQL for every query executed |
| [docs/data-audit/m1b/database_identity_receipt.json](data-audit/m1b/database_identity_receipt.json) | Gate 1 receipt: server, database, user, read-only proof, databases present |
| [docs/data-audit/m1b/schema_inventory.json](data-audit/m1b/schema_inventory.json) | Tables, columns, types, indexes, constraints |
| [docs/data-audit/m1b/baseline_results.json](data-audit/m1b/baseline_results.json) | Gate 2/3 counts, type splits, duplicates, date ranges |
| [docs/data-audit/m1b/migration_state.json](data-audit/m1b/migration_state.json) | Alembic revision and sync-history table presence |
| [docs/data-audit/m1b/forensics_results.json](data-audit/m1b/forensics_results.json) | Sequence state, physical sizes, statistics counters, duplicate keys |
| [docs/data-audit/m1b/context_results.json](data-audit/m1b/context_results.json) | Scope search across schemas, sequence controls, equity-table context |
| [docs/data-audit/m1b/reconciliation_results.json](data-audit/m1b/reconciliation_results.json) | Claim / archive / measured comparison with deltas |

Every JSON artefact records the exact SQL, parameters, execution timestamp (UTC), row count, and
the database identity for each query under `queries_executed`.

**Artefacts deliberately not created:**

- `daily_type_counts.csv` — the by-date/by-type source table has 0 rows. Writing a header-only CSV
  would be an empty placeholder; the true empty result is recorded explicitly in
  `baseline_results.json` (`prices_by_instrument_type: []`, `prices_window_by_type: []`) and
  `reconciliation_results.json`.
- `investigation_notes.md` — folded into §8, which carries the observations/calculations/
  hypotheses separation the notes file would have duplicated.

**Immutable M1-A artefacts:** untouched; hashes re-verified in §16.

---

## 16. Final safety verification

| Check | Result |
|---|---|
| Branch unchanged | `checkpoint/sprint19-20-20260822` ✓ |
| HEAD unchanged | `b2df61f1d56c5a5032787c96678326d0a73f6173` ✓ |
| Pre-existing modified tracked files preserved | 21 files, unmodified ✓ |
| Pre-existing untracked entries preserved | 311 entries, unmodified ✓ |
| Staged / committed / pushed | none ✓ |
| Reset / clean / stash / checkout / branch switch | none ✓ |
| Database writes (INSERT/UPDATE/DELETE/TRUNCATE) | **none issued** ✓ |
| DDL / schema change | **none issued** ✓ |
| Migrations run | **none** ✓ |
| Imports / backfills / syncs / schedulers / capture | **none** ✓ |
| Provider / network calls | **none** ✓ |
| Production connections | **none** ✓ |
| Connection disposition | every connection closed after `ROLLBACK` ✓ |
| Read-only verified | `transaction_read_only = on` on every stage ✓ |
| Secrets in evidence | none — password never read into output, host/user/db only ✓ |
| M1-A artefact hashes | unchanged (see below) ✓ |

Immutable artefact hashes, before and after (identical):

```
3aecc510d670c4a68938de429f10e494cec4b1acf8db803294b96fa6b22132ea  docs/OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md
425b02264bdc19423f48e452496072b093bd600710b6a8f52fbadf2b0f9ed14e  docs/data-audit/recount_result.json
722a712967efca3df70c9e9dfb41253968270ed90f496dc343bf42d166781a71  docs/data-audit/window_search_result.json
3e19bb91deb1b076c66ef70abe013a9e7b532de732a5a94b7a3bdd7f9c46dfba  docs/data-audit/recount.py
766e5bde98adc6a54556c79f30c63484be0469a95d7ca8cd242bc213013a4782  docs/data-audit/window_search.py
977304397b2b613244e680f587d3baa40090d204304a0035f14d5b263d6d78ee  docs/OPT_DATA_RECONCILIATION_M1_INDEPENDENT_AUDIT.md
```

---

## 17. Explicit actions NOT performed

- No connection to production, or to any database other than `ai_stock_scanner`.
- No connection to `test`, `college_cert_sys_staging`, `hubdb`, `postgres`, `template1`
  (catalogued only, never opened).
- No `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `COPY`, or DDL of any kind.
- No Alembic upgrade, downgrade, stamp, or revision generation.
- No imports, backfills, synchronizations, scheduler runs, capture jobs, provider or broker calls.
- No modification of application code, configuration, `.env`, schema, or archive data.
- No modification, move, rename, or deletion of any prior report or audit artefact.
- No `git add`, `commit`, `push`, `reset`, `clean`, `stash`, `checkout`, or branch switch.
- No package installation or environment change.
- No password or secret printed, logged, or written to any file.

---

## 18. Final statement

The current local development database `ai_stock_scanner` was identified unambiguously, queried
strictly read-only, and measured. It contains 2,781 option contract masters, zero futures
contracts, zero derivative daily-price rows, and zero participant-position rows.

The historical figures of 1,732,338 / 1,703,830 / 28,508 cannot be reproduced here, and the
evidence now goes beyond "the rows are missing": the `derivative_daily_prices` heap has never been
allocated and its identity sequence has never been used. Against that measured state the reported
882-row residual is not a database discrepancy at all — it is a difference between a UI-page
counter and an archive recount, neither of which is tied to a verified database identity.

The migration gap is explained without invoking data loss: the database is one revision behind, and
that revision is precisely the one that creates the absent `derivative_contract_sync_runs` table.

**Verdict: `PARTIALLY RECONCILED`.** Identity, schema, current counts, and the migration gap are
resolved. The 882-row composition, the 576 participant rows, and all production state remain
unresolved. **M2 remains on HOLD.**
