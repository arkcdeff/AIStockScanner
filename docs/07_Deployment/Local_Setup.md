# Local Setup

## Prerequisites
- Python 3.13+
- PostgreSQL 18+
- uv (Python package manager)

## Installation Steps

### 1. Clone Repository
```bash
git clone https://github.com/arkcdeff/AIStockScanner.git
cd AIStockScanner
```

### 2. Create Virtual Environment
```bash
uv venv
.venv\Scripts\Activate.ps1  # Windows
# OR
source .venv/bin/activate   # Linux/macOS
```

### 3. Install Dependencies
```bash
uv sync
```

### 4. Configure Environment
```bash
cp .env.example .env
```

Edit `.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:<POSTGRES_PASSWORD>@localhost:5432/aistock
DATABASE_URL_SYNC=postgresql+psycopg://postgres:<POSTGRES_PASSWORD>@localhost:5432/aistock
SECRET_KEY=your-secret-key-here
NSE_BHAVCOPY_URL=https://www.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{day:02d}{month}{year}bhav.csv.zip
NSE_BASE_URL=https://www.nseindia.com
RAW_DATA_DIR=data/raw/nse
```

### 5. Start PostgreSQL
```bash
# Windows (service)
net start postgresql-18

# Verify connection
psql -U postgres -c "SELECT 1;"
```

### 6. Run Migrations
```bash
uv run alembic upgrade head
```

### 7. Start Development Server
```bash
uv run uvicorn app.main:app --reload --port 8000
```

### 8. Verify Installation
```bash
# Health check
curl http://localhost:8000/health

# API docs
curl http://localhost:8000/docs
```

## Common Issues

### Port Already in Use
```bash
# Find process
netstat -ano | findstr :8000

# Kill process
taskkill /PID <pid> /F
```

### Database Connection Error
- Verify PostgreSQL is running
- Check credentials in .env
- Ensure database exists: `createdb aistock`

### Module Not Found
```bash
# Ensure venv is activated
.venv\Scripts\Activate.ps1
```

## Verification
```bash
# Run tests
uv run pytest -q

# Should show: 31 passed
```
