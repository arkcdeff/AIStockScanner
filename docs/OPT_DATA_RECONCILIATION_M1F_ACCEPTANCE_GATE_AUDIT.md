# OPT Data Reconciliation — M1-F Retired Baseline Acceptance-Gate Impact Audit

**Audit ID:** `M1F-GATE-20261024`
**Audit date:** 2026-10-24
**Repository:** `AIStockScanner` @ branch `checkpoint/sprint19-20-20260822`, HEAD `b2df61f1d56c5a5032787c96678326d0a73f6173`
**Nature:** static audit only. No file modified. No script executed. No database connected.
**Predecessor:** [docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md](OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md)

---

## 1. Executive verdict

# `ACTIVE ACCEPTANCE GATE USES RETIRED BASELINE`

**Exactly one executable acceptance gate asserts the retired figures**, and it is the one identified
in advance: `scripts/g8_pre_repair.py --verify`, via the `expected_values` dict at **lines 399–410**.
Three of its ten assertions encode retired or retired-population-dependent values.

**No additional retired-baseline gate exists.** A repository-wide search of the executable tree
(`app/`, `tests/`, `scripts/`, `migrations/`) found **zero** occurrences of `1732338`, `1703830`,
`28508`, or `75962` outside `scripts/g8_pre_repair.py`. There is **no CI pipeline** (no
`.github/workflows/`), the `Makefile` has no `verify` target, and `tests/test_g8_pre_repair.py`
(53 tests) never asserts a retired number — its three `--verify`-related tests are static-string and
guard tests that pass regardless of database contents.

Three structural findings materially change how the gate should be read:

**[FINDING 1 — the gate is mostly vacuous.]** Seven of the ten assertions are *zero-invariants*
(`= 0`). On an empty or freshly-migrated database all seven pass without establishing anything. The
gate's reassuring "All 10 assertions PASSED" therefore overstates its evidential strength: only
three assertions actually depend on a populated dataset, and all three are the retired ones.

**[FINDING 2 — the retirement list is incomplete: `74,902` is a third retired-dependent value.]**
The M1-E record retired five figures but not `option_lot_size_1: 74902` (line 404). It is not a
count of anything in its own right — it is a count *over the retired 75,962-contract population*,
so it cannot be satisfied without that population. It is functionally retired and should be
recorded as such.

**[FINDING 3 — the guard does not do what its docstring claims.]** `verify()` is documented as
"read-only, production-database only" (line 69), but `FORBIDDEN_DB_NAMES` (line 20) is
`{"postgres", "production", "prod", "master"}` and `_is_test_db` (lines 31–34) only rejects names
containing `test`. `ai_stock_scanner` is therefore **permitted** — the guard blocks the one database
that could not have held the data and allows the one that could. Nothing about the code enforces
"production only".

The gate is **not repaired by this audit.** M2 remains on HOLD.

---

## 2. Artifact identity

| Item | Value |
|---|---|
| Path | `scripts/g8_pre_repair.py` |
| Size / lines | 18,253 B / 475 lines |
| SHA-256 | `8ecccc08cd2a2d66d1d85845fc3c8e8eb42d67ce4c7fed67ee91982fcf3c150e` |
| Committed | `0027c86`, 2026-09-09 16:07:03 +0530 |
| Working-tree status | clean, unmodified (`git diff` shows no change to this file) |
| Test coverage | `tests/test_g8_pre_repair.py`, 53 tests |

The script has two other modes, both of which **write** (`--repair`): `repair_c1` (line 136,
`UPDATE derivative_contracts`) and `repair_c2` (line 182, `UPDATE` + `DELETE`). Those are not part of
`--verify` and are out of scope for this audit beyond the observation that they exist and that
`--verify` does not reach them.

---

## 3. Exact assertion inventory

`verify()` spans **lines 68–133** and issues **12 `SELECT` statements, 0 write statements, 0
commits**. It returns a 10-key `checks` dict. `main()` compares that dict against `expected_values`
(lines 399–410) in a loop at lines 412–431.

| # | Key | Query line | Assignment | Asserted condition | Expected | Classification | Evidence it depends on | Still valid? |
|---|---|---|---|---|---|---|---|---|
| 1 | `derivative_contracts` | 81 | 82 | `COUNT(*) FROM derivative_contracts` | **75,962** | **RETIRED** | Retired historical claim (M1-E) | **No** — retired baseline |
| 2 | `derivative_daily_prices` | 84 | 85 | `COUNT(*) FROM derivative_daily_prices` | **1,732,338** | **RETIRED** | Retired historical claim (M1-E) | **No** — retired baseline |
| 3 | `fii_derivative_statistic` | 87 | 88 | `COUNT(*) FROM fii_derivative_statistics` | 0 | Zero-invariant | Domain invariant (no synthetic FII data) | **Yes** — but vacuous on empty table |
| 4 | `gift_nifty_contracts` | 90–95 | 95 | contracts whose `underlying_index_id` = id of `market_indices.code='GIFT_NIFTY'` | 0 | Zero-invariant | Domain invariant | **Yes** — vacuous if no GIFT_NIFTY index row |
| 5 | `option_lot_size_1` | 97 | 99 | `OPTION AND lot_size=1` | **74,902** | **RETIRED-POPULATION-DEPENDENT** | Count over the retired 75,962-contract set | **No** — not satisfiable without the retired population |
| 6 | `option_lot_size_not_1` | 101–105 | 105 | `OPTION AND lot_size != 1` | 0 | Zero-invariant, but population-scoped | "lot sizes sourced, not placeholder 1" (C3/C4) | **Yes** — vacuously; measures nothing when empty |
| 7 | `orphan_ddp` | 107–113 | 113 | prices with no parent contract | 0 | Zero-invariant | Referential integrity | **Yes** — vacuous on empty table |
| 8 | `dup_contract_date` | 115–121 | 121 | `(contract_id, trade_date)` groups with `count>1` | 0 | Zero-invariant | Uniqueness; schema also enforces it | **Yes** — structurally impossible *and* vacuous |
| 9 | `sem_dup_contracts` | 123–125 | 125 | `idempotency_hash` groups with `count>1` | 0 | Zero-invariant | Uniqueness; schema also enforces it | **Yes** — structurally impossible *and* vacuous |
| 10 | `weekend_ddp` | 128–131 | 131 | `EXTRACT(dow FROM trade_date) IN (0,6)` | 0 | Zero-invariant | C5 invariant (no weekend rows) | **Yes** — vacuous on empty table |

### 3.1 Historical repair invariants vs current-state acceptance criteria

The distinction the task asked for, made explicitly:

**Genuinely historical repair invariants** — artefacts of C1/C2/C3/C5 that remain meaningful as
*statements about data if data exists*: #3 (`fii`), #4 (`GIFT_NIFTY`), #6 (lot source discipline),
#7 (orphans), #8 (duplicate contract/date), #9 (semantic duplicates), #10 (weekend rows).

**Population-shaped acceptance criteria that are retired**: #1 (75,962), #2 (1,732,338), #5 (74,902).

**Not all assertions are obsolete merely because some counts are retired.** Assertions 3, 4, 6–10
express real data-quality invariants and should survive any remediation. The audit does **not**
recommend deleting them.

### 3.2 Two of the invariants are doubly guaranteed

#8 and #9 assert uniqueness that the *schema* already enforces
(`uq_derivative_daily_prices_contract_date`, `uq_derivative_contracts_idempotency_hash`, measured in
M1-B §6). They can only fail if the schema constraint is absent or dropped, so they are cheap
sanity checks rather than population evidence.

### 3.3 An arithmetic implication of the retired assertions

If assertions #1, #5 and #6 had all held simultaneously on some database, then that population
contained **74,902 `OPTION` contracts and 1,060 `FUTURE` contracts** (`75,962 − 74,902 = 1,060`),
since no `OPTION` row had `lot_size != 1`. **This is an inference from the constants, not a
measurement**, and it is recorded only to show that `74,902` is inextricably part of the retired
75,962-contract claim rather than an independent one.

Note the vocabulary mismatch that follows: 1,060 historical *futures contracts* is not 28,508
historical *futures rows*, and neither is 28,837. These are different units and must not be
conflated.

---

## 4. Repository-wide reference inventory

Search performed over `app/`, `tests/`, `scripts/`, `migrations/`, `Makefile`, `pyproject.toml`,
`.github/`, and `docs/`, for both bare (`1732338`) and formatted (`1,732,338`) forms of all five
retired figures, plus the dependent `74,902`.

### 4.1 Classification legend

1. Historical evidence, correctly labeled
2. Active acceptance criterion
3. Executable assertion
4. Test fixture or expected value
5. Superseded status claim
6. Unclear; requires follow-up

### 4.2 Inventory

| Location | Figure(s) | Class | Notes |
|---|---|---|---|
| `scripts/g8_pre_repair.py:399-410` | 75,962 / 1,732,338 / 74,902 | **3 — executable assertion** | The only executable gate. **This is the finding.** |
| `scripts/g8_pre_repair.py:81-85` | — | **3** | The `COUNT(*)` producer queries the constants are compared against |
| `docs/AI_HANDOFF.md:24` (retired table) + `:55` | 75,962 / 1,732,338 | **5 — superseded status claim** | M1-E banner added at line 22; table retained verbatim. Line 55 still asserts "All 10 assertions PASSED" |
| `docs/05_Development/PROJECT_STATE.md:15` + retired block | 75,962 / 1,732,338 | **5 — superseded** | M1-E banner at line 22. Line 15 still lists `--verify` as a passing quality gate |
| `docs/CURRENT_TASK.md:34` + retired block | 75,962 / 1,732,338 | **5 — superseded** | M1-E banner at line 35, which also notes `--verify` no longer passes |
| `BROWSER_INSPECTION_REPORT.md:28,124,125` | 75,962 / 28,508 / 1,703,830 / 576 | **1 — historical evidence** | 2026-09-03 UI observation; primary source; untouched |
| `docs/19.1J_R5_R2A_R1_RECONCILIATION.md:112` | 576 | **1 — historical evidence** | Documents the `24 × 24 = 576` composition; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1.md` | all five | **1 — historical evidence** | Audit report; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1_INDEPENDENT_AUDIT.md` | all five | **1** | Audit report; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md:45,110,527-528,535,542,614,624` | all five | **1** | Audit evidence, including detailed script line citations; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md:60` | 75,962 / 1,732,338 | **1** | Untouched |
| `docs/OPT_DATA_RECONCILIATION_M1B_DATABASE_REPORT.md` | all five | **1** | Correctly labels them `[CLAIM]`; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1C_PROVENANCE_REPORT.md` | all five | **1** | Provenance findings; untouched |
| `docs/OPT_DATA_RECONCILIATION_M1D_TEST_DATABASE_REPORT.md` | all five | **1** | Untouched |
| `docs/OPT_DATA_RECONCILIATION_M1E_RETIREMENT_DECISION.md` | all five | **1** | The retirement record itself; cites them as retired |
| `docs/OPT_DATA_COVERAGE_M0.md` | historical framing | **1** | Untouched |
| `docs/G11OPT8_TEST_REPORT.md:57`, `docs/G11OPT14E_C_IMPLEMENTATION_REPORT.md:213` | test-suite names only | **1** | Reference `tests/test_g8_pre_repair.py` by name; no figures |
| `docs/OPT_DATA_AUDIT*.md`, `docs/OPT_DATA_AUDIT_EVIDENCE_INDEX.md` | — | 1 | Index/narrative only |
| `audit_payload.txt` | all five | **1** | Historical audit payload copy; untouched |
| `tests/test_g8_pre_repair.py` | **none** | — | See §4.3 |
| `app/**` | **none** | — | Counts are *computed*, never asserted |
| `migrations/**`, `Makefile`, `pyproject.toml`, `.github/` | **none** | — | No gate config |

### 4.3 Test-suite verification (the critical distinction)

`tests/test_g8_pre_repair.py` contains 53 tests and references `g8_pre_repair` 11 times, but:

| Test | Line | What it actually checks | Asserts a retired figure? |
|---|---|---|---|
| `test_verify_entry_point_exists` | 788 | `"async def main()"`, `"async def verify("`, `'if __name__ == "__main__":'` present in source text | **No** |
| `test_verify_production_db_guard` | 796 | `_is_test_db()` returns True for names containing `test`, False for `ai_stock_scanner` | **No** |
| `test_verify_refuses_test_database` | 805 | `verify(mock_session)` raises `RuntimeError` for a mocked test DB | **No** |

**The test suite therefore passes regardless of database contents or retirement status.** It tests
that the gate *exists* and that its guard *works* — not that its expectations are valid. This is why
"53/53 passing" and "1,609/1,609 passing" coexist with an unsatisfiable gate.

A repository-wide search for the script's runtime banner (`PRE-G.8 VERIFICATION MODE`) or a captured
`[PASS] derivative_daily_prices` line returns **zero** results — the same negative result M1-A
recorded. **No retained artefact shows the gate ever passing.**

### 4.4 Unrelated hardcoded gates found (not retired, not in scope)

Recorded for completeness so the search is auditable — these use **different datasets** and must not
be conflated with the retired derivative counts:

| Location | Constants | Dataset |
|---|---|---|
| `scripts/ingest_participant_q4_2025.py:225-229` | `q4_sessions == 62`, `q4_rows == 1488`, `pit_violations == 0`, `negative_counts == 0` | Q4-2025 participant volume/positions — **not** the retired 576 or 1.73M figures |
| `scripts/verify_g11c_production.py:100,134` | `deals_count == 25524`, `deals_sessions == 226`, `ca_count == 1222`, `ann_count == 132805` | Institutional flows / corporate actions — unrelated |
| `scripts/ingest_g11c_historical.py:97` | `Stock.id == 33792` | Single-row alias lookup — unrelated |

### 4.5 Remaining active acceptance criteria that still treat retired counts as verified

| # | Still active | Status |
|---|---|---|
| 1 | `scripts/g8_pre_repair.py` assertions #1, #2, #5 | **ACTIVE — unsatisfiable, asserts retired values** |
| 2 | `docs/AI_HANDOFF.md:55` — "All 10 assertions PASSED (exit code 0)" listed under *Quality Gates* | **Active claim, superseded** by the M1-E banner 33 lines above but not itself annotated |
| 3 | `docs/05_Development/PROJECT_STATE.md:15` — `--verify` listed among passing quality gates | **Active claim, superseded** by the banner below it |
| 4 | `docs/CURRENT_TASK.md:34` — "All 10 assertions PASSED" | **Superseded** by the M1-E banner at line 35, which explicitly states it no longer passes |

Items 2 and 3 are the only places where a *superseded* status claim sits outside the immediate
vicinity of its banner. They are historical status documents, not gates, so this is a labelling
residual rather than an active gate.

---

## 5. Current-state implications

### 5.1 Known state of the two inspected local databases (from M1-B/M1-C/M1-D)

| Measurement | `ai_stock_scanner` | `test` | Assertion # |
|---|---:|---:|---|
| `derivative_contracts` | 2,781 | 0 | 1 (expects 75,962) |
| `derivative_daily_prices` | 0 | 0 | 2 (expects 1,732,338) |
| `fii_derivative_statistics` | 0 | 0 | 3 (expects 0) |
| `derivative_contracts` where `OPTION` | 2,781 | 0 | 5, 6 |
| `derivative_participant_positions` | 0 | 0 | — |

### 5.2 Which assertions are satisfiable today

| # | Assertion | Satisfiable in current dev DB? | Basis |
|---|---|---|---|
| 1 | 75,962 contracts | **No** | measured 2,781 |
| 2 | 1,732,338 daily prices | **No** | measured 0 |
| 3 | FII = 0 | Yes | measured 0 |
| 4 | GIFT_NIFTY = 0 | Probably yes | not directly measured; `market_indices` contains no confirmed `GIFT_NIFTY` code |
| 5 | 74,902 OPTION lot_size=1 | **Almost certainly no** | 2,781 option contracts exist at most; **exact `lot_size=1` count never measured** |
| 6 | OPTION lot_size≠1 = 0 | Yes (vacuously) | 2,781 option contracts, all `lot_size > 0` (M1-B §7); whether any equal 1 is unmeasured |
| 7 | orphans = 0 | Yes | measured 0 (M1-B §7) |
| 8 | duplicate contract/date = 0 | Yes | schema-enforced + 0 rows |
| 9 | semantic duplicates = 0 | Yes | measured 0 groups |
| 10 | weekend rows = 0 | Yes | 0 rows |

**Net:** at least two, and probably three, of ten assertions cannot be satisfied.

### 5.3 The vacuity problem

On the current `ai_stock_scanner` database, seven assertions pass while the derivative dataset is
completely absent. A gate that reports 7/10 green on an empty dataset is not a population
guard — it is a schema/consistency guard wearing a population guard's clothes. Any future remedy
should not present the zero-invariants as evidence the data is intact.

---

## 6. Static verification-path analysis — INFERRED, NOT EXECUTED

The script was **not run** and **no database was connected** during this audit. Everything below is
a deduction from source code.

### 6.1 Control flow of `--verify`

```
main()                                          line 359
 ├─ argparse: --verify / --repair               361-378
 ├─ if neither → print help, exit 1             377-378
 ├─ f = get_session_factory()                   line 380   ← DB SESSION OPENED HERE
 ├─ async with f() as s:                        382
 ├─ db_name = _get_db_name(s)                   383  (reads bind.url.database)
 ├─ if _is_test_db(db_name) → exit 1            384-386
 ├─ if db_name in FORBIDDEN_DB_NAMES → exit 1   387-389
 ├─ print banner (db name + UTC timestamp)      391-395
 ├─ v = await verify(s)                         397
 │    ├─ guard: _is_test_db → RuntimeError      72-76
 │    └─ 10 × SELECT COUNT / aggregate          81-131
 ├─ expected_values = {...}                     399-410
 ├─ loop 10 keys → [PASS]/[FAIL]                412-431
 └─ all_pass ? exit 0 : exit 1                  434-439
```

### 6.2 Answers to the required questions

| Question | Answer | Basis |
|---|---|---|
| Which functions does `--verify` invoke? | `main` → `get_session_factory` → `_get_db_name` → `_is_test_db` → `verify` → the 10 `SELECT`s. Never `repair_c1`, `repair_c2`, `repair_c2_v2`, `_get_index_map`, `_canonical_fingerprint`, or `_fmt`. | static |
| Does it read from a database? | **Yes** — a session is created at line 380 and 12 `SELECT`s run inside `verify()`. It cannot run without a reachable database. | static |
| Does it mutate data? | **No** — 0 `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` and 0 `commit()` inside lines 68–133. The write paths are unreachable from `--verify`. | static |
| What exit status would it likely produce against the documented current state? | **Exit 1.** Assertions #1 and #2 fail definitively (2,781 ≠ 75,962; 0 ≠ 1,732,338), and #5 almost certainly fails. **This is an inference from code plus previously documented database state — not an execution result.** | inferred |
| Which assertions cannot be evaluated without database access? | **All ten.** Every one is a live `COUNT`/aggregate. None can be evaluated statically. | static |

### 6.3 Guard behaviour — two defects

**Defect A — the docstring is wrong.** Line 69 says *"Post-repair verification — read-only,
production-database only."* Nothing enforces "production only". `FORBIDDEN_DB_NAMES` (line 20) is
`{"postgres", "production", "prod", "master"}`; `_is_test_db` (31–34) is a substring test for
`test`. Consequence: `ai_stock_scanner` **passes both guards** and the gate would run against the
local development database.

**Defect B — the guard has no allow-list.** It is a deny-list, so any new database name is permitted
by default. A deny-list that omits the project's actual database is not protecting it.

Neither defect is a retired-baseline issue, and neither is repaired here. They matter because they
mean the gate *would* run against the dev database and *would* fail there — the retirement problem is
live, not theoretical.

### 6.4 Incidental observation (out of scope)

`repair_c2` (line 182) and `repair_c2_v2` (line 273) are byte-identical duplicated
implementations; `--repair` calls `repair_c2`. Recorded only so the next reader does not assume the
duplication is intentional versioning.

---

## 7. Smallest safe proposed remediation — PROPOSED ONLY, NOT IMPLEMENTED

Constraints on any future change: historical repair evidence must stay immutable; the archive's
counts must **not** be substituted into these assertions; M1-E labels must not be weakened.

### 7.1 Separation of the four concerns

| Concern | Where it belongs | Rationale |
|---|---|---|
| **Immutable historical repair evidence** | Stays in `docs/` (`BROWSER_INSPECTION_REPORT.md`, M0/M1/M1-A…M1-E, `audit_payload.txt`). `scripts/g8_pre_repair.py` is retained unmodified as a committed historical artefact | Deleting or rewriting the constants would destroy the record of what was asserted on 2026-09-09 |
| **Current database health checks** | Assertions #3, #4, #6, #7, #8, #9, #10 — the zero-invariants | These are the only ones that are true data-quality statements today |
| **Archive-derived population validation** | A **separate** check that reads `docs/data-audit/recount_result.json` (1,731,456 / 1,702,619 / 28,837) and compares against *archive contents*, not a database | Different dataset — see §7.3 |
| **Checks requiring authorized DB access** | Anything that queries `derivative_daily_prices`, `derivative_contracts`, `market_indices` | Requires explicit authorization; must not run in CI or by default |

### 7.2 Minimal change to `scripts/g8_pre_repair.py`

**Option A (recommended — smallest that removes the gate's force while preserving evidence):**

1. Rename the dict to `RETIRED_BASELINE_EXPECTATIONS` and add a docstring reference to M1-E. The
   numbers stay, byte-for-byte, with their status attached.
2. Add `--assert-retired-baseline` (default **off**). With it off, the three population assertions
   print as `[INFO] <key> = <measured> (retired baseline; not asserted — see M1-E)` and do **not**
   affect the exit code.
3. Keep the seven zero-invariants as hard `[PASS]`/`[FAIL]` assertions, always. Exit code reflects
   only those.
4. With `--assert-retired-baseline` on, restore exactly today's behaviour (all ten compared, exit 1
   on mismatch), so the historical gate remains reproducible for forensics.
5. Fix the line-69 docstring to describe what the guards actually do.

**Why this is the smallest safe change:** `tests/test_g8_pre_repair.py` requires only that
`async def verify(` and `async def main()` exist and that `_is_test_db` behaviour is unchanged — all
three survive Option A untouched. **No test change is needed**; no application code, schema,
migration, or template is affected.

**Option B (larger, not recommended now):** split the harness into `g8_pre_repair.py` (frozen
historical) and a new `scripts/derivehealth.py` (current health + archive validation). Cleaner
separation, but creates a new surface and rewrites call sites, so it exceeds "smallest safe".

**Option C (rejected):** deleting the retired assertions. It destroys the historical record, cannot
distinguish "retired" from "never asserted", and contradicts M1-E's preservation rule.

### 7.3 Why the archive counts must NOT be substituted into these assertions

This is the trap the remediation must avoid. These measure **different populations**:

| Aspect | Archive recount (1,731,456) | `derivative_daily_prices` (1,732,338 claimed) |
|---|---|---|
| Unit | raw bhavcopy CSV data lines | one row per persisted `(contract_id, trade_date)` |
| Versioned by | nothing | `available_at` / `fetched_at` PIT semantics |
| Vocabulary | `FinInstrmTp ∈ {STO, IDO, STF, IDF}` | `instrument_type ∈ {OPTION, FUTURE}` |
| De-duplication | none | `UNIQUE (contract_id, trade_date)` enforced by schema |
| Window | 45 archive sessions within 2026-06-08 … 2026-08-10 (a 64-calendar-date range) | undefined for the claim |
| Scope | archive completeness | database contents |

Substituting 1,731,456 for 1,732,338 would produce a **permanently unsatisfiable and misleading**
assertion: a correct import that de-duplicated, filtered unmapped contracts, or mapped
`STO`→`OPTION` could legitimately land on a different number. The 882-row gap between them is
explicitly **UNRESOLVED** in M1-E, so encoding either number as an expectation would assert an
unproven equivalence.

**The correct form** is a separate archive-integrity check that compares `recount_result.json`
against a re-run of `docs/data-audit/recount.py` — archive-to-archive, never archive-to-database.

### 7.4 What must not change

- `docs/` historical reports — untouched, per M1-E §10.
- The M1-E banners in the three status documents — they are the current correct labels.
- `74,902` must be added to the M1-E retired register as a **dependent** value (a documentation
  amendment, not a code change) so the register matches reality.

---

## 8. Unknowns and authorization boundaries

| Unknown | Why it matters | What would resolve it | Authorized here? |
|---|---|---|---|
| Exact `lot_size = 1` count among the 2,781 current `OPTION` contracts | Decides whether assertion #5 fails or merely cannot be evaluated | one authorized read-only `SELECT` | **No** |
| Whether `market_indices` contains a `GIFT_NIFTY` row | Decides whether assertion #4 is vacuous or meaningful | one authorized read-only `SELECT` | **No** |
| Whether `--verify` ever actually passed | The three status documents assert it did, but no stdout survived; M1-A's search for the banner returned zero results | retained run log, or a fresh authorized run with stdout archived | **No** |
| Which database the 2026-09-09 certification ran against | Determines whether the retired counts describe `ai_stock_scanner` at all | the same run log | **No** |
| Whether the current `ai_stock_scanner` state predates or postdates 2026-09-09 | The gate is unsatisfiable now; when it became so is unknown | server logs | **No** |
| Production `aistock` contents | Never authorized, never contacted | explicit separate authorization | **No** |

**Not performed, by mandate:** no database connection; no script execution (including `--verify`);
no migrations, imports, backfills, repairs; no provider or broker calls; no credential or `.env`
inspection; no file modification; no Git mutation.

**Scope note:** `--verify` cannot be exercised against the `test` database even with authorization —
`_is_test_db` rejects it by design (lines 31–34, 72–76, 384–386), and that database is empty. Any
validation must target an explicitly authorized non-test database.

---

## 9. What this audit does not claim

- **It does not claim the script currently fails.** That is an **inference** from static code plus
  previously documented database state, labelled as such in §5 and §6.2. It was not executed.
- **It does not claim the retired data is lost.** Unchanged from M1-C/M1-E: recoverability is
  `UNVERIFIED`.
- **It does not claim the archive reproduced the database.** The 882-row gap remains `UNRESOLVED`.
- **It does not repair anything.** §7 is a proposal only.
- **It does not authorize M2**, which remains on **HOLD**.

---

## 10. Git integrity confirmation

| Check | Value |
|---|---|
| Branch before / after | `checkpoint/sprint19-20-20260822` / `checkpoint/sprint19-20-20260822` |
| HEAD before / after | `b2df61f1d56c5a5032787c96678326d0a73f6173` / identical |
| `git status --short` before | 339 lines |
| `git status --short` after | 340 lines — **+1**, the new report only |
| Staged files | 0 — nothing staged |
| Commit / push | none |
| Reset / clean / stash / checkout / restore / branch switch | none |
| Files modified by this audit | **none** |
| Files created by this audit | `docs/OPT_DATA_RECONCILIATION_M1F_ACCEPTANCE_GATE_AUDIT.md` (this file) only |
| `scripts/g8_pre_repair.py` modified | **no** — SHA-256 unchanged at `8ecccc08…` |
| M1-E decision record modified | **no** |
| M1-A / M1-B / M1-C / M1-D reports modified | **no** |
| Audit evidence under `docs/data-audit/` modified | **no** |
| `BROWSER_INSPECTION_REPORT.md` / `audit_payload.txt` modified | **no** |
| Application code, schema, migrations, tests, templates, config modified | **none** |
| Database connections | **none** |
| SQL executed | **none** |
| `scripts/g8_pre_repair.py` executed (incl. `--verify`) | **no** |
| Credentials / `.env` read | **no** |
| M2 status | **HOLD** |
