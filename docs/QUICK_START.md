# AIStockScanner — Quick Start

Fastest path from zero to a running app on Windows.

## 1. Open the project

```powershell
cd D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner
```

## 2. Sync dependencies

```powershell
uv sync
```

## 3. Configure environment (first run only)

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

```
DATABASE_URL_ASYNC=postgresql+asyncpg://postgres:<YOUR_DATABASE_PASSWORD>@localhost:5432/ai_stock_scanner
DATABASE_URL_SYNC=postgresql+psycopg://postgres:<YOUR_DATABASE_PASSWORD>@localhost:5432/ai_stock_scanner
JWT_SECRET_KEY=<generate-a-strong-secret>
```

## 4. PostgreSQL check

```powershell
Get-Service *postgres* | Select-Object Name, Status
```

If the database does not exist yet:

```powershell
psql -U postgres -c "CREATE DATABASE ai_stock_scanner;"
```

## 5. Run migrations

```powershell
uv run alembic upgrade head
```

## 6. Create first admin (first run only)

```powershell
uv run python -m app.cli create-admin
```

If a superuser already exists, reset the password instead:

```powershell
uv run python -m app.cli reset-password reset-password --username admin --generate
```

## 7. Start the app

```powershell
uv run uvicorn app.main:app --reload
```

If port 8000 is busy:

```powershell
uv run uvicorn app.main:app --reload --port 8001
```

## 8. Open in browser

```
http://127.0.0.1:8000/login
```

Log in with the username/email and password you created.

## 9. Stop

Press `Ctrl+C` in the terminal.

---

**Daily restart**: start PostgreSQL → `cd` project → `uv run uvicorn app.main:app --reload` → open browser → login.

See [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md) for the full guide.