# Market Data API

## Base URL
`/market-data`

## Endpoints

### POST /market-data/download
Download NSE bhavcopy for a specific date.

**Request:**
```json
{
  "trade_date": "2024-01-15"
}
```

**Response:**
```json
{
  "trade_date": "2024-01-15",
  "path": "data/raw/nse/2024/01/cm15jan2024bhav.csv.zip",
  "size": 1048576,
  "checksum": "abc123",
  "downloaded": true
}
```

### POST /market-data/download/latest
Download the latest available NSE bhavcopy.

**Response:**
```json
{
  "trade_date": "2024-01-15",
  "path": "data/raw/nse/2024/01/cm15jan2024bhav.csv.zip",
  "size": 1048576,
  "checksum": "abc123",
  "downloaded": true
}
```

### POST /market-data/import-file
Import a bhavcopy file (ZIP/CSV) via multipart upload.

**Request:**
```
Content-Type: multipart/form-data

file: <binary>
```

**Response:**
```json
{
  "id": "uuid",
  "trade_date": "2024-01-15",
  "status": "completed",
  "source": "upload",
  "total_records": 1000,
  "processed_records": 1000,
  "error_count": 0,
  "started_at": "2024-01-15T10:00:00Z",
  "completed_at": "2024-01-15T10:05:00Z"
}
```

### POST /market-data/import/nse
Import NSE bhavcopy with optional delivery file.

**Request:**
```
Content-Type: multipart/form-data

bhavcopy: <binary>
delivery: <binary> (optional)
```

**Response:**
```json
{
  "id": "uuid",
  "trade_date": "2024-01-15",
  "status": "completed",
  "source": "nse",
  "total_records": 1000,
  "processed_records": 1000,
  "error_count": 0
}
```

### GET /market-data/prices
List daily prices with optional filters.

**Query Parameters:**
- `symbol` (optional): Stock symbol filter
- `trade_date` (optional): Date filter
- `offset` (default: 0): Pagination offset
- `limit` (default: 100, max: 1000): Page size

**Response:**
```json
[
  {
    "id": "uuid",
    "symbol": "RELIANCE",
    "series": "EQ",
    "trade_date": "2024-01-15",
    "open": 2500.00,
    "high": 2550.00,
    "low": 2490.00,
    "close": 2540.00,
    "previous_close": 2480.00,
    "last_price": 2540.00,
    "volume": 1000000,
    "turnover": 2540000000.00,
    "trade_count": 50000,
    "delivery_quantity": 200000,
    "delivery_percentage": 20.0
  }
]
```

### GET /market-data/imports
List import jobs.

**Query Parameters:**
- `limit` (default: 50, max: 200): Number of jobs to return

**Response:**
```json
[
  {
    "id": "uuid",
    "trade_date": "2024-01-15",
    "status": "completed",
    "source": "nse",
    "total_records": 1000,
    "processed_records": 1000,
    "error_count": 0,
    "started_at": "2024-01-15T10:00:00Z",
    "completed_at": "2024-01-15T10:05:00Z"
  }
]
```

### GET /market-data/summary
Get market data summary statistics.

**Response:**
```json
{
  "total_stocks": 1000,
  "total_daily_prices": 50000,
  "latest_date": "2024-01-15",
  "oldest_date": "2020-01-01",
  "total_imports": 500
}
```
