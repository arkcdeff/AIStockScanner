# Analytics API

Deterministic technical analytics computed per stock for the latest trading day.
Analytics are refreshed automatically by the EOD pipeline (import → indicators →
scanners → analytics).

## Base URL
`/api/v1/analytics`

## Analysis Model

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | string | Stock symbol |
| `company_name` | string | Company name |
| `trade_date` | date | Analysis date |
| `trend_score` | int (0-100) | Trend composite score |
| `momentum_score` | int (0-100) | Momentum composite score |
| `volume_score` | int (0-100) | Volume composite score |
| `delivery_score` | int (0-100) | Delivery composite score |
| `volatility_score` | int (0-100) | Volatility composite score |
| `overall_score` | int (0-100) | Weighted overall technical score |
| `support` / `resistance` | float | 20-day swing low / high |
| `pivot`, `pivot_r1..r3`, `pivot_s1..s3` | float | Classic pivot points |
| `fib_pivot`, `fib_r1..r3`, `fib_s1..s3` | float | Fibonacci pivot points |
| `relative_volume` | float | Volume vs 20-day average |
| `delivery_strength` | float | Delivery % vs 20-day average |
| `trend_rating` | string | Strong Uptrend / Uptrend / Neutral / Downtrend / Strong Downtrend |
| `momentum_rating` | string | Strong Bullish / Bullish / Neutral / Bearish / Strong Bearish |
| `volatility_rating` | string | Low / Moderate / High |
| `is_trending` | bool | Strong uptrend momentum flag |
| `is_breakout` | bool | Close above 20-day high with volume >= 1.5x avg |
| `latest_close` | float | Latest closing price |

## Endpoints

### GET /analytics/{symbol}
Get analytics for a single stock.

**Response:**
```json
{
  "id": 3,
  "symbol": "RELIANCE",
  "company_name": "Reliance Industries Ltd",
  "trade_date": "2026-08-04",
  "trend_score": 75,
  "momentum_score": 68,
  "volume_score": 80,
  "delivery_score": 55,
  "volatility_score": 65,
  "overall_score": 70,
  "support": 2460.0,
  "resistance": 2573.0,
  "pivot": 2533.0,
  "pivot_r1": 2573.0,
  "pivot_r2": 2613.0,
  "pivot_r3": 2653.0,
  "pivot_s1": 2493.0,
  "pivot_s2": 2453.0,
  "pivot_s3": 2413.0,
  "fib_pivot": 2533.0,
  "fib_r1": 2563.56,
  "fib_r2": 2582.44,
  "fib_r3": 2613.0,
  "fib_s1": 2502.44,
  "fib_s2": 2483.56,
  "fib_s3": 2453.0,
  "relative_volume": 1.92,
  "delivery_strength": 1.04,
  "trend_rating": "Uptrend",
  "momentum_rating": "Bullish",
  "volatility_rating": "Moderate",
  "is_trending": true,
  "is_breakout": false,
  "latest_close": 2534.0
}
```

### GET /analytics/top-rated
Top stocks by overall score.

**Query Parameters:**
- `limit` (default: 10, max: 50)

**Response:** Array of analysis objects, ordered by `overall_score` descending.

### GET /analytics/trending
Stocks flagged as trending.

**Query Parameters:**
- `limit` (default: 10, max: 50)

**Response:** Array of analysis objects where `is_trending` is true, ordered by
`overall_score` descending.

### GET /analytics/breakouts
Stocks flagged as price/volume breakouts.

**Query Parameters:**
- `limit` (default: 10, max: 50)

**Response:** Array of analysis objects where `is_breakout` is true, ordered by
`overall_score` descending.

## Scoring Rules (deterministic)

- **Trend Score:** price vs EMA20/50/200, EMA alignment, ADX strength, SuperTrend direction
- **Momentum Score:** RSI bands, MACD vs signal and zero line, Stochastic K/D relationship
- **Volume Score:** relative volume vs 2x/1.5x/1x average, 20-day volume max, OBV positivity
- **Delivery Score:** delivery % bands (60/50/40), above 20-day average/max
- **Volatility Score:** ATR% bands — moderate (1-2.5%) scores best, very low/high score lower
- **Overall Score:** `0.25*trend + 0.25*momentum + 0.20*volume + 0.15*delivery + 0.15*volatility`

## Status Codes
- `200 OK`: Success
- `404 Not Found`: No analytics for the symbol
- `422 Unprocessable Entity`: Invalid query parameters
