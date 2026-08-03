# Test Cases

## API Endpoint Tests

### Health API
- `test_health_endpoint` - GET /health returns application info
- `test_database_health_ok` - GET /health/database returns ok when DB is up
- `test_database_health_error` - GET /health/database returns error when DB is down

### Market Data API
- `test_download_bhavcopy` - POST /market-data/download downloads file
- `test_download_latest_bhavcopy` - POST /market-data/download/latest downloads latest
- `test_import_file` - POST /market-data/import-file imports uploaded file
- `test_import_nse_files` - POST /market-data/import/nse imports bhavcopy+delivery
- `test_list_prices` - GET /market-data/prices lists prices with filters
- `test_list_imports` - GET /market-data/imports lists import jobs
- `test_market_data_summary` - GET /market-data/summary returns stats

### Indicator API
- `test_calculate_indicators_all` - POST /indicators/calculate calculates for all stocks
- `test_calculate_indicators_stock` - POST /indicators/calculate calculates for specific stock
- `test_indicator_status` - GET /indicators/status returns indicator counts
- `test_indicator_status_symbol` - GET /indicators/status?symbol=RELIANCE returns per-stock stats

### Scanner API
- `test_list_scanners` - GET /scanners lists active scanners
- `test_run_scanner` - POST /scanners/run runs specific scanner
- `test_run_all_scanners` - POST /scanners/run-all runs all scanners
- `test_list_scan_results` - GET /scanners/results lists scan results

## Unit Tests

### NSE Parser
- `test_parse_bhavcopy` - Parses valid bhavcopy CSV
- `test_parse_bhavcopy_zip` - Parses bhavcopy ZIP file
- `test_parse_delivery` - Parses delivery CSV
- `test_invalid_csv_raises` - Invalid CSV raises error
- `test_missing_columns_raises` - Missing required columns raises error

### Scanner Registry
- `test_register_scanner` - Scanner can be registered
- `test_get_scanner` - Scanner can be retrieved
- `test_duplicate_register_raises` - Duplicate registration raises error
- `test_unknown_scanner_raises` - Unknown scanner raises error

### Scanner Logic
- `test_ema_bullish_crossover` - EMA crossover detection
- `test_golden_cross` - Golden cross detection
- `test_rsi_oversold` - RSI oversold detection
- `test_volume_2x_average` - Volume spike detection
- `test_delivery_above_60` - Delivery threshold detection
- `test_accumulation` - Combined accumulation detection

## Test Data
- Test database: `aistock_test`
- Test fixtures seed scan definitions
- Sample bhavcopy files in test data directory
