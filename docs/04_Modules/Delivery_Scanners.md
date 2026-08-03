# Delivery Scanners

## Overview
Delivery scanners identify stocks with high institutional delivery activity.

## Registered Scanners

### delivery_above_60
**Logic:** Delivery percentage >= 60%
**Metrics:** symbol, close, delivery_percent, reason
**Code:** `ScannerRegistry.register("delivery_above_60")`

### delivery_above_70
**Logic:** Delivery percentage >= 70%
**Metrics:** symbol, close, delivery_percent, reason
**Code:** `ScannerRegistry.register("delivery_above_70")`

### delivery_above_average
**Logic:** Current delivery% > 20-day average delivery%
**Metrics:** symbol, close, delivery_percent, average_delivery_20d, reason
**Code:** `ScannerRegistry.register("delivery_above_average")`

### delivery_breakout
**Logic:** Current delivery% > maximum delivery% in last 20 sessions
**Metrics:** symbol, close, delivery_percent, max_delivery_20d, breakout_level, reason
**Code:** `ScannerRegistry.register("delivery_breakout")`

## Data Requirements
Delivery data requires both:
1. Bhavcopy file (OHLC + volume)
2. Delivery file (delivery quantity and percentage)

Both files are parsed and merged by NSE parser:
```python
delivery_records = parse_delivery(delivery_bytes)
price_records, errors = parse_bhavcopy(bhavcopy_bytes, delivery=delivery_records)
```

## Usage

```python
from app.services.scanner_engine import ScannerRegistry

# List delivery scanners
delivery_scanners = [code for code in ScannerRegistry.all() if code.startswith("delivery_")]
```

## Interpretation
- High delivery (>60-70%) suggests institutional interest
- Delivery breakout indicates unusual institutional activity
- Combined with volume scanners for stronger signals
