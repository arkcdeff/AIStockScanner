# Portfolio API

Track broker accounts as portfolios, hold stocks in them, and get live valuations
(using the latest stored `DailyPrice`). Single-user (no auth).

## Base URL
`/api/v1/portfolios`

## Portfolio Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Portfolio id |
| `broker` | string \| null | Broker name |
| `account_name` | string \| null | Account label |
| `notes` | string \| null | Free-form notes |
| `created_at` / `updated_at` | datetime | Timestamps |

## Holding Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Holding id |
| `portfolio_id` | int | Parent portfolio |
| `stock_id` | int | Stock id |
| `quantity` | number | Quantity (Decimal, > 0) |
| `average_price` | number | Average buy price (Decimal, > 0) |
| `purchase_date` | date \| null | Purchase date |
| `broker_order_id` | string \| null | Broker reference |
| `notes` | string \| null | Free-form notes |

## Valuation Model (price-enriched holding)

| Field | Type | Description |
|-------|------|-------------|
| `symbol` / `company_name` | string | Stock identity |
| `latest_close` | float \| null | Latest close price |
| `previous_close` | float \| null | Previous close (for today's P&L) |
| `invested` | float | `quantity * average_price` |
| `current_value` | float \| null | `quantity * latest_close` |
| `pl` | float \| null | `current_value - invested` |
| `roi_pct` | float \| null | `pl / invested * 100` |
| `today_pl` | float \| null | `(latest_close - previous_close) * quantity` |
| `allocation_pct` | float | `current_value / portfolio total * 100` |

Valuations use the **latest** `DailyPrice` row for each stock; if a stock has no prices,
`current_value`/`pl`/`roi_pct`/`today_pl` are `null`.

## Summary Model

| Field | Type | Description |
|-------|------|-------------|
| `holdings_count` | int | Number of holdings |
| `total_invested` | float | Sum of invested |
| `total_value` | float | Sum of current values |
| `total_pl` | float | `total_value - total_invested` |
| `total_roi_pct` | float | `total_pl / total_invested * 100` |
| `today_pl` | float | Sum of today's P&L |

## Endpoints

### GET /portfolios/
List portfolios with summary fields (`holdings_count`, `total_value`, etc.).

### POST /portfolios/
Create a portfolio. At least one of `broker` / `account_name` is required.

**Body:**
```json
{ "broker": "Zerodha", "account_name": "Main", "notes": null }
```

### GET /portfolios/{portfolio_id}
Portfolio detail: base fields + `holdings_count` + summary + price-enriched `holdings` array.
`404` if not found.

### PUT /portfolios/{portfolio_id}
Update `broker` / `account_name` / `notes`. `404` if not found.

### DELETE /portfolios/{portfolio_id}
Delete the portfolio (cascades to holdings). `204` on success.

### GET /portfolios/summary
Overall summary across all portfolios (aggregated valuations).

### GET /portfolios/{portfolio_id}/holdings
Price-enriched holdings list. `404` if portfolio not found.

### POST /portfolios/{portfolio_id}/holdings
Add a holding.

**Body:**
```json
{ "stock_id": 1, "quantity": 10, "average_price": 2000, "purchase_date": null }
```
`400` if the stock is already held in the portfolio or ids/quantities are invalid.

### PUT /portfolios/{portfolio_id}/holdings/{holding_id}
Update `quantity` / `average_price` / etc.

### DELETE /portfolios/{portfolio_id}/holdings/{holding_id}
Remove a holding. `204` on success.

## Status Codes
- `200 OK`: Success
- `204 No Content`: Delete succeeded
- `400 Bad Request`: Validation, missing identifier, duplicate holding
- `404 Not Found`: Portfolio/holding not found
- `422 Unprocessable Entity`: Invalid body
