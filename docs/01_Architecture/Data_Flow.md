# Data Flow

## High-Level Data Flow

`mermaid
graph LR
    A[NSE Website] --> B[NSE Downloader]
    B --> C[Raw ZIP/CSV]
    C --> D[NSE Parser]
    D --> E[Validated Records]
    E --> F[NSE Import Service]
    F --> G[Stock Upsert]
    F --> H[Daily Price Upsert]
    F --> I[Import Job Record]
    G --> J[(PostgreSQL)]
    H --> J
    I --> J
    J --> K[Indicator Engine]
    K --> L[Indicator Results]
    L --> J
    J --> M[Scanner Engine]
    M --> N[Scan Results]
    N --> J
`

## Data Flow Details

### 1. NSE Data Ingestion Flow

`
1. User requests download for date X
2. NSEDownloader fetches bhavcopy ZIP from NSE
3. File saved to data/raw/nse/YYYY/MM/
4. NSEParser extracts CSV from ZIP
5. Parser validates and normalizes columns
6. Parser returns PriceRecord list
7. NSEImportService upserts stocks and daily_prices
8. ImportJob created with status/completed
`

### 2. Indicator Calculation Flow

`
1. IndicatorEngine.fetch_indicator_rows()
2. Query indicator_results + daily_prices for date
3. Build IndicatorRow per stock
4. Calculate indicators using pandas-ta
5. Bulk upsert into indicator_results
`

### 3. Scanner Execution Flow

`
1. ScannerService.run_scanner(scan_code)
2. ScanRunner._fetch_indicator_rows()
3. Build IndicatorRow with 20d aggregates
4. Call scanner function per stock
5. Collect matches into ScanResultDTO
6. Persist to scan_results
`

## Data Storage

| Data Type | Storage | Retention |
|-----------|---------|-----------|
| Raw bhavcopy | data/raw/nse/YYYY/MM/ | Permanent |
| Stocks | PostgreSQL stocks | Permanent |
| Daily Prices | PostgreSQL daily_prices | Permanent |
| Indicator Results | PostgreSQL indicator_results | Permanent |
| Scan Definitions | PostgreSQL scan_definitions | Permanent |
| Scan Results | PostgreSQL scan_results | Permanent |
| Import Jobs | PostgreSQL import_jobs | 30 days |
