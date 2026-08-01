# Architecture

The application is split into API, service, repository, database, model, and scanner layers.

- FastAPI handles HTTP routing and validation.
- Services contain business rules and transaction boundaries.
- Repositories contain database queries.
- SQLAlchemy async sessions use `asyncpg` during application runtime.
- Alembic uses a separate synchronous `psycopg` URL, which is reliable on Windows and avoids the ProactorEventLoop incompatibility seen with async migrations.
- Scanner implementations conform to a small plugin contract and are registered through `ScannerRegistry`.
