# Independent Audit Report — OPT Data Reconciliation M1-A Evidence Report

**Audit of:** `docs/OPT_DATA_RECONCILIATION_M1A_EVIDENCE_REPORT.md`
**Audit workspace:** `D:\SCTS\ai_m1a_audit`
**Audit date:** 2026-10-24
**Auditor role:** Independent senior data-engineering auditor
**Implementation repository reviewed:** `D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner`

---

## 1. Verdict

**ACCEPT WITH CONDITIONS**

The M1-A evidence report is internally coherent, materially improves on the prior M1 state, and its strongest quantitative claims are reproducible from independent re-measurement of the archive. The report does **not** independently verify the database-side figures, does **not** close the 882-row residual, and does **not** establish the provenance of the 1,732,338-row measurement.

Acceptance is conditioned on the explicit reservation that the report's headline "fully resolved" items include one category — the Q4-2025 provenance — that is resolved only to **authorized-by-a-later-phase**, not to **independently verified file integrity**. The report states this caveat correctly internally, but the executive framing should not be read as closing the file-integrity question.

The audit's own independent measurements corroborate:

- The 255-FO-ZIP / 234-unique-date inventory.
- The 45-session window physical row total of **1,731,456** (options **1,702,619**, futures **28,837**).
- The **71,709** distinct-`FinInstrmId` figure for the same window (where a distinct cross-date union — not a per-date sum — is the correct measure, and the report's table is read as a per-date measurement anyway).
- The **172 of 172** manifest `row_count` matches with zero deviation.
- The **7,105,914** manifest total matching `_meta.final_total_rows`.
- The **62 of 62** Q4-2025 files present, and the **2,208,035** parsed Q4 row total.
- The session manifest structure: 250 sessions, derivatives `ALREADY_IMPORTED` 25, `OFFICIAL_REMOTE_NOT_FOUND` 225, participant_oi `ALREADY_IMPORTED` 22.

The audit's independent search for an exact-match window for the claimed database figures (1,732,338 / 1,703,830 / 28,508) returned **zero matches** across contiguous archive windows ending at an archive date, including the 45-session window and its neighbors.

M2 remains **on HOLD** for the reasons stated in §9.

---

## 2. Executive summary

The M1-A report advances the prior M1 state materially. The archive counts are no longer quoted — they were recomputed by the report author from raw ZIPs and are now reproducible. The manifest vocabulary is recovered from phase documentation and corroborated against a documented 21+1 participant-OI import. The Q4-2025 "contradiction" is resolved by tracing a Git commit and a phase report.

However, three of the report's own listed open items remain open on evidence grounds, and the audit's independent window search reinforces rather than weakens the residual concern:

1. **The database identity behind 1,732,338 is still unknown.** No retained artifact names the database.
2. **The 882-row residual is still unexplained.** It is now well-characterized and localized to the database side, but its mechanism is unconfirmed.
3. **The window identification is not uniquely determined by counts.** Several windows sit within a few hundred rows; the 45-session window is best-supported by the browser "through 2026-08-10" statement, but that is a different evidence layer than the count itself.

The audit's independent measurements are recorded in `recount_result.json` and `window_search_result.json` in the audit workspace and are summarized in §5.

---

## 3. Claim-by-claim verification matrix

| # | M1-A claim (paraphrased) | Evidence relied on by M1-A | Independent check performed | Result |
|---|---|---|---|---|
| 1 | Archive counts reproduce exactly: 45-session window = 1,731,456 rows (1,702,619 options, 28,837 futures); 25 manifest dates = 887,557 | Direct recount of raw ZIPs + manifest `row_count` cross-check | Recounted all 255 FO ZIPs in two roots; recomputed window totals from ZIP contents | **Verified.** Recount produced 1,731,456 / 1,702,619 / 28,837 for the same 45 calendar-day window 2026-06-08..2026-08-10; 172/172 manifest `row_count` matches with zero deviation; manifest sum 7,105,914 = `_meta.final_total_rows`. |
| 2 | Counting method validated against producer's own numbers | Manifest `row_count` per date; 45/45 in-window match; 172-date total exact | Same as above | **Verified** for the 2026 manifest. Note: the manifest does not contain 2026-06-26, which the session manifest lists as a traded session; this is a minor internal inconsistency in the 2026 manifest, not a count discrepancy. |
| 3 | Manifest status vocabulary recovered: `OFFICIAL_REMOTE_NOT_FOUND` = HTTP 404 on official NSE historical paths; `OFFICIAL_REMOTE_FOUND` = 200 OK | `docs/AI_HANDOFF.md:127-129`, `docs/CURRENT_TASK.md:83-85` | Read both documents; searched repository for producer/consumer code | **Verified.** Vocabulary is present in the cited prose. No producer code exists in the repository. The vocabulary is acquisition-outcome, not database-coverage. |
| 4 | `ALREADY_IMPORTED` corroborated as database presence | 22 participant_oi sessions in manifest; documented 21+1 import | Counted manifest `ALREADY_IMPORTED` sets per family | **Verified** for participant_oi (22 = 21+1). Not verified at row level for any family. |
| 5 | Manifest is stale, not authoritative | Manifest committed 2026-08-22; 2026-09-07 DB had 1,732,338 rows requiring 45-session window including 20 `OFFICIAL_REMOTE_NOT_FOUND` sessions | Read manifest, `manifest_2026.json`, phase docs | **Verified as argument.** The staleness reasoning is sound and internally consistent. The 1,732,338 figure itself is not independently re-measured (no DB access); the reliance on it is conditional. |
| 6 | 882-row residual is not an archive artifact | Exhaustive window search 12,270 windows; zero duplicates; opposite-sign deltas | Independent window search across all 234 archive dates, contiguous windows ending at an archive date | **Verified direction.** Independent search found zero exact matches for the claimed DB figures. The 45-session window is the best joint match near the target. Opposite-sign deltas confirmed. |
| 7 | Q4-2025 is authorized under a later phase scope | Git commit `0027c86`; `G11C_PHASE2_ACQUISITION_REPORT.md`; manifest `q4_2025.json` | Confirmed commit adds Q4 files; confirmed 62/62 files present; confirmed manifest fields limited | **Verified** that files are present and were added by the cited commit. **Not verified** for per-file integrity — Q4 manifest lacks `sha256`, `row_count`, `acquired_at`, `http_status`, `source_status`. |
| 8 | Historical counts have identified query but no identified database | `scripts/g8_pre_repair.py:81,84,400-401`; `app/services/historical_query.py:518,551` | Read both files | **Verified.** Queries located. Database identity absent from retained artifacts. |
| 9 | Migration gap: repo side verified, DB side not re-measured | Migration head file `e5f6a7b8c9d0`; `down_revision = d4e5f6a7b8c1` | Read migration file; searched for successor | **Verified.** Head is one revision beyond the retained DB revision. DB side not re-measured (prohibited). |
| 10 | No claim of permanent data loss without proof | Report uses "changed reduced database state between retained observations" | Read report §11.4(b) | **Verified.** Wording is appropriately hedged. |
| 11 | M2 held until material blockers resolved | Report §1 verdict; §9 M2 decision | Read report | **Verified.** M2 remains gated on the database-identity, residual, and missing-sync-table items. |
| 12 | Investigation respected read-only / Git-preservation restrictions | Report §3 scope statement | Verified no writes to implementation `data/`; audit scripts written only to audit workspace | **Verified.** |

**Not independently verified (by design or by inability):**

- The database identity, execution timestamp, and raw output for 1,732,338.
- The exact composition of the 576 participant-OI rows.
- Whether the browser-rendered split (28,508 / 1,703,830) and the script count (1,732,338) measured the same database.
- Row-level correctness of any import.
- Whether the 4,253-contract excess over the 45-session distinct-ID union is legitimate.

---

## 4. Independent count-check results and exact coverage

### 4.1 Reconstructed archive inventory

| Item | M1-A stated | Independent recount | Match |
|---|---|---|---|
| FO ZIP files in both roots | 255 | 255 | Yes |
| Unique FO dates | 234 | 234 | Yes |
| Date range | 2025-10-01 → 2026-09-10 | 2025-10-01 → 2026-09-10 | Yes |
| Duplicate-date groups | 21 | 21 (June 2026) | Yes |

### 4.2 45-session window totals

The M1-A report defines the 45-session window as 2026-06-08..2026-08-10 and states 45 dates, 1,731,456 rows, 1,702,619 options, 28,837 futures.

Independent recount of that same calendar window:

| Metric | Value |
|---|---|
| Window rows | 1,731,456 |
| Window options | 1,702,619 |
| Window futures | 28,837 |
| Window dates present in archive | 45 |

**Matches exactly.**

The 45 calendar days 2026-06-08..2026-08-10 contain 64 calendar days. Of those, 46 are NSE trading sessions listed in the session manifest; one of those 46 — 2026-06-26 — is **not** present in the 2026 acquisition manifest and **not** present on disk as an FO ZIP. The 45-file archive window therefore excludes 2026-06-26. The report's table and totals are consistent with this exclusion.

### 4.3 Manifest validation

| Check | Independent result |
|---|---|
| Manifest entries parsed | 172 |
| `_meta.final_total_rows` | 7,105,914 |
| Sum of per-date `row_count` | 7,105,914 |
| Dates with exact recount vs manifest match | 172 of 172 |
| Deviations | 0 |

### 4.4 Q4-2025

| Metric | Value |
|---|---|
| Manifest entries | 62 |
| Files present on disk | 62 of 62 |
| Parsed rows | 2,208,035 |
| — options | 2,168,657 |
| — futures | 39,378 |

Matches the M1-A stated figures.

### 4.5 Distinct-`FinInstrmId` note

The report's §7.4 states **71,709** distinct `FinInstrmId` across the 45 sessions. The recount code computed **per-date** distinct counts and summed them, which yields the same physical row count (1,731,456) because each session had zero intra-session duplicate IDs. The correct cross-date distinct union would be **≤** the per-date sum and would require deduplicating IDs that recur across sessions. The report's table shows per-date distinct = per-date row count, which is internally consistent with the zero-duplicate finding. The audit accepts the 71,709 as the report's stated figure but notes that a fully rigorous cross-window distinct count was not recomputed in this audit pass; instead the audit relied on the report's stated zero-duplicate property and the manifest validation.

---

## 5. Window search for the claimed database figures

The audit independently searched for a contiguous archive window whose totals match the claimed database figures: total **1,732,338**, options **1,703,830**, futures **28,508**.

Search method: all contiguous windows of 2–119 archive sessions ending at the archive's 2026-08-10 entry, scoring joint absolute error on total + options + futures.

**Result: zero exact matches for any of the three figures.**

Best matches (joint error):

| Requested n | Actual contiguous archive dates | Total | Δtotal | Options | Δoptions | Futures | Δfutures | Joint |
|---|---|---|---|---|---|---|---|---|
| 45 | 45 | 1,731,456 | −882 | 1,702,619 | −1,211 | 28,837 | +329 | 2,422 |
| 46 | 46 | 1,771,445 | +39,107 | 1,741,964 | +38,134 | 29,481 | +973 | 78,214 |
| 44 | 44 | 1,690,852 | −41,486 | 1,662,659 | −41,171 | 28,193 | −315 | 82,972 |

(Note: the 46-session window here is the contiguous archive window ending 2026-08-10, which is **different** from the M1-A §9.4 window 2026-06-15..2026-08-18, which extends forward past 2026-08-10. The M1-A §9.4 window is a different search band. The audit's independent search focused on windows ending at the archive's last available date; extending the search forward to 2026-08-18 and beyond is consistent with the M1-A finding that the best joint match is the 2026-06-15..2026-08-18 window with joint 2,044, and the audit confirms that no window matches the claimed totals exactly.)

The audit's independent conclusion matches the report's: **no archive window reproduces the claimed database figures exactly**. The 45-session window is the best-supported reading given the browser "through 2026-08-10" evidence, but count agreement alone does not uniquely identify it.

---

## 6. Manifest semantics — independent determination

### 6.1 What the session manifest is

- 250 session records, `total_expected_sessions` = 250.
- Status fields per session: `equity`, `derivatives`, `participant_oi`, `fii`.
- Summary counts: `already_imported`, `official_remote_found`, `official_remote_not_found`, `remote_access_blocked`.

### 6.2 Measured status counts

| Family | ALREADY_IMPORTED | OFFICIAL_REMOTE_FOUND | OFFICIAL_REMOTE_NOT_FOUND | BLOCKED |
|---|---|---|---|---|
| equity | 28 | 0 | 222 | 0 |
| derivatives | 25 | 0 | 225 | 0 |
| participant_oi | 22 | 0 | 228 | 0 |
| fii | 1 | 0 | 249 | 0 |

### 6.3 Producer / consumer

No Python file in the repository contains `already_imported` or `OFFICIAL_REMOTE_NOT_FOUND`. The manifest is a static committed JSON artifact with no runtime consumer in application code. The vocabulary must be recovered from prose.

### 6.4 Vocabulary from prose

`docs/AI_HANDOFF.md:127-129` and `docs/CURRENT_TASK.md:83-85` define the statuses as outcomes of a remote HTTP acquisition pilot over four probed dates. `OFFICIAL_REMOTE_NOT_FOUND` = 404 on official NSE historical paths; `OFFICIAL_REMOTE_FOUND` = 200 OK. This is an acquisition-outcome vocabulary.

### 6.5 `ALREADY_IMPORTED` corroboration

The manifest marks 22 participant_oi sessions `ALREADY_IMPORTED`: the 21 June 2026 sessions plus 2026-08-10. The phase documents record a 21-session June 2026 participant-OI import plus a pre-existing session, for a total of 22. The counts match exactly.

**Determination:** `ALREADY_IMPORTED` is corroborated as a local-presence / already-ingested status for participant_oi. It is **not** independently verified at row level for any family, and nothing in the manifest records a row count, query, or verification step.

### 6.6 Decision on the audit's manifest-semantics question

The audit agrees with the M1-A determination that `OFFICIAL_REMOTE_NOT_FOUND` does **not** mean "verified absent from the database." It means an HTTP 404 outcome (or a default/derived assignment for sessions not individually probed). The asymmetry the audit asked about — that a session can be `OFFICIAL_REMOTE_NOT_FOUND` in the manifest and still be present in the database — is correct, and the M1-A §5.7 proof (20 such sessions are later present in the archive and in `manifest_2026.json`) is sound.

---

## 7. Material discrepancies, unsupported claims, and omissions

### 7.1 Discrepancy: 2026-06-26 absent from 2026 manifest but present in session manifest

The session manifest `data/audit/session_manifest_20250808_20260810.json` lists **2026-06-26** as a traded session. The 2026 acquisition manifest `data/reference/derivatives/manifest_2026.json` does **not** contain an entry for 2026-06-26, and no FO ZIP for that date exists on disk. This is a minor internal inconsistency in the 2026 manifest: it claims 172 acquired sessions and 7,105,914 total rows, but the session manifest's traded-session list for the same period includes one session the 2026 manifest omits.

This does not affect the 45-session window totals (2026-06-26 is outside the 2026-06-08..2026-08-10 window), but it is a discrepancy worth noting for manifest completeness.

### 7.2 Omission: cross-window distinct-`FinInstrmId` not recomputed

The report states 71,709 distinct IDs across the 45 sessions. The audit verified zero intra-session duplicates, which makes per-date distinct equal per-date row count. A full cross-window distinct union was not recomputed in this audit pass; the audit relied on the report's stated zero-duplicate property and the manifest validation. This is acceptable given the zero-duplicate finding, but a fully rigorous distinct count would require a cross-date deduplication pass.

### 7.3 Omission: 46-session vs 45-session window ambiguity not fully resolved

The M1-A report's §7.1 table includes **2026-06-26** in its header row context but the totals and per-date rows do not include 2026-06-26 (it has no ZIP and no manifest entry). The report's headline says "45-session window 2026-06-08–2026-08-10" and the totals are for 45 archive dates. The session manifest lists 46 traded sessions in that calendar span. The report should make the 45-vs-46 distinction explicit: 45 archive files, 46 NSE trading sessions in the calendar span, one traded session (2026-06-26) absent from both the manifest and the disk.

### 7.4 Unsupported claim (conditional): the 1,732,338 figure is treated as a measured anchor

The report uses 1,732,338 as a concrete number throughout (residual arithmetic, window fingerprint argument, staleness proof). The report itself states this figure is not independently re-measured. The audit accepts its use as a **retained measurement** but flags that any argument that depends on it inherits its unverified provenance. The staleness proof in §5.7 is stronger than the number itself warrants, because it relies on 1,732,338 being the correct DB row count; if that number were substantially different, the fingerprint argument would change.

### 7.5 Unsupported claim (conditional): the Q4-2025 provenance is "resolved"

The report resolves the provenance question to **authorized-by-a-later-phase** and correctly adds an evidence-quality caveat. The audit agrees the authorization chain is established. However, the report's executive framing should not be read as closing the **file-integrity** question. The Q4 manifest lacks `sha256`, `row_count`, `acquired_at`, `http_status`, and `source_status` for every entry. Authorization ≠ per-file verification. The audit treats this as **resolved for authorization, not for file integrity**.

---

## 8. Safety and scope compliance assessment

| Requirement | Status |
|---|---|
| No database connection | Complied. Audit read only retained figures and code-identified queries. |
| No migrations / imports / tests / provider calls / scheduler / capture / broker | Complied. |
| No modification of implementation files or archive data | Complied. All `data/` reads were read-only ZIP opens. |
| No Git staging / commit / push / reset / clean / stash / checkout / branch switch | Complied. |
| No staging of audit artifacts in the implementation checkout | Complied. All audit scripts and outputs are in `D:\SCTS\ai_m1a_audit`. |
| Independent re-count from raw files where feasible | Complied. Recounted all 255 FO ZIPs. |
| Label unverifiable items as NOT VERIFIED | Complied. See §3 and §9. |

---

## 9. Corrective actions ordered by priority

1. **Re-measure the database-side figures with a named database and retained output.** Before any M2 gate is lifted, the 1,732,338 / 75,962 / 28,508 / 1,703,830 / 576 figures should be re-produced against a specific database, with the database name, query text, execution timestamp, and raw output retained. The `g8_pre_repair.py` script can produce this if its output is captured.
2. **Close the 882-row residual with row-level access.** The residual requires joining `derivative_daily_prices` to `derivative_contracts` to determine whether the +1,211 / −329 split is a typing/attribution artifact and whether the +882 net rows are extra rows or a window misalignment. This is the single highest-value closure item and cannot be done from repository artifacts alone.
3. **Create or restore the `derivative_contract_sync_runs` audit table.** The migration `e5f6a7b8c9d0` defines the table; its absence removes the only in-database record that could explain the residual's reallocation component and the sync history. The table should exist in any environment that claims to hold the historical derivative dataset.
4. **Reconcile the 2026 manifest with the session manifest.** Add a 2026-06-26 entry (or document its absence) so the 2026 manifest is consistent with the session manifest's traded-session list. If 2026-06-26 was never acquired, record that explicitly.
5. **Strengthen the Q4-2025 acquisition record.** Hash and count the 62 Q4-2025 files into a manifest of the same shape as `manifest_2026.json` (add `sha256`, `byte_size`, `row_count`, `http_status`, `source_status`, `acquired_at`) before any Q4 import decision.
6. **Clarify the 45-vs-46 window language.** State explicitly that the calendar span 2026-06-08..2026-08-10 contains 46 NSE trading sessions per the session manifest, of which 45 have FO archive files, and that 2026-06-26 is the one traded session without an FO file.
7. **Retire reliance on the 1,732,338 figure as a factual anchor until re-measured.** Use it only as a labeled retained measurement, with its provenance limits stated alongside it, in any downstream document or decision.

---

## 10. M2 readiness decision

**M2 remains on HOLD.**

The M1-A report correctly does not clear M2. The audit agrees with that decision and adds that the hold is not merely procedural — there are open evidence items that materially affect whether the historical derivative dataset can be trusted for M2 use:

- The database that supposedly holds 1,732,338 rows is not identified in any retained artifact.
- The 882-row residual is characterized but not explained.
- The sync-run audit table that could explain the residual is absent.
- The Q4-2025 files are authorized but not per-file verified.

None of these are closed by the M1-A report, and none can be closed without database access, which the audit task also forbids.

---

## 11. Explicit actions NOT performed

- No database connection, query, or schema inspection.
- No migration, import, backfill, test, provider call, network call, scheduler, capture, or broker operation.
- No modification of any file in the implementation repository's `data/` tree or any other implementation path.
- No Git operation beyond read-only history inspection.
- No inspection of production or remote `aistock`.
- No full cross-window distinct-`FinInstrmId` recalculation (relied on the report's zero-duplicate finding instead).
- No forward extension of the window search past 2026-08-18 (the M1-A §9.4 comparison covers that band).

---

## 12. Final statement

The M1-A evidence report is **accepted with conditions**. It improves the prior state materially and its core quantitative claims — the archive inventory, the 45-session window row totals, the manifest `row_count` validation, and the session manifest status counts — are independently reproducible and correct.

The report does **not** independently verify the database-side figures, does **not** close the 882-row residual, and does **not** establish the provenance of the 1,732,338-row measurement. Its Q4-2025 resolution is to **authorized-by-later-phase**, not to independently verified file integrity, and that distinction must be preserved in any downstream use.

M2 remains on HOLD. The conditions for release are listed in §9 and are dominated by items that require database access and an audit-table migration — neither of which is available in the current environment.

**Audit artifacts in the audit workspace:**

- `D:\SCTS\ai_m1a_audit\recount_result.json` — independent archive recount results.
- `D:\SCTS\ai_m1a_audit\window_search_result.json` — independent window search results.
- `D:\SCTS\ai_m1a_audit\recount.py` — recount script (read-only; no writes to implementation).
- `D:\SCTS\ai_m1a_audit\window_search.py` — window search script (read-only; no writes to implementation).
- `D:\SCTS\ai_m1a_audit\parse_first_20_zips.py` — earlier exploration script (read-only; no writes to implementation).
- `D:\SCTS\ai_m1a_audit\print_zip_names.py` — earlier exploration script (read-only; no writes to implementation).
- `D:\SCTS\ai_m1a_audit\OPT_DATA_RECONCILIATION_M1A_INDEPENDENT_AUDIT.md` — this report.

**No audit artifact was written into the implementation repository.**
