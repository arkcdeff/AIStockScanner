# Architecture

## System Architecture

AI Stock Scanner follows a layered architecture with clear separation of concerns.

`mermaid
graph TB
    subgraph Client
        A[HTTP Client]
    end
    subgraph API_Layer
        B[FastAPI App]
        C[Middleware]
        D[Routers]
    end
    subgraph Service_Layer
        E[NSE Import Service]
        F[Indicator Engine]
        G[Scanner Engine]
        H[Stock Service]
    end
    subgraph Repository_Layer
        I[Stock Repository]
        J[Market Data Repository]
        K[Scan Repository]
    end
    subgraph Data_Layer
        L[SQLAlchemy ORM]
        M[asyncpg]
        N[PostgreSQL]
    end
    A --> B
    B --> C
    C --> D
    D --> E
    D --> F
    D --> G
    D --> H
    E --> I
    F --> I
    G --> K
    I --> L
    J --> L
    K --> L
    L --> M
    M --> N
`

## Architectural Principles

1. **Separation of Concerns**: Each layer has a distinct responsibility
2. **Dependency Inversion**: Services depend on repository interfaces, not concrete implementations
3. **Single Responsibility**: Each module handles one domain concern
4. **Open/Closed**: Scanner registry allows extension without modification
5. **Testability**: Clear interfaces enable comprehensive testing

## Key Patterns

### Async Session per Request
- get_db_session dependency provides scoped AsyncSession
- Session is committed or rolled back per request
- Engine disposal handled by test fixture

### Repository Pattern
- Each entity has a dedicated repository
- Queries encapsulated in repository methods
- Reusable across services and tests

### Service Pattern
- Business logic isolated from HTTP concerns
- Transaction boundaries managed in services
- Scanner execution orchestrated by ScannerService

### Registry Pattern
- Scanners registered via decorator
- Dynamic discovery of scanner functions
- Extensible without modifying core logic

### Migration-based Schema
- Alembic manages all schema changes
- Synchronous migrations for Windows compatibility
- Version-controlled database evolution
