# NSE Import

## Overview
NSE Import module handles downloading, parsing, and ingesting NSE bhavcopy files into the database.

## Components

### NSEDownloader (`app.services.nse_downloader.NSEDownloader`)
Downloads bhavcopy ZIP files from NSE website.

**Key Methods:**
- `download(trade_date)`: Download bhavcopy for specific date
- `download_latest()`: Download most recent available bhavcopy
- `find_latest_trade_date(max_lookback)`: Find latest trading day

**Features:**
- Session initialization with cookies
- Checksum verification (SHA-256)
- Duplicate detection (skip if checksum matches)
- Retry logic for network errors

### NSEParser (`app.data.nse_parser`)
Parses NSE bhavcopy CSV files.

**Key Functions:**
- `parse_bhavcopy(payload, file_name, delivery, allowed_series)`: Parse bhavcopy ZIP/CSV
- `parse_delivery(payload, file_name)`: Parse delivery file

**Supported Formats:**
- ZIP files containing CSV
- Direct CSV files
- Multiple column name aliases (SYMBOL, TCKRSYMB, etc.)

### NSEImportService (`app.services.nse_import`)
Orchestrates the import process.

**Key Methods:**
- `import_bhavcopy(trade_date)`: Full import from downloaded file
- `import_bytes(bhavcopy, bhavcopy_name, delivery, delivery_name)`: Import from bytes

## Data Flow

```
Download → Parse → Validate → Upsert Stocks → Upsert DailyPrices → Create ImportJob
```

## Error Handling

| Error | Handling |
|-------|----------|
| Network failure | Retry with backoff |
| Invalid ZIP | Log and skip |
| Missing columns | Raise NSEParseError |
| Duplicate data | Upsert (no error) |
| DB constraint | Rollback transaction |

## File Structure
```
data/raw/nse/YYYY/MM/cmDDMMMYYYYbhav.csv.zip
```
