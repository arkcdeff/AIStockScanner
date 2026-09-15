# AISTOCKSCANNER
# G.11-C — MISSING DATA RECOVERY REPORT

Date: 2026-09-11 (recovery pass executed off-hours, ~22:20–22:45 IST, Friday)
Branch: checkpoint/sprint19-20-20260822
Scope: RECOVERY ONLY — acquire/verify/manifest. No DB writes, no application code changes, no migrations, no commit/tag/push.
Supersedes the throttling hypothesis stated in `docs/G11C_PHASE2_ACQUISITION_REPORT.md` (see Source limitations).

---

## INSTITUTIONAL FLOW (CM - Category-wise Turnover, Jan–Aug 2026)

- **Before:** 113/164 sessions valid (51 missing, all recorded HTTP 404)
- **Recovered:** **0**
- **Remaining:** **51** — every one classified **SOURCE_UNAVAILABLE** (see classification)
- **Classification method (per mission):** each of the 51 dates re-probed off-hours with
  identity encoding, warmed session cookies, and a **control date (2026-01-01) fetched
  HTTP 200 in the same session** — proving the endpoint was healthy while the missing
  dates returned the deterministic server response
  `404 {"error":"Not Found, may be some files are unavailable","show":true}`.
  Static host also 404s for the inner file (`/content/cm/cat_turnover_030226.xls`).
- **Not throttling:** the earlier "IP-window throttle" diagnosis is disproven (control 200,
  off-hours, deterministic 404 bodies). **Not NO_DEALS:** HTTP failure is never treated as
  zero deals (mission rule 9) — these are recorded as HTTP 404 / SOURCE_UNAVAILABLE.
- **Missing dates (all 51, SOURCE_UNAVAILABLE):**
  2026-02-03, 02-04, 02-10, 02-11, 02-17, 02-18, 02-24, 02-25, 02-26,
  2026-03-04, 03-10, 03-11, 03-17, 03-18, 03-24, 03-25,
  2026-04-10, 04-15, 04-17, 04-24,
  2026-05-04, 05-11, 05-15, 05-18, 05-25, 05-26,
  2026-06-03, 06-04, 06-10, 06-11, 06-15, 06-17, 06-18, 06-24, 06-25,
  2026-07-03, 07-10, 07-15, 07-17, 07-24, 07-31,
  2026-08-03, 08-04, 08-10, 08-11, 08-17, 08-18, 08-24, 08-25, 08-26, 08-31
- **2026-02-01 (Sunday Budget special session):** VALID — 8 category rows acquired in Phase 2
  and retained. Remains flagged as an official special session, not a normal weekday
  (mission rule 11). No DailyPrice/calendar changes made.
- Final manifest state: `data/reference/institutional/manifest_20260101_20260831.json`
  (164 entries: 113 valid incl. 2026-02-01; 51 `source_status=SOURCE_UNAVAILABLE` with
  classification text and evidence).

## BULK DEALS

- **Before:** 166/168 intended sessions (2026-02-01, 2026-03-20 missing after Phase 2 endpoint failures)
- **Recovered:** **2** — HTTP 200 via the proven
  `/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&...&csv=true` family
  - 2026-02-01: **33 rows** (Budget special session), 3,641 bytes
  - 2026-03-20: **102 rows**, 11,599 bytes
- **Remaining:** **0** — bulk deals now **168/168** sessions, 17,916 total rows (+131 vs Phase 2)

## BLOCK DEALS

- **Before:** 166/168 intended sessions (same two special dates missing)
- **Recovered:** **2** — HTTP 200, header-only CSVs:
  - 2026-02-01: **0 rows → NO_DEALS** (genuine zero, HTTP 200 — not an HTTP failure)
  - 2026-03-20: **0 rows → NO_DEALS**
- **Remaining:** **0** — block deals now **168/168** sessions, 1,105 total rows (unchanged)

## SHA256

- **Deals (recovered files):** 4/4 re-fetch → fresh SHA256 == stored SHA256 (PASS).
  Container is a stable plain CSV for this family.
- **Institutional (valid files):** re-fetch of early/middle/late representatives
  (2026-01-01, 2026-08-28) returned the **same byte size (90,768 / 91,280)**, the **same
  inner-XLS CRC32**, and **semantically identical parsed data (all 8 category rows equal)**,
  but a **different container SHA256**. Diagnosis: NSE regenerates the ZIP container on every
  request (fresh embedded timestamp, identical payload) — container-level SHA256 is unstable
  **by design** for this endpoint family. Disk files match their acquisition-time manifest
  SHA256 **113/113 exactly** (disk-vs-manifest integrity confirmed; zero files missing).
- **Resolution:** per-entry `inner_crc32` + `inner_file_size` recorded as the stable
  verification fingerprint for this family (113/113 enriched); container SHA256 retained as
  acquisition-time evidence. No silent repair of any source value was performed.

## SOURCE LIMITATIONS

1. **Institutional archive attrition (hard):** NSE's live "CM - Category-wise Turnover"
   archive no longer serves 51 of the 164 Jan–Aug 2026 sessions. Deterministic 404 JSON,
   reproduced off-hours with healthy endpoint control. Recovery requires NSE re-publishing
   the files (or an alternative official channel, none found: `fii_dii` static family and
   `fiidiiTradeReact` API have no per-date history). Re-run recommendation: periodic re-probe;
   script `scratch/g11c_inst_classify51.py` is resume-safe and will recover any date the
   moment NSE republishes it.
2. **Container SHA256 instability (institutional family):** mitigated via inner-CRC32
   verification as documented above.

## FILES ADDED (this recovery pass)

- `data/reference/deals/raw/bulk_deals_20260201.csv` (3,641 B, 33 rows)
- `data/reference/deals/raw/block_deals_20260201.csv` (130 B, header-only)
- `data/reference/deals/raw/bulk_deals_20260320.csv` (11,599 B, 102 rows)
- `data/reference/deals/raw/block_deals_20260320.csv` (130 B, header-only)
- Manifest updates (metadata only): `data/reference/deals/manifest_2026.json` (2 sessions
  recovered, meta counters + recovery note), `data/reference/institutional/manifest_20260101_20260831.json`
  (51 dates classified SOURCE_UNAVAILABLE with evidence; inner_crc32 enrichment; verification method)
- Scratch tooling (not application code): `scratch/g11c_inst_recover.py`,
  `scratch/g11c_inst_classify51.py`, `scratch/_g11c_inst_missing.json`,
  `scratch/_g11c_recovery_sha_verify.json`, `scratch/_g11c_deals_retry_status.json`
- This report: `docs/G11C_MISSING_DATA_RECOVERY_REPORT.md`
- No new institutional raw files (0 recoverable — see SOURCE LIMITATIONS)

## DATABASE CHANGED: NO
## APPLICATION CODE CHANGED: NO
## COMMIT: NO
## TAG: NO
## PUSH: NO

Git discipline maintained: no git add/commit/tag/push performed. Pre-existing working-tree
modifications (`app/services/indicator_engine.py`, `pyproject.toml`, earlier G.11-C Phase 2
manifest/data files) were left exactly as found and are not products of this pass.

## FINAL STATUS: **RECOVERY PARTIAL**

- Bulk + Block: **RECOVERY COMPLETE** (168/168 sessions; the only two missing dates recovered;
  block zero-days proven genuine NO_DEALS with HTTP 200).
- Institutional: **not recoverable from official sources at this time** — 51/164 sessions are
  absent from NSE's live archive (SOURCE_UNAVAILABLE, evidence per-date in manifest).
  Coverage stands at 113/164 (68.9%), including the 2026-02-01 special session.
