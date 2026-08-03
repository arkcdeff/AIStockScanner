# Indicator API

## Base URL
`/indicators`

## Endpoints

### POST /indicators/calculate
Calculate technical indicators for a stock or all stocks.

**Query Parameters:**
- `symbol` (optional): Stock symbol (e.g., "RELIANCE"). If omitted, calculates for all stocks.

**Response:**
```json
{
  "symbol": "RELIANCE",
  "calculated": 100,
  "skipped": 0,
  "message": "Calculated 100 indicator rows"
}
```

**Response (Batch):**
```json
{
  "symbol": "ALL",
  "calculated": 50000,
  "skipped": 0,
  "message": "Batch calculation completed"
}
```

### GET /indicators/status
Get indicator calculation status.

**Query Parameters:**
- `symbol` (optional): Stock symbol

**Response (Per Stock):**
```json
{
  "symbol": "RELIANCE",
  "indicator_rows": 100,
  "latest_trade_date": "2024-01-15"
}
```

**Response (Global):**
```json
{
  "total_indicator_rows": 50000,
  "stocks_with_indicators": 500
}
```

## Supported Indicators

| Indicator | Field Name | Period | Description |
|-----------|-----------|--------|-------------|
| EMA | ema_9, ema_20, ema_50, ema_200 | 9/20/50/200 | Exponential Moving Average |
| SMA | sma_20 | 20 | Simple Moving Average |
| RSI | rsi_14 | 14 | Relative Strength Index |
| MACD | macd_line, macd_signal, macd_histogram | 12/26/9 | MACD line, signal, histogram |
| ATR | atr_14 | 14 | Average True Range |
| ADX | adx_14 | 14 | Average Directional Index |
| SuperTrend | supertrend | 10/3 | SuperTrend indicator |
| VWAP | vwap | session | Volume Weighted Average Price |
| OBV | obv | - | On-Balance Volume |
| Bollinger Bands | bollinger_upper, bollinger_lower, bollinger_width | 20/2 | Upper band, lower band, width |
| Stochastic | stochastic_k, stochastic_d | 14/3 | Stochastic oscillator |

## Status Codes
- `202 Accepted`: Calculation started (async)
- `404 Not Found`: Stock symbol not found
- `422 Unprocessable Entity`: Invalid request parameters
