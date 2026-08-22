# AIStockScanner — How to Run Locally

## 1. Requirements

- **OS**: Windows 10/11 (PowerShell)
- **Python**: 3.13.x (exactly 3.13; `requires-python = ">=3.13,<3.14"`)
- **uv**: 0.12.0 or newer
- **PostgreSQL**: any recent version (tested against a local instance on `localhost:5432`)
- **Browser**: any modern browser (Chrome / Edge / Firefox)

## 2. Open the Project

```powershell
cd D:\SCTS\college_cert_sys_phase1\AIStockScanner_Stage1_2\AIStockScanner_Milestone2_3\AIStockScanner
```

## 3. Install / Sync Dependencies

```powershell
uv sync
```

If you want the dev/test tooling (pytest, ruff) used by the repository's checks:

```powershell
uv sync --extra dev
```

## 4. Configure Environment

Copy `.env.example` to `.env` on first run:

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your editor and fill in the values below.

### Mandatory

| Variable | Purpose | Where to set |
|---|---|---|
| `DATABASE_URL_ASYNC` | Async SQLAlchemy connection string used by the app | `.env` |
| `DATABASE_URL_SYNC` | Sync connection string used by Alembic migrations | `.env` |
| `JWT_SECRET_KEY` | Signs access/refresh/CSRF tokens | `.env` |

Safe example (never commit real secrets):

```
DATABASE_URL_ASYNC=postgresql+asyncpg://postgres:<YOUR_DATABASE_PASSWORD>@localhost:5432/ai_stock_scanner
DATABASE_URL_SYNC=postgresql+psycopg://postgres:<YOUR_DATABASE_PASSWORD>@localhost:5432/ai_stock_scanner
JWT_SECRET_KEY=<generate-a-strong-secret>
```

The default database is:

- **Database name**: `ai_stock_scanner`
- **Host**: `localhost`
- **Port**: `5432`
- **User**: `postgres` (adjust to your actual user)

### Optional

| Variable | Purpose |
|---|---|
| `APP_NAME`, `APP_ENV`, `APP_DEBUG`, `APP_HOST`, `APP_PORT`, `LOG_LEVEL` | App identity / runtime tuning |
| `AI_PROVIDER` | `mock` (default, safe) \| `ollama` \| `deepseek` \| `openai` |
| `AI_DEEPSEEK_API_KEY` | DeepSeek key (only if using DeepSeek provider) |
| `AI_OPENAI_API_KEY` | OpenAI key (only if using OpenAI provider) |
| `AI_OLLAMA_URL`, `AI_OLLAMA_MODEL` | Ollama endpoint/model (only if using Ollama) |
| `NOTIFICATION_PROVIDER` | `mock` (default, safe) \| `telegram` \| `email` \| `in_app` |
| `NOTIFICATIONS_ENABLED` | Global notification switch (default `false`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID` | Telegram (only if using Telegram) |
| `EMAIL_HOST`, `EMAIL_USERNAME`, `EMAIL_PASSWORD` | SMTP (only if using email) |
| `EOD_SCHEDULER_ENABLED` | Enable EOD pipeline scheduler (default `false`) |
| `DAILY_SUMMARY_ENABLED` | Enable daily summary scheduler (default `false`) |
| `NOTIFICATION_DELIVERY_JOB_ENABLED` | Enable notification delivery job (default `false`) |
| `MARKET_INTELLIGENCE_*_ENABLED` | Enable market intelligence schedulers (default `false`) |
| `AUTH_COOKIE_SECURE` | Set `true` behind HTTPS in production |

## 5. PostgreSQL Setup

1. Start the PostgreSQL service (Windows):

```powershell
# Verify whether the service is running
Get-Service *postgres* | Select-Object Name, Status
```

If it is stopped, start it (service name may vary, e.g. `postgresql-x64-18`):

```powershell
Start-Service <your-postgresql-service-name>
```

2. Create the database (only if it does not already exist):

```powershell
psql -U postgres -c "CREATE DATABASE ai_stock_scanner;"
```

3. Verify connectivity through the application configuration:

```powershell
uv run python -c "from app.core.config import settings; print('DB:', settings.database_url_async.split('@')[0]); print('OK')"
```

## 6. Database Migration

```powershell
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

Verify everything is in sync:

```powershell
uv run alembic check
```

Current migration head (as of this document):

```
5f7b2c9d4e3a
```

## 7. Create First Super Admin

> **Note**: This only works when no superuser exists yet. If a superuser already
> exists, the command refuses with `A bootstrap superuser already exists`.

```powershell
uv run python -m app.cli create-admin
```

It will prompt:

```
Username:
Email:
Full name (optional):
Password:
Confirm password:
```

The password is **never printed or logged**. It is stored as an **Argon2id** hash.

If you prefer to pass the password via an environment variable (keeps it out of
history):

```powershell
$env:BOOTSTRAP_PASSWORD = "<YOUR_PASSWORD>"
uv run python -m app.cli create-admin --username <username> --email <email> --password-env BOOTSTRAP_PASSWORD
Remove-Item Env:BOOTSTRAP_PASSWORD
```

Password policy requirements:

- Minimum 12 characters
- At least 1 uppercase letter
- At least 1 lowercase letter
- At least 1 digit
- At least 1 special character (e.g. `!@#$%^&*`)

## 8. Reset Admin Password

Because a one-time bootstrap superuser may already exist, the safest way to
reset the admin password is the new `reset-password` CLI:

```powershell
uv run python -m app.cli reset-password reset-password --username admin
```

It will prompt (leaving blank auto-generates a temporary password shown once):

```
New password (leave blank to auto-generate):
Confirm password:
```

Or to auto-generate a strong temporary password:

```powershell
uv run python -m app.cli reset-password reset-password --username admin --generate
```

Or via environment variable:

```powershell
$env:NEW_ADMIN_PASSWORD = "<YOUR_PASSWORD>"
uv run python -m app.cli reset-password reset-password --username admin --password-env NEW_ADMIN_PASSWORD
Remove-Item Env:NEW_ADMIN_PASSWORD
```

You can also target by `--user-id <id>` or `--email <email>`.

## 9. Check Existing Admin Users

Use the application's own logic (read-only) with a small inline script — or log
in through the UI at `/admin/users` and inspect.

A safe Python one-liner (does not print hashes or tokens):

```powershell
uv run python -c "import asyncio; from app.db.session import get_session_factory, dispose_engine; from app.models.auth import User; from sqlalchemy import select; from sqlalchemy.orm import selectinload; async def m():\n  async with get_session_factory()() as s:\n    for u in (await s.scalars(select(User).options(selectinload(User.roles)).order_by(User.username))):\n      print(u.username, u.email, 'active=', u.is_active, 'super=', u.is_superuser, 'roles=', sorted(r.name for r in u.roles))\n  await dispose_engine()\nasyncio.run(m())"
```

## 10. Run the Application

```powershell
uv run uvicorn app.main:app --reload
```

If port 8000 is busy (for example, another service holds it), choose another port:

```powershell
uv run uvicorn app.main:app --reload --port 8001
```

## 11. Open in Browser

| Page | URL |
|---|---|
| Root (redirects to login/dashboard) | http://127.0.0.1:8000/ |
| Login | http://127.0.0.1:8000/login |
| Dashboard | http://127.0.0.1:8000/dashboard |
| Market Intelligence | http://127.0.0.1:8000/market-intelligence/ |
| Admin — Users | http://127.0.0.1:8000/admin/users |
| Admin — Roles | http://127.0.0.1:8000/admin/roles |
| Admin — Permissions | http://127.0.0.1:8000/admin/permissions |
| Scheduler | http://127.0.0.1:8000/scheduler/ |
| API docs (Swagger) | http://127.0.0.1:8000/docs |
| OpenAPI JSON | http://127.0.0.1:8000/openapi.json |
| Health | http://127.0.0.1:8000/api/v1/health |
| Health (database) | http://127.0.0.1:8000/api/v1/health/database |

> All examples above use port 8000. If you started on another port (e.g. 8001),
> adjust URLs accordingly.

## 12. Login

Use the **username or email** you created (for example `admin` or
`admin@example.com`) and the **password you chose** when creating/resetting.

Never share your real password. The app stores passwords as **Argon2id** hashes
only.

## 13. Main Pages

- **Dashboard** (`/dashboard`): Overview, health, pipeline status, AI status.
- **Market Intelligence** (`/market-intelligence/`): Market breadth, indices,
  sectors, macro, institutional flows, and AI explanations.
- **Users** (`/admin/users`): Manage users, create users, assign roles.
- **Roles** (`/admin/roles`): Manage built-in roles (`super_admin`, `admin`,
  `analyst`, `viewer`).
- **Permissions** (`/admin/permissions`): Manage permission codes.
- **Scheduler** (`/scheduler/`): Status of scheduled jobs.
- **API Docs** (`/docs`): Interactive Swagger UI.

> If Market Intelligence shows "no data" this is expected when no provider is
> configured / no snapshot has been generated. This means **APPLICATION WORKING**
> but **DATA PROVIDER NOT CONFIGURED** — not an application failure.

## 14. Safe Local Mode

To run locally without sending notifications or hitting external AI/market
services, the following `.env` values are the safe defaults (they are already
the defaults):

```ini
AI_PROVIDER=mock
NOTIFICATION_PROVIDER=mock
NOTIFICATIONS_ENABLED=false
EOD_SCHEDULER_ENABLED=false
DAILY_SUMMARY_ENABLED=false
NOTIFICATION_DELIVERY_JOB_ENABLED=false
MARKET_INTELLIGENCE_PREMARKET_ENABLED=false
MARKET_INTELLIGENCE_POSTMARKET_ENABLED=false
MARKET_INTELLIGENCE_SNAPSHOT_ENABLED=false
AI_ENABLED=true
```

- Scheduler starts **only** if at least one `*_ENABLED=true` is set. With all
  disabled, the app logs `Application scheduler disabled; not starting`.
- `mock` providers never make network calls.
- No notifications are sent when `NOTIFICATIONS_ENABLED=false`.

## 15. Stop the Application

Press `Ctrl+C` in the terminal running `uvicorn`.

## 16. Start Again Tomorrow

Short daily sequence:

1. Start PostgreSQL (if not running as a service).
2. Open PowerShell.
3. `cd` into the project.
4. Start the app:
   ```powershell
   uv run uvicorn app.main:app --reload
   ```
5. Open http://127.0.0.1:8000/login in the browser.
6. Log in with your admin username/email and your password.

(No migration needed unless the schema changed.)

## 17. Common Problems

### uv not found

Install uv first, or run via full path. Then:

```powershell
pip install uv
```

### Dependency environment broken

Re-sync from scratch:

```powershell
uv sync --reinstall
```

Optionally recreate the venv:

```powershell
uv venv --clear
uv sync
```

### Database connection refused

- Verify PostgreSQL is running: `Get-Service *postgres*`
- Check host/port/user/password in `.env` (`DATABASE_URL_ASYNC`, `DATABASE_URL_SYNC`)
- Ensure the database exists: `psql -U postgres -c "\l"`

### Alembic migration behind

```powershell
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

### Port already in use

```powershell
netstat -ano | findstr :8000
```

Then either start on another port:

```powershell
uv run uvicorn app.main:app --reload --port 8001
```

or identify the owning PID and stop it (only if you know it is safe):

```powershell
taskkill /PID <pid> /F
```

### Admin password forgotten

Use the reset command:

```powershell
uv run python -m app.cli reset-password reset-password --username admin --generate
```

### 401 Unauthorized

- You are not logged in (or session expired). Go to `/login` and sign in again.
- For API calls, provide a valid `Authorization: Bearer <token>` header.

### 403 Forbidden

- Your role does not include the required permission. Use the `super_admin`
  role or an `is_superuser=true` account.

### Scheduler / provider unavailable

- Schedulers are disabled by default. Enable only the ones you need in `.env`.
- If provider data is missing, verify the corresponding API key/provider settings.

### Missing optional AI API key

The default `AI_PROVIDER=mock` requires no key and makes no network calls. The
AI section will show mock/empty data but the app still works.

## 18. Useful Commands

| Purpose | Command |
|---|---|
| Environment check | `uv --version` |
| Python version | `uv run python --version` |
| Sync dependencies | `uv sync` |
| Import check | `uv run python -c "import app.main; print('APP IMPORT OK')"` |
| Migrations (upgrade) | `uv run alembic upgrade head` |
| Migrations (status) | `uv run alembic current` |
| Migrations (heads) | `uv run alembic heads` |
| Migrations (check) | `uv run alembic check` |
| Create first admin | `uv run python -m app.cli create-admin` |
| Reset password | `uv run python -m app.cli reset-password reset-password --username admin` |
| Seed roles/permissions | `uv run python -m app.cli seed-auth` |
| Run tests | `uv run pytest -q` |
| Ruff lint | `uv run ruff check .` |
| Start server | `uv run uvicorn app.main:app --reload` |
| View all CLI commands | `uv run python -m app.cli --help` |