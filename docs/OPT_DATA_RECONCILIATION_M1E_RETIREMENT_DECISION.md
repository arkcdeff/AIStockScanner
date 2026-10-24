# OPT Data Reference — M1-E Retirement Decision

**Decision ID:** `M1E-RETIRE-20261024`
**Decision date:** 2026-10-24
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Nature of change:** documentation and provenance only
**Predecessors:** M1-C ([provenance](OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md)), M1-D ([test database](OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md))

---

## 1. Decision

**The following historical figures are RETIRED AS BASELINE.** They must no longer be used as
trusted baseline expectations, acceptance criteria, verification targets, or evidence that any
database was populated:

| Retired figure | Recorded as |
|---|---|
| **1,732,338** derivative daily-price rows | `scripts/g8_pre_repair.py:401` (`expected_values`), `docs/AI_HANDOFF.md:24`, `docs/05_Development/PROJECT_STATE.md:23`, `docs/CURRENT_TASK.md` |
| **1,703,830** option rows | `BROWSER_INSPECTION_REPORT.md:28` (browser-rendered counter) |
| **28,508** futures rows | `BROWSER_INSPECTION_REPORT.md:28` (browser-rendered counter) |
| **75,962** contract masters | `scripts/g8_pre_repair.py:400`, `docs/AI_HANDOFF.md:23`, `BROWSER_INSPECTION_REPORT.md:28` |
| **576** participant open-interest rows | `BROWSER_INSPECTION_REPORT.md:28` (browser-rendered counter) |

**Their verification status is now: `UNVERIFIED HISTORICAL CLAIM — RETIRED AS BASELINE`.**

The reason is narrow and factual: **no originating database identity and no reproducible source
query have been established for any of these figures.** None is accompanied by a SQL statement, a
raw query output, a database name, a host, or an execution timestamp. Their earliest retained
appearances are a rendered browser page (2026-09-03), a committed assertion table (2026-09-09), and
a committed handoff table (2026-09-09) — see M1-C §4.

**In their place**, the archive-derived recount for one explicitly bounded window becomes the
reference population:

> ### ARCHIVE-DERIVED REFERENCE — 45-SESSION WINDOW ONLY
> | Metric | Reference value |
> |---|---:|
> | Total derivative rows | **1,731,456** |
> | Options | **1,702,619** |
> | Futures | **28,837** |

---

## 2. What retirement does and does not mean

**It means:**

- The five figures are no longer acceptance criteria, "expected" values, or proof of population.
  Any test, script, dashboard or checklist that asserts them must treat a mismatch as **expected**,
  not as a regression.
- A document, report, or table that records them without a verification caveat is stale.
- They may be cited **only** as historical claims, with their unverified status attached.

**It does not mean:**

- That the figures were fabricated. They are recorded in good faith in committed documents. Their
  *composition* is arithmetically coherent (`28,508 + 1,703,830 = 1,732,338`), and they plausibly
  describe a real dataset observed through the application's own UI on 2026-09-03 (M1-C §4.3).
- That data loss, permanent destruction, or recovery has been established. **No such claim is made.**
  Recoverability is `UNVERIFIED` (§8).
- That history is being rewritten. §10 lists every artifact that retains the original figures and
  states why each is preserved unmodified. Nothing is deleted, and no historical document is
  altered to imply the figures were never recorded.

---

## 3. Retired figures — evidentiary basis

Independently re-checked against retained artifacts during this decision, not taken from the task
brief.

| Figure | First retained appearance | Evidentiary class | Query preserved | Raw output preserved | DB identity | Timestamp |
|---|---|---|---|---|---|---|
| 1,732,338 | `scripts/g8_pre_repair.py:401` (committed `0027c86`, 2026-09-09) | **Derived sum** + *expected value* in a verification harness | No | No | **No** | Commit date only |
| 1,703,830 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03) | Rendered UI counter | No | No | No (app URL `127.0.0.1:8001` only) | Report date only |
| 28,508 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03) | Rendered UI counter | No | No | No (same) | Report date only |
| 75,962 | `scripts/g8_pre_repair.py:400` (committed `0027c86`) | *Expected value* in a verification harness | No | No | No | Commit date only |
| 576 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03) | Rendered UI counter | No | No | No (same) | Report date only |

### 3.1 Additional noted defect — the 1,732,338 figure is a sum, not an observation

`BROWSER_INSPECTION_REPORT.md` never states 1,732,338. It states only the two components. The total
appears first in `scripts/g8_pre_repair.py`, then in `docs/AI_HANDOFF.md:24` as
`DerivativeDailyPrice | Expected 1,732,338 | Live Count 1,732,338 | PASS` — a row whose "live count"
is the *result of asserting equality with the expected value*, recorded without a query. A passing
expectation is not a measurement.

### 3.2 Additional noted defect — the harness no longer passes

`docs/AI_HANDOFF.md` and `docs/CURRENT_TASK.md` both record
`scripts/g8_pre_repair.py --verify: All 10 assertions PASSED`. That result is **not reproducible
today**: the `ai_stock_scanner` database now reports `derivative_daily_prices = 0` and zero
`derivative_contracts` under `instrument_type = 'FUTURE'`, so at least two of the ten assertions
(`derivative_daily_prices: 1732338`, and the futures-dependent checks) would fail. The "certified"
state recorded in those documents does not describe the current database, and the certification
cannot be re-earned by re-running the harness without repopulating data. This is recorded as a
provenance fact, not as a data-loss finding.

---

## 4. The 576 participant-OI figure

Preserved observation, unchanged from M1-C: `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112` states
*"Participant OI currently has 576 rows across 24 stored report dates … Each date has 24 normalized
rows."* The arithmetic holds: **24 × 24 = 576**. The same document records participant **volume** at
504 rows over 21 dates (`24 × 21 = 504`), a consistent row geometry.

**This arithmetic composition does not establish the originating database, the producing query, or
the actual historical population.** 576 remains `UNVERIFIED HISTORICAL CLAIM — RETIRED AS BASELINE`
as a *verified database count*.

Two further caveats are carried forward: the figure's stated date set includes `2026-08-09` (a
Sunday) and `2026-08-21` (outside the one-year window ending 2026-08-10), leaving 21 qualifying
sessions; and the table that would hold it, `derivative_participant_positions`, is empty in both
local databases inspected.

---

## 5. Replacement reference — precise definition

# ARCHIVE-DERIVED REFERENCE — 45-SESSION WINDOW ONLY

Independently verified from [docs/data-audit/recount_result.json](data-audit/recount_result.json)
during this decision.

### 5.1 Values

| Metric | Value | Source field |
|---|---:|---|
| Total derivative rows | **1,731,456** | `window_rows` |
| Options | **1,702,619** | `window_options` |
| Futures | **28,837** | `window_futures` |

Internal consistency verified: `1,702,619 + 28,837 = 1,731,456` — the option/futures split sums
exactly to the total (`window_rows == window_options + window_futures` → `True`).

### 5.2 Window definition — read this before citing the numbers

- **Calendar range:** `2026-06-08` through `2026-08-10`, inclusive. That range contains
  **64 calendar dates** (`window_dates` has 64 entries).
- **Sessions with archive data:** **45** (`window_dates_present = 45`). The window is therefore
  properly described as *the 45 archive sessions falling within 2026-06-08 … 2026-08-10*, not as
  "45 calendar days".
- Citing these totals without stating the 45-session bound is a misuse.

### 5.3 Counting definitions

Derived from [docs/data-audit/recount.py](data-audit/recount.py) (lines 46, 59–61):

| Aspect | Definition |
|---|---|
| Source | `BhavCopy_NSE_FO_*.csv.zip` from the two archive roots |
| Row count | every CSV data line; **no de-duplication applied** |
| Options | `FinInstrmTp ∈ {STO, IDO}` |
| Futures | `FinInstrmTp ∈ {STF, IDF}` |
| Scope of the 45-session window | dates present in the archive within the inclusive calendar range |

The archive vocabulary (`STO/IDO/STF/IDF`) is **not** the database vocabulary
(`instrument_type ∈ {OPTION, FUTURE}`). Any comparison between this reference and a database must
translate between the two explicitly.

### 5.4 Integrity metadata

| Item | Value |
|---|---|
| Artifact | `docs/data-audit/recount_result.json` (2,158 bytes) |
| SHA-256 | `425b02264bdc19423f48e452496072b093bd600710b6a8f52fbadf2b0f9ed14e` |
| Recorded prefix (matches) | `425b0226` ✔ verified during this decision |
| Archive coverage | 255 ZIPs, 234 unique dates, `2025-10-01` … `2026-09-10` |
| Traceability | M1-D report §10; M1-A recount method |

### 5.5 Required labelling — and prohibited labelling

**Use this label:** `ARCHIVE-DERIVED REFERENCE — 45-SESSION WINDOW ONLY`

**Do not present these values as:**

- current database counts — they are not from any database;
- production database counts — production was never accessed;
- a full-history baseline — they cover one bounded 45-session window only;
- proof of what the database historically contained;
- proof that the historical data was lost.

For completeness and to prevent scope drift: the same artifact also records whole-archive totals
across all 234 dates (`total_rows` = 9,313,949; `total_options` = 9,164,719;
`total_futures` = 149,230). Those are **not** part of this reference, are not a full-history
baseline, and must not be substituted for the 45-session window figures.

---

## 6. Historical-to-archive differences — UNRESOLVED

Retained as arithmetic, with no explanatory claim attached:

| Metric | Retired historical claim | Archive reference (45 sessions) | Difference |
|---|---:|---:|---:|
| Total | 1,732,338 | 1,731,456 | **+882 historical rows** |
| Options | 1,703,830 | 1,702,619 | **+1,211 historical rows** |
| Futures | 28,508 | 28,837 | **−329 historical rows** |
| Net check | — | — | `+1,211 + (−329) = +882` ✔ internally consistent |

# The 882-row total discrepancy and the option/futures composition discrepancy are UNRESOLVED.

**No reconciliation is invented here.** The arithmetic is correct; the explanation is not known.
Specifically:

- No rows exist against which to decompose the +1,211 / −329 split, because the population is
  absent from both local databases (M1-D).
- The archive contains **no** contiguous window that exactly reproduces the retired total, option
  count, or futures count (`exact_total_matches = 0` in the recount artifact; M1-A window search).
  The +882 cannot therefore be attributed to a session-boundary difference either.
- Candidate explanations previously floated — import filtering, contract-master mapping,
  FUTURE→OPTION reclassification, duplicate suppression, a different session boundary — remain
  **hypotheses without evidence**. None is adopted.

This decision retires the claim; it does not explain the delta.

---

## 7. Local database findings — retained, not extrapolated

Retained from M1-C and M1-D as verified findings:

| Local database | Derivative daily-price rows | Derivative contracts | Participant OI rows | Status |
|---|---:|---:|---:|---|
| `ai_stock_scanner` | **0** | 2,781 (all `OPTION`; 0 `FUTURE`) | **0** | verified read-only (M1-B, re-verified M1-C) |
| `test` | **0** | **0** | **0** | verified read-only, all 69 tables empty except `alembic_version` (1) and `scan_definitions` (36) (M1-D) |

**Neither local database contains the historical population.**

**This finding is not extrapolated.** It says nothing about production `aistock`, which was never
accessed and remains unauthorized, and nothing about any off-machine backup, which was never
inspected. Stating the local result as a global one would be unsupported.

---

## 8. Recoverability — UNVERIFIED

| Aspect | Status |
|---|---|
| Recoverability of the historical population | **UNVERIFIED** |
| Backup artifacts examined | none — inspection outside this authorization |
| PostgreSQL server logs examined | none — outside this authorization |
| Production database accessed | **no** — unauthorized |
| Conclusion that the data is permanently lost | **NOT MADE** |

No claim of destruction and no claim of recovery. The question is open.

---

## 9. Documents changed by this decision

Narrowly scoped: a supersession banner was inserted above the "Read-Only Certified" database-state
blocks in three **current status** documents. In each case the original values and their original
wording are **preserved verbatim** beneath the banner; nothing was deleted or rewritten.

| File | Change | Original content |
|---|---|---|
| `docs/AI_HANDOFF.md` | banner added above *Post-Hardening Database Counts (Read-Only Certified)* | preserved verbatim |
| `docs/05_Development/PROJECT_STATE.md` | banner added above *Database State (Read-Only Certified)* | preserved verbatim |
| `docs/CURRENT_TASK.md` | banner added above *Database State (Read-Only Certified)* | preserved verbatim |
| `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` | **created** (this file) | n/a |

No application code, schema, migration, test, template, trading logic, or configuration file was
modified.

---

## 10. Artifacts preserved unmodified — register of retired figures

Constraint: historical evidence must not be rewritten so that it appears the figures were never
recorded. Each artifact below therefore retains its original figures and is **not** edited. Its
status is recorded here instead.

### 10.1 Historical evidence — preserved as-is, status now recorded

| Artifact | Contains | Disposition |
|---|---|---|
| `BROWSER_INSPECTION_REPORT.md` | 75,962 / 28,508 / 1,703,830 / 576 (lines 28, 124–125) | **preserved** — primary 2026-09-03 observation; the historical record of what the UI displayed |
| `docs/19.1J_R5_R2A_R1_RECONCILIATION.md` | 576 composition (line 112) | **preserved** — tracked; documents the 24 × 24 geometry |
| `audit_payload.txt` | M1 independent audit text | **preserved** — historical audit payload |
| `docs/OPT_DATA_COVERAGE_M0.md` | historical framing | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1.md` | all five figures (lines 13, 125–128) | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1_INDEPENDENT_AUDIT.md` | figures + verdict | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md` | figures + per-claim evidence table | **preserved** — audit evidence |
| `docs/OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md` | figures | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md` | figures, correctly labelled `[CLAIM]` | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md` | provenance findings | **preserved** — audit report |
| `docs/OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md` | test-DB findings | **preserved** — audit report |

The M1-B report already labels these figures `[CLAIM]` with `database_identity_recorded: null`; it
requires no correction and is consistent with this decision.

### 10.2 Code and method artifacts — NOT modified

| Artifact | Why it carries the figures | Disposition |
|---|---|---|
| `scripts/g8_pre_repair.py` | `expected_values` dict (lines 399–410) encodes 75,962 and 1,732,338 as **acceptance criteria** | **not modified.** Changing a committed verification harness would alter historical behavior and destroy evidence of what was previously asserted. **Retired for use as a gate.** Re-running `--verify` is not evidence of current state (§3.2). |
| `docs/data-audit/recount.py` | 1732338 / 1703830 / 28508 used as **search targets** to test whether the archive could reproduce them | **not modified.** These are inputs to a reproducibility test, not asserted facts; the test's answer was negative and is preserved in the result artifact. |
| `docs/data-audit/window_search.py` | same targets | **not modified** — same reason |

### 10.3 Data files

Occurrences of `28508` / `75962` inside `data/reference/corporate/nse_*.json` are unrelated corporate
announcement records, not derivative counts. Out of scope; untouched.

---

## 11. How to cite these figures from now on

| Situation | Correct treatment |
|---|---|
| Historical narrative about G.8 hardening | Cite with the suffix *"unverified historical claim, retired as baseline"* |
| Reconciliations or coverage statements | Use `ARCHIVE-DERIVED REFERENCE — 45-SESSION WINDOW ONLY` values |
| Test assertions or acceptance criteria | Do **not** assert the retired figures. Assert archive-derived values for the bounded window, or omit |
| Any comparison against a live database | State the database, host, revision, timestamp, SQL, and raw output — the standard the retired figures never met |

---

## 12. M2 status

# M2 remains on HOLD.

This decision is documentation only. It does **not** authorize or begin M2 implementation, data
recovery, imports, migrations, production access, or backup/log inspection.

| Gate | Status after M1-E |
|---|---|
| Baseline figures retired and replacement reference defined | **closed** (this decision) |
| Local databases checked for the historical population | **closed** — absent from both |
| 882-row / option-futures composition | **UNRESOLVED** — explicitly not explained |
| 576 producer query and originating database | **UNRESOLVED** — composition known, provenance unknown |
| Recoverability | **UNVERIFIED** |
| Production scope | **UNKNOWN** — unauthorized, no claim made |
| Q4-2025 per-file archive integrity | **open** (M1-A carry-over) |

M2 work on historical derivatives should be built on the archive-derived reference, whose method and
integrity are reproducible, rather than on the retired figures.

---

## 13. Integrity confirmation for this decision

| Check | Result |
|---|---|
| Git state inspected before editing | yes — branch, HEAD, `git status --short` (335 lines) |
| Pre-existing user changes preserved | yes — no reset/clean/stash/checkout/branch-switch/restore |
| Target collisions | none — decision record path was free; evidence directories untouched |
| Existing reports or evidence artifacts overwritten | **none** |
| Historical evidence files modified | **none** (§10.1) |
| Application code / schema / migrations / tests / config / trading logic changed | **none** (§10.2) |
| Database connections made | **none** |
| Credentials read or inspected | **none** |
| SQL / migrations / imports / backfills / repairs executed | **none** |
| Provider or broker requests made | **none** |
| Data loss, destruction, or recovery claimed | **no** (§2, §8) |
| M2 authorized or started | **no** |
| Git stage / commit / push | **none** |
| Files created | `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` (this file) |
| Files modified | `docs/AI_HANDOFF.md`, `docs/05_Development/PROJECT_STATE.md`, `docs/CURRENT_TASK.md` — supersession banners only, original content preserved |

### Independent verification performed for this decision

| Item | Method | Result |
|---|---|---|
| Archive reference values | read `docs/data-audit/recount_result.json` | 1,731,456 / 1,702,619 / 28,837 confirmed |
| Split sums to total | arithmetic on artifact fields | `True` |
| Window definition | `window_dates` length vs `window_dates_present` | 64 calendar dates / **45** with data |
| Counting rule | `docs/data-audit/recount.py` lines 46, 59–61 | `STO/IDO` = option, `STF/IDF` = futures, no dedup |
| Integrity hash | `sha256sum` | `425b0226…` ✔ |
| Archive never reproduces the claim | `exact_total_matches` | `0` |
| M1-D stated values | grep against M1-D report | consistent |
| Documents carrying the figures | repository-wide `grep` for all five figures (both formatted and unformatted) | enumerated in §10 |
