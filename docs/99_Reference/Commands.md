# Commands

## Development Commands

### Virtual Environment
```bash
# Create venv
uv venv

# Activate (Windows)
.venv\Scripts\Activate.ps1

# Activate (Linux/macOS)
source .venv/bin/activate
```

### Dependencies
```bash
# Install all dependencies
uv sync

# Install production dependencies only
uv sync --no-dev
```

### Database Migrations
```bash
# Upgrade to latest
uv run alembic upgrade head

# Downgrade one step
uv run alembic downgrade -1

# Create new migration
uv run alembic revision --autogenerate -m "description"

# Show current revision
uv run alembic current
```

### Run Server
```bash
# Development with auto-reload
uv run uvicorn app.main:app --reload --port 8000

# Production
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Run Tests
```bash
# Run all tests
uv run pytest -q

# Run with coverage
uv run pytest --cov=app --cov-report=term-missing

# Run specific test
uv run pytest tests/test_scanners.py -v

# Run with output
uv run pytest -s
```

### Linting/Formatting
```bash
# Lint with ruff
uv run ruff check app/

# Format with ruff
uv run ruff format app/
```

## CLI Commands
```bash
# Import NSE data for specific date
uv run python -m app.cli import-nse --date 2024-01-15

# Import with delivery file
uv run python -m app.cli import-nse --date 2024-01-15 --delivery ./delivery.csv
```

## Git Commands
```bash
# Status
git status

# Stage all
git add .

# Commit
git commit -m "message"

# Push
git push origin main

# Pull
git pull origin main
```

## Docker Commands
```bash
# Start PostgreSQL
docker-compose up -d postgres

# Stop services
docker-compose down

# View logs
docker-compose logs -f postgres
```
