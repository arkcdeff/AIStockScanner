# M1-I — `.freebuff/run.md:8` Migration-Head Claim Correction

**Report:** `docs/OPT_DATA_RECONCILIATION_M1I_RUNBOOK_HEAD_CORRECTION.md`
**Date:** 2026-10-24
**Type:** Documentation-only correction and its closure record
**Parent audit:** M1-H (`docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md`)
**Database access / Alembic / scripts / tests / `.env` / staging / commit / push / reset / clean / stash / restore / branch switch:** none, per the M1-I task report.

---

## 1. Purpose and scope

The runbook `.freebuff/run.md` carried a stale, flat assertion about the current Alembic migration
head. The claim in its line 8 stated that *the migration head was `478d723971bc`*, which is **not**
the current repository head — `478d723971bc` is a real, tracked migration sitting six revisions below
the working-tree head `e5f6a7b8c9d0`, as documented in M1-H.

This report records:
- what the stale claim was,
- how it was corrected (one-line replacement in `.freebuff/run.md:8`, preserving the original sentence),
- the verification reported for that replacement, and
- the closure of M1-H §8 item 1 that resulted from it.

This is a **documentation-only** task. It corrects provenance text for a runbook; it changes **no**
code, schemas, data, backup, migration file, or trust boundary.

---

## 2. Exact correction

**Consensus source of truth for the revision facts** is M1-H: the revision graph is 28 files, root
`0001`, single working-tree head `e5f6a7b8c9d0`, with `478d723971bc` as a five-parent / six-step
below-head ancestor (grandparent chain `478d723971bc → f1b9c8d7e6a5 → b3a4c5d6e7f8 → c4d5e6f7a8b9
→ d5e6f7a8b9c0 → d4e5f6a7b8c1 → e5f6a7b8c9d0`).

### Before — `.freebuff/run.md:8`

```
- Database: a live PostgreSQL 18 instance must be reachable at the `DATABASE_URL` in `.env`
  (schema is managed by Alembic; migration head is `478d723971bc`).
```

The claim was read as: *the current Alembic head is `478d723971bc`.*

### After — `.freebuff/run.md:8`

```
- Database: a live PostgreSQL 18 instance must be reachable at the `DATABASE_URL` in `.env`
  (schema is managed by Alembic). Working-tree Alembic head: `e5f6a7b8c9d0` (28 revision files;
  includes three untracked tail migrations). Committed-tree head: `c4d5e6f7a8b9` (25 revision
  files). These are repository graph heads, not claims about any database's applied revision. See
  `docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md`.
```

The correction changed one sentence while keeping the rest of that line unchanged, and it kept the
rest of the runbook intact. The runbook now states **two** graph heads:

- **Working-tree head:** `e5f6a7b8c9d0` — 28 revision files, including the three untracked tail
  migrations (`d5e6f7a8b9c0`, `d4e5f6a7b8c1`, `e5f6a7b8c9d0`).
- **Committed-tree head:** `c4d5e6f7a8b9` — 25 revision files (the graph minus those three).

and explicitly that these are **repository graph heads, not claims about any database's applied
revision**, with a cross-reference to M1-H.

---

## 3. Verification *(as reported; not independently re-run here)*

The M1-I task report supplied the following verification:

- One line was replaced; the runbook's other **17 lines were byte-identical** after the change; the
  file remained **18 lines** (size grew from 1,248 → 1,531 bytes).
- After the replacement, `.freebuff/run.md:8` no longer contains the string `478d723971bc`. (Direct
  observation from this report's read of the file: yes — `478d723971bc` does not appear anywhere in
  `.freebuff/run.md`.)
- The three untracked `?? .freebuff/` files are reported per Git as a **single untracked directory**,
  not three separate entries. (Here I checked `git status --short -- .freebuff/` and confirmed it is
  reported as one `?? .freebuff/` entry.)

**Items verified directly from repository evidence, in this report:** the post-correction `.freebuff/run.md:8`
attestation itself, the M1-H §8 item-1 closure annotation, and the M1-H §8 heading. These are
independently readable in the repository; the report links to them rather than re-stating raw output.

**Items taken from the M1-I task report without re-verification here:** the 1,248→1,531 byte change,
the "17 other lines byte-identical" claim, and the "file remained 18 lines" claim. They are labelled
as reported verification rather than re-verified.

---

## 4. Scope and safety

Per the M1-I task report, none of the following occurred:

- No database connection (PostgreSQL or otherwise).
- No Alembic command (`current`, `heads`, `check`, `upgrade`, `downgrade`, `stamp`, or any other).
- No script execution (`scripts/g8_pre_repair.py` or any other).
- No test execution.
- No `.env` read or secret exposure.
- No staging, commit, push, reset, clean, stash, restore, or branch switch.

What **was** changed: only `.freebuff/run.md` (one line) and `docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md`
(the §8 item-1 annotation). This M1-I report is the only new file created.

What **was not** changed by M1-I: `AI_HANDOFF.md`, `CURRENT_TASK.md`, `docs/05_Development/PROJECT_STATE.md`,
any migration file, any application file, test, schema, configuration, or the three untracked tail
migrations themselves.

M1-I was **not** a code or data remediation milestone. A runbook string was corrected; six open gates
remain open.

---

## 5. Open issues and status

- **Three tail migrations remain untracked** (`e5f6a7b8c9d0`, `d4e5f6a7b8c1`, `d5e6f7a8b9c0`). The M1-H
  §8 item-1 closure did nothing about this; item 2 in M1-H remains open. A clean clone's head is
  `c4d5e6f7a8b9`, not `e5f6a7b8c9d0`.
- **The revision ID `g7h8i9j0k1l2` is still a tooling hazard** for hex-only tooling: Alembic accepts
  it, but naive parsers that assume hexadecimal `revision`/`down_revision` will mis-derive the graph
  (the first M1-H parse silently did this). M1-H item 3 is open and recorded as a hazard, not a fix.
- **The applied database revision remains unverified.** No database was inspected for
  `alembic_version`; `.freebuff/run.md:8` now explicitly disclaims that its head values describe any
  database. M1-H item 4 is open.
- **M2 remains HOLD.** The runbook correction closes only a stale provenance string. It does **not**
  close any data-reconciliation gate, any historical-count provenance question, any production-authorization
  question, or any production access question.

---

## 6. Provenance and distinctions

- **Date.** This report is dated 2026-10-24, the date supplied for the M1-I work.
- **Direct observations used here.** The corrected `.freebuff/run.md:8` line, the M1-H §8 item-1
  closure text, the M1-H §8 heading (`item 1 resolved by M1-I; items 2–4 remain open`), and the
  `?? .freebuff/` Git status entry for the directory.
- **Reported-but-not-reverified here.** The byte counts, the "17 lines byte-identical" assertion and
  the "file remained 18 lines" assertion from the M1-I task report; the "no database / Alembic /
  script / test / `.env` / Git-mutation" assurance from the M1-I task report is recorded as a
  reported claim, not as an independent recheck for this report. (No incriminating action was observed
  here that would contradict it.)
- **Not established by M1-I.** Nothing establishing which revision, if any, is applied to any database
  (the `ai_stock_scanner`, `test`, or any other database). Nothing establishing historical counts, the
  882-row discrepancy, the participant-OI provenance, or any production system.

---

## 7. Files involved

| File | Role |
|---|---|
| `docs/OPT_DATA_RECONCILIATION_M1I_RUNBOOK_HEAD_CORRECTION.md` | **this report** — created, new, untracked |
| `.freebuff/run.md` | corrected, line 8 (runbook) |
| `docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md` | informed the correction and received the §8 item-1 closure annotation |

M1-H is referenced as the load-bearing evidence for the revision facts; `.freebuff/run.md:8` is the
operational artifact that was corrected; this report is the standalone record of the M1-I action.

---

## 8. Verdict

```
RUNBOOK HEAD CLAIM CORRECTED — DOCUMENTATION CLOSURE ONLY
```

The stale `.freebuff/run.md:8` claim (`478d723971bc` as current head) was replaced with a statement that
records both working-tree and committed-tree graph heads as repository-graph facts and explicitly disclaims
any database claim. M1-H §8 item 1 was annotated as resolved by M1-I, with the §8 heading updated to
reflect that item 1 is resolved while items 2–4 remain open. No data reconciliation, no database inspection,
no production anything, and no M2 authorization followed from this. M2 remains **HOLD**.

---

*Generated as an audit record only. No staging, commit, push, reset, clean, stash, restore, or branch
switch was performed for this report.*
