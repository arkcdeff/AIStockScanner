# Scanner API

## Base URL
`/scanners`

## Endpoints

### GET /scanners
List all active scanner definitions.

**Response:**
```json
[
  {
    "id": "uuid",
    "code": "ema_bullish_crossover",
    "name": "EMA Bullish Crossover",
    "category": "trend",
    "description": "Fast EMA crosses above slow EMA",
    "parameters": {},
    "is_active": true
  }
]
```

### POST /scanners/run
Run a specific scanner by code.

**Query Parameters:**
- `scan_code` (required): Scanner code (e.g., "volume_2x_average")

**Response:**
```json
{
  "scanner": "Volume 2x Average",
  "matched_stocks": 25,
  "score": 0.85,
  "execution_time_ms": 1250.5,
  "results": [
    {
      "id": 0,
      "scan_definition_id": "uuid",
      "stock_id": "uuid",
      "symbol": "RELIANCE",
      "scan_date": "2024-01-15",
      "score": 0.92,
      "metrics": {
        "current_volume": 2000000,
        "avg_volume_20d": 1000000
      }
    }
  ]
}
```

### POST /scanners/run-all
Run all active scanners.

**Response:**
```json
{
  "total_scanners": 36,
  "total_matched": 150,
  "execution_time_ms": 45000.0,
  "results": {
    "volume_2x_average": [
      {
        "id": 0,
        "scan_definition_id": "uuid",
        "stock_id": "uuid",
        "symbol": "RELIANCE",
        "scan_date": "2024-01-15",
        "score": 0.92,
        "metrics": {
          "current_volume": 2000000,
          "avg_volume_20d": 1000000
        }
      }
    ],
    "ema_bullish_crossover": []
  }
}
```

### GET /scanners/results
List historical scan results.

**Query Parameters:**
- `scan_code` (optional): Filter by scanner code
- `symbol` (optional): Filter by stock symbol
- `limit` (default: 100, max: 500): Number of results
- `offset` (default: 0): Pagination offset

**Response:**
```json
[
  {
    "id": "uuid",
    "scan_definition_id": "uuid",
    "stock_id": "uuid",
    "symbol": "RELIANCE",
    "scan_date": "2024-01-15",
    "score": 0.92,
    "metrics": {
      "current_volume": 2000000,
      "avg_volume_20d": 1000000
    }
  }
]
```

## Scanner Categories

| Category | Count | Examples |
|----------|-------|----------|
| trend | 8 | ema_bullish_crossover, golden_cross |
| momentum | 8 | rsi_oversold, macd_bullish |
| volatility | 3 | bollinger_band_squeeze |
| volume | 4 | volume_2x_average, volume_breakout |
| delivery | 4 | delivery_above_60, delivery_breakout |
| combined | 3 | high_volume_high_delivery |
| trend_strength | 2 | strong_trend_adx |
| vwap | 2 | vwap_bullish |

## Status Codes
- `200 OK`: Successful execution
- `404 Not Found`: Scanner code not found
- `422 Unprocessable Entity`: Invalid parameters
