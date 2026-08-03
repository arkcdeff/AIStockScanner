# Folder Structure

`
AIStockScanner/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application entry point
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py              # Main API router
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── health.py          # Health check endpoints
│   │       ├── stocks.py          # Stock CRUD endpoints
│   │       ├── market_data.py     # Market data import/export
│   │       ├── indicators.py      # Indicator calculation
│   │       └── scanners.py        # Scanner execution
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── __main__.py            # CLI entry point
│   │   └── import_nse.py          # NSE import CLI
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings and configuration
│   │   ├── logging.py             # Logging configuration
│   │   └── middleware.py          # Custom middleware
│   ├── data/
│   │   ├── __init__.py
│   │   └── nse_parser.py          # NSE bhavcopy parser
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py                # SQLAlchemy base model
│   │   └── session.py             # Async session management
│   ├── models/
│   │   ├── __init__.py
│   │   ├── stock.py               # Stock model
│   │   ├── daily_price.py         # Daily price model
│   │   ├── import_job.py          # Import job model
│   │   ├── indicator_result.py    # Indicator result model
│   │   ├── scan_definition.py     # Scan definition model
│   │   ├── scan_result.py         # Scan result model
│   │   └── sector.py              # Sector model
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── base.py                # Base repository
│   │   ├── stock.py               # Stock repository
│   │   ├── market_data.py         # Market data repository
│   │   └── scan.py                # Scan repository
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── health.py              # Health schemas
│   │   ├── stock.py               # Stock schemas
│   │   ├── market_data.py         # Market data schemas
│   │   ├── indicator.py           # Indicator schemas
│   │   └── scanner.py             # Scanner schemas
│   └── services/
│       ├── __init__.py
│       ├── stock.py               # Stock service
│       ├── nse_downloader.py      # NSE downloader
│       ├── nse_import.py          # NSE import service
│       ├── indicator_engine.py    # Indicator calculator
│       └── scanner_engine.py      # Scanner engine
├── tests/
│   ├── conftest.py                # Test configuration
│   ├── test_health.py
│   ├── test_indicators.py
│   ├── test_market_data.py
│   ├── test_nse_parser.py
│   ├── test_scanner_registry.py
│   ├── test_scanners.py
│   └── test_volume_delivery_scanners.py
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_create_core_tables.py
│       ├── 0002_market_data_foundation.py
│       ├── c64742f7da53_add_indicator_results_table.py
│       ├── fda0edd539bd_seed_scan_definitions.py
│       └── bdc596b3589a_seed_volume_and_delivery_scan_.py
├── data/
│   └── raw/
│       └── nse/                   # Raw downloaded bhavcopy files
├── docs/                          # Documentation
├── pyproject.toml
├── alembic.ini
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── README.md
├── CHANGELOG.md
└── uv.lock
`

## Design Principles

- **Flat structure**: Avoid deep nesting
- **Domain grouping**: Related files in same module
- **Explicit over implicit**: Clear file names and locations
- **Test co-location**: Tests mirror source structure
