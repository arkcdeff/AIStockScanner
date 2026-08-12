# Backtesting API

Base URL: `/api/v1/backtests`

REST management for backtest strategies, runs, execution, and results (Sprint 17
Task 17.5). Routes are defined in `app/api/routes/backtests.py`.

## Permissions

New permission codes added in Task 17.5 (`app/services/builtin_auth.py`,
`docs/01_Architecture/Permission_Matrix.md`):

| Code | Allowed roles |
| --- | --- |
| `backtests.read` | super_admin, admin, analyst, viewer |
| `backtests.run` | super_admin, admin, analyst |
| `backtests.write` | super_admin, admin |

All endpoints require authentication (Bearer token). Unauthenticated requests get
`401`. The ownership policy below is applied on top of the permission checks.

## Ownership policy

- **Strategies are shared definitions.** Anyone with `backtests.read` can list and
  view them; `backtests.write` is required to create/update/deactivate. Strategy
  delete is a **soft deactivate** (`is_active = false`) so historical runs keep
  their foreign keys valid.
- **Runs are private to their `requested_by_user_id`.** A non-admin caller may
  only access runs they requested. Administrators (`admin` role, `super_admin`
  role, or `is_superuser`) may access any run; an `admin_override` audit event is
  recorded on such access. Denied accesses record an `access_denied` audit event.
- `list_runs` hides other users' runs from non-admin callers.

## Endpoints

| Method | Path | Permission | Description |
| --- | --- | --- | --- |
| POST | `/strategies` | write | Create a strategy |
| GET | `/strategies` | read | List strategies (paged, filterable) |
| GET | `/strategies/{strategy_id}` | read | Get a strategy |
| PUT | `/strategies/{strategy_id}` | write | Update a strategy |
| DELETE | `/strategies/{strategy_id}` | write | Soft-deactivate a strategy |
| POST | `/runs` | write | Create a run |
| GET | `/runs` | read | List runs (paged, filterable) |
| GET | `/runs/{run_id}` | read | Get run detail |
| POST | `/runs/{run_id}/execute` | run | Execute the run synchronously |
| GET | `/runs/{run_id}/summary` | read | Run summary + metric |
| GET | `/runs/{run_id}/trades` | read | Page trades with filters |
| GET | `/runs/{run_id}/equity` | read | Equity curve points |
| GET | `/runs/{run_id}/metrics` | read | Persisted metric row |

## Lifecycle

```
strategy (create) -> run (create, status=pending) -> execute
  -> status=running -> status=completed (or failed)
     -> summary / trades / equity / metrics
```

- A run is executed synchronously in the request (no Celery/Redis). Concurrent
  executions of the same run are serialized by an in-process per-run lock; a run
  already `running` or `completed` is rejected with `409`.
- On success the run is persisted as `completed` with `final_capital`,
  `finished_at`, `duration_ms`, and its trades, equity curve, and metric row.
- On failure the run is persisted as `failed` with a truncated `error_message`
  and the response is sanitized (see Error codes).
- Metrics are only available after the run completes (`GET .../metrics` returns
  `409` while pending/running/failed).

## Strategy payload

```json
{
  "name": "API Trend Strategy",
  "code": "api_trend",
  "strategy_type": "scanner",
  "scanner_code": "ema_bullish_crossover",
  "configuration_json": {"execution_mode": "next_open"},
  "is_active": true
}
```

- `strategy_type` must be `scanner`, `rule_based`, or `composite`.
- `code` is unique; duplicates return `409`.
- `scanner_code`, when present, must reference an **active** `scan_definitions`
  row; otherwise `422`.
- `configuration_json` supports `execution_mode` (`next_open`, `next_close`,
  `signal_close`) and an `exit` object (`stop_loss_percent`, `take_profit_percent`,
  `max_holding_days`, `opposite_scanner_code`), validated during execution.

## Run payload

```json
{
  "strategy_id": 1,
  "start_date": "2023-01-02",
  "end_date": "2023-01-06",
  "initial_capital": 100000,
  "commission_rate": 0.001,
  "slippage_rate": 0.001,
  "position_size_type": "fixed_quantity",
  "position_size_value": 10,
  "max_positions": 5
}
```

Validation (`422` when violated):

- `start_date <= end_date`.
- `initial_capital > 0`; `commission_rate`, `slippage_rate`, `position_size_value >= 0`.
- `position_size_type` one of `fixed_amount`, `fixed_quantity`, `percent_capital`,
  `equal_weight`.
- `end_date` cannot be in the future.
- `benchmark_symbol`, when present, must reference an active stock.
- The referenced strategy must be active.

## Execute response

```json
{
  "run_id": 1,
  "status": "completed",
  "final_capital": "99996.000",
  "summary": {
    "run_id": 1,
    "start_date": "2023-01-02",
    "end_date": "2023-01-06",
    "signals_received": 1,
    "entries_created": 1,
    "entries_skipped": 0,
    "trades_closed": 1,
    "open_positions_forced_closed": 0,
    "initial_capital": "100000",
    "final_capital": "99996.000",
    "gross_pnl": "0.000",
    "total_costs": "4.000",
    "net_pnl": "-4.000",
    "trading_days": 3,
    "duration_ms": 12.34,
    "skip_reasons": {}
  },
  "metrics": {
    "run_id": 1,
    "total_return_percent": "-0.004",
    "total_trades": 1,
    "winning_trades": 0,
    "losing_trades": 1,
    "win_rate": "0",
    "max_drawdown_percent": "0",
    "sharpe_ratio": null,
    "sortino_ratio": null,
    "benchmark_return_percent": null,
    "alpha": null
  }
}
```

`metrics` is `null` if metric computation fails (execution itself still succeeds).

## Trades listing

`GET /runs/{run_id}/trades?limit=20&offset=0&status=closed&position_side=long&profitable=true&symbol=RELIANCE`

- `status`: `open` | `closed`
- `position_side`: `long` | `short`
- `profitable`: `true` (net_pnl > 0) / `false` (net_pnl <= 0)
- `symbol`: exact stock symbol (upper-cased server-side)

## Equity listing

`GET /runs/{run_id}/equity` returns the equity curve ordered by `trade_date` with
`cash`, `invested_value`, `portfolio_value`, `drawdown_percent`.

## Summary

`GET /runs/{run_id}/summary` returns `run`, `strategy`, `trade_count` (closed
trades), and `metric` (the persisted metric row, `null` until completion).

## Error codes

| Code | Meaning |
| --- | --- |
| `401` | Missing/invalid bearer token |
| `403` | Authenticated but no permission / not the run owner and not an admin |
| `404` | Strategy/run not found, or metrics missing |
| `409` | Duplicate strategy code; run already running/completed; metrics before completion |
| `422` | Validation failure (schema or active-scanner/stock/strategy checks) |
| `400` | Execution failed on a `ValueError` (sanitized) |
| `500` | Execution failed on an unexpected exception (sanitized) |

Execution failures never leak internals: the detail is
`"Backtest execution failed: <ExceptionType>"` (e.g. `ValueError`) and the run is
persisted as `failed`.

## Settlement correction (Task 17.5A)

The simulator previously double-counted the entry commission in `final_capital`
(entry outlay deducted from cash, then `net_pnl` also subtracted the entry
commission). Both settlement sites (`exit_result` close in the daily loop and the
`end_of_backtest` forced close) now credit
`net_pnl + entry_value + entry_commission`, preserving the identity
`final_capital = initial_capital + gross_pnl - costs`. Regression tests live in
`tests/test_backtest_simulator.py` (section `# SETTLEMENT`) and the API-level check
in `tests/test_backtest_api.py::test_execute_run_end_to_end_and_settlement`.
