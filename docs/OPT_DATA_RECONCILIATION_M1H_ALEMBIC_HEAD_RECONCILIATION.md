# M1-H — Stale Alembic Revision and Migration-Head Reconciliation

**Report:** `docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md`
**Date:** 2026-10-24 (local ~16:44, UTC+05:30 / ~11:14 UTC)
**Type:** Documentation-only reconciliation using static repository evidence
**Predecessors:** M1-E, M1-F, M1-G
**Database access:** NONE. **Script execution:** NONE. **Alembic invocation:** NONE.

---

## 1. Verdict

```
DOCUMENTATION RECONCILED — REPOSITORY HEAD VERIFIED STATICALLY
(working-tree head only; no claim about any database's applied revision)
```

The stale revision claims were corrected with **ten one-line qualifications across three
documents**. No revision string was replaced, because `478d723971bc` is **not a typo and not a
stale ID** — it is a real, tracked migration that sits **six revisions behind** the current head
in a strictly linear 28-revision chain. Blind replacement would have destroyed a valid historical
reference.

One finding materially changes how the "repository head" must be described: **three of the newest
revision files are untracked**, so `e5f6a7b8c9d0` is the head of *this working tree*, **not** of a
clean clone. That distinction is now stated in the documents and is the central limitation in §9.

---

## 2. Scope and safety boundaries

**Performed:** read-only Git commands (`rev-parse`, `status`, `diff`, `ls-files`, `check-ignore`,
`log`-equivalent inspection), file reads, `grep`, and one read-only Python script that only
*parses* `migrations/versions/*.py` text to derive the revision graph.

**Not performed:** no PostgreSQL or other database connection; no `alembic current/heads/check/
history/upgrade/downgrade/stamp`; no execution of `scripts/g8_pre_repair.py` or any application
script or test; no credentials or `.env` read; no changes to migration files, application code,
tests, configuration, or schema; no staging, commit, push, reset, clean, stash, restore, or branch
switch; no overwriting of any existing report.

---

## 3. Repository state

| Item | Value |
|---|---|
| Branch | `checkpoint/sprint19-20-20260822` |
| HEAD (final, unchanged) | `b2df61f1d56c5a5032787c96678326d0a73f6173` |
| HEAD (initial) | `b2df61f1d56c5a5032787c96678326d0a73f6173` — identical |
| M1-E report present | Yes (20,029 B, 2026-10-24 16:18) |
| M1-F report present | Yes (27,749 B, 2026-10-24 16:26) |
| M1-G report present | Yes (21,350 B, 2026-10-24 16:35) |
| M1-H target before this task | **Absent** — no collision |

---

## 4. Migration graph — established from source, not from prior reports

### 4.1 Method

Every `migrations/versions/*.py` file was parsed for its `revision` and `down_revision` literals.
`down_revision` is `None` only in the root. Parent/child sets were then compared to detect roots,
heads, branch points, merge points, and dangling parents.

**Methodology note (disclosed for reproducibility).** A first parse attempt used the character
class `[0-9a-f]` for revision IDs and a minimum length of 6. That **silently mis-parsed two files**
and produced a false "27 revisions / 4 roots / 4 heads" result. The causes were:

- `g7h8i9j0k1l2` (news/policy migration) is **not hexadecimal**, so it was skipped entirely.
- `c64742f7da53`'s parent is `"0002"`, which is **4 characters** and fell below the length floor.

The corrected parse uses unbounded alphanumeric IDs and is the one reported below. This is recorded
because the graph is the load-bearing evidence of this report and because the failure mode — a
plausible-looking but wrong graph — is exactly the class of error M1-H exists to prevent.

### 4.2 Result

| Property | Finding |
|---|---|
| Revision files | **28** |
| Roots | **1** — `0001` |
| Heads | **1** — `e5f6a7b8c9d0` |
| Branch points | **0** |
| Merge revisions (multi-parent) | **0** |
| Dangling / missing parents | **0** |
| Graph shape | **Strictly linear** |
| Head file | `migrations/versions/e5f6a7b8c9d0_add_derivative_contract_sync_runs.py` |

### 4.3 Full chain (root → head)

```
0001 → 0002 → c64742f7da53 → fda0edd539bd → bdc596b3589a → d1e2f3a4b5c6
     → e1f2a3b4c5d6 → f0a1b2c3d4e5 → fd96a19d8b61 → 22d886d53759 → 3f8c1a2b4d6e
     → d7b4cc79fd45 → 25e6f159a646 → 5f7b2c9d4e3a → 95a0be65d4a7 → ac9c6a6767be
     → b71d9e2a3456 → c82e3f4a5678 → e91f4a5678ab → f1a2b3c4d5e6 → g7h8i9j0k1l2
     → 478d723971bc → f1b9c8d7e6a5 → b3a4c5d6e7f8 → c4d5e6f7a8b9 → d5e6f7a8b9c0
     → d4e5f6a7b8c1 → e5f6a7b8c9d0   [HEAD]
```

`478d723971bc` is the **22nd of 28** revisions and is **6 steps below the head**:

```
478d723971bc → f1b9c8d7e6a5 → b3a4c5d6e7f8 → c4d5e6f7a8b9 → d5e6f7a8b9c0 → d4e5f6a7b8c1 → e5f6a7b8c9d0
     ①              ②              ③              ④              ⑤              ⑥          HEAD
```

It is a legitimate revision in the chain — `478d723971bc_add_industry_and_comparablecompany.py`,
which added Industry and ComparableCompany — and is the direct parent of `f1b9c8d7e6a5`. Its own
parent is `g7h8i9j0k1l2`. **It is a valid historical reference, not a stale identifier.**

### 4.4 Committed vs. working-tree head — the critical distinction

`git ls-files` shows that **only the three newest revisions are untracked**:

| Revision | File | Git status |
|---|---|---|
| `e5f6a7b8c9d0` | `..._add_derivative_contract_sync_runs.py` | **UNTRACKED** |
| `d4e5f6a7b8c1` | `..._add_derivative_contract_metadata_history.py` | **UNTRACKED** |
| `d5e6f7a8b9c0` | `..._add_provider_segment_and_provider_identity_index.py` | **UNTRACKED** |
| `c4d5e6f7a8b9` | `..._add_banknifty_market_index.py` | TRACKED |
| `b3a4c5d6e7f8` | `..._add_option_chain_snapshots_and_quotes.py` | TRACKED |
| `f1b9c8d7e6a5` | `..._add_bulk_block_deals_table.py` | TRACKED |
| `478d723971bc` | `..._add_industry_and_comparablecompany.py` | TRACKED |

Consequently:

- **This working tree:** 28 revisions, head `e5f6a7b8c9d0`.
- **A clean clone of the committed tree:** 25 revisions, head `c4d5e6f7a8b9`.

The documentation was therefore qualified as the "**working-tree** repository head", not simply
the repository head. This does not affect the retirement/audit findings of M1-E through M1-G, but it
does mean the applied schema state is still **not reproducible from a clone** — an open finding
carried forward from M1-C/M1-G and **not resolved here**.

---

## 5. Claim inventory and classification

Search scope: the three named documents, plus a repository-wide `478d723971bc` sweep across all
`*.md` and `*.py`. Classes: **1** current repository-head claim · **2** database-applied revision
claim · **3** historical project-state record · **4** historical test result · **5** migration-history
description · **6** unclear/out-of-scope.

| # | Location | Content | Class | Action |
|---|---|---|---|---|
| 1 | `docs/AI_HANDOFF.md:8` | `Alembic \`478d723971bc\` (head, no new migrations)` | 1 | **EDITED** |
| 2 | `docs/AI_HANDOFF.md:52` | `Alembic: clean (\`478d723971bc (head)\`, no new upgrades)` | 4 | **EDITED** (label appended) |
| 3 | `docs/CURRENT_TASK.md:11` | `**Alembic head**: \`478d723971bc\`` | 1 | **EDITED** |
| 4 | `docs/CURRENT_TASK.md:29` | `uv run alembic current` → `478d723971bc (head)` | 4 | **EDITED** (label appended) |
| 5 | `docs/CURRENT_TASK.md:30` | `uv run alembic heads` → `478d723971bc (head)` | 4 | **EDITED** (label appended) |
| 6 | `docs/05_Development/PROJECT_STATE.md:9` | `**Alembic**: \`478d723971bc\` (head, …)` | 1 | **EDITED** |
| 7 | `docs/05_Development/PROJECT_STATE.md:67` | `Alembic current: \`478d723971bc (head)\`` | 4 | **EDITED** (label appended) |
| 8 | `docs/05_Development/PROJECT_STATE.md:68` | `Alembic heads: \`478d723971bc (head)\`` | 4 | **EDITED** (label appended) |
| 9 | `docs/05_Development/PROJECT_STATE.md:69` | `Linear history: 27 revisions` | 1 | **EDITED** (stale-count label) |
| 10 | `docs/05_Development/PROJECT_STATE.md:80` | Stage 2.6.2 … `478d723971bc` … `(head)` | 5 | **EDITED** |
| 11 | `docs/05_Development/PROJECT_STATE.md:84` | Stage 2.6.2 migration `478d723971bc` (dual-industry note) | 5 | Unchanged |
| 12 | `docs/05_Development/PROJECT_STATE.md:369` | `head \`95a0be65d4a7\`` (test evidence) | 4 | Unchanged |
| 13 | `docs/05_Development/PROJECT_STATE.md:431` | `head \`5f7b2c9d4e3a\`` (release evidence) | 4 | Unchanged |
| 14 | `docs/G11-D7-SPEC-R1.md:149` | Calls `CURRENT_TASK.md` STALE; cites `478d723971bc` and certified `f1b9c8d7e6a5` | 3 | Unchanged |
| 15 | `docs/G11OPT14E_B_FULL_REGRESSION_REPORT.md:26` | Chain description, "28 revisions" | 5 | Unchanged |
| 16 | `docs/G11OPT14E_B_FULL_REGRESSION_REPORT.md:~173` | `get_heads()` → `['e5f6a7b8c9d0']`, 28 revisions | 4 | Unchanged |
| 17 | `docs/OPT_DATA_RECONCILIATION_M1G_…md:373` | M1-G open finding "repository head is `e5f6a7b8c9d0`; not corrected here" | 3 | Unchanged (closed by this report) |
| 18 | `.freebuff/run.md:8` | "migration head is `478d723971bc`" | 1 / 6 | Unchanged — out of scope |
| 19 | `migrations/versions/478d723971bc_*.py` | `revision`/`down_revision` definitions | 5 | Unchanged (source of truth) |
| 20 | `migrations/versions/f1b9c8d7e6a5_*.py` | `down_revision = '478d723971bc'` | 5 | Unchanged (source of truth) |

Class **2** (a database-applied revision claim) was **not found in any of the three documents** as a
current claim. Both `CURRENT_TASK.md` and `PROJECT_STATE.md` present `478d723971bc` as the *Alembic*
value, i.e. as a repository head, not as `alembic_version` in a database. M1-B's captured
`alembic_version` value of `d4e5f6a7b8c1` lives in `docs/data-audit/m1b/migration_state.json` (a
dated evidence artifact) and was **not** touched or re-presented here.

---

## 6. Exact documentation changes

Ten single-line qualifications. In every case the **original text, including the revision string,
is preserved verbatim ahead of the correction.**

### `docs/AI_HANDOFF.md`

**Line 8 — before**
```
- **Version / schema**: `0.14.0` / Alembic `478d723971bc` (head, no new migrations)
```
**after**
```
- **Version / schema**: `0.14.0` / Alembic `478d723971bc` (head **as of this record, 2026-09-07**;
  no new migrations — **superseded**: statically verified working-tree repository head as of
  2026-10-24 is `e5f6a7b8c9d0`; see M1-H)
```
(written as one line in the file)

**Line 52 — appended to the `- Alembic: **clean** (…)` quality-gate result**
```
— **historical result (2026-09-07)**: `478d723971bc` is a real, tracked revision six steps behind
the verified repository head `e5f6a7b8c9d0` (M1-H); this line is not a statement about today's head
```

### `docs/CURRENT_TASK.md`

**Line 11** — appended to `- **Alembic head**: \`478d723971bc\` (no new migrations)`:
```
— **historical (2026-09-07)**: statically verified working-tree repository head as of 2026-10-24
is `e5f6a7b8c9d0` (28 revisions, single head; see M1-H)
```

**Line 29** — appended to `- \`uv run alembic current\`: \`478d723971bc (head)\``:
```
— **historical result (2026-09-07)**; verified repository head 2026-10-24 is `e5f6a7b8c9d0`
```

**Line 30** — appended to `- \`uv run alembic heads\`: \`478d723971bc (head)\``:
```
— **historical result (2026-09-07)**; one head today: `e5f6a7b8c9d0`
```

### `docs/05_Development/PROJECT_STATE.md`

**Line 9** — same treatment as `AI_HANDOFF.md:8` (identical wording, `**Alembic**:` label).

**Lines 67–69 — before**
```
- Alembic current: `478d723971bc (head)`
- Alembic heads: `478d723971bc (head)`
- Linear history: 27 revisions
```
**after** (three lines, each corrected in place)
```
- Alembic current: `478d723971bc (head)` — **historical result (2026-09-07)**; verified repository
  head 2026-10-24 is `e5f6a7b8c9d0`
- Alembic heads: `478d723971bc (head)` — **historical result (2026-09-07)**; one head today:
  `e5f6a7b8c9d0`
- Linear history: 27 revisions — **stale**; 28 revision files verified statically on 2026-10-24
```

**Line 80** — the Stage 2.6.2 `(head)` tag became:
```
(head **at the time of this list**; **not** the current head — `478d723971bc` is six revisions
behind the verified repository head `e5f6a7b8c9d0`)
```

Net diff: `AI_HANDOFF.md` +16/−3, `CURRENT_TASK.md` +14/−4, `PROJECT_STATE.md` +16/−6 across the
whole uncommitted diffs — which also contain pre-existing M1-E/M1-G content (see §10).

---

## 7. Claims intentionally left unchanged, and why

| Location | Why unchanged |
|---|---|
| `PROJECT_STATE.md:84` | Describes the Stage 2.6.2 migration's *content* (dual industry architecture). Makes **no head claim**; `478d723971bc` is correct there. |
| `PROJECT_STATE.md:369`, `:431` | Historical test/release evidence naming `95a0be65d4a7` and `5f7b2c9d4e3a`. Both verified as **real ancestors** (positions 15 and 14). They are old head values recorded at the time, not stale current-state claims. |
| `G11-D7-SPEC-R1.md:149` | A dated source-assessment record. Its claim that `CURRENT_TASK.md` was stale was accurate when written; both IDs it cites (`478d723971bc`, `f1b9c8d7e6a5`) are real. Rewriting it would erase history. |
| `G11OPT14E_B_FULL_REGRESSION_REPORT.md:26, ~173` | Independent corroboration: records **28 revisions** and a **single head `e5f6a7b8c9d0`** with the same chain tail. Matches this report's parse exactly. Correct as written. |
| `M1G …:373` | M1-G's own open finding. Editing a completed audit report to show its own finding as closed would falsify the record; this report closes it instead. |
| `.freebuff/run.md:8` | **Deliberately not edited.** The line — "schema is managed by Alembic; migration head is `478d723971bc`" — is a genuine stale current-state claim, but the file is **untracked, git-ignored, and inside the tool-managed `.freebuff/` directory**, outside this task's three-document scope and outside version control. Reported rather than modified (see §8). |
| Migration source files | Definitional evidence. Modifying them is prohibited and unnecessary. |

No historical content was deleted. No M1-E retirement banner was weakened or removed.

---

## 8. Revision-adjacent issues (item 1 resolved by M1-I; items 2–4 remain open)

1. **`.freebuff/run.md:8` stated the head was `478d723971bc`.** Untracked, so it did not affect
   repository correctness, but any agent or operator who reads it as a runbook would get the wrong
   head. Recommended correcting it if that file is retained as a runbook.
   **STATUS — RESOLVED by M1-I (2026-10-24).** `478d723971bc` is no longer stated as the current head
   in `.freebuff/run.md:8`. The line now records the **working-tree** Alembic graph head
   `e5f6a7b8c9d0` (28 revision files, including three untracked tail migrations) and the
   **committed-tree** head `c4d5e6f7a8b9` (25 revision files), and states explicitly that these are
   repository graph heads, not claims about any database's applied revision. M1-I verified the change
   as a one-line replacement with the runbook's other 17 lines byte-identical. Because that correction
   establishes nothing about which revision is applied to any database, item 4 below remains open, as
   do items 2 and 3.
   *Accuracy note recorded during M1-I:* this finding originally described the file as "git-ignored";
   `git check-ignore` returns no matching rule for `.freebuff/run.md`. The file is **untracked**, and
   Git reports the whole directory as a single `?? .freebuff/` entry.
2. **Three tail migrations remain untracked** (`e5f6a7b8c9d0`, `d4e5f6a7b8c1`, `d5e6f7a8b9c0`), so a
   clone's head is `c4d5e6f7a8b9`, not `e5f6a7b8c9d0`. Whether to track them is a Git decision
   outside this task and was not made.
3. **`g7h8i9j0k1l2` is not a hexadecimal revision ID.** Alembic accepts it, but it breaks naive
   hex-based tooling (it broke this audit's first parser). Not a defect to fix here; recorded as a
   hazard.
4. **The applied database revision was not inspected** and is deliberately not restated as current
   in any document by this task.

---

## 9. Limitations

- **No database was contacted.** Nothing in this report states, implies, or updates which revision
  is applied to `ai_stock_scanner`, `test`, production `aistock`, or any other database. The
  `alembic_version` value `d4e5f6a7b8c1` appears only as a *quotation of M1-B's dated artifact*,
  with no claim that it is still current.
- **A repository head is not a database state.** A single linear graph with head
  `e5f6a7b8c9d0` proves only what files declare. It does not prove any database has those revisions
  applied, and it does not prove any table exists.
- **The graph is derived from the working tree, which contains untracked migrations.** The
  "committed head" (`c4d5e6f7a8b9`) differs from the "working-tree head" (`e5f6a7b8c9d0`).
- **No Alembic command was run**, so `ScriptDirectory.get_heads()` was not independently executed;
  the graph is a parse of the same source Alembic reads. Agreement with the M1-14E-B regression
  report's `get_heads()` output (`['e5f6a7b8c9d0']`) is corroboration, not execution.
- **Documentation edits do not resolve any data question.** The retired counts, the 882-row
  discrepancy, data recoverability, and production scope are untouched and remain as M1-E/M1-G
  left them.

---

## 10. Git integrity

### 10.1 Status summary

| Point | Tracked modified | Untracked | Total `git status --short` lines |
|---|---|---|---|
| M1-H start | 24 | 317 | 341 |
| M1-H end (after new report) | 24 | 318 | 342 |

The tracked-modified count is **unchanged at 24** because all three edited documents were **already
in the modified set** before M1-H — `AI_HANDOFF.md` and `PROJECT_STATE.md` from M1-G, and
`CURRENT_TASK.md` from earlier M1-E banner work (proven by the pre-existing
`⚠️ SUPERSEDED — RETIRED AS BASELINE (2026-10-24, M1-E)` hunk in its diff, which M1-H did not
write). M1-H therefore added **no new file** to the modified set; it added exactly **one** untracked
file.

### 10.2 Files changed by M1-H

| File | Status | Change |
|---|---|---|
| `docs/AI_HANDOFF.md` | modified (pre-existing) | 2 lines qualified |
| `docs/CURRENT_TASK.md` | modified (pre-existing) | 3 lines qualified |
| `docs/05_Development/PROJECT_STATE.md` | modified (pre-existing) | 4 lines qualified (5 lines of text) |
| `docs/OPT_DATA_RECONCILIATION_M1H_ALEMBIC_HEAD_RECONCILIATION.md` | **new, untracked** | this report |

No other file was created, edited, moved, or deleted.

### 10.3 Preservation evidence

- `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md` — mtime 2026-10-23 17:27, untouched.
- `docs/OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md` — mtime 2026-10-24 12:18, untouched.
- `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` — 16:18, untouched.
- `docs/OPT_DATA_RECONCILIATION_M1F_ACCEPTANCE_GATE_AUDIT.md` — 16:26, untouched.
- `docs/OPT_DATA_RECONCILIATION_M1G_DOCUMENTATION_RECONCILIATION.md` — 16:35, untouched.
- `docs/data-audit/recount_result.json`, `docs/data-audit/window_search_result.json` — 12:18,
  untouched (immutable M1-A evidence).
- All mtimes precede this task; none of these files appears in the modified set.

---

## 11. Confirmations

- ✅ Branch and HEAD identical before and after: `checkpoint/sprint19-20-20260822` /
  `b2df61f1d56c5a5032787c96678326d0a73f6173`.
- ✅ Nothing staged (`git diff --cached` empty); no commit, push, reset, clean, stash, restore, or
  branch switch.
- ✅ No database connection, query, migration, `stamp`, `upgrade`, or `downgrade`.
- ✅ No application script, test, or `scripts/g8_pre_repair.py` execution.
- ✅ No credentials, secrets, or `.env` contents read.
- ✅ No migration file, application code, test, or configuration modified.
- ✅ No existing report overwritten; the target path was verified absent first.
- ✅ All pre-existing modified and untracked files preserved.
- ✅ No claim made about any database's applied revision.

---

## 12. M2 status

**M2 remains on HOLD.**

M1-H closes one documentation inconsistency only. It does **not** close any reconciliation gate:

| Gate | Status after M1-H |
|---|---|
| Retired historical counts no longer used as baselines in docs | Closed by M1-E/M1-G; unchanged |
| `scripts/g8_pre_repair.py --verify` asserts retired values (75,962 / 1,732,338 / 74,902) | **OPEN — code remediation not authorized** |
| 882-row total discrepancy | **UNRESOLVED** |
| Option/futures composition (+1,211 / −329) | **UNRESOLVED** |
| Historical DB identity / provenance of 1,732,338 | **UNRESOLVED** (M1-C) |
| Participant OI 576 provenance | **UNRESOLVED** |
| Data recoverability | **UNVERIFIED** |
| Production scope | **UNAUTHORIZED / UNKNOWN** |
| Applied database revision vs. repository head | **NOT INVESTIGATED** (prohibited here) |
| Tail migrations untracked → schema not reproducible from clone | **OPEN** |

This report does not authorize M2, code remediation, or database access.
