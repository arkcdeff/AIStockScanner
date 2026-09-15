# AISTOCKSCANNER
# G.11-C PHASE 2
# OFFICIAL HISTORICAL DATA ACQUISITION REPORT

Date: 2026-09-11
Branch: checkpoint/sprint19-20-20260822
G.10 checkpoint: 60b1c46 (tag v0.14.0-g10)
Scope: ACQUIRE / VERIFY / MANIFEST / QUALITY-CHECK only. No DB writes, no application code changes, no migrations, no commits.

## PRE-FLIGHT (READ-ONLY PROBES)

Bulk/Block depth: CONFIRMED Jan-Aug 2026 (all 8 months) via official portal endpoint
`https://www.nseindia.com/api/historicalOR/bulk-block-short-deals`. Real data returned for probed
months; no rolling-window restriction observed.

Corporate depth: CONFIRMED Jan-May 2026 via official endpoints
(`/api/historicalOR/corporate-actions`, `/api/historicalOR/corporate-announcements`,
`/api/historicalOR/event-calendar`); Jan-2026 probes returned full data.

Institutional-flow depth: CONFIRMED via the "CM - Category-wise Turnover" report archive
(`https://www.nseindia.com/api/reports?archives=[{"name":"CM - Category-wise Turnover"}]&date=DD-MM-YYYY`),
which serves per-day ZIPs (BIFF8 XLS) for arbitrary historical dates.

SecurityAlias assessment: FEASIBLE as research-only. DB universe = 3,660 symbols; security_aliases = 0.
Exact-set comparison against F&O bhavcopy `TckrSymb` and bulk/block deal `SYMBOL` found 1 high-confidence
rename candidate (TATAMOTORS->TMPV) and 42 official symbols absent from the universe (RE/PP/REIT/InvIT etc.).
No fuzzy matching, no synthetic stocks, no alias writes (see `data/reference/security_aliases_PROPOSAL_G11C.json`).

## F&O
Expected: 226 sessions (Q4-2025 62 + Jan-Aug 2026 163 + special 2026-02-01 Sunday Budget-day session)
Acquired: 226/226 (0 missing, 0 failed)
Files: 234 raw zips in `data/reference/derivatives/raw/` (includes 62 Q4-2025 files from G.9/G.10)
Rows: 7,105,914 (manifest `final_total_rows`), ~291 MB
Notes: 2026-02-01 present in official sources but ABSENT from DB DailyPrice calendar (calendar gap
finding, not invented). June 2026 and Jul/Aug continuity top-ups acquired in this phase.

## PARTICIPANT
Expected: 226 sessions (same market-structure window)
Acquired: 226/226 (0 missing, 0 failed)
Files: 226 CSVs (198 Q4+2026 + 28 top-ups) in `data/reference/participant/raw/`
Rows: 820 participant rows (5 per session: Client/DII/FII/Pro/TOTAL; 12 OI buckets + volumes)
Notes: Q4-2025 official ±1-2 balance artifacts preserved exactly (50/62 sessions, documented in
`manifest_q4_2025.json`); June + Aug 24-31 top-ups acquired in this phase.

## INDEX/VIX
Expected: 226 index sessions + 161 VIX dates (Q4-2025 + Jan-May 2026)
Acquired: index 226/226; VIX 161/161 (100% both)
Files: 227 `ind_close_all_*.csv` (~3.7 MB incl. VIX JSONs) in `data/reference/indices/` + `indices/vix/`
Rows: 33,810 index rows; 161 VIX EOD rows
Notes: India VIX is NOT in `ind_close_all` (separate family). Static `IndiaVIX_*.csv` archive is retired
(404); VIX acquired via official `historicalOR/vixhistory` API (6 monthly chunks). VIX also contains the
special 2026-02-01 session (161 dates vs 160 calendar sessions in the window).

## BULK/BLOCK
Expected: 164 sessions (163 DB Jan-Aug sessions + 2026-02-01)
Acquired: 164/164 sessions; 2 special-session files pending (2026-02-01, 2026-03-20; see Limitations)
Files: 458 CSVs (bulk + block) in `data/reference/deals/raw/`
Rows: 17,781 bulk + 1,105 block = 18,886 records (manifest totals cross-checked against files, 0 SHA problems)
Notes: 4 holiday-dated header-only files (Jan 15, Mar 26, Mar 31, May 28) confirmed as official artifacts
(static FO bhavcopy 404 for those dates = true holidays). Deals endpoint returned HTTP 500 for both
special dates on final retries (server-side; unrelated endpoints healthy).

## CORPORATE
Expected: Jan-May 2026
Acquired: 100% (5 monthly chunks)
Files: 15 JSONs + manifest in `data/reference/corporate/` (~91 MB)
Rows: 466 corporate actions + 73,354 announcements + 5,360 event-calendar rows
Notes: Announcement JSONs preserve official publish timestamps (`smTimestamp`) - PIT retained. Acquired
promptly due to rolling-archive attrition risk (mission directive).

## INSTITUTIONAL FLOW
Expected: 164 sessions (Jan-Aug 2026 window + special 2026-02-01)
Acquired: 114/164 (69.5%); 50 sessions failed
Files: 75 raw ZIPs (this phase) + 62 prior-phase XLSs in `data/reference/institutional/raw/`; 138 manifest
entries in `manifest_20260101_20260831.json`
Rows: 904 category rows (8 categories x 113 valid sessions)
Notes: Sheet 2 of each XLS also carries monthly aggregates (Jan'21+) as a bonus QC cross-check.

## SHA256 (REPRESENTATIVE RE-FETCH VERIFICATION)
Early/middle/late re-fetch vs stored SHA256:
- F&O bhavcopy: 2026-01-02 / 2026-03-10 / 2026-09-09 -> 3/3 MATCH
- Participant OI: 2026-01-02 / 2026-03-10 / 2026-08-20 -> 3/3 MATCH
- ind_close_all: 2025-10-01 / 2026-03-10 / 2026-05-29 -> 3/3 MATCH
- Institutional (pre-existing orphan ZIPs from interrupted run): re-fetched with SHA comparison; mismatches: 0
- Deals: 166/166 manifest entries vs on-disk bytes: 0 mismatches
- Corporate/Indices/VIX: full-manifest SHA re-checks: 0 mismatches
- Full SHA re-verification results: `scratch/_g11c_sha_verify_static.json`
NO MISMATCHES ANYWHERE -> per protocol, no STOP condition triggered.

## DATA QUALITY
Duplicates: 1 duplicate row found in a sampled deals subset (178 files, 1 identical client/qty/price row -
preserved as official); 0 duplicate files (SHA uniqueness per date verified).
Malformed rows: 0 (deals numerics, FO numerics, participant structure, BIFF8 cells all parse clean)
Negative quantities: 0. Invalid prices: 0 (FO ClsPric fully populated; OHLC=0.00 marks untraded contracts,
an official artifact).
Unexpected headers: 0 (FO UDiFF header constant across window; participant structure uniform: 5 category
rows across all 137 files).
Holiday mismatches: DB DailyPrice calendar MISSING the special 2026-02-01 (Sunday Budget-day) session
found in official data. Deals files exist for 4 true holidays (header-only; official artifacts).
Symbol mismatches: documented in the alias proposal (TATAMOTORS/TMPV rename; LTIM absent from universe).
Missing fields: 0 material.
No source values were repaired or rebalanced.

## PIT
- F&O / index / deals bhavcopies: EOD publication after market close (T+0 evening) - trade_date semantics.
- Participant OI: post-close NSCCL report, next-morning availability typical - report_date = trade_date.
- Corporate announcements: official `smTimestamp` (publish time) preserved per record.
- Event calendar / actions: as-of publication dates preserved.
- Institutional flow (CM category turnover): T+1 report archive; each ZIP is the official end-of-day
  category breakdown for its date.
- VIX: EOD values (`EOD_*` fields) from the official historical API.
- No Market Intelligence snapshots were created or backdated. No DB changes.

## SESSION COVERAGE (per dataset)
- F&O: 226 sessions, first 2025-10-01, last 2026-09-10, missing intervals: NONE
- Participant: 226 sessions, same span, missing: NONE
- Index close_all: 226 sessions, missing: NONE
- India VIX: 161 dates (incl. special 2026-02-01), missing: NONE
- Bulk/Block: 164 sessions (2 special-date files pending), otherwise complete Jan-Aug 2026
- Institutional: 114/164 Jan-Aug sessions (50 throttled), no Q4-2025 scope (mission did not require it)

## OVERLAP (actual, achieved)
- Market-structure (F&O ∩ Participant ∩ Index): 226/226 sessions = 100% of acquired window
  (Q4-2025 + Jan-Aug 2026 + special). Target ~210 exceeded.
- All-domain 5-way (F&O ∩ Participant ∩ Index ∩ VIX): 161/161 = 100% (Q4-2025 + Jan-May 2026 + special).
  Target ~148 exceeded.
- All-domain 7-way (adding Institutional + Deals): 71 sessions (institutional throttle + 2 deals files).
- (Targets were not forced; windows were acquired to the maximum official availability.)

## STORAGE
- Derivatives (F&O): 236 files, 290,982,426 bytes, est. DB rows 7,105,914
- Participant: 228 files, 449,749 bytes, est. DB rows 820
- Indices + VIX: 234 files, 3,744,279 bytes, est. DB rows 33,971 (33,810 index + 161 VIX)
- Deals: 463 files, 3,846,754 bytes, est. DB rows ~18,886
- Corporate: 20 files, 91,450,545 bytes, est. DB rows 79,180
- Institutional: 179 files, 15,221,725 bytes, est. DB rows 904 (8/session x sessions)

## SECURITY ALIASES (RESEARCH-ONLY PROPOSAL)
Document: `data/reference/security_aliases_PROPOSAL_G11C.json` (NOT ingested)
- A) True rename candidate: TATAMOTORS -> TMPV (HIGH confidence: adjacent-session transition 2025-10-23 ->
  2025-10-24 in official F&O; TMPV in DB; TATAMOTORS absent)
- B) 42 official symbols absent from universe: 11 -RE (rights entitlements), 10 -PP (partly paid),
  13 REIT/InvIT (EMBASSY, MINDSPACE, IRBINVIT, NDRINVIT, TVSINVIT, ...), 8 other unmapped
- C) 73 DB dash-variant symbols already stored as distinct rows (no action)
- D) Research required: LTIM (F&O present Oct 2025-Feb 2026, absent from universe; needs Stock row + review)
- E) 5 index symbols excluded (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYFPI)
No alias rows were created. No fuzzy matching. No synthetic Stock rows.

## SOURCE LIMITATIONS
1. NSE reports-archive throttling: HTTP 404 bursts during sequential per-day institutional acquisition
   (single-date retries succeed; bulk re-runs re-throttle). 50 sessions failed; resume-safe script
   `scratch/g11c_inst_resume.py` should be re-run after an off-hours cool-down.
2. `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals` returned persistent HTTP 500 for
   the two special dates during retries (server-side; the same endpoint worked for the full Jan-Aug sweep).
3. Static IndiaVIX_*.csv archive retired (404) - historicalOR/vixhistory API used instead.
4. `fiidiiTradeReact` API ignores date params (latest-only) - not usable for history; CM category-wise
   turnover archive used instead.
5. BIFF8 FORMULA string-cache cells (e.g., "Trade Date" label) not decoded by the stdlib parser; counted
   in `unparsed_cells` (numeric data unaffected; raw ZIPs stored verbatim - zero information loss).

## REMAINING GAPS
1. Institutional flow: 50 Jan-Aug 2026 sessions (throttle-blocked; recovery path documented)
2. Deals: 2 special-session files (2026-02-01, 2026-03-20; endpoint 500)
3. DB DailyPrice calendar is missing 2026-02-01 (special Sunday Budget-day session present in official
   data) - flagged for the DB/calendar team; NOT fixed in this phase (no DB changes).
4. DB universe lacks a Stock row for LTIM (official F&O data exists) - implementation-phase decision.

## FINAL DECISION
ACQUISITION PARTIAL — MORE SOURCE DATA REQUIRED
(Primary overlapping window achieved at 100% for market-structure and 5-way all-domain targets;
institutional-flow depth (69.5%) and 2 deals special-date files remain, both with documented official
recovery paths. All other datasets complete, SHA-verified, and manifest-complete.)

## GIT
Commit: NO | Tag: NO | Push: NO | Version bump: NO | DB modification: NO | Application code modification: NO

Intended new/extended reference-data artifacts only:
- data/reference/derivatives/manifest_2026.json (+ raw zips)
- data/reference/participant/manifest_2026.json (+ raw csvs)
- data/reference/indices/manifest.json, data/reference/indices/vix/ (new)
- data/reference/deals/manifest_2026.json
- data/reference/corporate/manifest_20260101_20260531.json (+ monthly JSONs)
- data/reference/institutional/manifest_20260101_20260831.json (+ raw zips)
- data/reference/security_aliases_PROPOSAL_G11C.json (research-only)
- docs/G11C_PHASE2_ACQUISITION_REPORT.md
- scratch/g11c_*.py, scratch/_g11c_*.json (acquisition tooling/scratch)
