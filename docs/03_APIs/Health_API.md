# Health API

## Endpoints

### GET /health
Returns application health status.

**Response:**
```json
{
  "application": "AI Stock Scanner",
  "environment": "development"
}
```

### GET /health/database
Returns database health status.

**Response (Healthy):**
```json
{
  "status": "ok",
  "database": "postgresql"
}
```

**Response (Unhealthy):**
```json
{
  "status": "error",
  "database": "postgresql",
  "detail": "connection refused"
}
```
