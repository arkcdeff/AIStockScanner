# Indicator Engine

## Overview
Calculates technical indicators for all stocks using pandas-ta library.

## Supported Indicators

| Indicator | Fields | Period | Description |
|-----------|--------|--------|-------------|
| EMA | ema_20, ema_50, ema_100, ema_200 | 20/50/100/200 | Exponential Moving Average |
| SMA | sma_20 | 20 | Simple Moving Average |
| RSI | rsi_14 | 14 | Relative Strength Index |
| MACD | macd, macd_signal, macd_hist | 12/26/9 | MACD components |
| Stochastic | stochastic_k, stochastic_d | 14/3 | Stochastic oscillator |
| ATR | atr | 14 | Average True Range |
| Bollinger Bands | bollinger_upper, bollinger_middle, bollinger_lower | 20/2 | Bollinger bands |
| ADX | adx | 14 | Average Directional Index |
| SuperTrend | supertrend, supertrend_direction | 10/3 | SuperTrend indicator |
| VWAP | vwap | session | Volume Weighted Average Price |
| OBV | obv | - | On-Balance Volume |

## Architecture

```mermaid
graph LR
    A[DailyPrices] --> B[IndicatorEngine]
    B --> C[pandas DataFrame]
    C --> D[pandas-ta calculations]
    D --> E[IndicatorResult objects]
    E --> F[Bulk upsert to DB]
```

## Key Methods

### `calculate_for_stock(symbol)`
- Fetches price history for a stock
- Builds pandas DataFrame
- Calculates all indicators
- Bulk upserts results

### `calculate_all(batch_size=50)`
- Iterates all symbols
- Calls `calculate_for_stock` per symbol
- Returns summary dict

## Storage
Results stored in `indicator_results` table with unique constraint on (stock_id, trade_date).

## Error Handling
- Skips stocks with insufficient data
- Logs warnings for calculation failures
- Returns partial results on error
