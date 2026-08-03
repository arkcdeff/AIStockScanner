# Relationships

## Entity Relationships

```
stocks (1) ──── (n) daily_prices
stocks (1) ──── (n) indicator_results
stocks (1) ──── (n) scan_results
stocks (n) ──── (1) sectors
import_jobs (1) ──── (n) daily_prices (implicit)
scan_definitions (1) ──── (n) scan_results
```

## Foreign Key Constraints

| From Table | From Column | To Table | To Column | On Delete |
|------------|-------------|----------|-----------|-----------|
| daily_prices | stock_id | stocks | id | RESTRICT |
| indicator_results | stock_id | stocks | id | RESTRICT |
| scan_results | scan_definition_id | scan_definitions | id | RESTRICT |
| scan_results | stock_id | stocks | id | RESTRICT |

## Unique Constraints

| Table | Columns | Description |
|-------|---------|-------------|
| stocks | symbol | Each stock symbol must be unique |
| daily_prices | stock_id, trade_date | One price record per stock per day |
| indicator_results | stock_id, trade_date | One indicator result per stock per day |
| import_jobs | trade_date | One import job per date |
| scan_definitions | code | Each scanner code must be unique |

## Index Strategy

### Primary Keys
- All tables use UUID primary keys (gen_random_uuid())

### Secondary Indexes
- `idx_stocks_sector` - Filter stocks by sector
- `idx_daily_prices_trade_date` - Query by trade date
- `idx_indicator_results_trade_date` - Query indicators by date
- `idx_scan_results_scan_date` - Query scan results by definition and date
- `idx_scan_results_stock_date` - Query scan results by stock and date

### Composite Indexes
- `idx_stock_date` on daily_prices(stock_id, trade_date) - Most common query pattern
- `idx_stock_date` on indicator_results(stock_id, trade_date) - Most common query pattern

## Data Integrity Rules

1. **Stock Uniqueness**: Symbol must be unique across all stocks
2. **Daily Price Uniqueness**: One record per stock per trade date
3. **Indicator Uniqueness**: One record per stock per trade date
4. **Import Job Uniqueness**: One job per trade date
5. **Scanner Code Uniqueness**: Each scanner code must be unique
6. **Cascade Behavior**: No cascading deletes; RESTRICT prevents orphaned records
