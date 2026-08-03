# Volume Scanners

## Overview
Volume scanners identify unusual volume activity in stocks.

## Registered Scanners

### volume_2x_average
**Logic:** Current volume >= 2x 20-day average volume
**Metrics:** symbol, close, volume, average_volume_20d, volume_multiple, reason
**Code:** `ScannerRegistry.register("volume_2x_average")`

### volume_5x_average
**Logic:** Current volume >= 5x 20-day average volume
**Metrics:** symbol, close, volume, average_volume_20d, volume_multiple, reason
**Code:** `ScannerRegistry.register("volume_5x_average")`

### highest_volume_20d
**Logic:** Current volume >= maximum volume in last 20 sessions
**Metrics:** symbol, close, volume, max_volume_20d, reason
**Code:** `ScannerRegistry.register("highest_volume_20d")`

### volume_breakout
**Logic:** Current volume > maximum volume in last 20 sessions (strict breakout)
**Metrics:** symbol, close, volume, max_volume_20d, breakout_level, reason
**Code:** `ScannerRegistry.register("volume_breakout")`

## Usage

```python
from app.services.scanner_engine import ScannerRegistry

# List volume scanners
volume_scanners = [code for code in ScannerRegistry.all() if code.startswith("volume_")]

# Run specific scanner
ScannerRegistry.get("volume_2x_average")(current_row, previous_row)
```

## Combined Scanners
These scanners combine volume with other criteria:
- `high_volume_high_delivery`: Volume >= 2x avg AND delivery >= 60%
- `price_breakout_high_delivery`: Price breakout + volume >= avg + delivery >= 60%
- `accumulation`: Price up + volume > avg + delivery > avg
