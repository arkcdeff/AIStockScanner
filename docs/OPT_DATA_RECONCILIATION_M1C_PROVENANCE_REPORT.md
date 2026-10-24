# OPT Data Reconciliation — M1-C Historical State and Evidence Provenance Investigation

**Investigation ID:** `M1C-PROVENANCE-20261024`
**Milestone:** M1-C (historical database state and evidence provenance)
**Investigation date:** 2026-10-24 (IST)
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Mode:** read-only. No writes, no DDL, no migrations, no restores, no Git mutation.
**Deliverable:** this report only.

---

## 1. Executive summary and verdict

# `PARTIALLY ESTABLISHED`

The historical counts are **not fabricated**. Traced to their earliest retained appearances, they
resolve into three different evidentiary classes, and only one of them has meaningful provenance:

| Count | Earliest appearance | Class | Provenance |
|---|---|---|---|
| 1,732,338 | `scripts/g8_pre_repair.py:401` — **committed** in `0027c86` (2026-09-09) | **Derived sum** (28,508 + 1,703,830) asserted as an *expected* invariant, echoed as a "Live Count … PASS" | **PARTIALLY ESTABLISHED** |
| 1,703,830 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03) | Rendered UI counter on `127.0.0.1:8001` | **PARTIALLY VERIFIED** |
| 28,508 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03) | Rendered UI counter on `127.0.0.1:8001` | **PARTIALLY VERIFIED** |
| 576 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03); composition in `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112` | Rendered UI counter **plus a documented 24 × 24 decomposition** | **PARTIALLY ESTABLISHED** |

Four findings dominate this investigation.

**[FINDING 1 — the M1-B report is accurate; the "discrepancy" was a misdiagnosis.]**
The task premise states that M1-B reported the 1,732,338 poputation as current database state and
that an independent audit contradicted it. **The evidence does not support that premise.** M1-B
labels these figures `[CLAIM]` (§2), lists them in its reconciliation table under the column header
*"Historical claim (no DB identity)"* with `current_database_45_session = 0` (§11), and its raw
evidence file records the claim object as `"database_identity_recorded": null, "sql_recorded": null`
([reconciliation_results.json](data-audit/m1b/reconciliation_results.json)). M1-B's own verdict was
`PARTIALLY RECONCILED`, and it stated explicitly that the counts *cannot* be reproduced. The
independent audit's core measurements (0 price rows, 2,781 option contracts, unused sequence,
absent `derivative_contract_sync_runs`, revision `d4e5f6a7b8c1`) **agree with M1-B row for row**.
There was no numerical conflict to reconcile.

**[FINDING 2 — the independent audit was never persisted, and it contained two factual errors.]**
`docs/OPT_DATA_RECONCILIATION_M1B_INDEPENDENT_AUDIT.md` **does not exist**; no `*M1B*INDEPENDENT*`
artifact exists anywhere in the working tree. The "independent audit" therefore exists only as a
session transcript, not as citable retained evidence. Its two substantive criticisms are
contradicted by direct inspection:

- It alleged migration-head inconsistency ("empty-head set … not a consistent head"). **False.** The
  graph has exactly one head (`e5f6a7b8c9d0`), one root (`0001`), 28 revisions, no missing parents,
  and no unreachable revisions. M1-B's §10 claim is correct.
- It framed M1-B as presenting staged counts as measurements. **Contradicted** by Finding 1.

One independent-audit observation *was* correct and is *not* in M1-B: three of the migrations —
including the head `e5f6a7b8c9d0` — are **untracked** in Git. See §7.

**[FINDING 3 — 1,732,338 is a sum, not an observation, and it was an expectation before it was a
"live count".]** `BROWSER_INSPECTION_REPORT.md` never states 1,732,338. It states the two components
(28,508 futures + 1,703,830 options). The total appears in `scripts/g8_pre_repair.py:401` as a
hardcoded **`expected_values`** entry in the pre-G.8 verification harness, committed 2026-09-09, and
is then echoed into `docs/AI_HANDOFF.md:24` as `Expected 1,732,338 | Live Count 1,732,338 | PASS` —
a table whose "live" column is the output of that same assertion, and which records **no database
name, host, SQL, or timestamp**. A passing expectation and a measurement are not the same evidence.

**[FINDING 4 — 576 is no longer unexplained.]** `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112`
(tracked, committed) decomposes it: *"Participant OI currently has 576 rows across 24 stored report
dates … Each date has 24 normalized rows."* `24 × 24 = 576` — verified. The same document records
participant **volume** at 504 rows / 21 dates (`24 × 21 = 504`), which is internally consistent. It
also names two contaminated dates (`2026-08-09`, a Sunday; `2026-08-21`, outside the audit window).
This upgrades 576 from "no provenance" to a documented, arithmetically coherent composition — while
still lacking a query, a timestamp, and a database identity.

**The current database state is independently re-confirmed in this investigation** (read-only,
`transaction_read_only = on`): `derivative_daily_prices` = **0** rows, `alembic_version` =
`d4e5f6a7b8c1`, `derivative_contract_sync_runs` absent.

**No backup or snapshot exists** in the repository, and no local database on this server is large
enough to hold a 1.73-million-row derivative table. Recoverability is therefore **UNVERIFIED**, and
the historical population is **not** established as lost — only as absent from the one database
examined.

**M2 remains on HOLD.**

---

## 2. Scope and safety restrictions

**Performed:** read-only inspection of repository documents, JSON evidence, SQL, scripts and
migration files; read-only Git history queries (`log`, `ls-files`, `rev-parse`, `status`,
`--error-unmatch`); `grep`/`find` over the working tree; one read-only database connection to the
already-identified local development database `ai_stock_scanner` to re-confirm four values.

**Explicitly not performed, and prohibited by the task:** database writes of any kind; DDL;
`ALTER`/`TRUNCATE`; migrations (`upgrade`, `downgrade`, `stamp`, `revision`); imports, syncs,
backfills, schedulers; provider/broker/network calls; connection to production `aistock` or any
remote database; connection to `test`, `college_cert_sys_staging`, `hubdb`, `postgres`,
`template1`; restore, move, copy, delete or modification of any backup; creation of any database;
any Git staging, commit, push, reset, clean, stash, checkout or branch switch; overwriting any
existing report or evidence artifact; execution of any potentially destructive script.

**Secret handling:** the database password appears in `.env` and was used only to open the
connection. It is not reproduced in this report. No token, key, or credential value is disclosed.

**Method note on the prior session.** One prior database connection (in the immediately preceding
session) was made with `autocommit = True` and without an explicit read-only transaction; it issued
only `SELECT`/`SHOW`. The connection made in *this* investigation used
`set_session(readonly=True, autocommit=True)` and verified `SHOW transaction_read_only` = `on`
before querying. No write statement has been issued in either session.

---

## 3. Files and Git history examined

### 3.1 Existence check of every file named in the task

| Path | Status | Tracked? | Size / mtime |
|---|---|---|---|
| `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md` | **present** | untracked | 61,370 B / Oct 23 17:27 |
| `docs/OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md` | **present** | untracked | 24,436 B / Oct 24 12:18 |
| `docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md` | **present** | untracked | 37,558 B / Oct 24 15:34 |
| `docs/OPT_DATA_RECONCILIATION_M1B_INDEPENDENT_AUDIT.md` | **ABSENT** | — | — |
| `docs/data-audit/m1b/` (14 files) | **present** | untracked | see §3.2 |
| `scripts/g8_pre_repair.py` | **present** | **tracked** | commit `0027c86` |
| `app/services/historical_query.py` | present | tracked | 588 lines |
| migration `e5f6a7b8c9d0` | present | **UNTRACKED** | `migrations/versions/e5f6a7b8c9d0_add_derivative_contract_sync_runs.py` |
| `.env`, `alembic.ini`, `migrations/env.py` | present | tracked (`.env` ignored) | config read; secrets not reproduced |

> **MATERIAL EVIDENCE DEFECT.** The M1-B independent audit named in the task brief was **never
> written to disk**. It exists only in a session transcript and cannot be hash-verified, cited by
> line, or reviewed by a third party. Every statement attributed to it in this report is taken from
> the transcript and is therefore itself `UNVERIFIED` as a durable artifact. This report does **not**
> recreate it — doing so would manufacture evidence.

### 3.2 `docs/data-audit/m1b/` inventory (all mtimes Oct 24 15:31, except noted)

| File | Bytes | Role |
|---|---:|---|
| `run_m1b_readonly.py` | 38,797 | single read-only runner, all stages |
| `baseline_queries.sql` | 14,594 | verbatim SQL |
| `schema_inventory.json` | 469,086 | full schema |
| `context_results.json` | 16,463 | cross-schema scope search |
| `database_identity_receipt.json` | 8,814 | Gate 1 receipt |
| `baseline_results.json` | 9,902 | counts |
| `forensics_results.json` | 8,970 | sequence/physical/statistics |
| `reconciliation_results.json` | 5,289 | claim vs archive vs measured |
| `migration_state.json` | 1,669 | revision + table presence |
| `audit_capture_independent.json` | 9,241 | prior session's independent capture (15:57) |
| `audit_capture_body.py` | 12,216 | prior session's capture script (15:56) |
| `audit_capture_independent.py` | 5,187 | prior session's wrapper (15:54) |
| `run_independent_capture_run_simple.py` | 311 | prior session's runner (15:56) |
| `audit_capture_cmd.bat` | 119 | prior session's stub (15:51) |

**Observation.** Across *both* M1-B and the independent capture, **no artifact anywhere in this
directory contains a query whose result is 1,732,338.** The string 1,732,338 appears in
`reconciliation_results.json` and `run_m1b_readonly.py` **only inside the `historical_claim` /
`target` constants**, never as a `SELECT count(*)` output. This is consistent with M1-B's own
labelling and inconsistent with any claim that M1-B measured it.

### 3.3 Git history examined (read-only)

| Query | Result |
|---|---|
| `git rev-parse HEAD` | `b2df61f1d56c5a5032787c96678326d0a73f6173` (unchanged) |
| `git branch --show-current` | `checkpoint/sprint19-20-20260822` (unchanged) |
| `git log --all -S "1732338"` | **`0027c86` only** — the string was introduced once, in `scripts/g8_pre_repair.py` |
| `git log -1 0027c86` | `0027c8607b3aff1c7e7bf7204bde48d4e166c217`, **2026-09-09 16:07:03 +0530**, *"Complete Stage 2 G.8 official NSE F&O expansion and OI intelligence"* |
| `git log --follow scripts/g8_pre_repair.py` | `0027c86` only |
| `git ls-files docs/ \| grep OPT_DATA` | **none tracked** — every M0/M1/M1-A/M1-B/M1-C audit document is untracked |
| `git log --all -- docs/data-audit/` | **empty** — the entire evidence directory has no committed history |
| `git log --all -- docs/AI_HANDOFF.md` | `0027c86`, `58a9549` (G.7), `b60bdcc` (G.6) — **tracked since 2026-09-09** |
| `git log --all -- docs/19.1J_R5_R2A_R1_RECONCILIATION.md` | `6d71c82` *"Add missing Sprint 19 source and Sprint 20.0 design files"* — **tracked** |
| `git log --all -- migrations/versions/` | no history for `e5f6a7b8c9d0`, `d4e5f6a7b8c1`, `d5e6f7a8b9c0` |

**Consequence.** The M1-A and M1-B audit corpora — including every hash the M1-B report treats as
immutable — are **working-tree-only**. Hash verification (as performed in M1-A/M1-B) proves the
files did not change *since the hash was taken*; it does not prove integrity against a committed
baseline, because no committed baseline exists. Commit `0027c86` proves only that
`scripts/g8_pre_repair.py`, `docs/AI_HANDOFF.md` and `docs/05_Development/PROJECT_STATE.md` contained
those numbers on 2026-09-09. It does **not** prove any database state.

---

## 4. Provenance of each historical count

### 4.1 First appearance and evidentiary class

| Count | First retained appearance | Date | Later repetitions (are *not* corroboration) |
|---|---|---|---|
| 28,508 (futures) | `BROWSER_INSPECTION_REPORT.md:28` | 2026-09-03 | M1:125, M1-A:531, M1-B §11, window_search targets |
| 1,703,830 (options) | `BROWSER_INSPECTION_REPORT.md:28` | 2026-09-03 | M1:126, M1-A, M1-B §11, `window_search.py` targets |
| 1,732,338 (total) | `scripts/g8_pre_repair.py:401` | 2026-09-09 (commit) | `docs/AI_HANDOFF.md:24`, `PROJECT_STATE.md:23`, M0/M1/M1-A/M1-B |
| 576 (participant OI) | `BROWSER_INSPECTION_REPORT.md:28` | 2026-09-03 | `docs/19.1J…:112` (composition), M1:128, M1-A:531, M1-B §9 |
| 75,962 (contracts) | `scripts/g8_pre_repair.py:400` | 2026-09-09 (commit) | `AI_HANDOFF.md:23`, `PROJECT_STATE.md:18,23`, `BROWSER_INSPECTION_REPORT.md:28` |

### 4.2 Assessment per required dimension

| Count | Direct DB measurement? | Query preserved? | Raw output preserved? | DB name / environment recorded? | Execution timestamp? | Reproducible from evidence? |
|---|---|---|---|---|---|---|
| **1,732,338** | **No** — it is `28,508 + 1,703,830` | **No** | **No** | **No** | Only doc/commit dates | **No** |
| **1,703,830** | **No** — UI counter | **No** | **No** (screenshot/page state not retained) | **Partial** — `127.0.0.1:8001` app URL, not a DB name | 2026-09-03 (report date) | **No** |
| **28,508** | **No** — UI counter | **No** | **No** | **Partial** — same | 2026-09-03 | **No** |
| **576** | **No** — UI counter; composition documented | **No** | **No** | **No** | 2026-09-03; composition doc predates it | **Composition yes; count no** |
| **75,962** | **No** — expected value | **No** | **"Live Count" = "Expected"** | **No** | commit date only | **No** |

### 4.3 The 1,732,338 chain, reconstructed

```
pre-G.8 repair (C1–C5, 76,387 → 75,962 contracts)
        │           docs/05_Development/PROJECT_STATE.md:16-24
        ▼
scripts/g8_pre_repair.py:399-410   ← COMMITTED 0027c86, 2026-09-09
   expected_values = {
       "derivative_contracts":      75962,
       "derivative_daily_prices":   1732338,   ← the number enters history here
       "option_lot_size_1":         74902,  ... }
        │                                            verification harness asserts equality
        ▼
docs/AI_HANDOFF.md:20-30  "Post-Hardening Database Counts (Read-Only Certified)"
   | DerivativeDailyPrice | Expected 1,732,338 | Live Count 1,732,338 | PASS |
        │                                            ← "Live Count" is the assertion result
        ▼
BROWSER_INSPECTION_REPORT.md (2026-09-03, 127.0.0.1:8001)
   75,962 masters · 28,508 futures · 1,703,830 options · 576 participant OI
        │      28,508 + 1,703,830 = 1,732,338  ✔ consistent
        ▼
M0 → M1 → M1-A → M1-B   (restate the same numbers; no new measurement)
```

**What this establishes.** The components (28,508 / 1,703,830 / 75,962) cohere as a description of a
genuinely populated local application instance observed through its own UI on 2026-09-03, and they
are arithmetically consistent with the independently committed expectation of 1,732,338 six days
later. Two independent artifacts converging is real corroboration of *a dataset having existed*.

**What this does not establish.** It does not identify **which** database held it. The 2026-09-03
observation is a web UI at `127.0.0.1:8001`; the 2026-09-07 handoff records counts with no
database, host, or SQL. Neither states `ai_stock_scanner`. The inference that the observed instance
was the local `ai_stock_scanner` database is **reasonable but unproven**, and it is currently
**contradicted** by the physical state of the database bearing that name (§5, §6).

### 4.4 The 576 decomposition

`docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112` (tracked):

> "Participant OI currently has 576 rows across 24 stored report dates … Each date has 24
> normalized rows."

Verified: `24 × 24 = 576`. Same document, next paragraph: participant **volume** = 504 rows over 21
dates (`24 × 21 = 504`) — consistent row-geometry. It also flags two non-qualifying dates:
`2026-08-09` (a Sunday) and `2026-08-21` (outside the one-year window ending 2026-08-10), leaving
21 legitimate sessions.

So 576 is a **structurally coherent** figure: 24 dates × 24 participant/bucket rows. What is still
missing is the producing query, the report date of the read, and the database identity — and the
figure as stated includes at least two dates that a session-bounded reconciliation would exclude.
M1-A's earlier judgement that "neither yields 576 by any obvious arithmetic"
(`docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md:538`) is **superseded**: the arithmetic is
24 × 24 and it is documented in a tracked file. That is a correction to M1-A, not to this task's
premise.

---

## 5. M1-B's reported results versus the independent audit

### 5.1 Do they actually disagree?

**No — on every measurable quantity they agree.**

| Quantity | M1-B report | Independent audit | Agreement |
|---|---|---|---|
| `derivative_daily_prices` rows | 0 (§7) | 0 | **agree** |
| `derivative_contracts` rows | 2,781 (§7) | 2,781 | **agree** |
| Contracts classified `FUTURE` | 0 (§7) | 0 | **agree** |
| `derivative_contract_metadata_histories` | 0 (§6) | 0 | **agree** |
| `derivative_lot_size_histories` | 0 (§6) | 0 | **agree** |
| `derivative_participant_positions` | 0 (§9) | 0 | **agree** |
| `fii_derivative_statistics` | 0 (§6) | 0 | **agree** |
| `derivative_contract_sync_runs` | absent (§10) | absent | **agree** |
| PostgreSQL version | 18.4 (§4) | 18.4 | **agree** |
| DB revision | `d4e5f6a7b8c1` (§10) | `d4e5f6a7b8c1` | **agree** |
| 1,732,338 reproducible? | **No** (§11) | No | **agree** |
| Verdict | `PARTIALLY RECONCILED` | `BLOCKED` | *labels differ* |

### 5.2 Where the two diverge, and who is right

| Independent-audit assertion | Evidence | Verdict on the assertion |
|---|---|---|
| "M1-B presented the 1,732,338 count as a current database measurement" | M1-B §2 `[CLAIM]`; §11 column *"Historical claim (no DB identity)"*; `reconciliation_results.json` → `historical_claim.database_identity_recorded = null`, `sql_recorded = null`; no `SELECT count(*)` anywhere returns 1,732,338 | **CONTRADICTED** — misreads M1-B |
| "M1-B does not show that those numbers came from the database it identifies" | True, and M1-B says so itself (§2, §11, §13.7) | **TRUE but not a defect** — it is M1-B's stated limitation, not an unstated error |
| "Migration heads are inconsistent / empty-head set" | Graph: 1 head `e5f6a7b8c9d0`, 1 root `0001`, 28 revisions, 0 missing parents, 0 unreachable | **CONTRADICTED** — M1-B §10 is correct |
| "`e5f6a7b8c9d0` is untracked" | `git ls-files` → untracked (also `d4e5f6a7b8c1`, `d5e6f7a8b9c0`) | **VERIFIED**, and **missing from M1-B** |
| Verdict should be `BLOCKED` | M1-B's Gate 1 passed legitimately; identity is unambiguous | **OVERSTATED** — M1-B's `PARTIALLY RECONCILED` is the better-supported label |

**Conclusion.** The "discrepancy between M1-B and the independent audit" is **not a factual
discrepancy**. M1-B's measurements are sound and its labelling of the historical figures as
unproven claims is exactly right. The independent audit's *data* was right, its *reading of M1-B*
was wrong, and — decisively — it was never written to disk, so it cannot function as retained
evidence. Neither document resolves the underlying question, which is provenance.

---

## 6. Existing backup and artifact inventory

| Candidate | Path | Size / date | Can it establish DB identity + counts? |
|---|---|---|---|
| PostgreSQL dumps in repo | none found | — | **No** |
| `*.dump`, `*.backup`, `*.bak`, `*.sql.gz`, `*.tar*` (maxdepth 4, excl. `.git`) | none found | — | **No** |
| Documented backup policy | `docs/07_Deployment/Backup.md:8,14-25` — `pg_dump -U postgres aistock > /backups/…`, `BACKUP_DIR="/backups"`, S3 copy | policy only | **No** — describes a Linux production host and S3; no local artifact, and it targets production `aistock`, which is out of scope |
| `logs/app.log` | 8,311,443 B, Oct 24 15:26 | application log | **No** — 2,926 `derivative` mentions; **zero** lines matching import/count/`derivative_daily` patterns. Not a DB log. |
| `logs/app.2026-08-21_*.log.zip` | 910,475 B | archived app log | **No** — application, not database; not opened |
| `logs/app.2026-08-31_*.log.zip` | 851,422 B | archived app log | **No** — same |
| `audit_payload.txt` | 29,696 B, Oct 23 17:04 | copy of the M1 independent audit text | **No** — narrative, no DB evidence |
| M1-A recount artifacts | `docs/data-audit/{recount,window_search}.{py,json}` | present | **No** — they count *archive ZIPs*, not database rows |
| Local DB `test` | 34 MB, catalogued, **not opened** | — | **No** — see size argument below |

### 6.1 A size argument that materially narrows recoverability

`ai_stock_scanner` is **40 MB** total; `test` is **34 MB**; every other local database is ≤ 20 MB.
A 1.73-million-row `derivative_daily_prices` table cannot fit in 34 MB: at even ~60 bytes/row of
heap alone that is ~100 MB, before the `(contract_id, trade_date)` index and the parent table.
PostgreSQL does not return disk to the OS on `DELETE`, so a table that once held 1.73M rows would
retain its allocated size (1,732,338 rows ≈ 17,000+ pages ≈ 130+ MB).

The measured facts — `derivative_daily_prices` `relpages = 0`, heap bytes = 0, `reltuples = -1`
(never analyzed), `id` sequence `last_value IS NULL` — are consistent with **a database instance
that never held the population**, not with one that lost it. Narrowing caveats, all stated by M1-B
and none newly excluded here:

- `TRUNCATE … RESTART IDENTITY` + `VACUUM` would reproduce this signature exactly.
- Inserts supplying explicit `id` values would bypass `nextval` and leave the sequence NULL. The
  model/importer for `derivative_daily_prices` was **not** located under the names searched
  (`app/models/derivative_contract.py`, `app/cli/import_nse.py`, `app/repositories/derivative.py`),
  so this alternative remains open.

**No backup exists in this repository. Recoverability is `UNVERIFIED`, not disproven.** Equally,
"the data was permanently lost" is **not established** — only that the database currently named
`ai_stock_scanner` is not the instance described by the historical counts.

---

## 7. Migration graph findings

Parsed directly from `migrations/versions/*.py` (`revision` / `down_revision`), no Alembic command run:

| Property | Value |
|---|---|
| Revision files | **28** |
| Heads | **exactly one: `e5f6a7b8c9d0`** |
| Root | **`0001`** |
| Missing parents | **none** |
| Unreachable revisions | **none** |
| Chain length from head to root | **28** (complete, linear) |
| `e5f6a7b8c9d0.down_revision` | `d4e5f6a7b8c1` |
| `d4e5f6a7b8c1.down_revision` | `d5e6f7a8b9c0` |
| Database revision (re-verified read-only this session) | **`d4e5f6a7b8c1`** |
| `derivative_contract_sync_runs` (re-verified) | **absent** (`to_regclass` → NULL) |
| `transaction_read_only` at witness | **`on`** |

**The migration gap is fully and innocently explained.** The database is exactly one revision behind
head, and the single unapplied revision is precisely
`e5f6a7b8c9d0_add_derivative_contract_sync_runs.py` — the migration that would create the absent
table. M1-B's §10 conclusion is **VERIFIED**, and the independent audit's "inconsistent head" claim
is **CONTRADICTED**.

**New finding not in M1-B:** the head revision file and its two predecessors are **untracked**:

```
?? migrations/versions/e5f6a7b8c9d0_add_derivative_contract_sync_runs.py
?? migrations/versions/d5e6f7a8b9c0_add_provider_segment_and_provider_identity_index.py
?? migrations/versions/d4e5f6a7b8c1_add_derivative_contract_metadata_history.py
```

The applied revision `d4e5f6a7b8c1` — the one the database actually reports — **exists only in this
working tree**. A fresh clone at HEAD would have a different migration lineage and a different head.
Any future reproducibility claim about schema state must account for this.

**No causal link between the migration gap and the missing data is asserted or supported.** The
absent table is a *synchronization-history* table; its absence means no import lineage was ever
recorded through it, which is a provenance gap — not a cause of the population's absence. Per
instruction, no migration was run and no loss is inferred from the mismatch.

---

## 8. Evidence status matrix

| # | Claim | Required evidence | Result | Status |
|---|---|---|---|---|
| 1 | M1-B counts were freshly measured | Original query + raw output tied to DB identity and timestamp | No query returns 1,732,338 anywhere in the evidence; M1-B itself records `database_identity_recorded: null`, `sql_recorded: null` | **CONTRADICTED** as a claim about M1-B; the premise is false — M1-B never asserted it |
| 2 | Historical database identity is known | Evidence naming database and environment | Only `127.0.0.1:8001` (an app URL) and no DB name in the handoff | **UNVERIFIED** |
| 3 | Current database has zero derivative daily-price rows | Current attributable read-only measurement | Re-measured this session read-only: `0`; `transaction_read_only = on`; corroborated by M1-B and prior capture | **VERIFIED** |
| 4 | A 1,732,338-row population existed in a particular database | Direct historical evidence naming the DB | Components documented; total is a committed *expected* value; no DB ever named; no local DB large enough now | **PARTIALLY VERIFIED** — population plausibly existed; *which* database is **UNVERIFIED** |
| 5 | The historical population is recoverable | Verified existing backup or snapshot | No dump/backup/snapshot exists; documented policy targets production `aistock` on another host | **UNVERIFIED** (no artifact found; not disproven) |
| 6 | The 576 participant-OI count has provenance | Source query, table, timestamp, raw output | Composition documented and arithmetically exact (24 × 24) in a tracked file; query and timestamp still absent | **PARTIALLY VERIFIED** |
| 7 | Migration state is consistent | Repo graph + DB revision evidence | One head, one root, 28 linear revisions, no missing parents; DB exactly one revision behind; that revision creates the absent table | **VERIFIED** (with the untracked-file caveat in §7) |
| 8 | M2 prerequisites are closed | Independent evidence for every applicable gate | Identity gate closed; dataset provenance, 882-row composition, 576 producer query, Q4-2025 integrity, and production scope all open | **BLOCKED** |

### 8.1 Additional statuses required by the task's Phase 2 questions

| Question | Answer | Status |
|---|---|---|
| Which exact database identity did M1-B record? | `ai_stock_scanner` @ `localhost:5432`, `::1/128`, PG 18.4, `d4e5f6a7b8c1`, superuser `postgres`, `transaction_read_only = on` | VERIFIED |
| Was the 1,732,338 count freshly queried or copied? | **Neither.** It is a derived sum, carried as a committed *expected* invariant and labelled `[CLAIM]` throughout M1-B | VERIFIED |
| Does the supporting artifact contain the raw count result? | **No.** It appears only as a constant in `historical_claim` / `target` | VERIFIED (negative) |
| Was the capture script executed against the identified database? | Yes — `stage_run_utc 2026-10-24T10:01:34Z`, per-query timestamps, target block present | VERIFIED |
| Does the script's output agree with the report? | Yes, on every count re-checked (§5.1) | VERIFIED |
| Was a staged/expected count presented as a measurement? | **No** — not by M1-B. It *was* presented that way by `docs/AI_HANDOFF.md:24` (`Live Count` = `Expected` = PASS, no SQL, no DB name) | CONTRADICTED for M1-B; VERIFIED as a defect of the **handoff** |
| Does evidence point to a different DB / snapshot / earlier environment? | Yes, circumstantially: no local DB is large enough; all surviving rows postdate 2026-09-24; the historical observation is a UI on port 8001 | UNVERIFIED (hypothesis) |
| Does the independent audit accurately represent the evidence? | Its **measurements** yes; its **characterisation of M1-B** and its **migration claim** no; and it was never persisted | PARTIALLY VERIFIED |

---

## 9. What is known, unknown, and contradicted

### Known (verified)
1. The local development database `ai_stock_scanner` contains **0** derivative daily-price rows, **2,781** option contract masters, **0** futures, **0** participant-position rows, revision `d4e5f6a7b8c1`.
2. `derivative_daily_prices` has 0 heap pages, `reltuples = -1`, and an `id` sequence that has never been used.
3. The migration graph is linear, single-headed, complete; the DB is one revision behind; that revision creates the absent `derivative_contract_sync_runs`.
4. `1,732,338 = 28,508 + 1,703,830`, and it was committed as an **expected** value on 2026-09-09 before appearing as a "live count".
5. `576 = 24 × 24`, documented in a tracked reconciliation file.
6. No backup, dump, or snapshot exists in the repository.
7. Every M1/M1-A/M1-B audit artifact is **untracked**; there is no committed baseline to verify against.
8. The named M1-B independent audit **does not exist on disk**.

### Unknown (unverified, not disproven)
1. Which database the 2026-09-03 UI read (port 8001) and the 2026-09-07 handoff actually measured.
2. Whether 1,732,338 was ever a true `SELECT count(*)` result, and if so on which host/date.
3. The +1,211 / −329 composition of the 882-row residual.
4. The producing query and timestamp behind 576.
5. Whether the historical population is recoverable anywhere (local `test` DB unexamined; no backup found).
6. Production `aistock` state — out of scope, unauthorized, no claim made.

### Contradicted (evidence conflicts with the claim)
1. **"M1-B presented historical counts as current measurements."** Contradicted by M1-B §2/§11 and `reconciliation_results.json`.
2. **"Repository migration heads are inconsistent."** Contradicted by the parsed graph: one head, no missing parents, 28-revision linear chain.
3. **"The independent audit is a retained artifact."** Contradicted: no such file exists.
4. **"M1-A established that 576 has no derivable arithmetic."** Superseded: `24 × 24 = 576` is documented in `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112`.

### Explicitly not concluded
- The historical population is **not** declared lost. The evidence supports "absent from the database currently named `ai_stock_scanner`", not "destroyed".
- No claim is made that a `TRUNCATE`, `DROP`, restore, or migration caused the absence.
- No claim is made about production.

---

## 10. Minimal evidence needed to resolve each blocker

| Blocker | Minimal read-only evidence that would close it |
|---|---|
| Which database held the historical population | A `SELECT count(*)` output, log line, or handoff note pairing the 2026-09-03/2026-09-07 figures with a database name and host; **or** the same three counts found in `test` (34 MB — requires explicit new authorization) |
| Whether 1,732,338 was ever measured | One retained query result, app log line, or UI screenshot tying the number to a date and instance |
| Composition of the +1,211 / −329 residual | The original import's row-level report, **or** the archive-derived per-date table diffed against any recovered population — unavailable while the population is absent |
| 576 provenance | The page or CLI command that rendered it, plus a `report_date` breakdown; composable today from `derivative_participant_positions` **once that table is repopulated** |
| Recoverability | Any `pg_dump`/base backup, or PostgreSQL server logs showing `TRUNCATE`/`DROP`/restore activity 2026-09-07 … 2026-09-26 (files outside DB scope; would need authorization) |
| Migration-file integrity | Commit `e5f6a7b8c9d0`, `d4e5f6a7b8c1`, `d5e6f7a8b9c0` to Git so the head is reproducible from a clone |
| Q4-2025 per-file archive integrity | Carried over from M1-A; untouched by M1-B and M1-C |

---

## 11. Recommended next action

**Treat the historical counts as retired claims with a partially documented provenance, and stop
pursuing database forensics against `ai_stock_scanner`.** It has never held the population, and no
read-only query can recover what is not there.

Recommended order:

1. **Formally reclassify** 1,732,338 / 1,703,830 / 28,508 / 576 as *historical UI-observed claims*
   (not database measurements), and record that 1,732,338 is a **sum computed from components**,
   first committed as an *expectation* in `scripts/g8_pre_repair.py`.
2. **Correct the record** in M1-A: replace "576 has no derivable arithmetic" with the documented
   `24 × 24` composition, and note the two non-qualifying dates.
3. **Persist the M1-B independent audit** (or explicitly withdraw it) so the audit trail is not a
   chat transcript. It should also retract its migration-head claim.
4. **Decide the provenance question with new authorization**, in this order of cost: (a) query the
   local `test` database read-only for derivative counts; (b) read PostgreSQL server logs for
   2026-09-07 … 2026-09-26. Both are outside this task's authorization and were **not** performed.
5. **Re-derive the dataset from the archive, which is verified.** The M1-A recount
   (`docs/data-audit/recount_result.json`, SHA-256 `425b0226…`) reproduces 1,731,456 / 1,702,619 /
   28,837 for the 45-session window from primary NSE F&O bhavcopy ZIPs. That is the only
   *reproducible* population in this project. If M2 needs historical derivative data, it should be
   built on that, not on the retired claims.
6. **Commit the migration files** so schema state is reproducible from a clone.

**M2 must remain on HOLD.** M1-C closes no gate and sharpens one: the only surviving reproducible
derivative population in this repository is archive-derived, not database-derived.

---

## 12. Operations not performed (confirmation)

| Check | Result |
|---|---|
| Database writes (INSERT / UPDATE / DELETE / TRUNCATE / COPY) | **none issued** |
| DDL / schema change | **none issued** |
| Migrations (`upgrade`, `downgrade`, `stamp`, `revision`) | **none run** |
| Imports, backfills, syncs, schedulers, capture jobs | **none run** |
| Provider / broker / network calls | **none made** |
| Production `aistock` connection | **none** |
| Connections to `test`, `college_cert_sys_staging`, `hubdb`, `postgres`, `template1` | **none** — catalogued only |
| Backup restored, copied, moved, deleted or modified | **none** |
| New database created | **none** |
| Git stage / commit / push / reset / clean / stash / checkout / branch-switch | **none** |
| Existing report or evidence artifact overwritten | **none** |
| Destructive script executed for inspection | **none** |
| Secrets printed or persisted | **none** |
| Database connections this session | **1**, read-only, `SHOW transaction_read_only` = `on`, 4 values read, closed |

---

## 13. Final integrity confirmation

| Item | State |
|---|---|
| Branch | `checkpoint/sprint19-20-20260822` — unchanged |
| HEAD | `b2df61f1d56c5a5032787c96678326d0a73f6173` — unchanged |
| `git status --short` line count | 333 before writing this report (unchanged from the pre-write snapshot) |
| Pre-existing modified tracked files (21) | preserved, unmodified |
| Pre-existing untracked entries | preserved, unmodified |
| `docs/OPT_DATA_RECONCILIATION_M1A_*` | unmodified |
| `docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md` | unmodified |
| `docs/data-audit/**` | unmodified |
| Files created by this investigation | **only** `docs/OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md` (new, untracked) |
| M1-A immutable-artifact hashes | not re-derived this session; no artifact was opened for writing and none changed mtime |
| M2 status | **HOLD** |
