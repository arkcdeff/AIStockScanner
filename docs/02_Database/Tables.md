# Tables

## stocks

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| symbol | VARCHAR(20) | No | | Stock symbol (unique) |
| name | VARCHAR(255) | No | | Company name |
| sector | VARCHAR(100) | Yes | | Industry sector |
| listed_date | DATE | Yes | | Date listed on exchange |
| created_at | TIMESTAMP | No | now() | Record creation |
| updated_at | TIMESTAMP | No | now() | Last update |

**Indexes:**
- PRIMARY KEY (id)
- UNIQUE KEY idx_symbol (symbol)
- IDX_stocks_sector (sector)

## daily_prices

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| stock_id | UUID | No | | Foreign key to stocks |
| trade_date | DATE | No | | Trading date |
| open_price | NUMERIC(12,4) | No | | Opening price |
| high_price | NUMERIC(12,4) | No | | High price |
| low_price | NUMERIC(12,4) | No | | Low price |
| close_price | NUMERIC(12,4) | No | | Closing price |
| volume | BIGINT | No | 0 | Total volume |
| value | NUMERIC(18,4) | No | 0 | Total value traded |
| trades | INTEGER | No | 0 | Number of trades |
| delivery_qty | BIGINT | Yes | | Delivery quantity |
| delivery_percent | NUMERIC(5,2) | Yes | | Delivery percentage |
| created_at | TIMESTAMP | No | now() | Record creation |

**Indexes:**
- PRIMARY KEY (id)
- UNIQUE KEY idx_stock_date (stock_id, trade_date)
- IDX_daily_prices_trade_date (trade_date)

**Constraints:**
- FOREIGN KEY (stock_id) REFERENCES stocks(id)

## import_jobs

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| trade_date | DATE | No | | Import date |
| status | VARCHAR(20) | No | pending | Job status |
| source | VARCHAR(100) | No | | Data source |
| total_records | INTEGER | Yes | 0 | Total records in file |
| processed_records | INTEGER | Yes | 0 | Records processed |
| error_count | INTEGER | Yes | 0 | Errors encountered |
| error_message | TEXT | Yes | | Error details |
| started_at | TIMESTAMP | Yes | | Job start time |
| completed_at | TIMESTAMP | Yes | | Job completion time |
| created_at | TIMESTAMP | No | now() | Record creation |

**Indexes:**
- PRIMARY KEY (id)
- UNIQUE KEY idx_trade_date (trade_date)

## indicator_results

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| stock_id | UUID | No | | Foreign key to stocks |
| trade_date | DATE | No | | Calculation date |
| ema_9 | NUMERIC(12,4) | Yes | | 9-period EMA |
| ema_20 | NUMERIC(12,4) | Yes | | 20-period EMA |
| ema_50 | NUMERIC(12,4) | Yes | | 50-period EMA |
| ema_200 | NUMERIC(12,4) | Yes | | 200-period EMA |
| sma_20 | NUMERIC(12,4) | Yes | | 20-period SMA |
| rsi_14 | NUMERIC(6,2) | Yes | | 14-period RSI |
| macd_line | NUMERIC(12,4) | Yes | | MACD line |
| macd_signal | NUMERIC(12,4) | Yes | | MACD signal |
| macd_histogram | NUMERIC(12,4) | Yes | | MACD histogram |
| atr_14 | NUMERIC(12,4) | Yes | | 14-period ATR |
| adx_14 | NUMERIC(6,2) | Yes | | 14-period ADX |
| supertrend | NUMERIC(12,4) | Yes | | SuperTrend value |
| vwap | NUMERIC(12,4) | Yes | | VWAP |
| obv | NUMERIC(18,4) | Yes | | On-Balance Volume |
| bollinger_upper | NUMERIC(12,4) | Yes | | Bollinger upper band |
| bollinger_lower | NUMERIC(12,4) | Yes | | Bollinger lower band |
| bollinger_width | NUMERIC(12,4) | Yes | | Bollinger width |
| stochastic_k | NUMERIC(6,2) | Yes | | Stochastic %K |
| stochastic_d | NUMERIC(6,2) | Yes | | Stochastic %D |
| created_at | TIMESTAMP | No | now() | Record creation |

**Indexes:**
- PRIMARY KEY (id)
- UNIQUE KEY idx_stock_date (stock_id, trade_date)
- IDX_indicator_results_trade_date (trade_date)

**Constraints:**
- FOREIGN KEY (stock_id) REFERENCES stocks(id)

## scan_definitions

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| code | VARCHAR(50) | No | | Scanner code (unique) |
| name | VARCHAR(255) | No | | Scanner name |
| category | VARCHAR(50) | No | | Scanner category |
| description | TEXT | Yes | | Scanner description |
| parameters | JSONB | Yes | {} | Scanner parameters |
| is_active | BOOLEAN | No | true | Active status |
| created_at | TIMESTAMP | No | now() | Record creation |

**Indexes:**
- PRIMARY KEY (id)
- UNIQUE KEY idx_code (code)

## scan_results

| Column | Type | Null | Default | Description |
|--------|------|------|---------|-------------|
| id | UUID | No | gen_random_uuid() | Primary key |
| scan_definition_id | UUID | No | | Foreign key to scan_definitions |
| stock_id | UUID | No | | Foreign key to stocks |
| trade_date | DATE | No | | Scan date |
| matched | BOOLEAN | No | | Match result |
| score | NUMERIC(5,2) | Yes | | Match score |
| metrics | JSONB | Yes | | Scanner metrics |
| scanned_at | TIMESTAMP | No | now() | Scan timestamp |

**Indexes:**
- PRIMARY KEY (id)
- IDX_scan_results_scan_date (scan_definition_id, trade_date)
- IDX_scan_results_stock_date (stock_id, trade_date)

**Constraints:**
- FOREIGN KEY (scan_definition_id) REFERENCES scan_definitions(id)
- FOREIGN KEY (stock_id) REFERENCES stocks(id)
