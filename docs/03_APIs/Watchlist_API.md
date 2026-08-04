# Watchlist API

Manage named watchlists and the stocks inside them. Single-user (no auth); the schema
is ready for per-user tables later.

## Base URL
`/api/v1/watchlists`

## Watchlist Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Watchlist id |
| `name` | string | Watchlist name |
| `description` | string \| null | Free-form notes |
| `color` | string \| null | Display color |
| `icon` | string \| null | Display icon |
| `sort_order` | int | Manual sort priority |
| `is_favorite` | bool | Pinned to top |
| `is_default` | bool | Default watchlist (only one allowed) |
| `stock_count` | int | Number of stocks (list/detail responses) |
| `created_at` / `updated_at` | datetime | Timestamps |

## Watchlist Stock Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Entry id |
| `watchlist_id` | int | Parent watchlist |
| `stock_id` | int | Stock id |
| `notes` | string \| null | Per-stock notes |
| `priority` | int | Per-stock sort priority |
| `added_at` | datetime | When added |
| `symbol` | string | Stock symbol (price-enriched responses) |
| `company_name` | string | Company name (price-enriched responses) |
| `latest_close` | float \| null | Latest close (price-enriched responses) |
| `trade_date` | date \| null | Latest price date (price-enriched responses) |

## Endpoints

### GET /watchlists/
List all watchlists with `stock_count`.

### POST /watchlists/
Create a watchlist.

**Body:**
```json
{
  "name": "My Watchlist",
  "description": null,
  "color": "#2563eb",
  "icon": null,
  "sort_order": 0,
  "is_favorite": false,
  "is_default": false
}
```
Creating with `is_default: true` demotes any existing default. Returns the created
watchlist (200) or `400` if `name` is missing.

### GET /watchlists/{watchlist_id}
Watchlist detail with `stocks` array (price-enriched). `404` if not found.

### PUT /watchlists/{watchlist_id}
Update fields (only provided fields change). `404` if not found.

### DELETE /watchlists/{watchlist_id}
Delete the watchlist (cascades to its stock entries). `204` on success.

### GET /watchlists/search
Search stocks by symbol or company name (substring match).

**Query Parameters:**
- `q` (default: `""`)
- `limit` (default: 20, max: 50)

**Response:**
```json
[
  { "id": 1, "symbol": "RELIANCE", "company_name": "Reliance Industries Ltd" }
]
```

### GET /watchlists/{watchlist_id}/stocks
Price-enriched list of stocks in the watchlist. `404` if watchlist not found.

### POST /watchlists/{watchlist_id}/stocks
Add a stock to the watchlist.

**Body:**
```json
{ "stock_id": 1, "notes": "watch closely", "priority": 2 }
```
`400` if the stock is already in the watchlist or the ids are invalid.

### PATCH /watchlists/{watchlist_id}/stocks/{stock_id}
Update `notes` / `priority` for an entry.

### DELETE /watchlists/{watchlist_id}/stocks/{stock_id}
Remove a stock from the watchlist. `204` on success.

## Status Codes
- `200 OK`: Success
- `204 No Content`: Delete succeeded
- `400 Bad Request`: Validation or duplicate stock
- `404 Not Found`: Watchlist/stock not found
- `422 Unprocessable Entity`: Invalid body
