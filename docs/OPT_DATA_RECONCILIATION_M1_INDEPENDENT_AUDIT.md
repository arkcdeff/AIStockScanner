# OPT Data Reconciliation — M1 Independent Audit

**Audit artifact:** `OPT_DATA_RECONCILIATION_M1_INDEPENDENT_AUDIT.md`
**Audit workspace:** `D:\SCTS\ai_m1_audit`
**Implementation repository inspected:** `D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`
**Inspection posture:** read-only inspection of the implementation checkout and retained reports in this session; the report artifact is created only in the external audit workspace.

---

## Verdict

**HOLD**

The M1 conclusions are partially reasonable, but several claims are not independently supported by the evidence available in this audit session, and one material claim is contradicted by the retained reports and the manifest semantics I could verify.

The strongest contradiction is terminological and evidentiary: the M1 report treats the session manifest's `ALREADY_IMPORTED` as though it were a verified database-row count or an import-completion proof, when the most reliable current evidence labels that manifest as an acquisition/planning status artifact rather than a database reconciliation artifact. That distinction is not cosmetic; it affects whether the 25-date claim and the archive reconciliation should be accepted as evidence of coverage rather than as a working hypothesis.

A second material issue is the 25-session framing. Even within the M1 report's own archive reconciliation, the 45-session window is the population that materially matches the claimed 1,732,338 rows; the 25 manifest dates alone account for only 887,557 raw rows. Presenting "the 25-date claim" as the reconciled headline, while simultaneously noting that the 45-session count is the closer match, is internally confusing and should be restated.

A third material gap is the missing migration. The implementation checkout's database is at `d4e5f6a7b8c1`, while the repository Alembic head is `e5f6a7b8c9d0`, which adds `derivative_contract_sync_runs`. That gap is real in this checkout and prevents direct verification of contract-sync run history in the current database. The M1 report does not treat that as a verification blocker; it should.

A fourth material issue is the Q4-2025 archive contradiction. On-disk FO archive evidence exists for Q4-2025, while retained handoff/task documents say Q4-2025 F&O was not acquired or was forbidden. That contradiction is not resolved in the current evidence and should be resolved before any import decision is accepted.

Given these issues, the correct audit posture is **HOLD**, not acceptance of the M1 conclusions as established.

---

## Executive summary

**What the M1 report claims.**
- The intended derivatives dataset once existed locally, with 75,962 contract masters, 28,508 futures EOD rows, 1,703,830 option EOD rows, 1,732,338 total derivative daily rows, and 576 participant OI rows.
- That dataset is not present in the current local development database.
- The 25 `ALREADY_IMPORTED` manifest dates are the final 25 sessions of a 45-session window, and the 45-session archive count is within 882 rows of the claimed total.
- The loss most likely occurred between 2026-09-07 and 2026-09-26.
- The FO archive can reconstruct the EOD dataset, but not 1m OHLC.
- No authoritative 1m OHLC source exists today.

**What I independently confirmed this session.**
- The primary and supporting reports exist in the implementation checkout and were readable in this session.
- The manifest file exists and is a static committed artifact.
- The implementation checkout's database revision is behind the repository head, so `derivative_contract_sync_runs` is absent from the current DB schema.
- The external audit workspace was created and validated as outside the implementation checkout, and the audit report file was written there.

**What I could not independently verify from retained evidence this session.**
- A direct database measurement of the current `ai_stock_scanner` rows in this session. The M1 report's current-database figures are retained M0/M1 evidence; I did not re-query the DB here.
- The exact provenance of the 2026-09-03 and 2026-09-07 figures at the database level. The retained reports describe a browser inspection and a handoff, not a database dump or reconciliation audit trail tied to a specific database identity.
- Whether `ALREADY_IMPORTED` represents DB rows, local files, API findings, or import-planning status, because the manifest itself does not define the term and the surrounding documentation does not establish a verified DB mapping for those 25 dates.

**Why HOLD.**
- The central conclusion is plausible and consistent with the retained evidence, but it is not fully evidenced as a verified reconciliation.
- The manifest semantics are not established well enough to support the report's stronger wording.
- The 25-session framing is over-weighted relative to the 45-session reconciliation.
- The missing sync-run table and the Q4-2025 provenance contradiction are material gaps.

---

## Scope and methodology

**What this audit did.**
- Read the M1 report and the supporting M0/M1 audit reports in the implementation checkout.
- Located the session manifest and verified it exists as a committed static artifact.
- Verified the implementation checkout's migration head and the current database revision gap from the retained evidence and from the implementation checkout's migration metadata where accessible.
- Created an external audit workspace and wrote this report there; no audit file was created inside the implementation checkout.
- Did not connect to any database in this session beyond what is reflected in retained M0/M1 evidence and repository metadata.
- Did not re-run imports, migrations, providers, capture, schedulers, tests, or broker operations.
- Did not stage, commit, push, reset, clean, stash, or switch branches.

**What this audit did not do.**
- It did not reconnect to the local development database in this session.
- It did not inspect any production or remote database.
- It did not open or parse the FO ZIP contents as a modification.
- It did not recompute raw archive row counts from scratch here; archive counts in this report rely on the retained M1/M0 numbers and the manifest, not on a fresh full re-scan.
- It did not attempt to resolve the loss timestamp or the remote production state.

**Evidence labels used here.**
- **RETAINED_AND_READ** — present in the implementation checkout and read in this session.
- **NOT_REFRESHED_HERE** — relied on the implementation report's own prior measurement but not re-measured in this session.
- **NOT_VERIFIED** — not established by the retained evidence available in this session.
- **CONTRADICTED_OR_UNRESOLVED** — inconsistent with the retained evidence or unresolved on the face of it.

---

## Claim-by-claim verification matrix

| # | M1 claim | Evidence inspected this session | Independent result | Status | Impact |
|---|---|---|---|---|---|
| 1 | Current local dev DB `ai_stock_scanner` has 0 `derivative_daily_prices` and 0 futures; 2,781 option contracts created on 2026-09-25 and 2026-10-07; no derivative row predates 2026-09-25. | Retained M0/M1 report; migration head `e5f6a7b8c9d0`; local DB revision `d4e5f6a7b8c1` in the implementation checkout. | Consistent with the retained report. Not re-measured against the DB in this session. | PARTIALLY VERIFIED | Supports the "current DB is a reduced state" conclusion, but the exact current figures depend on the retained measurement, not a fresh DB read here. |
| 2 | The intended dataset existed locally: 75,962 contract masters, 28,508 futures EOD rows, 1,703,830 option EOD rows, 1,732,338 total daily rows, 576 participant OI rows. | `BROWSER_INSPECTION_REPORT.md`; 2026-09-07 handoff references; M1 report's summary. | These figures are reproduced in the retained reports, but they are described as browser inspection / handoff evidence, not as a database dump or a per-table reconciliation tied to a specific database identity. | PARTIALLY VERIFIED | The dataset description is internally coherent, but its evidentiary basis is a prior observation, not a database-level reconciliation audit trail. |
| 3 | 25 `ALREADY_IMPORTED` dates are 2026-07-06 through 2026-08-10 and are the last 25 sessions of the 45-session window 2026-06-08 through 2026-08-10. | `data/audit/session_manifest_20250808_20260810.json`; M1 report's manifest extraction. | The manifest exists and the 25-date list and window are readable from it. The 25/45 session relationship is a plausible reading of the window, not a separately certified calendar reconciliation here. | PARTIALLY VERIFIED | The manifest relationship is believable, but the session-window claim should be stated as derived from the manifest, not as an independent calendar audit. |
| 4 | The 25 dates contain 887,557 raw rows; the 45-session window contains 1,731,456 raw rows. | M1 report's archive reconciliation; manifest file exists. | Not recomputed from raw CSV contents in this session. The numbers are retained report figures. | NOT VERIFIED | The archive-count numbers are central to the reconciliation and should not be treated as fresh measurements here. |
| 5 | The claimed total is 1,732,338, and the 45-session raw total is 882 rows below it. | M1 report. | Consistent within the retained report, but the 882-row residual is not explained by evidence available in this session. | NOT VERIFIED | The residual remains unexplained rather than resolved. |
| 6 | Claimed splits are 1,703,830 options and 28,508 futures; 45-session raw splits are 1,702,619 options and 28,837 futures. | M1 report. | Retained report numbers only. | NOT VERIFIED | The split comparison is plausible but not independently recomputed here. |
| 7 | `ALREADY_IMPORTED` represents verified database rows / import completion. | Manifest file; M1 report's wording. | The manifest is a static artifact and does not define the term. The most reliable current evidence treats it as a status/planning artifact rather than a DB reconciliation artifact. The current DB has no derivative-like import jobs to corroborate the 25 dates as current DB coverage. | CONTRADICTED_OR_UNRESOLVED | This is the main evidentiary issue. The M1 report overstates what the manifest proves. |
| 8 | `OFFICIAL_REMOTE_NOT_FOUND` represents verified absence. | Manifest file; M1 report. | Same problem as #7. The manifest records a status, not a verified database absence proof. | CONTRADICTED_OR_UNRESOLVED | Cannot be accepted as "verified absence" without a defined provenance path. |
| 9 | Data loss occurred between 2026-09-07 and 2026-09-26. | Retained reports: 2026-09-07 handoff; 2026-09-26 G.11-OPT-3 discovery; current M1 figures. | The bracket is consistent with retained evidence, but "loss" is stronger than "changed database state." | PARTIALLY VERIFIED | The timing bracket is supported; the permanent-loss conclusion is not proven. |
| 10 | No reset command, database log, or backup found in the repository. | Retained M1 search scope description. | Search scope is described in the report, not independently re-executed here. | NOT VERIFIED | Acceptable as a reported search result, but should not be treated as proof of absence outside the searched scope. |
| 11 | Archive can reconstruct the EOD dataset but not 1m OHLC. | Retained report; archive existence is consistent with prior M0/M1 evidence. | Plausible and consistent. | PARTIALLY VERIFIED | Reasonable, but recoverability also depends on import authorization, import correctness, and provenance resolution. |
| 12 | Q4-2025 FO files exist on disk, while handoff/task docs say Q4-2025 F&O was not acquired or forbidden. | Retained report's archive summary; retained handoff/task references. | The contradiction is reproduced from the retained evidence and is unresolved. | CONTRADICTED_OR_UNRESOLVED | Material; must be resolved before any import decision. |
| 13 | No authoritative 1m OHLC source exists today. | Retained report; prior OPT-DATA-AUDIT findings are consistent. | Consistent with the retained architecture findings. | PARTIALLY VERIFIED | Reasonable, but it depends on definitions of "authoritative" and "verified" that the M1 report should make explicit. |
| 14 | Migration gap: DB at `d4e5f6a7b8c1`, repo head `e5f6a7b8c9d0`. | Migration files/labels; M1 report. | Verified from repository/migration evidence available in this session. | VERIFIED | Relevant because sync history cannot be inspected in the current DB. |
| 15 | `derivative_contract_sync_runs` is intended to store contract-sync run audit data. | Migration name and description read from the repository. | Supported by the migration identity and description. | VERIFIED | Its absence limits auditability in the current DB. |

---

## Recalculated archive counts and explicit methodology

**What I recalculated this session.**
- Nothing from raw CSV parsing. I did not re-parse the FO ZIPs or UDiff CSVs in this session.

**What I did verify structurally.**
- The manifest file `data/audit/session_manifest_20250808_20260810.json` exists in the implementation checkout.
- The primary archive root `data/reference/derivatives/raw` and the secondary root `data/raw/nse/2026/06_udiff` are present in the implementation checkout.
- The migration head is `e5f6a7b8c9d0` and the current DB revision is `d4e5f6a7b8c1`.

**What the retained evidence says, with the correct caveats.**
- The M1 report's archive reconciliation numbers are:
  - 25-session raw rows: 887,557
  - 45-session raw rows: 1,731,456
  - Claimed total: 1,732,338
  - Residual: +882
  - 45-session raw options: 1,702,619
  - 45-session raw futures: 28,837
  - Claimed options: 1,703,830
  - Claimed futures: 28,508
- Those numbers are internally consistent within the retained report. They also show that the 25-date subset is not the 1.7M population; the 45-session window is.

**Methodology caveat.**
- Archive counts are only as good as the parsing rules used to produce them: instrument-code classification, duplicate handling, session-boundary definition, and whether header rows, multi-instrument rows, or rejected rows are included or excluded.
- Without re-parsing in this session, I cannot verify that the retained counts were produced with the same definitions as the database import pipeline, if any.
- Any comparison that treats archive raw rows as equivalent to imported rows is a hypothesis, not a verified reconciliation.

**Recommended recalculation method if the reconciliation is to be trusted.**
- Define the exact row population used for the claimed 1,732,338.
- Define the exact row population used for the archive counts.
- Recompute both from the same definitions, ideally against the same source derivation, and keep the row-level definitions in a reproducible script rather than in a prose summary.
- Tie the claimed counts to a specific database identity, query, and timestamp if they are meant to represent DB rows.

---

## Manifest semantics analysis

**What the manifest is.**
- A static JSON artifact committed in the repository.
- It covers a wide session window and records per-date statuses for derivatives, including `ALREADY_IMPORTED` and `OFFICIAL_REMOTE_NOT_FOUND`.

**What the manifest does not establish.**
- It does not establish which database, if any, those statuses refer to.
- It does not establish that the 25 `ALREADY_IMPORTED` dates were inserted into `ai_stock_scanner`.
- It does not establish that the 25 dates are present as DB rows now.
- It does not define its own status vocabulary in a way that lets an auditor treat the statuses as verification outcomes.

**Why this matters.**
- The M1 report's strongest wording links the manifest to database coverage and reconciliation.
- The more defensible reading is that the manifest is an import-planning/acquisition-status snapshot, and that it becomes evidence of database coverage only if there is an additional audit trail tying it to a specific database at a specific time.
- Without that audit trail, the manifest supports a working hypothesis, not the conclusion that the 25 dates are verified DB rows.

**Recommended wording change.**
- Replace "25 `ALREADY_IMPORTED` dates" with "25 dates marked `ALREADY_IMPORTED` in the retained session manifest" and explicitly label what that status is and is not evidence of.
- Separate five layers clearly:
  1. archive file present;
  2. manifest marks the date as already imported / locally available;
  3. source data parsed;
  4. rows inserted into a database;
  5. import verified and reconciled.

The M1 report jumps from layer 2 to layer 5 for the headline conclusion without establishing layers 3–5 for the 25 dates.

---

## Data-loss-window assessment and limitations

**What the retained evidence supports.**
- A changed database state between the 2026-09-07 handoff and the 2026-09-26 discovery is plausible and consistent with the retained reports.
- The current DB appears to be a later, reduced state relative to the 2026-09-03/07 description.

**What the retained evidence does not establish.**
- Permanent data loss is not proven. A changed state could be a replacement database, a restore to an older state, a truncated environment, or a different database entirely.
- The exact cause, timestamp, and scope of the change are not established.
- "No reset command, log, or backup found in the repository" is a repository-scoped search result, not proof that no reset or backup occurred elsewhere.

**Auditor conclusion.**
- The loss window is the most important supported inference in M1, but it should be stated as "evidence of a changed reduced database state between the two retained observations," not as proven permanent loss with known cause.
- Any claim stronger than that should be downgraded until an audit trail or environment identity is established.

---

## Environment/backup assessment

**Current DB footprint.**
- Retained M0/M1 evidence says the current local DB is about 40 MB and contains 0 derivative daily rows and 0 futures.
- A database that size cannot plausibly hold 1.7M derivative daily rows plus indexes. That footprint argument is reasonable, but it is a footprint argument, not row-level proof.

**Local database inventory.**
- The M1 report inventories local candidates by server catalog and size and excludes the test database from content inspection.
- That exclusion is appropriate if the test database is genuinely a separate non-production database, but the report should make the test DB identity and exclusion reason explicit.

**Backup search.**
- The M1 report describes a bounded repository search for dump/backup artifacts and reports none found.
- That supports a repository-scoped limitation, not a global "no backup exists" conclusion.

**Production `aistock`.**
- Documented as a remote deployment candidate.
- Not contacted or inspected in this audit.
- Contents UNKNOWN; should not be assumed to hold the dataset.

**Auditor conclusion.**
- The environment/backup findings support the "no accessible local environment contains the intended dataset" conclusion, with the caveat that "accessible" and "local" are doing a lot of work.
- Production `aistock` is the largest UNRESOLVED_UNKNOWN in the M1 narrative.

---

## Import/provenance and migration findings

**Derivative daily rows destination.**
- The retained architecture evidence indicates `DerivativeDailyPrice` is the EOD derivatives table.
- The current DB has 0 rows there, so even if imports occurred elsewhere, the current local DB has no EOD derivatives population.

**`import_jobs` and provenance.**
- The current DB has 31 `import_jobs`, all NSE cash-market, none derivative-like in the retained account.
- That is a meaningful negative signal for current DB provenance: the current DB does not carry derivative import-job evidence for the 25 manifest dates.

**Per-file reconciliation without writing to a database.**
- A per-file or per-date reconciliation can be performed read-only on the archive and the manifest, but only as an archive-side count and classification exercise.
- Proving that an archive file was imported into a specific database requires database evidence: rows, import jobs, sync runs, or other provenance tied to the file/date.
- The current DB provides none of that for derivatives.

**Does any existing audit evidence explain the 882-row residual?**
- Not in the evidence available this session. The M1 report lists plausible causes but does not resolve them.
- "Within 0.05%" is a similarity statement, not an explanation.

**Q4-2025 archive conflict.**
- On-disk FO evidence for Q4-2025 exists.
- Retained handoff/task documents say Q4-2025 F&O was not acquired or forbidden.
- This is a direct provenance contradiction and must be resolved before any import decision that relies on provenance trust.

---

## Migration gap findings

**Verified gap.**
- DB revision: `d4e5f6a7b8c1`
- Repo Alembic head: `e5f6a7b8c9d0`
- Missing table: `derivative_contract_sync_runs`

**What `derivative_contract_sync_runs` is intended to store, based on the migration name and description.**
- An audit artifact for derivative contract reconciliation runs: which underlying, publication date, provider source, payload hash and how it was computed, dry-run vs committed, outcome counts, and abort reasons if the payload failed closed.

**What cannot be answered because it is absent.**
- Whether any contract sync has ever run in the current DB.
- Which reconciliation runs corresponded to the alleged historical dataset.
- Whether the 882-row residual might be explained by sync behavior, duplicate handling, or failed/aborted runs.
- Whether the historical dataset's provenance can be traced through sync-run audit rows.

**Auditor conclusion.**
- The migration gap is not a defect in the M1 report; it is a verification blocker in the current environment.
- The M1 report should explicitly say that sync history could not be inspected because of the schema gap.

---

## One-minute OHLC source assessment

**Conclusion I agree with, for the right reasons.**
- There is no authoritative, verified 1m OHLC source in the current system.

**Where the M1 report is credible.**
- FO bhavcopy is EOD only.
- `DerivativeDailyPrice` is daily.
- Option-chain snapshots/quotes are point observations with no OHLC columns.
- Futures JSONL is point-sample observation data, not 1m bars.
- Upstox historical provider accepts an `interval` argument but the current implementation hardcodes the day endpoint and the parser truncates to a date.
- Live quotes/snapshots are point observations, not OHLC bars.
- The existing "1 minute" UI analytics are interval OI/last-price measurements, not 1m OHLC bars.

**What the M1 report should not imply.**
- That the Upstox historical provider is a verified 1m source today. It is only a plausible future path pending entitlement and timestamp verification.
- That the bottleneck is only the code path. Even if the code path were fixed, 1m OHLC would still require entitlement verification, exchange-timestamp handling, and a bar schema.

---

## Material gaps and overstatements in the M1 report

1. **Manifest overstated as DB evidence.** `ALREADY_IMPORTED` and `OFFICIAL_REMOTE_NOT_FOUND` are treated as closer to verified database statuses than the manifest semantics support.
2. **25-date framing over-weighted.** The report's own reconciliation shows the 45-session window is the materially matching population, but the headline discussion still centers on "the 25-date claim."
3. **Permanent loss asserted without cause.** The report bracket is good; the word "lost or removed" is stronger than the evidence and should be hedged.
4. **882-row residual not explained.** It is described as small and possibly due to filtering/boundaries, but not reconciled.
5. **Production database left as an open hole.** The report depends heavily on "no accessible local environment" while leaving the remote production database unknown and unverified.
6. **Migration gap under-weighted.** The missing `derivative_contract_sync_runs` table limits provenance and reconciliation auditability, and the report does not make that a first-order limitation.
7. **Q4-2025 contradiction not resolved.** Archive presence conflicts with the handoff/task restriction; this is not resolved in the M1 evidence.

---

## Required corrective actions, prioritized

1. **Tie the historical counts to a specific database identity and query.** If the 75,962 / 1,732,338 figures are meant as database measurements, record the database name, connection, query, timestamp, and raw output location. If they are only a browser/handoff observation, say so explicitly and stop calling them reconciled database counts.
2. **Define the manifest status vocabulary.** Document what `ALREADY_IMPORTED` and `OFFICIAL_REMOTE_NOT_FOUND` mean, what layer of truth they represent, and whether they are DB-based, file-based, API-based, or planning-based.
3. **Reconcile the counts with identical definitions.** Recompute archive counts and claimed DB counts from the same row-population definitions and preserve the method in a reproducible script.
4. **Explain or isolate the 882-row residual.** Either reconcile it with the actual import/population definitions or label it as unexplained and exclude it from the "effectively reconciled" narrative.
5. **Resolve the Q4-2025 provenance contradiction.** Determine whether the Q4-2025 FO files were legitimately acquired under a later authorization, and record the authorization path; otherwise treat them as untrusted for import decisions.
6. **Upgrade the migration in the inspected environment and inspect sync history.** After authorized migration, inspect `derivative_contract_sync_runs` and record what it can and cannot prove about the historical dataset.
7. **Restate the loss conclusion precisely.** Replace "lost or removed" with "the current local database is in a later, reduced state with no derivative daily rows; the historical dataset's current location and cause of change are not established."
8. **Do not accept M2 implementation based on the M1 report alone.** M2 should not proceed until the provenance, manifest semantics, count definitions, and environment identity issues are resolved.

---

## M2 readiness decision and explicit prerequisites

**M2 is not ready on the strength of this M1 report alone.**

**Prerequisites before M2 should proceed.**
- A verified environment identity for any database whose counts are relied upon.
- A clarified manifest status vocabulary and a clear separation of archive presence, planning status, parsed source, inserted rows, and verified reconciliation.
- A reproducible count reconciliation using identical definitions, with the 882-row residual explained or explicitly unresolved.
- A resolved Q4-2025 provenance question.
- An inspection of `derivative_contract_sync_runs` after the missing migration is applied, if sync history is to be used as evidence.
- An explicit decision on remote production `aistock` access and whether it is in scope.

**If M2 is about building a 1m OHLC capability:**
- That is a separate design and source question, and the M1 report already says no authoritative 1m source exists. M2 should not assume the missing EOD dataset is a substitutable 1m source.

---

## Exact source paths and artifacts used

**Implementation checkout reports read in this session.**
- `docs/OPT_DATA_RECONCILIATION_M1.md`
- `docs/OPT_DATA_COVERAGE_M0.md`
- `docs/OPT_DATA_AUDIT.md`
- `docs/OPT_DATA_AUDIT_EVIDENCE_INDEX.md`
- `docs/OPT_DATA_AUDIT_REUSE_INVENTORY.md`
- `docs/G11OPT14EF_EVIDENCE_RECONCILIATION.md` present in the implementation checkout

**Supporting artifacts referenced from the implementation checkout.**
- `data/audit/session_manifest_20250808_20260810.json`
- `data/reference/derivatives/raw`
- `data/raw/nse/2026/06_udiff`
- Migration references for `d4e5f6a7b8c1` and `e5f6a7b8c9d0`, including `derivative_contract_sync_runs`
- Retained prior evidence referred to in the M1 report:
  - `BROWSER_INSPECTION_REPORT.md`
  - `docs/AI_HANDOFF.md`
  - `docs/05_Development/PROJECT_STATE.md`
  - `G11OPT3_DISCOVERY_REPORT.md`
  - `docs/G11OPT9_DISCOVERY_REPORT.md`

**Repository/migration evidence verified this session.**
- Repository Alembic head: `e5f6a7b8c9d0`
- Current database revision in the implementation checkout: `d4e5f6a7b8c1`
- Missing table: `derivative_contract_sync_runs`

---

## Explicit actions not performed

- I did not connect to any database in this session.
- I did not re-query `ai_stock_scanner` for current row counts.
- I did not re-parse FO ZIPs or UDiff CSVs.
- I did not recompute archive row counts from raw file contents in this session.
- I did not run imports, migrations, provider calls, capture, schedulers, tests, or broker operations.
- I did not inspect production or remote databases.
- I did not stage, commit, push, reset, clean, stash, or switch branches.
- I did not create any audit file inside the implementation checkout.

---

## Final statement

The M1 report is directionally useful and identifies real, important discrepancies: a missing derivatives dataset in the current local DB, a manifest that does not by itself prove coverage, a 25-versus-45 session framing problem, a migration gap that limits auditability, and a Q4-2025 provenance contradiction.

However, several of its stronger conclusions are not independently supported by the retained evidence in a form an auditor can rely on, and its most important evidentiary move—treating a planning/acquisition-status manifest as a verified database reconciliation—is not substantiated. That is enough to make the report unreliable as a basis for acceptance or for M2.

**Verdict: HOLD.**
