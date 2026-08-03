# Tech Stack

## Backend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Python | 3.13 | Core programming language |
| Framework | FastAPI | >=0.115,<1.0 | Async API framework |
| ASGI Server | Uvicorn | >=0.34,<1.0 | Production ASGI server |
| ORM | SQLAlchemy | >=2.0.36,<3.0 | Database ORM |
| Async Driver | asyncpg | >=0.30,<1.0 | Async PostgreSQL driver |
| Sync Driver | psycopg[binary] | >=3.2,<4.0 | Sync PostgreSQL driver (Alembic) |
| Migrations | Alembic | >=1.14,<2.0 | Database schema migrations |
| Validation | Pydantic | >=2.10,<3.0 | Data validation |
| Settings | pydantic-settings | >=2.7,<3.0 | Environment configuration |
| HTTP Client | httpx | >=0.28,<1.0 | Async HTTP for NSE downloads |
| Indicators | pandas-ta | >=0.3.14b | Technical indicator calculations |
| Data | pandas | >=2.0,<3.0 | Data manipulation |
| Numeric | numpy | >=1.24,<3.0 | Numerical operations |
| Logging | loguru | >=0.7,<1.0 | Structured logging |
| Testing | pytest | >=8.3,<10.0 | Test framework |
| Async Testing | pytest-asyncio | >=0.25,<2.0 | Async test support |
| Linting | ruff | >=0.9,<1.0 | Linting and formatting |

## Database

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Database | PostgreSQL | 18 | Primary data store |
| Migration | Alembic | >=1.14,<2.0 | Schema versioning |

## Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Container | Docker | Containerization |
| Process | uv | Python package management |
| Version Control | Git | Source control |

## Technology Choices Rationale

### FastAPI
- Native async support
- Automatic OpenAPI documentation
- High performance (on par with NodeJS)
- Type hints and validation via Pydantic

### SQLAlchemy 2.x
- Modern async ORM
- Mature and battle-tested
- Excellent PostgreSQL support
- Clean query API

### asyncpg
- Fastest async PostgreSQL driver
- Native async protocol
- Low overhead

### pandas-ta
- 100+ technical indicators
- Pandas-native API
- Active maintenance

### PostgreSQL 18
- ACID compliant
- Excellent performance
- Rich data types
- JSON support
