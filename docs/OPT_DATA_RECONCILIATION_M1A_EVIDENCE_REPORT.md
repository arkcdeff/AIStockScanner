# OPT Data Reconciliation — M1-A Evidence Investigation

**Date:** 2026-10-23
**Milestone:** M1-A — Evidence reconciliation for the M1 findings that the independent audit placed on HOLD
**Repository:** `D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`
**Branch / HEAD:** `checkpoint/sprint19-20-20260822` / `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Mode:** Read-only investigation. No database connection, no migrations, no tests, no imports, no provider calls, no application-code edits, no Git mutation.
**Deliverable:** This report only.

---

## 1. Verdict

**PARTIALLY RESOLVED**

The independent M1 audit raised seven material gaps plus a set of interpretation and wording corrections. From repository artifacts and raw archive files alone, this investigation:

- **Fully resolved four**: the archive row counts (recomputed and validated to the row against the producer's own recorded counts), the manifest status vocabulary (recovered and independently corroborated), the Q4-2025 provenance contradiction (authorization chain established in Git and phase documentation), and the 25-versus-45 session framing (now a measured fact rather than a reading).
- **Materially advanced one**: the 882-row residual. It is now structurally characterized as an additive total (`+882 = +1,211 options − 329 futures`) and its three most plausible M1 explanations — a different session boundary, duplicate suppression, and import filtering — are ruled out by measurement rather than argument. Its actual mechanism remains unresolved because it can only be closed with database access.
- **Partially resolved one**: the provenance of the historical database counts. The producing query is now identified in code, but the database identity, execution timestamp, and raw output are still absent from every retained artifact.
- **Did not resolve two**: the current database's revision gap (repo side verified, database side deliberately not re-measured) and the contents of production `aistock` (out of scope, not contacted).

It is **not** "RESOLVED" because three questions that matter for M2 remain open on evidence grounds alone: which database produced 1,732,338, what caused the 882-row offset, and what `derivative_contract_sync_runs` would have recorded. It is **not** "HOLD" either: the manifest and Q4-2025 items no longer block, and the count comparison is no longer an unverified claim — it is a reproduced measurement.

---

## 2. Executive summary

**What changed relative to M1.**

1. **The archive counts now reproduce exactly.** Reading all 255 FO ZIPs (234 unique dates) and parsing the 45-session window `2026-06-08`–`2026-08-10` yields **1,731,456** physical data rows, of which **1,702,619** are options and **28,837** futures. The 25 manifest dates yield **887,557**. These are the exact M1 figures — but they are now measured, not quoted.

2. **The counting method is validated against the producer's own numbers.** The retained acquisition manifest `data/reference/derivatives/manifest_2026.json` records a `row_count` for each of its 172 dates. My independent recount matched **45 of 45** in-window dates with **zero** deviation, and the manifest's own `row_count` sum across all 172 dates is **7,105,914**, exactly its `_meta.final_total_rows` (`manifest_2026.json:24`). The row population is therefore established as *physical CSV data rows, header excluded, one row per instrument per session* — not an inference.

3. **The manifest status vocabulary is recovered.** No producer code exists in the repository, but the vocabulary is defined in two phase documents: `OFFICIAL_REMOTE_NOT_FOUND` means an HTTP **404** on the official NSE historical path, and `OFFICIAL_REMOTE_FOUND` means **200 OK** (`docs/AI_HANDOFF.md:127-129`, `docs/CURRENT_TASK.md:83-85`). It is a *remote-acquisition outcome*, not a database statement.

4. **`ALREADY_IMPORTED` is corroborated as "present in the database".** The manifest marks **22** `participant_oi` sessions as `ALREADY_IMPORTED`: the 21 June-2026 sessions `2026-06-01`–`2026-06-30` plus `2026-08-10`. `docs/CURRENT_TASK.md:87-89` records exactly "Acquired & Ingested 21 missing sessions of Participant OI data (2026-06-01 to 2026-06-30)" and "Total in DB: 22 sessions in 1-year window". The 22 matches the 22. `ALREADY_IMPORTED` is a database-presence status for at least that family.

5. **The manifest is proven stale, not wrong.** It was committed on **2026-08-22** (`6d71c82`), yet the 2026-09-07 database demonstrably held `1,732,338` derivative daily rows whose row-count fingerprint requires a 45-session window that includes 20 sessions the manifest marks `OFFICIAL_REMOTE_NOT_FOUND`. Those dates were later acquired successfully from `nsearchives.nseindia.com` with HTTP 200 (`manifest_2026.json` entries). The statuses describe the 2026-08-22 acquisition state at one endpoint family; they cannot be used to reason about the 2026-09-07 database.

6. **The 882-row residual is not an archive artifact.** An exhaustive search of **12,270** contiguous windows (2–60 sessions) across all 234 dates found **no** window matching the claimed total, and **no** window matching the claimed options figure. The 45-session window ranks second-best on joint agreement and is the smallest residual that is also plausible in date. Crucially, the offsets have **opposite signs** (database has 1,211 *more* options and 329 *fewer* futures than the archive), and no session-boundary change can move options up while moving futures down. There are **zero** intra-session duplicate `FinInstrmId` values and **zero** malformed rows, so duplicate suppression and parse rejection cannot explain it either. An import *filter* would remove rows, but the total delta is **positive**. The residual therefore originates on the database side, not the archive side.

7. **Q4-2025 is authorized, with an evidence-quality caveat.** The 62 Q4-2025 FO ZIPs and `manifest_q4_2025.json` were added in commit **`0027c86` (2026-09-09)**, "Complete Stage 2 G.8 official NSE F&O expansion and OI intelligence". The "forbidden" statements in `docs/AI_HANDOFF.md:10,47` and `docs/CURRENT_TASK.md:43` are the **Pre-G.8** phase state dated 2026-09-07 (HEAD `58a9549`, "G.8 Status: NOT STARTED", "Do NOT start G.8"). `docs/G11C_PHASE2_ACQUISITION_REPORT.md:30-33` documents the Q4-2025 acquisition explicitly: 226 F&O sessions expected, 226 acquired, including "62 Q4-2025 files from G.9/G.10". The prohibition was phase-scoped and expired when G.8 began. **Caveat:** Q4 manifest entries carry only `trade_date`, `filename`, `source_url`, `format` — no `sha256`, no `row_count`, no `acquired_at`, unlike every 2026 entry — so per-file integrity for Q4 is not independently attestable from retained records.

8. **The historical counts have an identified query but no identified database.** `scripts/g8_pre_repair.py:81` and `:84` run raw `SELECT COUNT(*)` against `derivative_contracts` and `derivative_daily_prices`, and `:400-401` hardcode the expectations `75962` and `1732338`. The script prints the database name and a timestamp when run — but no run log survived, and no retained artifact names the database. The 28,508/1,703,830 split has a different producer: `app/services/historical_query.py:518` and `:551`, which count `DerivativeDailyPrice` rows partitioned by `DerivativeContract.instrument_type`.

**Net effect on the audit's blocking list:**

| Audit blocker | M1-A result |
|---|---|
| Manifest semantics undefined | **Resolved.** Vocabulary recovered; `ALREADY_IMPORTED` corroborated against a documented 21+1 import; `OFFICIAL_REMOTE_NOT_FOUND` is an HTTP outcome. |
| Archive counts unverified | **Resolved.** Recomputed from raw files; matches the producer's per-date `row_count` 45/45 and its 172-date total exactly. |
| 882-row residual unexplained | **Partially resolved.** Characterized and localized to the database side; mechanism still unknown. |
| Q4-2025 provenance contradiction | **Resolved** to authorized-by-phase-scope, with a per-file hash/row-count evidence caveat. |
| Historical count database identity | **Not resolved.** Query identified; identity, timestamp, raw output absent. |
| Migration gap | **Repo side verified; database side not re-measured** (prohibited in this task). Remains a verification blocker for sync history. |
| Production `aistock` unknown | **Unchanged.** Not contacted. |

---

## 3. Scope and safety statement

**Performed.**

- Read the three required reports and the supporting audit/handoff/task documents in the implementation checkout.
- Traced the manifest's status vocabulary through phase documentation and searched the repository for any producer or consumer code.
- Parsed every FO bhavcopy ZIP in the two archive roots in memory (Python `zipfile`, no extraction to disk, no repackaging, no writes to `data/`) and recomputed row counts, type splits, duplicate analysis, and window searches.
- Read Git history for the manifest, the Q4-2025 archive, and the migration chain.
- Verified the repository Alembic head from migration files only.
- Ran `git status --porcelain` before and after, and wrote temporary analysis scripts **outside** the repository.

**Explicitly not performed.**

- **No database connection of any kind.** No read, no query, no schema inspection, no `alembic current`. Every database figure in this report is a *retained measurement* from M0/M1 or a *code-identified query*, never a M1-A measurement.
- No migrations, imports, backfills, tests, provider/API calls, network calls, schedulers, capture, or broker operations.
- No application-code edits and no permanent helper scripts in the repository. The four analysis scripts used were written to the OS temp directory.
- No archive file modified, moved, renamed, deleted, or re-zipped.
- No Git staging, commit, push, reset, clean, stash, checkout, or branch switch.
- No inspection of production or remote `aistock`.
- No start of M2 and no change to its authorization status.

**Read-only guarantees.** `data/reference/derivatives/raw` and `data/raw/nse/2026/06_udiff` were opened read-only as ZIP containers. The temporary analysis output was written outside the checkout.

---

## 4. Evidence inventory

### 4.1 Reports examined

| Path | Lines | Role in this investigation |
|---|---:|---|
| `docs/OPT_DATA_COVERAGE_M0.md` | 477 | Environment/DB coverage baseline; archive inventory; prior-status claim at `:264`. |
| `docs/OPT_DATA_RECONCILIATION_M1.md` | 245 | The report under audit; source of the 887,557 / 1,731,456 / 882 figures (`:14`, `:116-153`, `:176-201`). |
| `docs/OPT_DATA_RECONCILIATION_M1_INDEPENDENT_AUDIT.md` | 370 | The HOLD verdict and the seven material gaps this report addresses. |
| `docs/OPT_DATA_AUDIT.md` | 392 | Architecture baseline (referenced, not re-litigated here). |
| `docs/OPT_DATA_AUDIT_EVIDENCE_INDEX.md` | — | Evidence index (referenced). |
| `docs/OPT_DATA_AUDIT_REUSE_INVENTORY.md` | — | Reuse inventory (referenced). |
| `docs/G11OPT14EF_EVIDENCE_RECONCILIATION.md` | — | Prior evidence reconciliation (referenced). |

### 4.2 Primary artifacts measured this session

| Artifact | Path | What was measured |
|---|---|---|
| Session manifest | `data/audit/session_manifest_20250808_20260810.json` | Structure, 250 session records, status vocabulary, per-family status counts, `ALREADY_IMPORTED` date lists. Tracked in Git; added by `6d71c82` (2026-08-22 19:23:46 +0530). |
| 2026 acquisition manifest | `data/reference/derivatives/manifest_2026.json` | `_meta` (`:2-24`), 172 date entries each with `http_status`, `byte_size`, `sha256`, `parser_format`, `row_count`, `acquired_at`, `source_status`. |
| Q4-2025 acquisition manifest | `data/reference/derivatives/manifest_q4_2025.json` | 62 entries; fields limited to `trade_date`, `filename`, `source_url`, `format`. |
| FO archive, primary | `data/reference/derivatives/raw` | 234 FO ZIPs (278 MB). |
| FO archive, duplicate root | `data/raw/nse/2026/06_udiff` | 21 FO ZIPs (byte-identical to their primary copies) + 21 cash-market CM ZIPs. |
| Migration head | `migrations/versions/e5f6a7b8c9d0_add_derivative_contract_sync_runs.py` | `revision` at `:25`, `down_revision` at `:26`, table purpose in docstring `:1-16`. |
| Count-producing script | `scripts/g8_pre_repair.py` | `COUNT(*)` at `:81`, `:84`; expected values at `:400-401`; script prints database name and timestamp at `:396-398`. |
| Split-producing service | `app/services/historical_query.py` | `get_futures_price_coverage` `:518-549`; `get_options_price_coverage` `:551-586`. |
| Browser observation | `BROWSER_INSPECTION_REPORT.md` | `:28` (Derivatives Workbench counts), `:124-125` (contract/option row counts with "through" dates). |
| Handoff counts | `docs/AI_HANDOFF.md` | `:20-32` certified count table; `:10`, `:47` Q4-2025 restriction; `:127-131` status vocabulary and June Participant-OI import. |
| Task state | `docs/CURRENT_TASK.md` | `:36` counts; `:43` Q4 restriction; `:83-89` status vocabulary and 21+1 Participant-OI import. |
| Project state | `docs/05_Development/PROJECT_STATE.md` | `:23-24` counts, no database identity. |
| Acquisition report | `docs/G11C_PHASE2_ACQUISITION_REPORT.md` | `:30-33` F&O 226/226 including "62 Q4-2025 files from G.9/G.10". |
| Production shape | `docs/07_Deployment/Production.md` | `:35-37`, `:54-55` deployment layout and `CREATE DATABASE aistock`. |

### 4.3 Direct observation versus copied statement

This distinction is the audit's core complaint, so it is applied explicitly throughout:

| Claim | Status in M1 | Status in M1-A |
|---|---|---|
| 887,557 / 1,731,456 raw rows | **Copied/derived in M1, not shown recomputed** | **Direct measurement**, and cross-validated against `manifest_2026.json` `row_count` |
| 1,702,619 / 28,837 archive splits | **Copied in M1** | **Direct measurement** by `FinInstrmTp` |
| 71,709 distinct `FinInstrmId` | Stated in M1 | **Direct measurement** — reproduced exactly |
| 75,962 / 28,508 / 1,703,830 / 1,732,338 / 576 | **Copied from browser report and handoff** | **Traced to producing query in code**; still not re-measured (no DB access permitted) |
| `ALREADY_IMPORTED` meaning | **Assumed/hedged** ("may mean local source availability") | **Resolved from documentation + an exact 22=21+1 corroboration** |
| Q4-2025 provenance | **Flagged as contradiction, unresolved** | **Resolved from Git commit + phase documentation** |
| Migration gap | Asserted | **Repo side verified from files**; DB side explicitly not re-measured |

### 4.4 Archive inventory (measured)

| Item | Value |
|---|---:|
| FO ZIP files in both roots | **255** |
| Unique FO dates | **234** |
| Date range | **2025-10-01** → **2026-09-10** |
| Duplicate-date groups | **21** (all June 2026: `2026-06-01` … `2026-06-30`) |
| Duplicate groups with byte-identical members | **21 of 21** |
| Cash-market CM ZIPs (excluded, not derivatives) | 21 |

These reproduce M0 §7.1 (`docs/OPT_DATA_COVERAGE_M0.md:223-250`) exactly and confirm the M1 archive summary (`docs/OPT_DATA_RECONCILIATION_M1.md:176-201`).

---

## 5. Manifest producer/consumer semantics

### 5.1 What the manifest actually is

`data/audit/session_manifest_20250808_20260810.json` is a **tracked, static JSON snapshot** committed on **2026-08-22 19:23:46 +0530** in commit `6d71c82` ("Add missing Sprint 19 source and Sprint 20.0 design files").

Structure:

```
{
  "start_date": "2025-08-08",
  "end_date": "2026-08-10",
  "total_expected_sessions": 250,
  "sessions": [ { "trade_date", "equity", "derivatives", "participant_oi", "fii" }, ... ],
  "summary_counts": { "already_imported", "official_remote_found",
                      "official_remote_not_found", "remote_access_blocked" }
}
```

Measured content: 250 session records, matching `total_expected_sessions`. Derivatives statuses: `ALREADY_IMPORTED` 25, `OFFICIAL_REMOTE_NOT_FOUND` 225. Per-family `ALREADY_IMPORTED` counts: equity 28, derivatives 25, participant_oi 22, fii 1.

### 5.2 Producer: not present in the repository

A repository-wide search for `already_imported` and `OFFICIAL_REMOTE_NOT_FOUND` in `*.py` returned **zero matches**. No application module, script, or CLI creates, writes, or reads this manifest. It is a hand-produced session artifact committed as data.

**Consequence:** there is no code-level definition of the statuses. The vocabulary must be recovered from prose, and it can only be treated as authoritative to the extent that prose is corroborated. Two independent corroborations exist, described below.

### 5.3 Consumers: none in application code

No application code consumes the manifest. `docs/OPT_DATA_COVERAGE_M0.md:264` cites it as "MEASURED prior-status claim"; `docs/OPT_DATA_RECONCILIATION_M1.md:90-108` reads it as text. It has **no runtime consumer**, so it cannot be a cache or materialized view that some later process refreshes. It is a point-in-time record only.

### 5.4 The vocabulary, as defined in retained documentation

Both phase documents define the statuses in the same words, describing a **remote HTTP acquisition pilot** over four probed dates (`2025-09-15`, `2025-12-15`, `2026-03-16`, `2026-06-15`):

- `docs/AI_HANDOFF.md:127` — "Direct Equity & F&O Historical ZIP Archives: `OFFICIAL_REMOTE_NOT_FOUND` (404 on official NSE historical paths)"
- `docs/AI_HANDOFF.md:128` — "Participant OI (`fao_participant_oi_DDMMYYYY.csv`): `OFFICIAL_REMOTE_FOUND` (200 OK)"
- `docs/AI_HANDOFF.md:129` — "FII Statistics (`fii_derivatives_DDMMYYYY.csv`): `OFFICIAL_REMOTE_NOT_FOUND` (404)"
- `docs/CURRENT_TASK.md:83-85` — same three statements.

So `OFFICIAL_REMOTE_NOT_FOUND` / `OFFICIAL_REMOTE_FOUND` are **HTTP response outcomes** from a remote fetch attempt. `remote_access_blocked` (declared, count 0) is the sibling state for a blocked (e.g. 403/throttled) attempt. This is an *acquisition-outcome* vocabulary, not a database-coverage vocabulary.

### 5.5 `ALREADY_IMPORTED` — corroborated as database presence

The manifest marks **22** sessions as `participant_oi: ALREADY_IMPORTED`:

`2026-06-01`, `2026-06-02`, `2026-06-03`, `2026-06-04`, `2026-06-05`, `2026-06-08`, `2026-06-09`, `2026-06-10`, `2026-06-11`, `2026-06-12`, `2026-06-15`, `2026-06-16`, `2026-06-17`, `2026-06-18`, `2026-06-19`, `2026-06-22`, `2026-06-23`, `2026-06-24`, `2026-06-25`, `2026-06-29`, `2026-06-30`, `2026-08-10`.

That is the **21 June-2026 sessions plus one other**. `docs/AI_HANDOFF.md:131` and `docs/CURRENT_TASK.md:87-89` record for the same phase:

> "Successfully acquired and imported 21 missing sessions of Participant OI data for June 2026 (`2026-06-01` to `2026-06-30`)."
> "Participant OI Sessions Ingested: +21 sessions (Total in DB: 22 sessions in 1-year window)"

21 imported + 1 pre-existing = **22 in the database**, and the manifest marks **exactly 22** sessions in the same one-year window. This is not a loose resemblance; it is an exact set-size match against an independently documented database state, on the same date range, for the same artifact family.

**Conclusion:** `ALREADY_IMPORTED` means *this session's artifact for this family was already present locally / already ingested, so no remote acquisition was attempted*. For `participant_oi` it demonstrably tracks database presence. It is **not** an independently verified row-level reconciliation for any family — nothing in the manifest records a row count, a query, or a verification step.

### 5.6 Status-by-status determination

Requested per-criterion breakdown, applied to each status separately. **Y** = supported, **N** = not supported, **U** = unknown from retained evidence.

| Criterion | `ALREADY_IMPORTED` | `OFFICIAL_REMOTE_NOT_FOUND` |
|---|---|---|
| Archive file exists locally | **Y** — all 25 derivatives dates are present in `data/reference/derivatives/raw` (verified) | **Y** — the same 234-date archive covers 225 of the 250 manifest dates for other years too; archive presence is independent of this status |
| Source data was downloaded | **Y** (implied — it is present) | **N** — download failed (404) |
| Source data was parsed | **U** — no parse record exists | **N** — nothing to parse |
| Rows were inserted | **Y for `participant_oi`** (22 = 21+1 documented); **U for derivatives** | **N** at the time of the manifest |
| Database rows independently verified | **N** — no row counts, queries, or verification artifacts | **N** |
| Status is planning/acquisition metadata | **Partly** — it is an acquisition-planning state that, for `participant_oi`, is anchored in a real import | **Y** — it is an HTTP outcome at one endpoint family |

**The decisive asymmetry for the audit:** `OFFICIAL_REMOTE_NOT_FOUND` does **not** mean "verified absent from the database". It means "the remote fetch at the probed endpoint returned 404 (or inherited that status)". A session can be `OFFICIAL_REMOTE_NOT_FOUND` in this manifest and still be fully present in the database — and §5.7 proves that twenty such sessions were.

### 5.7 Proof the manifest is stale, not authoritative

The manifest (2026-08-22) marks **20** sessions `2026-06-08`–`2026-07-03` as `derivatives: OFFICIAL_REMOTE_NOT_FOUND`.

The 2026-09-07 database held **1,732,338** `derivative_daily_prices` rows. §7 of this report shows that value is only reachable by a ~45-session window that necessarily includes those 20 sessions: their archive contribution alone is **843,899** rows, and the 25 `ALREADY_IMPORTED` dates contribute only **887,557**. A database with 1,732,338 rows could not have covered only the 25 marked dates.

Two further facts explain the divergence without any inconsistency:

1. **Different endpoint family.** The 2026-08-22 pilot probed "official NSE historical paths" and got 404. The later acquisition used `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip` and recorded **`http_status: 200`** for every date (`manifest_2026.json`, all 172 entries, all `source_status: VALID`). The status was endpoint-specific.
2. **Different dates.** The pilot probed only **four** dates (`2025-09-15`, `2025-12-15`, `2026-03-16`, `2026-06-15`), yet the manifest assigns a definite status to all 250. For the other 246 sessions the status is a **default/derived** value, not a per-session observation.

**So the manifest's `OFFICIAL_REMOTE_NOT_FOUND` is a default-status assignment for sessions not already local at 2026-08-22, informed by a four-date probe.** It is neither a per-session 404 record nor a database-absence proof. This directly answers the audit's open question in its "Manifest semantics analysis" section.

---

## 6. Reproducible archive-count methodology

### 6.1 Population definition

| Item | Definition used |
|---|---|
| File filter | `BhavCopy_NSE_FO_*.zip` in `data/reference/derivatives/raw` and `data/raw/nse/2026/06_udiff` |
| Session date | Parsed from filename field 7 (`BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip`) |
| CSV member | The single `.csv` entry inside each ZIP; required exactly one |
| Encoding | UTF-8 with replacement on decode errors (no replacement occurred — verified no malformed rows) |
| Row population | **Physical CSV data rows**: every non-blank line after the header line. Header excluded. |
| Field integrity | Rows with fewer fields than the header are counted as physical rows and flagged separately (count: **0**) |
| Duplicate files | One file per date; duplicates verified byte-identical first (§6.4), so the choice is immaterial |
| Duplicate rows | No de-duplication applied at row level; verified unnecessary (§6.4) |
| Rejected rows | **0** |

This definition is not arbitrary: §6.5 shows it reproduces the producer's own per-date `row_count` for **45 of 45** dates and its 172-date total **exactly**.

### 6.2 Instrument classification

Classification is by the `FinInstrmTp` column of the UDiff CSV, with no inference from symbol names:

| `FinInstrmTp` | Class | Rows in 45-session window |
|---|---|---:|
| `STO` (stock option) | **options** | 1,465,702 |
| `IDO` (index option) | **options** | 236,917 |
| `STF` (stock future) | **futures** | 28,162 |
| `IDF` (index future) | **futures** | 675 |
| anything else | other | **0** |

**Options = 1,702,619. Futures = 28,837. Total = 1,731,456.**

Every row in every in-window file is one of the four types; there are no residual instrument types to explain the gap.

### 6.3 Window definition

- 45-session window: `2026-06-08` … `2026-08-10` — **45** dates present in the archive.
- 25 manifest dates: the `derivatives: ALREADY_IMPORTED` set from the session manifest, `2026-07-06` … `2026-08-10` — **25** dates, exactly the last 25 of the 45-session window (verified by ordered comparison, not by assumption).
- 20 earlier sessions: `2026-06-08` … `2026-07-03` — **843,899** rows.

### 6.4 Duplicate analysis

| Check | Result |
|---|---|
| Duplicate-date groups | 21 |
| Groups whose members are byte-identical | **21 of 21** |
| Example | `2026-06-01`: `data\raw\nse\2026\06_udiff` and `data\reference\derivatives\raw`, both 1,142,739 bytes, same SHA-256 |
| Intra-session duplicate `FinInstrmId` | **0** — for all 45 dates, distinct `FinInstrmId` count equals row count exactly |
| Effect on totals | **None.** Taking either copy yields identical counts. |

This eliminates "duplicate suppression" as a residual explanation: there is nothing to suppress, and any de-duplication rule would be a no-op.

### 6.5 Method validation against retained producer output

`data/reference/derivatives/manifest_2026.json` records `row_count` per date. Comparison:

- **45 of 45** in-window dates matched with **delta 0** (`2026-06-08` 40,604 … `2026-08-10` 33,601).
- Sum of `row_count` over all 172 entries = **7,105,914** = `_meta.final_total_rows` (`manifest_2026.json:24`) — **exact**.
- All 172 entries have `source_status: VALID`; none is missing `row_count`.

**Implication:** the retained acquisition manifest's `row_count` and my recount use the same population definition, and the definition scales correctly across 172 sessions and 7.1M rows. The archive-side numbers in this report are therefore not merely self-consistent — they are **reproducible against a prior independent producer**.

### 6.6 Counting-rule variants and whether they matter

| Variant | Effect on 45-session total | Verdict |
|---|---|---|
| Physical rows (chosen) | 1,731,456 | Matches producer exactly |
| Exclude zero-volume rows | would remove 1,012,605 | Not a plausible import rule; far too large |
| Exclude zero-OI rows | would remove 749,646 | Not plausible |
| Exclude zero-volume **and** zero-OI | would remove 747,721 | Not plausible |
| Unique (date, `FinInstrmId`) | identical to physical rows | No effect (0 duplicates) |
| Distinct contracts across window | 71,709 | Not a row count; see §7.4 |

No defensible row-level variant lands near 882. The variants that could produce a *small* delta do not exist.

---

## 7. Per-date and per-file results

### 7.1 The 45-session window

| Date | Rows | Options | Futures | Distinct IDs | Files |
|---|---:|---:|---:|---:|---:|
| 2026-06-08 | 40,604 | 39,960 | 644 | 40,604 | 2 |
| 2026-06-09 | 41,286 | 40,642 | 644 | 41,286 | 2 |
| 2026-06-10 | 41,508 | 40,864 | 644 | 41,508 | 2 |
| 2026-06-11 | 42,108 | 41,464 | 644 | 42,108 | 2 |
| 2026-06-12 | 42,881 | 42,237 | 644 | 42,881 | 2 |
| 2026-06-15 | 43,926 | 43,282 | 644 | 43,926 | 2 |
| 2026-06-16 | 44,245 | 43,601 | 644 | 44,245 | 2 |
| 2026-06-17 | 44,507 | 43,863 | 644 | 44,507 | 2 |
| 2026-06-18 | 44,856 | 44,212 | 644 | 44,856 | 2 |
| 2026-06-19 | 45,252 | 44,608 | 644 | 45,252 | 2 |
| 2026-06-22 | 45,463 | 44,819 | 644 | 45,463 | 2 |
| 2026-06-23 | 45,630 | 44,986 | 644 | 45,630 | 2 |
| 2026-06-24 | 45,931 | 45,287 | 644 | 45,931 | 2 |
| 2026-06-25 | 46,232 | 45,588 | 644 | 46,232 | 2 |
| 2026-06-29 | 46,554 | 45,910 | 644 | 46,554 | 2 |
| 2026-06-30 | 46,823 | 46,179 | 644 | 46,823 | 2 |
| 2026-07-01 | 37,268 | 36,628 | 640 | 37,268 | 1 |
| 2026-07-02 | 32,350 | 31,710 | 640 | 32,350 | 1 |
| 2026-07-03 | 33,211 | 32,571 | 640 | 33,211 | 1 |
| 2026-07-06 | 34,012 | 33,372 | 640 | 34,012 | 1 |
| 2026-07-07 | 34,652 | 34,012 | 640 | 34,652 | 1 |
| 2026-07-08 | 35,345 | 34,705 | 640 | 35,345 | 1 |
| 2026-07-09 | 36,195 | 35,555 | 640 | 36,195 | 1 |
| 2026-07-10 | 36,565 | 35,925 | 640 | 36,565 | 1 |
| 2026-07-13 | 36,898 | 36,258 | 640 | 36,898 | 1 |
| 2026-07-14 | 37,124 | 36,484 | 640 | 37,124 | 1 |
| 2026-07-15 | 37,319 | 36,679 | 640 | 37,319 | 1 |
| 2026-07-16 | 37,595 | 36,955 | 640 | 37,595 | 1 |
| 2026-07-17 | 37,819 | 37,179 | 640 | 37,819 | 1 |
| 2026-07-20 | 38,038 | 37,398 | 640 | 38,038 | 1 |
| 2026-07-21 | 38,175 | 37,535 | 640 | 38,175 | 1 |
| 2026-07-22 | 38,343 | 37,703 | 640 | 38,343 | 1 |
| 2026-07-23 | 38,572 | 37,932 | 640 | 38,572 | 1 |
| 2026-07-24 | 38,800 | 38,160 | 640 | 38,800 | 1 |
| 2026-07-27 | 38,978 | 38,338 | 640 | 38,978 | 1 |
| 2026-07-28 | 39,184 | 38,544 | 640 | 39,184 | 1 |
| 2026-07-29 | 31,284 | 30,647 | 637 | 31,284 | 1 |
| 2026-07-30 | 29,240 | 28,603 | 637 | 29,240 | 1 |
| 2026-07-31 | 30,456 | 29,819 | 637 | 30,456 | 1 |
| 2026-08-03 | 31,503 | 30,866 | 637 | 31,503 | 1 |
| 2026-08-04 | 32,322 | 31,685 | 637 | 32,322 | 1 |
| 2026-08-05 | 32,597 | 31,960 | 637 | 32,597 | 1 |
| 2026-08-06 | 32,940 | 32,303 | 637 | 32,940 | 1 |
| 2026-08-07 | 33,264 | 32,627 | 637 | 33,264 | 1 |
| 2026-08-10 | 33,601 | 32,964 | 637 | 33,601 | 1 |
| **Total** | **1,731,456** | **1,702,619** | **28,837** | **1,731,456** | — |

Note: `2026-08-07` is present in the archive and in `manifest_2026.json`, but is **not** in the 25 `ALREADY_IMPORTED` dates (the manifest jumps `2026-08-06` → `2026-08-10`).

### 7.2 The 25 manifest dates

| Metric | Value |
|---|---:|
| Sessions | 25 |
| Rows | **887,557** |
| Options | 871,581 |
| Futures | 15,976 |
| Share of the 45-session window | 51.3% |
| Reproduces M1's figure? | **Yes, exactly** |

### 7.3 The 20 earlier sessions

**843,899** rows (`2026-06-08` … `2026-07-03`), i.e. 48.7% of the window. Their existence is what makes the 25-date framing insufficient: a database containing only the 25 marked dates could hold at most 887,557 rows, which is 844,781 short of 1,732,338.

### 7.4 Contract masters

| Metric | Value |
|---|---:|
| Distinct `FinInstrmId` across the 45 sessions | **71,709** |
| — options | 70,816 |
| — futures | 1,068 |
| Claimed contract masters | 75,962 |
| Difference | 4,253 |

Reproduces M1's 71,709 exactly. The difference is expected: a contract master can include listed contracts not traded in any single window, whereas the archive only lists contracts appearing in a session file. This is **not** evidence of a discrepancy, and 75,962 remains an unverified database figure with the provenance limits described in §11.

---

## 8. Option/futures classification and duplicate-handling rules

**Classification rule (chosen).** `FinInstrmTp ∈ {STO, IDO}` → options; `∈ {STF, IDF}` → futures. This is the same rule the database importer must use to populate `DerivativeContract.instrument_type`, because that column drives the split queries in `app/services/historical_query.py:518-586` (`WHERE DerivativeContract.instrument_type == "FUTURE"` / `"OPTION"`).

**Why this matters for the residual.** The database's futures/options split is not a property of the archive rows; it is a property of **how each `FinInstrmId` was typed in `derivative_contracts`**. A contract typed `OPTION` in the master will count its price rows under options even if the archive row says `STF`. This is the only mechanism available to the database that can move rows from the futures bucket to the options bucket — and it operates on exactly the boundary the residual straddles.

**Duplicate handling.**
- File-level: 21 date groups, all byte-identical; one file per date used. No effect on counts.
- Row-level: **no duplicates exist**. For all 45 dates, `count(distinct FinInstrmId) == count(rows)`. A `(contract, trade_date)` uniqueness rule is therefore a **no-op** on this archive.
- Consequence: the handoff's certified "Duplicate contract/date = 0" and "Semantic duplicate contracts = 0" (`docs/AI_HANDOFF.md:30-31`) are consistent with the archive having nothing to deduplicate. It does not follow that the loader was correct — only that deduplication did not shrink the population.

---

## 9. Residual analysis for the 882-row difference

### 9.1 The residual, restated precisely

| Layer | Total | Options | Futures |
|---|---:|---:|---:|
| Archive, 45 sessions, physical rows (measured) | 1,731,456 | 1,702,619 | 28,837 |
| Claimed database (retained browser/handoff figures) | 1,732,338 | 1,703,830 | 28,508 |
| **Delta (database − archive)** | **+882** | **+1,211** | **−329** |

The three deltas are arithmetically consistent (`+1,211 − 329 = +882`).

### 9.2 What is now excluded, by measurement

| M1's proposed cause | Test performed | Result |
|---|---|---|
| "a slightly different session boundary" | Exhaustive search of **12,270** contiguous windows (2–60 sessions) across all 234 dates, scoring joint agreement on total + options + futures | **Excluded.** No window matches the total. No window matches the options figure. Only `futures = 28,508` matches anywhere, at an unrelated 45-session Q4-2025 window (`2025-10-23`–`2025-12-26`) — a coincidence (§9.4). |
| "duplicate suppression" | Counted intra-session duplicate IDs | **Excluded.** Zero duplicates exist; suppression would remove nothing. |
| "import filtering" | Signed direction of the deltas | **Excluded for the total.** A filter removes rows; the database has **882 more** rows than the archive, not fewer. |
| "contract-master mapping" | Cannot be tested without `derivative_contracts` | **Plausible and not excluded** — and now the leading candidate for the split (§8). |
| Parse rejection / malformed rows | Counted short rows and blank lines | **Excluded.** 0 of each. |

### 9.3 The structural finding

The deltas have **opposite signs**: the database has *more* option rows and *fewer* future rows than the archive. Changing which sessions are included moves options and futures **in the same direction** (every session contains both). Therefore **no session-selection hypothesis can produce this signature** — it is arithmetically impossible, not merely unfound.

This localizes the residual to the **database side**, and splits it into two separable components:

1. **+882 net rows in the database that have no archive counterpart** (extra rows);
2. **a 329-row reallocation from futures to options** (typing/attribution).

Any hypothesis must supply both. Neither can be confirmed from repository artifacts.

### 9.4 The strongest alternative window, and why it does not help

The best joint match anywhere in the archive is a **46-session** window `2026-06-15`–`2026-08-18`:

| Window | Sessions | Total | Δtotal | Δoptions | Δfutures | Joint score |
|---|---:|---:|---:|---:|---:|---:|
| `2026-06-15`–`2026-08-18` | 46 | 1,732,262 | −76 | −1,022 | +946 | **2,044** |
| `2026-06-08`–`2026-08-10` | 45 | 1,731,456 | −882 | −1,211 | +329 | 2,422 |

The 46-session window is closer on the *total* by 806 rows but **worse** on the split, and it requires the database to contain data through **2026-08-18**, for which there is no retained evidence — while the browser report explicitly says options EOD rows ran "Through 2026-08-10" (`BROWSER_INSPECTION_REPORT.md:125`). On the totality of evidence the 45-session window remains the best-supported reading, but this comparison shows that **count agreement alone does not identify the window**. That is exactly the audit's point, now quantified: several windows sit within a few hundred rows of 1.73M because per-session volume is 29k–47k.

### 9.5 Conclusion on the residual

**UNRESOLVED but bounded.** The residual is a real, precisely measured **+882 total / +1,211 options / −329 futures** offset. It is not an artifact of archive parsing, duplicate handling, malformed rows, or session selection. It is a database-side difference requiring (a) 882 extra rows and (b) a 329-row futures→options reallocation. Closing it requires row-level access to `derivative_daily_prices` joined to `derivative_contracts` — which this task forbids and M1 forbade.

Per the audit's instruction, the residual is **left explicitly unresolved** and is **excluded** from any "effectively reconciled" claim.

---

## 10. Q4-2025 file inventory and provenance decision

### 10.1 Inventory (measured)

| Metric | Value |
|---|---:|
| Q4-2025 manifest entries | **62** (`2025-10-01` → `2025-12-31`) |
| Files present on disk | **62 of 62** |
| Total bytes | **69,408,193** (69.4 MB) |
| Parsed rows | **2,208,035** |
| — options | 2,168,657 |
| — futures | 39,378 |
| Archive root | `data/reference/derivatives/raw` |
| Git status | **Tracked** (part of 236 tracked paths under `data/reference/derivatives/`) |

### 10.2 The contradiction, and its resolution

**The restriction as retained.** Both phase documents are dated **2026-09-07** and describe the *Pre-G.8 hardening* state (HEAD `58a9549`):

- `docs/AI_HANDOFF.md:10` — "**Q4-2025 F&O Acquisition**: NOT ACQUIRED (forbidden)"
- `docs/AI_HANDOFF.md:45-47` — "Next Step for Incoming Agent / Auditor: … Do NOT start G.8. Do NOT acquire Q4-2025 F&O data."
- `docs/CURRENT_TASK.md:14` — "**G.8 Status**: NOT STARTED — all work strictly restricted to Pre-G.8 final hardening (N1-N3)"
- `docs/CURRENT_TASK.md:43` — "No Q4-2025 F&O data acquired."

Note the structure: the prohibition is listed **inside** the Pre-G.8 scope block, immediately after "Do NOT start G.8" — i.e. the restriction is *phase-scoped*, and G.8 is named as the next phase.

**What actually happened, from Git and phase documentation.**

1. Commit **`0027c86` (2026-09-09)** — "Complete Stage 2 G.8 official NSE F&O expansion and OI intelligence" — is the commit that **added** both `manifest_q4_2025.json` and every Q4-2025 ZIP (`git log --diff-filter=A` on `BhavCopy_NSE_FO_0_0_0_20251001_F_0000.csv.zip` returns only `0027c86`). The Pre-G.8 restriction was therefore superseded two days after it was written, by the phase it was pointing at.
2. `docs/G11C_PHASE2_ACQUISITION_REPORT.md:30-33` (2026-09-11) records the Q4-2025 acquisition as in-scope and complete:
   - "Expected: 226 sessions (Q4-2025 62 + Jan-Aug 2026 163 + special 2026-02-01 Sunday Budget-day session)"
   - "Acquired: 226/226 (0 missing, 0 failed)"
   - "Files: 234 raw zips in `data/reference/derivatives/raw/` (includes 62 Q4-2025 files from G.9/G.10)"
3. Subsequent commits (`e0df66d`, `7153adb` 2026-09-11; `cc47e94` 2026-09-15 "Complete G.11-C M-1 institutional repair") continued to treat the Q4 files as in-scope, with no revert or removal.

### 10.3 Decision

**AUTHORIZED UNDER A LATER PHASE SCOPE — not UNTRUSTED_FOR_IMPORT_DECISIONS.**

The contradiction is resolved as **stale phase state versus superseding authorized scope**, supported by a dated commit that adds the files, a phase report that names Q4-2025 as an explicit objective, and a filename-qualified provenance statement ("from G.9/G.10"). The M1 report's framing of this as an unresolved contradiction was correct at the time (it had not traced the commit), and it is now closed.

**Evidence-quality caveat, which must be carried into any import decision.** The Q4-2025 acquisition record is materially weaker than the 2026 record:

| Evidence field | `manifest_2026.json` (172 entries) | `manifest_q4_2025.json` (62 entries) |
|---|---|---|
| `source_url` | Yes | Yes |
| `format` | Yes | Yes |
| `http_status` | **Yes (200)** | **Absent** |
| `byte_size` | **Yes** | **Absent** |
| `sha256` | **Yes** | **Absent** |
| `row_count` | **Yes** | **Absent** |
| `acquired_at` | **Yes** | **Absent** |
| `source_status` | **Yes (VALID)** | **Absent** |

Q4-2025 file integrity therefore rests on Git object identity (the bytes are committed and hash-anchored by Git) and on the phase report's summary, **not** on a per-file acquisition record. Authorization is established; **per-file completeness and hash attestation are not**. If Q4-2025 data is ever to be imported, its files should be hashed and counted into a manifest of the same shape as the 2026 one first.

**No Q4-2025 file was imported, deleted, moved, renamed, or edited by this investigation.**

---

## 11. Historical count provenance matrix

### 11.1 The five figures

| Figure | Value | Retained source(s) | Measurement type | Producing query identified? | Database identity retained? | Timestamp retained? | Raw output retained? |
|---|---:|---|---|---|---|---|---|
| Contract masters | **75,962** | `docs/AI_HANDOFF.md:23`; `docs/CURRENT_TASK.md:36`; `docs/05_Development/PROJECT_STATE.md:23`; expectation at `scripts/g8_pre_repair.py:400` | `SELECT COUNT(*) FROM derivative_contracts` | **Yes** — `scripts/g8_pre_repair.py:81` | **No** | Phase date only (2026-09-07); script prints one but no log retained | **No** |
| Total derivative daily rows | **1,732,338** | Same as above plus `scripts/g8_pre_repair.py:401` | `SELECT COUNT(*) FROM derivative_daily_prices` | **Yes** — `scripts/g8_pre_repair.py:84` | **No** | as above | **No** |
| Option EOD rows | **1,703,830** | `BROWSER_INSPECTION_REPORT.md:28,125` | Browser-rendered figure from `total_price_rows` | **Yes** — `app/services/historical_query.py:551-570` (COUNT of `DerivativeDailyPrice.id` join `DerivativeContract` where `instrument_type='OPTION'`) | **No** | 2026-09-03 (report date) | **No** |
| Futures EOD rows | **28,508** | `BROWSER_INSPECTION_REPORT.md:28` | Browser-rendered figure from `total_price_rows` | **Yes** — `app/services/historical_query.py:518-535` (same, `instrument_type='FUTURE'`) | **No** | 2026-09-03 | **No** |
| Participant OI rows | **576** | `BROWSER_INSPECTION_REPORT.md:28` | Browser-rendered figure | **No query identified** | **No** | 2026-09-03 | **No** |

### 11.2 What is established

- **Two measurement paths, not one.** The 75,962 and 1,732,338 figures come from a *script* (`g8_pre_repair.py`) that runs raw `COUNT(*)` against a non-test database and refuses test databases (`:70-78`). The 28,508 / 1,703,830 split comes from a *different* path — a web page rendering `HistoricalQueryService` coverage queries. They are not guaranteed to share a definition, though they are numerically consistent (§11.3).
- **The split queries are type-partitioned price-row counts.** They count `DerivativeDailyPrice.id` — rows, not contracts — filtered by the *contract's* `instrument_type`. So the split is a property of the contract master's typing, which is exactly the mechanism §8 identifies as the only plausible source of the 329-row reallocation.
- **Internal consistency is real but weak evidence.** 28,508 + 1,703,830 = 1,732,338 exactly. For that to hold, the partition must be exhaustive and mutually exclusive, which requires **zero orphan price rows** — and the handoff certifies "Orphan DerivativeDailyPrice = 0" (`docs/AI_HANDOFF.md:29`). So the three numbers are mutually consistent *given* that certification. They are not three independent measurements of each other.
- **576 participant OI rows are not reconstructible from retained evidence.** The documented Participant-OI import was 22 sessions; the G.11-C report describes participant files as "5 per session (Client/DII/FII/Pro/TOTAL; 12 OI buckets + volumes)" (`docs/G11C_PHASE2_ACQUISITION_REPORT.md:40`). Neither yields 576 by any obvious arithmetic. **UNRESOLVED** — the figure's composition is unknown, and it should not be used in any reconciliation.

### 11.3 What is not established (and cannot be, without DB access)

1. **Which database.** No retained artifact names the database that held 1,732,338 rows. `g8_pre_repair.py` prints `Database: {db_name}` at `:396-398`, but no captured run output exists in the repository — a repository-wide search for `PRE-G.8 VERIFICATION MODE` and `[PASS] derivative_daily_prices` returns **zero** results.
2. **Which query, at which moment.** The phase date 2026-09-07 is the only temporal anchor. There is no query text, no `EXPLAIN`, no row sample, no transaction id.
3. **Whether the two paths measured the same database.** The browser observation is 2026-09-03; the script certification is 2026-09-07. They could be four days apart in the same database, or different databases.
4. **Whether the 75,962 contract figure and the 1,702,619 archive option rows are the same population.** They are different kinds of thing (masters vs. rows) and cannot be compared directly; §7.4 documents the 4,253 difference against the 45-session distinct-ID union.

**A close count match is not a reconciliation.** The +882 offset is a genuine difference between two different measurement layers, one of which (the database layer) has no retained audit trail. That remains true after this investigation, and is the single largest reason M2 stays gated.

### 11.4 Interpretation corrections applied

Three wording and framing corrections required by the audit are adopted in this report and should supersede the corresponding M1 language.

**(a) Five evidence layers are kept separate.** No statement in this report moves from a lower layer to a higher one without naming the evidence that permits the step.

| Layer | What it means | Status for the 45-session window | Status for the 25 manifest dates |
|---|---|---|---|
| 1. Archive file present | A ZIP for that session exists on disk | **Established** for all 45 (measured) | **Established** for all 25 (measured) |
| 2. Manifest status | A status string in a static snapshot | Not applicable (manifest covers different dates) | `ALREADY_IMPORTED` — **established as a status**, meaning corroborated for `participant_oi`, **unknown at row level for derivatives** |
| 3. Source data parsed | Rows were read and classified | **Established** (this report parsed them: 1,731,456) | **Established** (887,557) |
| 4. Rows inserted into a database | Rows exist in some database | **Not established for any specific database** | **Not established for any specific database** |
| 5. Import verified and reconciled | Inserted rows match source under identical definitions | **Not established** — this is precisely what the +882 residual prevents | **Not established** |

M1's headline moved from layer 2 to layer 5. This report stops at layer 3 and says so everywhere.

**(b) "Changed reduced database state", not "permanent data loss".** The retained evidence supports a change between two observations: the 2026-09-03/07 state and the 2026-09-26 state. It does not prove permanent loss, because a different database, a restore to an older state, a truncated environment, or a separate deployment all produce the same observable. Accordingly this report uses **"changed reduced database state between retained observations"** and does not assert loss, cause, or finality. What *is* established is narrower and still important: the current local database contains 2,781 option contracts, 0 futures and 0 derivative daily rows (retained M1 measurement), while a database with 1,732,338 derivative daily rows was observed 20 days earlier.

**(c) The residual is not absorbed into a similarity claim.** M1's "within 0.05%" is a similarity statement. This report treats the residual as an **open, quantified discrepancy** (§9) and excludes it from any reconciliation claim. It is not described as effectively reconciled anywhere, and the M2 decision treats it as a blocking item.

---

## 12. Migration and production-access limitations

### 12.1 The revision gap (repository side verified)

| Check | Evidence | Result |
|---|---|---|
| Repo Alembic head | `migrations/versions/e5f6a7b8c9d0_add_derivative_contract_sync_runs.py:25` | `revision = "e5f6a7b8c9d0"` |
| Head's parent | same file `:26` | `down_revision = "d4e5f6a7b8c1"` |
| Any migration revising from the head | repository-wide search for `e5f6a7b8c9d0` | Only the head file itself (plus its `.pyc`) — **no successor** |
| Conclusion | — | `e5f6a7b8c9d0` **is** the repository head |

**Database revision `d4e5f6a7b8c1` is a retained measurement, not a M1-A measurement.** It is recorded in `docs/OPT_DATA_COVERAGE_M0.md:73-108` and `docs/OPT_DATA_RECONCILIATION_M1.md:51-88`. This task forbade database connections, so the gap was **not** re-confirmed at the database. What is verified here is only that the head is `e5f6a7b8c9d0` and that the head is exactly one revision beyond `d4e5f6a7b8c1`.

### 12.2 What the missing table would have provided

Per the migration's own docstring (`migrations/versions/e5f6a7b8c9d0_...py:1-16`), `derivative_contract_sync_runs` records, per reconciliation run: which underlying, which publication date, provider source, payload hash and **how it was computed**, dry-run vs committed, counts by reconciliation outcome (`newly_listed`, `unchanged`, `metadata_changed`, `metadata_drift`, `expired_absent`, `missing_while_unexpired`, `stale`, `conflicts`, `total_rows`), and the abort reason if the payload failed closed.

Because the table is absent, none of the following can be answered in the current environment:

1. Whether any contract sync ever ran there.
2. Which sync runs corresponded to the alleged historical dataset.
3. Whether the archive's `FinInstrmId` population was ever reconciled to `derivative_contracts` — i.e. **whether the 329-row futures→options reallocation came from a type assignment or re-typing event**.
4. Whether a sync aborted mid-run and left a partial population.
5. Whether the 4,253-contract excess over the 45-session distinct-ID union reflects legitimate unlisted contracts or drift.

**Item 3 is the important one.** The table's outcome counters are the only in-database record that could explain the residual's reallocation component. Its absence is therefore not incidental to this investigation — it removes the single artifact that could close the residual without guesswork.

### 12.3 Production `aistock`

- `docs/07_Deployment/Production.md:35-37,54-55` documents a deployment shape: `/opt/aistock`, `CREATE DATABASE aistock`, `CREATE USER aistock`, PostgreSQL 18, Nginx → Uvicorn → FastAPI.
- **No retained artifact establishes the contents of that database.** No coverage query, no count, no export, no log from that environment exists in the repository.
- It was **not contacted** by this investigation and must not be inferred to hold the dataset.

**Status: UNKNOWN. Unchanged by M1-A.** It remains the largest single unknown in the whole narrative, because "no accessible local environment contains the intended dataset" is only as strong as the word "accessible".

---

## 13. Unresolved questions and precise next actions

### 13.1 Unresolved

| # | Question | Why it is still open | What would close it |
|---|---|---|---|
| 1 | Which database produced 75,962 / 1,732,338? | No artifact names it; no captured script output retained | A single authorized read-only run of `scripts/g8_pre_repair.py --verify`, with stdout retained verbatim (it prints database name + timestamp) |
| 2 | What causes the 882-row residual? | Requires row-level DB comparison; forbidden here | Per-date, per-instrument-type row comparison of `derivative_daily_prices` joined to `derivative_contracts`, against the archive per-date table in §7.1 |
| 3 | What caused the 329-row futures→options reallocation? | `derivative_contract_sync_runs` absent; contract typing history unavailable | Apply `e5f6a7b8c9d0`, then inspect `derivative_contract_sync_runs` and `derivative_contract_metadata_histories` |
| 4 | What is the composition of 576 participant OI rows? | No query identified; arithmetic does not reach 576 from the documented 22 sessions | Identify the page/query that rendered it, or re-derive from `derivative_participant_positions` |
| 5 | Does production `aistock` contain the dataset? | Never contacted; explicitly out of scope | An explicit scope decision plus authorized read-only access |
| 6 | Were the 62 Q4-2025 files complete at acquisition? | Q4 manifest records no hash, size, or row count | Hash + count the 62 files into a manifest matching the 2026 shape |
| 7 | Does the current database still hold 2,781 option contracts / 0 derivative daily rows? | Retained M0/M1 measurement; not re-measured here | Authorized read-only re-measurement |

### 13.2 Precise next actions, in dependency order

1. **Retain a database-identity receipt.** Run the existing `scripts/g8_pre_repair.py --verify` once under authorization and archive its stdout. This costs one read-only query run and closes question 1 permanently. It must be done **before** any count is relied upon again.
2. **Close or formally accept the residual.** One authorized read-only query comparing `derivative_daily_prices` counts per (`trade_date`, `instrument_type`) against §7.1 will show within minutes whether the 882 rows sit in specific dates or in the typing boundary. If it is not closed, record it as an accepted, quantified residual — never as "effectively reconciled".
3. **Apply `e5f6a7b8c9d0` under the approved migration process**, then inspect `derivative_contract_sync_runs` and `derivative_contract_metadata_histories`. This is the only path to explaining question 3.
4. **Re-hash the 62 Q4-2025 files** into `manifest_q4_2025.json` with `sha256`, `byte_size`, and `row_count`, so Q4 provenance matches 2026 provenance in evidence quality. Expected totals to verify against: 62 files, 69,408,193 bytes, 2,208,035 rows.
5. **Decide production scope explicitly.** Either bring `aistock` into an authorized read-only review or record it as permanently out of scope for M2. Leaving it "unknown" silently is what the audit objected to.
6. **Re-measure the current database's derivative state** when authorized, to confirm or supersede the M1 figures of 2,781 / 0 / 0.

**Actions that should *not* be taken.** Re-importing the archive "to see if it matches" would destroy the only remaining evidence of the historical state's shape. Until questions 1–3 are closed, the archive must stay the untouched reference and the current database must stay untouched.

---

## 14. Explicit actions NOT performed

- **No database connection of any kind**, read or write; no query, no `alembic current`, no schema read, no row count. Every database figure in this report is quoted from M0/M1 or traced to code.
- No migrations applied, planned, or generated.
- No imports, backfills, dry runs, or staging loads.
- No test suite execution and no `pytest` invocation.
- No provider/API calls, no network calls, no downloads.
- No scheduler, capture session, broker, or trading action.
- No application-code edits; no permanent helper scripts added to the repository (the four analysis scripts used were written to the OS temp directory, outside the checkout).
- No archive file created, modified, moved, renamed, deleted, re-zipped, or extracted to disk. ZIPs were read in memory only.
- No Git staging, commit, push, tag, reset, clean, stash, checkout, or branch switch. No pre-existing modification or untracked file was altered.
- No inspection of production or remote `aistock`.
- No M2 work; no change to M2's authorization status.
- No existing file overwritten. This report is a new file at the required path.

**Deliberate analytical restraint.** For the residual, no cause is asserted. For the historical counts, no database identity is inferred. For production, no content is assumed. For Q4-2025, "authorized" is stated together with its exact evidence-quality limit rather than as an unqualified clearance.

---

## 15. M2 readiness decision

**M2 is NOT ready to proceed.** The blocking set is smaller than it was under the audit's HOLD, but it is not empty.

### 15.1 What no longer blocks

| Former blocker | Why it no longer blocks |
|---|---|
| Manifest semantics undefined | Vocabulary recovered and corroborated; statuses shown to be 2026-08-22 acquisition metadata, proven stale against the 2026-09-07 database (§5.7) |
| Archive counts unverified | Recomputed and validated against the producer's own per-date `row_count` (45/45) and 172-date total (7,105,914) (§6.5) |
| 25-vs-45 framing ambiguous | Measured: 25 dates = 887,557 rows (51.3%); the 1.73M figure requires the 45-session population (§7) |
| Q4-2025 provenance contradiction | Resolved to authorized under the superseding G.8→G.11-C scope, with a stated per-file evidence caveat (§10) |

### 15.2 What still blocks

1. **Verified database identity for any count relied upon.** No artifact names the database that held 1,732,338 rows. Until a retained receipt exists, no count in this narrative is attributable to a specific environment.
2. **The 882-row residual is unresolved.** It is now quantified (+882 / +1,211 / −329) and localized to the database side, and three of M1's five proposed causes are excluded — but the mechanism is not known. M2 must not be planned on the assumption that the archive and the historical database are the same population.
3. **`derivative_contract_sync_runs` does not exist in the current environment.** The only in-database artifact that could explain the typing reallocation is missing. Any M2 work that touches contract typing or attribution is being planned against an environment whose contract-sync history is unreadable.
4. **Production `aistock` remains UNKNOWN.** An explicit in-scope/out-of-scope decision is required, not an implicit assumption.

### 15.3 Decision

**M2 remains gated.** The gate is now three specific, closable items (database-identity receipt, residual closure or formal acceptance, migration + sync-run inspection) plus one policy decision (production scope). That is a materially better position than "the report is unreliable" — it is a short, actionable list.

**If M2 is scoped to one-minute OHLC:** this investigation changes nothing. M1's conclusion stands and was not challenged by the audit: the FO archive is EOD-only, `DerivativeDailyPrice` is daily, option-chain snapshots are point observations with no OHLC columns, and the only plausible intraday path (Upstox historical candles) currently hardcodes the day endpoint. The 1.73M EOD rows — wherever they are — are not a minute dataset.

---

## Appendix A — Reproducing the archive counts

The counts in §6–§8 are reproducible with a read-only script that:

1. Globs `BhavCopy_NSE_FO_*.zip` under `data/reference/derivatives/raw` and `data/raw/nse/2026/06_udiff`.
2. Derives the session date from filename field 7 (`BhavCopy_NSE_FO_<a>_<b>_<c>_YYYYMMDD_F_<d>.csv.zip`).
3. Opens each ZIP and requires exactly one `.csv` member; reads it in memory (never extracting).
4. Reads the header, then counts every non-blank subsequent line as one physical row.
5. Classifies by `FinInstrmTp`: `STO`/`IDO` → options, `STF`/`IDF` → futures.
6. Verifies duplicate-date groups are byte-identical before choosing one file per date.
7. Asserts `count(distinct FinInstrmId) == count(rows)` per date (holds for all 45).
8. Cross-checks each date's count against `manifest_2026.json[trade_date].row_count`.

Expected outputs, for verification:

- 255 files, 234 unique dates, `2025-10-01` … `2026-09-10`
- 21 duplicate groups, all byte-identical
- 45-session window: **1,731,456** total, **1,702,619** options, **28,837** futures
- 25 manifest dates: **887,557**
- Distinct `FinInstrmId`: **71,709**
- manifest_2026 comparison: 45/45 exact; 172-date sum 7,105,914
- Q4-2025: 62 files, 69,408,193 bytes, 2,208,035 rows

Any deviation from these values indicates a different population definition, not a different truth.

---

## Appendix B — Paths and identifiers cited

| Identifier | Where |
|---|---|
| `checkpoint/sprint19-20-20260822` | current branch |
| `b2df61f1d56c5a5032787c96678326d0a73f6173` | current HEAD |
| `6d71c82` | commit adding the session manifest (2026-08-22) |
| `0027c86` | commit adding Q4-2025 archive + manifest (2026-09-09) |
| `cc47e94` | commit "Complete G.11-C M-1 institutional repair" (2026-09-15) |
| `e5f6a7b8c9d0` | repository Alembic head (verified from file) |
| `d4e5f6a7b8c1` | database revision — **retained measurement only**, not re-measured here |
| `data/audit/session_manifest_20250808_20260810.json` | 250-session status snapshot |
| `data/reference/derivatives/manifest_2026.json` | 172-date acquisition manifest with per-date `row_count` |
| `data/reference/derivatives/manifest_q4_2025.json` | 62-date Q4 plan, no hashes or counts |
| `data/reference/derivatives/raw` | 234 FO ZIPs |
| `data/raw/nse/2026/06_udiff` | 21 byte-identical FO duplicates + 21 CM ZIPs |
| `scripts/g8_pre_repair.py:81,84,400,401` | the count-producing queries and their hardcoded expectations |
| `app/services/historical_query.py:518,551` | the futures/options split queries |
| `docs/AI_HANDOFF.md:20-32,127-131` | certified counts; status vocabulary; June Participant-OI import |
| `docs/CURRENT_TASK.md:36,43,83-89` | counts; Q4 restriction; status vocabulary; 21+1 import |
| `docs/G11C_PHASE2_ACQUISITION_REPORT.md:30-33` | 226/226 F&O acquisition incl. "62 Q4-2025 files from G.9/G.10" |
| `BROWSER_INSPECTION_REPORT.md:28,124,125` | 2026-09-03 rendered counts and their "through" dates |
| `docs/07_Deployment/Production.md:35-37,54-55` | production `aistock` deployment shape |
