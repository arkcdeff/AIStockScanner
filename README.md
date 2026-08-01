# AI Stock Scanner — Milestones 1–3

FastAPI + PostgreSQL foundation with NSE EOD CSV/ZIP import.

## Upgrade an existing Milestone 1 database

```powershell
uv sync --extra dev --link-mode=copy
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8001
```

Open `http://127.0.0.1:8001/docs`.

## Import NSE files

Swagger: `POST /api/v1/market-data/import/nse`

CLI:

```powershell
uv run python -m app.cli import-nse "D:\data\bhavcopy.csv" --delivery "D:\data\delivery.csv"
```

Bhavcopy may be CSV or ZIP. Both legacy NSE and newer UDiFF-style column names are accepted.

## Useful endpoints

- `GET /api/v1/market-data/summary`
- `GET /api/v1/market-data/prices`
- `GET /api/v1/market-data/imports`
- `GET /api/v1/stocks`
- `GET /api/v1/health/database`

## Test

```powershell
uv run pytest
uv run ruff check .
```
