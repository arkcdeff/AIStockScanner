# Sprint 04: Indicator Engine

## Duration
2026-08-01 (1 day)

## Status
Completed

## Objectives
- Build pandas-ta based indicator calculation engine
- Calculate 15 technical indicators
- Store results in indicator_results table
- Create indicator APIs

## Deliverables
- [x] IndicatorEngine with pandas-ta integration
- [x] 15 technical indicators (EMA, SMA, RSI, MACD, ATR, ADX, SuperTrend, VWAP, OBV, Bollinger Bands, Stochastic)
- [x] Bulk upsert to indicator_results
- [x] Indicator calculation APIs
- [x] Auto-calculation after import

## Files Created
- `app/models/indicator_result.py`
- `app/services/indicator_engine.py`
- `app/schemas/indicator.py`
- `app/api/routes/indicators.py`
- `tests/test_indicators.py`

## Files Modified
- `app/models/stock.py` - Added indicator_results relationship
- `app/models/__init__.py` - Added IndicatorResult
- `app/api/router.py` - Registered indicators.router
- `pyproject.toml` - Added pandas-ta, pandas, numpy

## Migration
- `c64742f7da53_add_indicator_results_table.py` - Created indicator_results table

## Indicators Calculated
- EMA 20, 50, 100, 200
- SMA 20
- RSI 14
- MACD (line, signal, histogram)
- Stochastic %K, %D
- ATR 14
- Bollinger Bands (upper, middle, lower)
- ADX 14
- SuperTrend + direction
- VWAP
- OBV

## APIs Implemented
- `POST /api/v1/indicators/calculate` - Calculate indicators (all or specific stock)
- `GET /api/v1/indicators/status` - Get indicator status (all or specific stock)

## Test Coverage
- Indicator calculation tests
- API endpoint tests
- Error handling tests

## Next Sprint
Scanner Engine with 25 scanners.
