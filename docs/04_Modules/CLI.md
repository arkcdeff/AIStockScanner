# CLI

## Overview
Command-line interface for NSE data import operations.

## Entry Point
`python -m app.cli`

## Commands

### import-nse
Download and import NSE bhavcopy for a specific date.

**Usage:**
```bash
python -m app.cli import-nse --date 2024-01-15
```

**Options:**
- `--date`: Trade date (YYYY-MM-DD format)
- `--delivery`: Path to delivery file (optional)

**Example:**
```bash
# Import bhavcopy for Jan 15, 2024
python -m app.cli import-nse --date 2024-01-15

# Import with delivery file
python -m app.cli import-nse --date 2024-01-15 --delivery ./delivery.csv
```

## Implementation

```python
# app/cli/import_nse.py
from app.cli.__main__ import app
from app.services.nse_downloader import NSEDownloader
from app.services.nse_import import NSEImportService

@app.command()
def import_nse(date: str, delivery: str | None = None):
    # Download bhavcopy
    with NSEDownloader() as downloader:
        path, meta = downloader.download(date)
    
    # Import to database
    # ... (implementation in app/cli/import_nse.py)
```

## Output
```
INFO - Downloaded bhavcopy for 2024-01-15 to data/raw/nse/2024/01/cm15jan2024bhav.csv.zip
INFO - Import completed: 1000 records processed, 0 errors
```
