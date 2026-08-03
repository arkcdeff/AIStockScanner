# Import Flow

## NSE Bhavcopy Import Flow

```mermaid
graph TB
    A[CLI: python -m app.cli --date 2024-01-15] --> B[app.cli.import_nse]
    B --> C[app.services.nse_downloader.NSEDownloader.download_bhavcopy]
    C --> D[httpx.get NSE bhavcopy URL]
    D --> E[Save to data/raw/nse/YYYY/MM/]
    E --> F[app.data.nse_parser.NSEParser.parse_zip]
    F --> G[Extract CSV from ZIP]
    G --> H[Normalize columns: SYMBOL, OPEN, HIGH, LOW, CLOSE, TTLQTY, etc.]
    H --> I[Validate and filter rows]
    I --> J[app.services.nse_import.NSEImportService.import_bhavcopy]
    J --> K{Upsert Stocks}
    K --> L{Upsert DailyPrices}
    L --> M[Create ImportJob]
    M --> N[Commit transaction]
    N --> O[Return ImportResult]
```

## Import Service Flow

```mermaid
graph LR
    A[PriceRecord list] --> B[NSEImportService.import_bhavcopy]
    B --> C{For each record}
    C --> D[Upsert Stock]
    D --> E[Upsert DailyPrice]
    E --> F[Collect results]
    F --> G[Create ImportJob]
    G --> H[Return ImportResult]
```

## Data Transformation

```
NSE CSV Columns → Internal Models
- SYMBOL → stock.symbol
- OPEN → open_price
- HIGH → high_price
- LOW → low_price
- CLOSE → close_price
- TTLQTY → volume
- TTLTRDVAL → value
- TOTALTRADES → trades
- DELIVQTY → delivery_qty
- DELIVPER → delivery_percent
```

## Error Handling

| Error | Handling |
|-------|----------|
| Network failure | Retry with backoff |
| Invalid ZIP | Log and skip |
| Missing columns | Raise ValueError |
| Duplicate data | Upsert (no error) |
| DB constraint | Rollback transaction |
