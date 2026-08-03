# Migrations

## Migration History

### 0001_create_core_tables
- **Created**: stocks, sectors, daily_prices, import_jobs tables
- **Description**: Initial schema with core data tables
- **Status**: Applied

### 0002_market_data_foundation
- **Created**: Added indexes on daily_prices(stock_id, trade_date)
- **Description**: Performance optimization for market data queries
- **Status**: Applied

### c64742f7da53_add_indicator_results_table
- **Created**: indicator_results table
- **Description**: Added table for storing technical indicator calculations
- **Columns**: stock_id, trade_date, ema_9/20/50/200, sma_20, rsi_14, macd_line/signal/histogram, atr_14, adx_14, supertrend, vwap, obv, bollinger_upper/lower/width, stochastic_k/d
- **Status**: Applied

### fda0edd539bd_seed_scan_definitions
- **Created**: scan_definitions table
- **Seeded**: 25 trend/momentum/volatility scanners
- **Description**: Scanner definition registry
- **Status**: Applied

### bdc596b3589a_seed_volume_and_delivery_scan_
- **Created**: scan_results table
- **Seeded**: 11 volume and delivery scanners
- **Description**: Added volume/delivery scanners and scan results table
- **Status**: Applied

## Running Migrations

```bash
# Upgrade to latest
uv run alembic upgrade head

# Downgrade one step
uv run alembic downgrade -1

# Create new migration
uv run alembic revision --autogenerate -m "description"
```

## Migration Conventions
- All migrations use synchronous SQLAlchemy for Windows compatibility
- Migrations are version-controlled in migrations/versions/
- Each migration has up/down methods for rollback
