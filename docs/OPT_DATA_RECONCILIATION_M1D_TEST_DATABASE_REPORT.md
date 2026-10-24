# OPT Data Reconciliation — M1-D Test Database Provenance Check

**Investigation ID:** `M1D-TESTDB-20261024`
**Milestone:** M1-D (narrowly scoped read-only inspection of the local `test` database)
**Investigation date:** 2026-10-24, ~16:12 IST
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Target:** local PostgreSQL database `test` on `localhost:5432`
**Mode:** strictly read-only. No writes, DDL, migrations, restores, or Git mutations.
**Predecessor:** [docs/OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md](OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md)

---

## 1. Verdict

# `NO MATCH FOUND IN TEST DATABASE`

The local `test` database is a **structurally complete but entirely unpopulated** application
schema. Across all **69** public base tables, exactly **two** contain any rows: `alembic_version`
(1 row) and `scan_definitions` (36 rows). Every derivative table — `derivative_daily_prices`,
`derivative_contracts`, `derivative_participant_positions`, `derivative_contract_metadata_histories`,
`derivative_lot_size_histories`, `fii_derivative_statistics`, `derivative_contract_sync_runs` — holds
**0 rows**. So do `daily_prices` and `stocks`.

It therefore contains **no evidence whatsoever** of the historical 1,732,338 / 1,703,830 / 28,508 /
576 / 75,962 population, and cannot be the database those figures described. This is not a
data-loss finding: `test` is the pytest target database ([`tests/conftest.py:46-107`](../tests/conftest.py#L46)),
created by migration and used as an empty fixture; it was never intended to hold production-like
history.

**Two substantive by-products:**

1. **The local-database search is now closed.** M1-C established that `ai_stock_scanner` never held
   the population; M1-D eliminates the only other plausible local candidate. No local database on
   this server contains it. Combined with the size argument (34 MB cannot hold 1.73M rows), the
   provenance question behind 1,732,338 **cannot be answered from any local database**.
2. **M1-B's migration conclusion is empirically confirmed.** `test` is at Alembic revision
   **`e5f6a7b8c9d0`** — the repository head — and *does* contain
   `derivative_contract_sync_runs`. That head migration has therefore been applied successfully
   somewhere, proving the "single head, one revision behind" explanation of the absent table in
   `ai_stock_scanner` (M1-B §10). This also definitively retires the M1-B-audit's claim of
   "inconsistent migration heads."

**M2 remains on HOLD.**

---

## 2. Authorization and safety boundaries

**Authorized for this task:** connect only to the existing local database `test`; verify identity and
read-only state before querying application data; run read-only catalog and count queries; write
evidence and a report under `docs/`.

**Enforced in code**, not merely claimed — see
[docs/data-audit/m1d/capture_test_readonly.py](data-audit/m1d/capture_test_readonly.py):

- `dbname="test"` is a hard-coded constant; the database name parsed from `.env` is read into
  `_configured_db` and **never used**.
- Every connection opens with `con.set_session(readonly=True, autocommit=True)`.
- The script asserts `SHOW transaction_read_only == 'on'`, `current_database() == 'test'`, and
  `inet_server_addr() ∈ {::1, 127.0.0.1}` **before** touching any application table, and returns
  exit code 2 without further queries if any check fails.
- Only `SELECT`, `SHOW`, and `to_regclass`/catalog reads are issued. There is no `INSERT`,
  `UPDATE`, `DELETE`, `TRUNCATE`, `COPY`, or DDL anywhere in the script.
- The `.env` password is parsed in memory and passed as a driver argument. It is never printed,
  logged, or written to an artifact. Verified post-run: **zero** occurrences of the credential
  string in any file under `docs/data-audit/m1d/`.

**Not performed:** connection to `ai_stock_scanner`, production `aistock`, or any other database;
inspection of PostgreSQL server logs, backup directories, S3, or remote hosts; migrations or Alembic
commands; importers, backfills, sync jobs, schedulers, capture jobs; broker/provider calls;
database creation, restore, copy, move, or deletion; Git stage/commit/push/reset/clean/stash/
checkout/branch-switch; overwriting any existing report or artifact.

**No collision.** `docs/OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md` and `docs/data-audit/m1d/`
did not exist at the start of the task; both were created fresh.

---

## 3. Database identity and read-only verification

Evidence: [docs/data-audit/m1d/test_db_identity_receipt.json](data-audit/m1d/test_db_identity_receipt.json)

| Check | Observed | Required | Result |
|---|---|---|---|
| `SHOW transaction_read_only` | `on` | `on` | **PASS** |
| `current_database()` | `test` | `test` | **PASS** |
| `inet_server_addr()` | `::1/128` (IPv6 loopback) | loopback | **PASS** |
| `inet_server_port()` | `5432` | — | recorded |
| `current_user` / `session_user` | `postgres` / `postgres` | — | recorded |
| `server_version` | `18.4` (`server_version_num` 180004) | — | recorded |
| `version()` | `PostgreSQL 18.4 on x86_64-windows, compiled by msvc-19.44.35227, 64-bit` | — | recorded |
| `data_directory` | `C:/Program Files/PostgreSQL/18/data` | local install | **PASS** |
| `timezone` | `Asia/Calcutta` | — | recorded |
| `now()` at receipt | `2026-10-24 16:12 IST` | — | recorded |
| Database size | `34 MB` | — | recorded |
| Statements executed | **163** | — | 0 errors |

**Application-table queries began only after `gate.pass == true` was recorded in the receipt.**

Same PostgreSQL cluster as M1-B/M1-C (`postmaster` on `C:/Program Files/PostgreSQL/18/data`), but a
**different database** from `ai_stock_scanner`. Credentials were the same role; the database name is
the scope boundary, and it was hard-enforced.

---

## 4. Tables and queries examined

Evidence: [docs/data-audit/m1d/test_db_schema_inventory.json](data-audit/m1d/test_db_schema_inventory.json),
[docs/data-audit/m1d/test_db_queries.sql](data-audit/m1d/test_db_queries.sql),
[docs/data-audit/m1d/test_db_results.json](data-audit/m1d/test_db_results.json)

### 4.1 Scope

| Item | Value |
|---|---|
| Non-system schemas | **1** — `public` only |
| Public base tables | **69** |
| Tables with `count(*) > 0` | **2** (`alembic_version` = 1, `scan_definitions` = 36) |
| Count errors | **0** |
| Total statements | **163**, all recorded verbatim with UTC timestamps |

The schema was discovered by catalog queries first (`information_schema.tables`,
`pg_class`/`pg_namespace`, `pg_attribute`, `pg_constraint`, `pg_indexes`) and every query was
constrained to columns proven to exist. **No query failed**, so no schema assumption was violated.

### 4.2 Tables in scope and columns verified

All eight derivative-relevant tables **exist**, and each was inventoried for columns, constraints,
and indexes before any data query:

| Table | Exists | Rows | Columns captured |
|---|---|---|---|
| `derivative_daily_prices` | yes | **0** | yes |
| `derivative_contracts` | yes | **0** | yes |
| `derivative_participant_positions` | yes | **0** | yes |
| `derivative_contract_metadata_histories` | yes | **0** | yes |
| `derivative_lot_size_histories` | yes | **0** | yes |
| `fii_derivative_statistics` | yes | **0** | yes |
| `derivative_contract_sync_runs` | **yes** | **0** | yes |
| `alembic_version` | yes | **1** | yes |

Plus context tables: `daily_prices` (**0**), `stocks` (**0**), `option_chain_snapshots` (**0**),
and the `option_chain_quotes*` partitions (**0**).

### 4.3 Queries executed (grouped)

- **Identity/gate (13):** `version()`, `server_version`, `server_version_num`, `data_directory`,
  `timezone`, `now()`, `pg_database_size`, `current_database`, `current_user`, `session_user`,
  `inet_server_addr`, `inet_server_port`, `transaction_read_only`.
- **Schema (69 counts + 8 inventories):** `count(*)` on every public table; column/constraint/index
  dumps for the 8 derivative-relevant tables + `alembic_version` + `daily_prices` +
  `option_chain_snapshots`; cross-schema relation search for `%deriv%`, `%particip%`, `%fii%`,
  `%option%`, `%alembic%`.
- **Targeted derivative queries (~40):** totals; `instrument_type` distribution; `source_name`
  distribution; `created_at` by day; `expiry_date` distribution; distinct symbols/expiries/
  underlyings; `trade_date` min/max, distinct dates, distinct contracts; in/out of the
  2026-06-08…2026-08-10 window; rows by year; orphan prices; participant rows by `report_date` and
  by `dataset_type` × `instrument_bucket`; duplicate checks on real keys
  (`idempotency_hash`; `(contract_id, trade_date)`; `(report_date, dataset_type, participant_type,
  instrument_bucket)`).
- **Forensics (2):** sequence state for 5 sequences; `relpages`/`reltuples`/heap bytes for 6 tables.

### 4.4 Duplicate analysis

Executed against real constraints, not assumed keys:

| Check | Groups found |
|---|---|
| duplicate `idempotency_hash` in `derivative_contracts` | 0 |
| duplicate `(contract_id, trade_date)` in `derivative_daily_prices` | 0 |
| duplicate `(report_date, dataset_type, participant_type, instrument_bucket)` | 0 |

These are vacuous on an empty table and are recorded as such — they are not evidence about the
historical population.

---

## 5. Actual counts and date ranges

### 5.1 Derivative tables

| Table | `count(*)` | Date range |
|---|---:|---|
| `derivative_daily_prices` | **0** | `min(trade_date)` = NULL, `max` = NULL; 0 distinct dates; 0 distinct contracts |
| `derivative_contracts` | **0** | `min/max(created_at)` = NULL |
| `derivative_participant_positions` | **0** | `min/max(report_date)` = NULL |
| `derivative_contract_metadata_histories` | **0** | — |
| `derivative_lot_size_histories` | **0** | — |
| `fii_derivative_statistics` | **0** | — |
| `derivative_contract_sync_runs` | **0** | — |

Composition queries on these tables all returned **empty sets**, not errors:

- `contracts_by_instrument_type` → `[]` (no `OPTION`, no `FUTURE`)
- `ddp_by_instrument_type` → `[]`
- `participant_by_report_date` → `[]`
- `participant_by_dataset_bucket` → `[]`
- `ddp_in_45_session_window` → `0`; `ddp_outside_45_session_window` → `0`
- `ddp_rows_by_year` → `[]`
- `ddp_orphans` → `0`

### 5.2 Physical forensics — every sequence unused, every heap unallocated

| Sequence | `last_value` | `start_value` |
|---|---|---|
| `derivative_daily_prices_id_seq` | **NULL (never used)** | 1 |
| `derivative_contracts_id_seq` | **NULL (never used)** | 1 |
| `derivative_participant_positions_id_seq` | **NULL (never used)** | 1 |
| `derivative_contract_sync_runs_id_seq` | **NULL (never used)** | 1 |
| `daily_prices_id_seq` | **NULL (never used)** | 1 |

| Table | `relpages` | `reltuples` | heap bytes |
|---|---:|---:|---:|
| `derivative_daily_prices` | **0** | **-1** (never analyzed) | 0 |
| `derivative_contracts` | **0** | **-1** | 0 |
| `derivative_participant_positions` | **0** | 0 | 0 |
| `derivative_contract_metadata_histories` | **0** | **-1** | 0 |
| `derivative_contract_sync_runs` | **0** | **-1** | 0 |
| `daily_prices` | **0** | **-1** | 0 |

Compared with `ai_stock_scanner`, where some sequences *had* advanced (`daily_prices_id_seq` = 105,459;
`derivative_contracts_id_seq` = 2,801), `test` shows the signature of a database that has **never had
a single row inserted through any default sequence**. Its 34 MB consists entirely of the 69 tables'
structure, indexes, and the two seed rows.

### 5.3 The only populated rows

| Table | Rows | Interpretation |
|---|---:|---|
| `alembic_version` | 1 | revision marker only |
| `scan_definitions` | 36 | seed/reference data; unrelated to derivatives |

---

## 6. Comparison with each historical claim

| Historical claim | `test` measurement | Delta | Match? |
|---|---:|---:|---|
| Derivative daily prices **1,732,338** | **0** | −1,732,338 | **NO** |
| Options **1,703,830** | **0** (0 contracts, 0 prices) | −1,703,830 | **NO** |
| Futures **28,508** | **0** (0 contracts of type `FUTURE`) | −28,508 | **NO** |
| Participant OI **576** | **0** | −576 | **NO** |
| Contract masters **75,962** | **0** | −75,962 | **NO** |

### 6.1 Corroboration attempt — none available

The task correctly notes that a matching total alone would not prove identity. Here no total matches
at all, and **every** corroborating avenue returns null:

| Corroborating dimension | `test` result |
|---|---|
| Trade-date range | NULL (no dates) |
| Instrument classification (`OPTION` / `FUTURE`) | absent — 0 rows |
| Participant row geometry (24 dates × 24 rows) | absent — 0 rows |
| Import/sync history (`derivative_contract_sync_runs`) | table exists, **0 rows** |
| Contract creation timestamps | NULL |
| Source provenance (`source_name`, `source_reference`) | absent — 0 rows |

There is therefore **no partial match, no residue, and no trace**. Even the participant-OI figure,
which M1-C showed decomposes cleanly as 24 × 24, has left nothing behind here.

### 6.2 What this rules in and out

- **Rules out** the hypothesis that `test` holds a copy, snapshot, or partial remnant of the
  historical population. M1-B §13.5 flagged this as "the single most valuable remaining lead"; it is
  now closed.
- **Rules out** `test` as the database the 2026-09-03 UI (`127.0.0.1:8001`) read. A pytest fixture
  database would not have rendered 75,962 contract masters.
- **Does not rule out** that the population existed somewhere — only that no local database on this
  server currently holds it (M1-C: `ai_stock_scanner` never held it; M1-D: `test` never held it).
- **Does not establish** that the data was lost or destroyed. No backup was inspected (prohibited),
  and production remains unexamined.

---

## 7. Import provenance

| Question | Finding |
|---|---|
| Does `derivative_contract_sync_runs` exist in `test`? | **Yes** — unlike `ai_stock_scanner`, where it is absent |
| Does it contain any rows? | **No — 0** |
| Any import jobs recorded? | `import_jobs` is one of the 67 empty tables (0 rows) |
| Any sync-run provenance for derivatives? | **None** |

Even in the database that has the synchronization-history table, there is **no import lineage** —
because there was never an import. Import provenance for the historical population remains
**UNVERIFIED**, and the table that could have provided it was never applied to the database where
history might have been expected (`ai_stock_scanner`).

---

## 8. Migration revision and schema differences

| Item | `test` | `ai_stock_scanner` |
|---|---|---|
| `alembic_version` | **`e5f6a7b8c9d0`** (repository **head**) | `d4e5f6a7b8c1` (one behind head) |
| `derivative_contract_sync_runs` | **present** (0 rows) | **absent** |
| `option_chain_quotes_2025_04` | **present** | absent |
| Public base tables | **69** | 66 |
| Total rows, all tables | 38 | ≈ 105,000+ |

### 8.1 This empirically confirms M1-B §10

M1-B concluded that the absence of `derivative_contract_sync_runs` in `ai_stock_scanner` is fully
explained by one unapplied revision, `e5f6a7b8c9d0`, whose parent is `d4e5f6a7b8c1` (the revision
that database reports). `test` demonstrates that this exact head revision **has been applied
successfully** on this cluster, producing that table and no others. The explanation is therefore
verified by execution, not merely by reading the migration graph.

### 8.2 This retires the M1-B-audit's migration objection

The (never-persisted) M1-B independent audit alleged "inconsistent migration heads". The repository
graph has exactly one head; the database that is at head reports it; the database that is behind is
behind by exactly that one revision. The claim is **CONTRADICTED** on three independent grounds.

### 8.3 Schema drift worth noting

`test` has **3 more** public tables than `ai_stock_scanner` (69 vs 66), including
`option_chain_quotes_2025_04`. The two databases are therefore not merely at different revisions —
they carry structurally different objects. Any future comparison between them must account for
this, and neither can be assumed representative of the other's schema.

**No Alembic command was run. No migration was applied. The working-tree graph was not assumed to
have been applied to `test` — it was read from `alembic_version` in the database itself.**

---

## 9. Limitations and unresolved questions

1. **Absence here does not prove absence everywhere.** M1-D closes the *local* search only.
   Production `aistock` (remote, unauthorized) and any off-machine backup remain unexamined.
2. **No backup was inspected** — prohibited by this task. Whether a recoverable copy exists is still
   `UNVERIFIED`, not disproven.
3. **PostgreSQL server logs were not read** — prohibited. The `TRUNCATE … RESTART IDENTITY` and
   explicit-`id`-insert caveats from M1-B/C remain open for `ai_stock_scanner` (they are moot for
   `test`, which has no prior data at all to explain).
4. **A 34 MB database cannot hold 1.73M derivative rows** (~100 MB+ of heap before indexes), so even
   a priori, `test` was an implausible host. The measurement confirms the inference rather than
   replacing it.
5. **The provenance of 1,732,338 is unchanged by M1-D.** M1-C's finding stands: it is a derived sum
   (`28,508 + 1,703,830`) that entered history as an *expected* value in
   `scripts/g8_pre_repair.py:401` and was echoed as a "Live Count … PASS" in
   `docs/AI_HANDOFF.md:24`. M1-D adds no evidence for or against its ever being a real query result.
6. **Which database the 2026-09-03 UI read remains unidentified.** It is now known *not* to be
   either local database.
7. **`test` is a pytest fixture database.** Its emptiness is expected, not anomalous — but this
   should be recorded so that no future investigation re-treads this path as a live lead.

---

## 10. M2 decision

**M2 remains on HOLD.**

| Blocker | Status after M1-D |
|---|---|
| Current dev DB state verified | **Closed** (M1-B, M1-C, re-confirmed) |
| Historical DB identity established | **Still open** — and now narrowed to: neither local database |
| Local-copy hypothesis for the historical population | **Closed — eliminated** |
| 882-row composition | **Still open**; unresolvable without the source population |
| 576 provenance | **Partially established** (composition only; M1-C) |
| Migration state consistent | **Closed** — verified from both DBs and the repo graph |
| Recoverability | **Unverified** — no artifact found; backups/logs prohibited |
| Production scope | **Unknown** — unauthorized, no claim made |

M1-D **closes** the local-copy lead and **confirms** the migration explanation. It changes nothing
about the availability of the historical dataset: no local database contains it, and the only
reproducible derivative population in this repository remains the **archive-derived** recount
(`docs/data-audit/recount_result.json`, SHA-256 `425b0226…`: 1,731,456 / 1,702,619 / 28,837 for the
45-session window).

This investigation authorizes **no** data recovery, import, migration, production access, or M2
implementation.

---

## 11. Integrity confirmation

| Check | Result |
|---|---|
| Database writes (INSERT / UPDATE / DELETE / TRUNCATE / COPY / DDL) | **none issued** |
| Migrations or Alembic commands | **none run** |
| Databases connected to | **exactly one: `test`** |
| `ai_stock_scanner` connection this task | **none** |
| Production `aistock` / remote hosts | **none** |
| Backup or PostgreSQL log inspection | **none** |
| Backup created, restored, copied, moved, deleted or modified | **none** |
| New database created | **none** |
| Imports, backfills, syncs, schedulers, broker/provider calls | **none** |
| Git stage / commit / push / reset / clean / stash / checkout / branch-switch | **none** |
| Existing report or evidence file overwritten | **none** |
| Credentials printed or persisted | **none** — verified: zero matches for the credential string under `docs/data-audit/m1d/` |
| Statements executed against `test` | 163, all `SELECT`/`SHOW`/catalog, 0 errors |
| Read-only enforcement | session (`readonly=True`) **and** verified `transaction_read_only = on` |
| Connection disposition | closed after capture |
| Files created by this task | `docs/OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md`; `docs/data-audit/m1d/` (5 files) — all new, all untracked |
| Branch / HEAD | `checkpoint/sprint19-20-20260822` / `b2df61f1d56c5a5032787c96678326d0a73f6173` — unchanged |
| M2 status | **HOLD** |

### Evidence files

| File | Contents |
|---|---|
| [capture_test_readonly.py](data-audit/m1d/capture_test_readonly.py) | the single read-only capture script, with the safety gate in code |
| [test_db_identity_receipt.json](data-audit/m1d/test_db_identity_receipt.json) | redacted identity receipt, gate results, every statement with timestamp |
| [test_db_schema_inventory.json](data-audit/m1d/test_db_schema_inventory.json) | 69 tables; columns, constraints and indexes for every relevant table |
| [test_db_queries.sql](data-audit/m1d/test_db_queries.sql) | all 163 statements verbatim, in execution order |
| [test_db_results.json](data-audit/m1d/test_db_results.json) | machine-readable raw results, row counts for all 69 tables, per-query timestamps |
