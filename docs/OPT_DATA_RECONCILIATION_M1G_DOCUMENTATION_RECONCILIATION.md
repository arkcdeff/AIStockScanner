# OPT Data Reconciliation — M1-G Documentation Reconciliation

**Reconciliation ID:** `M1G-DOCRECON-20261024`
**Date:** 2026-10-24
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Nature:** documentation only. No code, script, test, migration, schema, or configuration changed.
No database accessed. No script executed.
**Amends:** [M1-E](OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md) (addendum; M1-E is preserved unchanged)
**Follows:** [M1-F](OPT_DATA_RECONCILIATION_M1F_ACCEPTANCE_GATE_AUDIT.md)

---

## 1. Verdict

# `DOCUMENTATION RECONCILED — CODE GATE STILL UNCHANGED`

Three documentation defects identified in M1-E/M1-F are corrected by **one new addendum and two
narrow edits**, and one suspected defect was found to be already resolved and correctly left alone.

| # | Defect | Disposition |
|---|---|---|
| 1 | M1-E's retired register omits `option_lot_size_1 = 74,902`, a value that is population-dependent on the retired 75,962 | **Corrected** in this record (§3) |
| 2 | `docs/AI_HANDOFF.md` — `--verify` "All 10 assertions PASSED" appears under `### Quality Gates`, outside the scope of the M1-E banner (which speaks only of "the table below") | **Corrected** by a narrow edit (§4.1) |
| 3 | `docs/05_Development/PROJECT_STATE.md` — `--verify` "clean (all 10 assertions pass)" sits in the `Quality Gates` bullet that *precedes* the M1-E banner | **Corrected** by a narrow edit (§4.2) |
| 4 | `docs/CURRENT_TASK.md` — suspected same defect | **No edit needed**; the adjacent M1-E banner already names `--verify` explicitly and supersedes the claim (§4.3) |

**The code gate is intentionally untouched.** `scripts/g8_pre_repair.py` still asserts
`75,962` / `1,732,338` / `74,902` at lines 400, 401 and 404. Its SHA-256 is unchanged at
`8ecccc08cd2a2d66d1d85845fc3c8e8eb42d67ce4c7fed67ee91982fcf3c150e`. Remediation remains a proposal
in M1-F §7 and is **not** authorized or begun here.

**M2 remains on HOLD.**

### 1.1 Why an addendum rather than an edit

M1-E is a dated decision record and M1-F is a dated audit. Rewriting either would destroy the
provenance that makes them citable. The retired register is therefore **extended** by this
document, which is authoritative for the `74,902` correction only; for everything else M1-E stands
as written.

---

## 2. Repository integrity — initial state

Recorded before any edit.

| Item | Value |
|---|---|
| Branch | `checkpoint/sprint19-20-20260822` |
| HEAD | `b2df61f1d56c5a5032787c96678326d0a73f6173` |
| `git status --short` | **340 lines** |
| Staged files | **0** |
| `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` | present, 20,029 B, SHA-256 `229187a1…` |
| `docs/OPT_DATA_RECONCILIATION_M1F_ACCEPTANCE_GATE_AUDIT.md` | present, 27,749 B, SHA-256 `e3f2da2a…` |
| `docs/OPT_DATA_RECONCILIATION_M1G_DOCUMENTATION_RECONCILIATION.md` | **absent** — no collision |
| `docs/data-audit/recount_result.json` | SHA-256 `425b0226…` |
| `scripts/g8_pre_repair.py` | SHA-256 `8ecccc08…` |

Pre-existing working-tree state (21 modified tracked application files, and the large untracked
inventory) was left untouched throughout.

---

## 3. Retirement-register correction: `option_lot_size_1 = 74,902`

### 3.1 The correction

**`option_lot_size_1 = 74,902` is hereby RETIRED AS BASELINE, as a population-dependent value.**

It is added to the M1-E retired register:

| Retired figure | Unit | Status |
|---|---|---|
| 1,732,338 | derivative daily-price rows | RETIRED (M1-E) |
| 1,703,830 | option rows | RETIRED (M1-E) |
| 28,508 | futures rows | RETIRED (M1-E) |
| 75,962 | contract masters | RETIRED (M1-E) |
| 576 | participant OI rows, as a verified database count | RETIRED (M1-E) |
| **74,902** | **`OPTION` contracts with `lot_size = 1`** | **RETIRED — population-dependent (added by M1-G)** |

### 3.2 Why it is retired despite not being in the original five

`74,902` is not a free-standing measurement. It is a count *taken over the retired 75,962-contract
population* — specifically, the number of rows where `instrument_type='OPTION' AND lot_size=1`
(`scripts/g8_pre_repair.py:97,99`). It cannot be satisfied, or meaningfully discussed, without that
population existing. It therefore inherits the retirement of the population it counts.

M1-E retired the container (75,962) without retiring this constituent count; M1-F identified the
omission; this record closes it.

### 3.3 The arithmetic implication — stated as inference, not fact

Three of the gate's historical expectations were:

- `derivative_contracts` = **75,962** (all instrument types)
- `option_lot_size_1` = **74,902**
- `option_lot_size_not_1` = **0**

**If all three had held simultaneously**, then the number of `OPTION` contracts would have been
`74,902 + 0 = 74,902`, and the implied remainder would have been:

> **75,962 − 74,902 = 1,060 futures contracts**

Status of that figure, stated precisely:

- It is an **inference from historical constants**, derived arithmetically from three values that
  were never independently proven to hold together.
- It is **not** an independently measured fact. No query result records 1,060 futures contracts.
- It is **not** a current measurement, and nothing here asserts the current database contains 1,060
  of anything. (Measured today: 2,781 contracts, **all** `OPTION`, **zero** `FUTURE`.)
- It is **not** the same quantity as 28,508. 28,508 is a claimed count of futures *daily-price rows*;
  1,060 would be a count of futures *contract masters*. Different tables, different units. Neither
  can be used to validate the other.

The implication is recorded only because it shows that `74,902` is structurally inseparable from the
retired 75,962 claim — which is the reason it must be retired alongside it.

### 3.4 What `74,902` must not be represented as

- **Not** a current measurement. The count of `lot_size = 1` option contracts in the present
  `ai_stock_scanner` database **has never been measured** (M1-F §8) and is unknown.
- **Not** archive-derived. The archive recount produces no lot-size dimension at all.
- **Not** a target to restore the database toward.

---

## 4. Status-document reconciliation

Inspection was limited to the exact surrounding text in the three documents. For each, the question
was narrow: **could a reader take `--verify` as a currently passing acceptance gate?**

### 4.1 `docs/AI_HANDOFF.md` — **edited**

| Item | Detail |
|---|---|
| Section | `### Quality Gates` (heading at line 47) |
| Line changed | 55 |
| Original text (preserved) | ``- `python scripts/g8_pre_repair.py --verify`: **All 10 assertions PASSED** (exit code 0)`` |
| Risk | The M1-E banner at line 22 says "the table below", scoping it to the retired-counts table only. `### Quality Gates` is a **separate section below that table**, so the banner's wording did not cover this line. A reader scanning Quality Gates would see a passing gate. |
| Change | Appended, on the same line: `— **HISTORICAL, SUPERSEDED (M1-E / M1-F)**: this gate asserted the retired baselines (derivative_contracts 75,962, derivative_daily_prices 1,732,338, option_lot_size_1 74,902) and is therefore **not a currently satisfiable acceptance criterion**. It was **not re-executed**; that it would now fail is an inference from source code plus documented database state (M1-F §6.2), not a confirmed result. Retained verbatim as history.` |
| Original statement erased? | **No** — the "All 10 assertions PASSED (exit code 0)" text is untouched; only a label follows it |

### 4.2 `docs/05_Development/PROJECT_STATE.md` — **edited**

| Item | Detail |
|---|---|
| Section | `- **Quality Gates**: …` (line 15) |
| Line changed | 15 |
| Original text (preserved) | ``… | `git diff --check` clean | `scripts/g8_pre_repair.py --verify` clean (all 10 assertions pass)`` |
| Risk | This bullet sits **above** the M1-E banner (line 22). A reader reading strictly top-to-bottom reaches the passing gate claim before any caveat. |
| Change | Appended to the end of the bullet: `— **HISTORICAL, SUPERSEDED (M1-E / M1-F)**: this gate asserted the retired baselines (75,962 / 1,732,338 / 74,902) and is **not a currently satisfiable acceptance criterion**. It was **not re-executed**; the expected failure is an inference (M1-F §6.2), not a confirmed result` |
| Original statement erased? | **No** — `--verify clean (all 10 assertions pass)` remains, followed by the label |

### 4.3 `docs/CURRENT_TASK.md` — **intentionally NOT edited**

| Item | Detail |
|---|---|
| Section | `- **Quality Gates (All PASS)**:` (line 24), `--verify` line at 34 |
| Finding | **Already resolved.** The M1-E banner occupies lines 35–44, immediately after the Quality Gates block, and explicitly addresses this exact claim: *"Note also that `scripts/g8_pre_repair.py --verify` no longer passes against the current database, so the 'All 10 assertions PASSED' result above is historical, not re-earnable."* |
| Rationale | The banner names `--verify`, names the claim ("result above"), states it is historical, and states it is not re-earnable. Unlike §4.1 and §4.2, the supersession here is **explicit and adjacent** rather than scoped to a different table. |
| Action | **None.** Adding a second marker would duplicate an unambiguous caveat and would contradict the instruction to avoid unnecessary edits. |

### 4.4 Banner preservation

All three M1-E banners remain intact and unmodified — one per document:

| File | `SUPERSEDED — RETIRED AS BASELINE` occurrences | Retired counts marked `(historical, retired)` |
|---|---|---|
| `docs/AI_HANDOFF.md` | 1 | ✓ |
| `docs/05_Development/PROJECT_STATE.md` | 1 | ✓ |
| `docs/CURRENT_TASK.md` | 1 | ✓ |

No banner was weakened, moved, reworded, or removed.

---

## 5. Evidence distinctions — preserved explicitly

The three populations remain strictly separated. This is the discipline the task requires and the
one the original documents blurred.

### 5.1 (1) Historical database-count claims — `UNVERIFIED HISTORICAL CLAIM`

| Figure | Original recording | Provenance status |
|---|---|---|
| 1,732,338 | derived sum `28,508 + 1,703,830`; first committed in `scripts/g8_pre_repair.py:401` (2026-09-09) as an *expected* value | No database identity, no SQL, no raw output (M1-C §4) |
| 1,703,830 | `BROWSER_INSPECTION_REPORT.md:28` (2026-09-03 UI counter) | Rendered page, no query |
| 28,508 | `BROWSER_INSPECTION_REPORT.md:28` | Rendered page, no query |
| 75,962 | `scripts/g8_pre_repair.py:400` (expected value) | No query output retained |
| 576 | `BROWSER_INSPECTION_REPORT.md:28`; composition `24 × 24` in `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112` | Composition documented; producing query unknown |

### 5.2 (2) Retired executable expectations — `RETIRED AS BASELINE`

| Key | Line | Expected | Class |
|---|---|---|---|
| `derivative_contracts` | 400 | 75,962 | retired baseline |
| `derivative_daily_prices` | 401 | 1,732,338 | retired baseline |
| `option_lot_size_1` | 404 | 74,902 | **retired; population-dependent (this record)** |
| `fii_derivative_statistic` | 402 | 0 | zero-invariant — still valid, vacuous |
| `gift_nifty_contracts` | 403 | 0 | zero-invariant — still valid, vacuous |
| `option_lot_size_not_1` | 405 | 0 | zero-invariant — still valid, vacuous |
| `orphan_ddp` | 406 | 0 | zero-invariant — still valid, vacuous |
| `dup_contract_date` | 407 | 0 | zero-invariant — still valid, vacuous |
| `sem_dup_contracts` | 408 | 0 | zero-invariant — still valid, vacuous |
| `weekend_ddp` | 409 | 0 | zero-invariant — still valid, vacuous |

These remain in the source file, unmodified, and continue to fail-or-pass at runtime. **Labelling
them retired does not disable them.** The code gate is unchanged (§1).

### 5.3 (3) Archive-derived reference — `ARCHIVE-DERIVED REFERENCE — 45-SESSION WINDOW ONLY`

| Metric | Value | Source field |
|---|---:|---|
| Total CSV data lines | **1,731,456** | `window_rows` |
| Options | **1,702,619** | `window_options` |
| Futures | **28,837** | `window_futures` |
| Sessions | **45** archive sessions | `window_dates_present` |

Window: the inclusive date range **2026-06-08 through 2026-08-10**, which contains 64 calendar dates
of which **45 are archive sessions with data**. This is not a 45-calendar-day window and must not be
described as one.

Integrity: `docs/data-audit/recount_result.json`, SHA-256
`425b02264bdc19423f48e452496072b093bd600710b6a8f52fbadf2b0f9ed14e` (unchanged).

Counting rule (`docs/data-audit/recount.py:46,59-61`): `FinInstrmTp ∈ {STO, IDO}` = option,
`∈ {STF, IDF}` = future; every CSV data line counted; **no de-duplication**.

### 5.4 The three populations are not interchangeable

| | Historical claim | Retired expectation | Archive reference |
|---|---|---|---|
| What it counts | claimed database rows | asserted database rows | raw bhavcopy CSV lines |
| Unit | `derivative_daily_prices` row | same | source file line |
| Vocabulary | `OPTION` / `FUTURE` | same | `STO`/`IDO`/`STF`/`IDF` |
| De-duplicated | unknown | schema-enforced unique | **no** |
| Window | undefined | all time | 45 sessions in 2026-06-08…2026-08-10 |

**The archive figures were not substituted into the database assertions**, and no wording in this
record suggests they should be. Substituting `1,731,456` for `1,732,338` would assert an equivalence
between a raw-line count and a de-duplicated, schema-constrained table count that M1-F §7.3 shows is
unfounded.

### 5.5 The 882-row difference remains unresolved

| Metric | Historical claim | Archive reference | Difference |
|---|---:|---:|---:|
| Total | 1,732,338 | 1,731,456 | **+882** |
| Options | 1,703,830 | 1,702,619 | **+1,211** |
| Futures | 28,508 | 28,837 | **−329** |

# UNRESOLVED — no reconciliation is offered, implied, or newly invented here.

The arithmetic balances (`+1,211 − 329 = +882`); the explanation does not exist. The archive
reproduces **no** contiguous window matching any of the three claimed figures
(`exact_total_matches = 0`).

---

## 6. Files intentionally left unchanged

| File | Why unchanged |
|---|---|
| `scripts/g8_pre_repair.py` | Code gate; out of scope. SHA-256 `8ecccc08…` verified identical before and after |
| `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` | Dated decision record; extended by this addendum, not rewritten. SHA-256 `229187a1…` identical |
| `docs/OPT_DATA_RECONCILIATION_M1F_ACCEPTANCE_GATE_AUDIT.md` | Dated audit; SHA-256 `e3f2da2a…` identical |
| `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md`, `…M1A_INDEPENDENT_AUDIT.md` | Historical audit evidence |
| `docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md` | Historical audit |
| `docs/OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md` | Historical audit |
| `docs/OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md` | Historical audit |
| `docs/OPT_DATA_RECONCILIATION_M1.md`, `…M1_INDEPENDENT_AUDIT.md` | Historical audit |
| `docs/OPT_DATA_COVERAGE_M0.md` | Historical framing |
| `docs/19.1J_R5_R2A_R1_RECONCILIATION.md` | Tracked historical record of the `24 × 24` composition |
| `BROWSER_INSPECTION_REPORT.md` | Primary 2026-09-03 observation |
| `audit_payload.txt` | Historical audit payload |
| `docs/data-audit/**` | Evidence artefacts and hashes |
| `tests/test_g8_pre_repair.py` | Tests the gate's *existence and guards*, never its constants (M1-F §4.3); no change needed |
| All application code, templates, migrations, `.env`, configuration | Out of scope; untouched |

---

## 7. Evidence, inference, and unresolved — stated separately

### Directly observed (evidence)

1. `scripts/g8_pre_repair.py` lines 400/401/404 contain `75962`, `1732338`, `74902`, and the file is unmodified (hash verified twice).
2. `docs/AI_HANDOFF.md:55` and `docs/05_Development/PROJECT_STATE.md:15` claimed `--verify` passed, outside the reach of their banners.
3. `docs/CURRENT_TASK.md` supersession banner explicitly names `--verify` and its "All 10 assertions PASSED" result.
4. All three documents retain their original figures and banners after this reconciliation.
5. `docs/data-audit/recount_result.json` still holds `1,731,456` / `1,702,619` / `28,837` with `window_dates_present = 45` and 64 calendar dates in range.

### Inference (reasoning, not measurement)

1. `75,962 − 74,902 = 1,060` implied futures contracts, **if** the three expectations held together (§3.3).
2. `--verify` would exit non-zero against the current database — from code plus documented counts; **not executed**, **not confirmed**.
3. `74,902` is unsatisfiable without the retired population — from the query at line 97 being scoped to `derivative_contracts`.

### Unresolved

1. The 882-row difference and its `+1,211` / `−329` composition.
2. Which database, if any, ever returned 75,962 / 1,732,338 / 74,902.
3. The exact `lot_size = 1` count in the current database (never measured).
4. Whether `--verify` ever actually passed, and where — no retained stdout.
5. Recoverability of the historical population — **`UNVERIFIED`**.
6. Production `aistock` state — unauthorized, unexamined.

### Explicitly not claimed

- No claim that the historical data was lost, destroyed, or recovered.
- No claim that `--verify` was freshly executed, or that its failure was confirmed.
- No claim that either local database (`ai_stock_scanner`, `test`) represents production. Neither
  contains the population; that finding is not extrapolated.
- No claim that the archive reproduced the database.

---

## 8. Final integrity state

| Item | Value |
|---|---|
| Branch (final) | `checkpoint/sprint19-20-20260822` — **unchanged** |
| HEAD (final) | `b2df61f1d56c5a5032787c96678326d0a73f6173` — **unchanged** |
| `git status --short` (initial → final) | 340 → **341 lines** (+1: this report) |
| Staged files | **0** |
| Committed / pushed | **none** |
| Reset / clean / stash / restore / checkout / branch-switch | **none** |
| Database connections | **none** |
| SQL executed | **none** |
| `scripts/g8_pre_repair.py` executed (incl. `--verify`) | **no** |
| Credentials / `.env` read | **no** |
| `scripts/g8_pre_repair.py` SHA-256 | `8ecccc08…` — identical to initial |
| M1-E SHA-256 | `229187a1…` — identical to initial |
| M1-F SHA-256 | `e3f2da2a…` — identical to initial |
| `recount_result.json` SHA-256 | `425b0226…` — identical to initial |

### Exact changed-file list

**Created (1):**

| File | Size |
|---|---|
| `docs/OPT_DATA_RECONCILIATION_M1G_DOCUMENTATION_RECONCILIATION.md` | this report |

**Modified this task (2):**

| File | Section | Nature of change |
|---|---|---|
| `docs/AI_HANDOFF.md` | `### Quality Gates`, line 55 | Supersession label appended to the `--verify` line |
| `docs/05_Development/PROJECT_STATE.md` | `- **Quality Gates**:` bullet, line 15 | Supersession label appended to the `--verify` clause |

**Modified earlier by M1-E (pre-existing, not re-edited by this task):** `docs/CURRENT_TASK.md`,
`docs/AI_HANDOFF.md`, `docs/05_Development/PROJECT_STATE.md`.

**Not modified:** `scripts/g8_pre_repair.py`, M1-E, M1-F, all M0/M1/M1-A/M1-B/M1-C/M1-D reports,
`docs/data-audit/**`, `BROWSER_INSPECTION_REPORT.md`, `audit_payload.txt`,
`docs/19.1J_R5_R2A_R1_RECONCILIATION.md`, all application code, templates, tests, migrations and
configuration.

### M2 status

**M2 remains on HOLD.** This task authorizes no code remediation, no M2 implementation, no data
recovery, no migrations, and no production access. The smallest safe code remediation remains the
proposal in M1-F §7, unimplemented.

---

## 9. Outstanding documentation issues

| # | Issue | Severity |
|---|---|---|
| 1 | The code gate still asserts three retired values and will fail on any verification run (M1-F §5) | **Open — code, requires separate authorization** |
| 2 | `scripts/g8_pre_repair.py:69` docstring says "production-database only" while the guard permits `ai_stock_scanner` (M1-F §6.3) | Open — code |
| 3 | `docs/AI_HANDOFF.md` and `docs/CURRENT_TASK.md` record Alembic head `478d723971bc`; the repository head is `e5f6a7b8c9d0` (M1-C §7). Not corrected here — outside this task's scope | Open — documentation |
| 4 | The three revised migrations (`e5f6a7b8c9d0`, `d4e5f6a7b8c1`, `d5e6f7a8b9c0`) remain untracked in Git, so the applied schema state is not reproducible from a clone (M1-C §7) | Open — repository |
| 5 | `74,902` now appears with a retirement label in M1-G and in the two edited documents, but M1-E itself still lists only five figures. Readers of M1-E alone will not see the correction; this addendum must be read with it | By design |

---

## 10. Closing statement

The retirement register now matches reality: six values are retired as baselines, with `74,902`
correctly recorded as population-dependent rather than as an independent measurement. Two status
documents no longer present `--verify` as a currently passing gate; a third was verified as already
adequate and left untouched. Every original figure, banner, and historical statement survives
verbatim.

The executable gate is unchanged and still asserts retired baselines. Documentation and code
disagree by design until the M1-F §7 proposal is separately authorized.

**Verdict: `DOCUMENTATION RECONCILED — CODE GATE STILL UNCHANGED`.**
