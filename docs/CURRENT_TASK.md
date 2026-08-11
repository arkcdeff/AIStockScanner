# Current Task

Last updated: Sprint 17 Task 17.4 (Backtest Performance Metrics Engine) complete and verified.

## In progress / status

- Task 17.1 — COMPLETE & verified (persistence/domain foundation).
- Task 17.2 — VERIFIED (historical NSE fetch + replay; manual local-file fallback confirmed).
- Task 17.3 — COMPLETE (simulation engine; two cost-math bugs fixed in `backtest_simulator.py`;
  23 tests in `tests/test_backtest_simulator.py`).
- Task 17.4 — **COMPLETE & VERIFIED** (performance metrics engine fully implemented, CLI-wired,
  40 tests in `tests/test_backtest_metrics.py`; benchmark TODO resolved; risk-free = 0).
- CLI regression — **FIXED** (create_admin Typer/legacy dual mode; main() guards added).

## Verification status

- alembic current/heads/check: clean (`d7b4cc79fd45 (head)`, no new upgrade operations)
- ruff: clean
- pytest ×3: 521 passed, 1 warning (unrelated httpx deprecation) each
- git diff --check: clean

## Next action

**Task 17.5 — Backtesting REST APIs.** Do NOT start 17.5 until this handoff is read and the
project owner starts the new task. Leave all current changes uncommitted.

## Key files

- `app/services/backtest_metrics.py` — Task 17.4 metrics engine (returns, trade stats, drawdown,
  Sharpe/Sortino, benchmark return, alpha; None-when-undefined convention; sanitized failure policy).
- `tests/test_backtest_metrics.py` — Task 17.4 test suite (40 tests).
- `app/cli/backtest.py` — metrics table shown after successful simulation; `compute_metrics` /
  `simulate_and_metrics` reuse the same table helper.
- `app/services/backtest_simulator.py` — simulation engine (known issue: entry commission
  double-counted in `final_capital`; outside 17.4 scope, see Sprint_17.md Known issues).
- `docs/05_Development/Sprint_17.md`, `docs/05_Development/PROJECT_STATE.md` — updated for 17.4.
