# Market Data Guide (Sprint 19 Task 19.1A)

Practical guide to acquiring, importing, backfilling, and monitoring NSE EOD market data.
Covers the **Market Data** page (`/market-data`), the REST API under `/api/v1/market-data`,
the `import_nse` CLI, and the EOD scheduler settings added by Task 19.1A.

---

## 1. Overview

Task 19.1A unified every market-data entry point (web UI, REST API, CLI, EOD scheduler) onto
one code path: `MarketDataService` (`app/services/market_data_service.py`), which reuses the
existing `NSEDownloader`, `NSEHistoricalService`, and `NSEImportService`.

Entry points:

| Entry point | What it does |
|-------------|--------------|
| `POST /market-data/import` (page) | Upload a local bhavcopy/delivery CSV or ZIP and import it |
| `POST /market-data/download-latest` (page) | Download + import the latest NSE trading day |
| `POST /market-data/download-date` (page) | Download + import a chosen trade date |
| `POST /market-data/backfill` (page) | Backfill every missing weekday in a date range |
| `POST /api/v1/market-data/backfill` (API) | Same backfill as a JSON API |
| `GET /api/v1/market-data/coverage` (API) | Per-day coverage report for a range |
| `GET /api/v1/market-data/readiness` (API) | Scanner-readiness counts (20/50/100/200 sessions) |
| `GET /api/v1/market-data/status` (API) | DB summary + latest import + missing dates |
| `uv run python -m app.cli.import_nse <cmd>` | CLI: `file`, `date`, `latest`, `range`, `missing`, `status` |
| EOD scheduler | Automatic download+import each trading day, with retry + optional catch-up |

All browser actions require the `market_data.write` permission and CSRF tokens; API writes
require `market_data.write`; reads require `stocks.read`.

## 2. Formats supported

`app/data/nse_parser.py` auto-detects the file format via `detect_format`:

| Format constant | Detected when |
|-----------------|---------------|
| `FORMAT_LEGACY` | classic NSE bhavcopy header (`SYMBOL,SERIES,OPEN,...,ISIN`) |
| `FORMAT_UDIFF` | UDiFF header without `LASTPRIC` |
| `FORMAT_CM_UDIFF` | UDiFF header **with** `LASTPRIC` |
| `FORMAT_DELIVERY` | delivery file (delivery columns, no price columns) |
| `FORMAT_UNKNOWN` | unrecognized header → `NSEParseError` |

`parse_bhavcopy_detailed` returns a `BhavcopyParseResult` with `records`, `skipped_series`,
`errors`, `total_rows`, `delivery_missing`, and `format`. Legacy files (the format the owner
uses today) parse `27-Jul-2026` style dates. When a same-day delivery file is supplied it is
merged onto price rows by symbol (`DELIV_QTY`/`DELIV_PER`).

**Allowed series** (imported): `EQ`, `BE`, `BZ`, `SM`, `ST`. Everything else (GB/SGB bonds,
N-series bonds, etc.) is counted in `skipped_series` and never stored.

### Verified against the owner's real files

| File | Format | Rows | Records | Skipped | Errors | Date |
|------|--------|------|---------|---------|--------|------|
| `20260727_NSE.csv` | legacy | 3453 | 3186 (EQ 2413, SM 324, BE 285, ST 125, BZ 39) | 267 | 0 | 2026-07-27 |
| `20260728_NSE.csv` | legacy | 3413 | 3163 (EQ 2416, SM 321, BE 282, ST 119, BZ 25) | 250 | 0 | 2026-07-28 |

Both files have no delivery columns (`delivery_missing` = rows) — delivery enrichment requires
a separate delivery file. BSE files (e.g. `20260727_BSE.csv`, `SC_CODE,...` header) are
**out of scope**; Task 19.1A is NSE-specific.

## 3. Upload (UI)

1. Open **Market Data** (`/market-data`) with a `market_data.write` role.
2. Use the **Upload bhavcopy** form (CSV or ZIP, optional delivery file, max
   `max_upload_size` = 50 MB → `413 File too large` beyond that; empty file → `400`).
3. Uploaded bytes are archived under `data/raw/nse/YYYY/MM/` (sanitized filename) with a
   `.sha256` sidecar, then imported (`mode=upload`, checksum recorded on the ImportJob).
4. The result page redirects with a flash alert showing inserted/updated/rejected counts and
   the detected format.

## 4. Download (UI / API)

- `POST /market-data/download-latest` — downloads and imports the latest NSE trading day
  (`mode=latest_download`).
- `POST /market-data/download-date` — downloads and imports one chosen date
  (`mode=date_download`).
- `POST /api/v1/market-data/download` and `POST /api/v1/market-data/download/latest` — raw
  download only (returns path/size/checksum; no DB import).

The NSE site blocks plain automation (`nse_download_blocked` = `True`); the downloader reports
`blocked_403` honestly and never bypasses anti-bot measures. For blocked/unavailable dates the
UI surfaces a clear error and the manual upload path remains the reliable fallback.

## 5. Backfill

- UI: `POST /market-data/backfill` with start/end dates (`mode=range_backfill`).
- API: `POST /api/v1/market-data/backfill`
  ```json
  { "start_date": "2026-07-01", "end_date": "2026-07-31" }
  ```
  Validates `start_date <= end_date` and range ≤ `MAX_RANGE_DAYS` (366 days).
- CLI: `uv run python -m app.cli.import_nse range --start 2026-07-01 --end 2026-07-31 --delay 1.0`
- Only **missing** weekdays are fetched (never re-downloads existing dates). NSE HTTP
  responses are blocked → those dates are reported as `blocked` in the summary and are left
  missing (they can be backfilled again later or uploaded manually).

## 6. Coverage / readiness / status

- `GET /api/v1/market-data/coverage?start_date=...&end_date=...` — one row per calendar day:
  `status` ∈ `complete` (prices + delivery), `partial` (prices only), `missing`, `weekend`;
  plus price/delivery/stock/indicator/scan counts. Also rendered in the Market Data page
  coverage table.
- `GET /api/v1/market-data/readiness` — `latest_trade_date`, how many stocks have
  ≥ 20/50/100/200 distinct trading sessions (`scanner_readiness`), plus latest-day price and
  delivery counts.
- `GET /api/v1/market-data/status` — DB summary (stocks/daily prices/first/last date),
  latest completed import (id/date/mode/format/counts), and missing dates over the trailing
  `eod_scheduler_catchup_max_days` window.
- CLI: `uv run python -m app.cli.import_nse missing --start ... --end ...` and
  `uv run python -m app.cli.import_nse status`.

## 7. EOD scheduler settings (Task 19.1A)

All in `app/core/config.py` (env-overridable):

| Setting | Default | Meaning |
|---------|---------|---------|
| `eod_scheduler_enabled` | `False` | master switch for the weekday EOD job |
| `eod_scheduler_hour` / `eod_scheduler_minute` / `eod_scheduler_timezone` | `18` / `30` / `Asia/Kolkata` | when the EOD job runs |
| `eod_scheduler_retry_enabled` | `True` | retry failed EOD import runs |
| `eod_scheduler_max_retries` | `3` | max retry attempts |
| `eod_scheduler_retry_minutes` | `15` | delay between retries |
| `eod_scheduler_run_scanners` | `True` | run scanners after a successful import (else stop after indicators) |
| `eod_scheduler_auto_catchup` | `False` | on app start, backfill missing dates in the trailing window |
| `eod_scheduler_catchup_max_days` | `7` | catch-up window size |

The scheduler **retries only when** the failed run's `failed_stage == "import"` (i.e. the NSE
source was unreachable/blocked) — never on indicator/scanner failures. Catch-up runs at app
startup (after `scheduler.start()` in `app/main.py`) via `run_market_data_catchup`, which
backfills missing weekdays in the trailing window and records each day's mode as
`auto_catchup`.

## 8. Import audit fields

Every `ImportJob` records (in `details`) mode, detected format, `rows_discovered`,
`rows_skipped_series`, `delivery_rows`, `delivery_missing`, `has_delivery_columns`,
`duration_ms`, `checksum`, `parse_errors`, and `valid_rows`. Failed jobs also record mode +
checksum. The Market Data page imports table shows mode/format/date/counts/duration.

## 9. Notes

- Browser writes are CSRF-protected; uploads are size-guarded; file names are sanitized before
  archiving.
- Delivery count is `None` when no delivery file exists — it is never fabricated as zero.
- No migration was needed for Task 19.1A (reuses existing `import_jobs`, `stocks`,
  `daily_prices` tables); alembic head remains `95a0be65d4a7`.