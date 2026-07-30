# AI Stock Scanner — Milestone 1

A clean Windows-compatible foundation using Python 3.13, FastAPI, PostgreSQL, asynchronous SQLAlchemy runtime, and synchronous Alembic migrations.

## 1. Create the PostgreSQL database

In pgAdmin Query Tool, run:

```sql
CREATE DATABASE ai_stock_scanner;
```

Skip this command if the database already exists.

## 2. Open the new project

```cmd
cd /d D:\SCTS\AIStockScanner
```

Use any location you prefer, but avoid folder names containing `(1)` to reduce command-line confusion.

## 3. Create the virtual environment

```cmd
py -3.13 -m venv .venv
.venv\Scripts\activate
python --version
```

Expected: Python 3.13.x.

## 4. Install dependencies

```cmd
python -m pip install --upgrade pip uv
uv sync --extra dev --link-mode=copy
```

`--link-mode=copy` suppresses the harmless hardlink warning when the uv cache and project are on different filesystems.

## 5. Configure environment variables

```cmd
copy .env.example .env
notepad .env
```

Replace `YOUR_PASSWORD` in both database URLs with your PostgreSQL password.

Important: keep both URLs.

- `DATABASE_URL_ASYNC` uses `asyncpg` for the FastAPI application.
- `DATABASE_URL_SYNC` uses synchronous `psycopg` for Alembic.

## 6. Apply the included first migration

```cmd
uv run alembic upgrade head
```

The initial migration is already included. This migration setup does not call `asyncio.run()` and therefore avoids the Windows ProactorEventLoop error. For future model changes, create a migration with:

```cmd
uv run alembic revision --autogenerate -m "describe your change"
uv run alembic upgrade head
```

## 7. Run the tests

```cmd
uv run pytest
uv run ruff check .
```

## 8. Start the API

```cmd
uv run uvicorn app.main:app --reload
```

Open:

- API root: http://127.0.0.1:8000/
- Swagger documentation: http://127.0.0.1:8000/docs
- Application health: http://127.0.0.1:8000/api/v1/health
- Database health: http://127.0.0.1:8000/api/v1/health/database

## Useful commands

```cmd
uv run alembic current
uv run alembic history
uv run alembic upgrade head
uv run pytest --cov=app
```

## Milestone 1 contents

- Structured FastAPI application
- Pydantic settings loaded from `.env`
- Async SQLAlchemy runtime with asyncpg
- Sync Alembic migrations with psycopg
- Four core PostgreSQL models
- Repository and service layers
- Scanner plugin contract and registry
- Request ID middleware and Loguru logging
- Health and stock APIs
- Tests, Ruff configuration, Dockerfile, and Docker Compose
