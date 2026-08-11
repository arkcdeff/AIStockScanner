"""Backtest CLI commands.

Provides commands to:
- create strategies
- create runs
- simulate runs
- compute metrics
- list strategies/runs
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from app.db.session import get_session_factory as async_session_factory
from app.repositories.backtest import (
    BacktestRunRepository,
    BacktestStrategyRepository,
)
from app.schemas.backtest import (
    BacktestRunCreate,
    BacktestStrategyCreate,
    BacktestStrategyUpdate,
)
from app.services.backtest_metrics import (
    BacktestMetricCalculator,
    MetricSummary,
    sanitized_metric_failure,
)
from app.services.backtest_simulator import BacktestSimulator

app = typer.Typer()
console = Console()


def _fmt_metric(value: Decimal | None) -> str:
    """Format a nullable Decimal metric value."""
    return "N/A" if value is None else f"{value:.4f}"


def _fmt_pct(value: Decimal | None) -> str:
    """Format a nullable Decimal percent metric value."""
    return "N/A" if value is None else f"{value:.2f}%"


async def _compute_and_print_metrics(
    run_id: int, calculator: BacktestMetricCalculator
) -> MetricSummary:
    """Compute metrics for a run and print the performance summary table.

    If metric computation fails, a sanitized error is surfaced (no internal
    exception details) and the process exits with a non-zero status.
    """
    try:
        metrics = await calculator.compute_metrics(run_id)
    except Exception as exc:
        console.print(str(sanitized_metric_failure(run_id, exc)), style="red")
        raise typer.Exit(1) from exc

    run = await calculator.runs.get(run_id)
    table = Table(title=f"Backtest Metrics — Run {run_id}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    rows = [
        ("Initial Capital", f"{run.initial_capital:,.2f}" if run else "N/A"),
        (
            "Final Capital",
            f"{run.final_capital:,.2f}"
            if run and run.final_capital is not None
            else "N/A",
        ),
        ("Total Return", _fmt_pct(metrics.total_return_percent)),
        ("CAGR", _fmt_pct(metrics.cagr)),
        ("Trades", str(metrics.total_trades)),
        ("Win Rate", _fmt_pct(metrics.win_rate)),
        ("Max Drawdown", _fmt_pct(metrics.max_drawdown_percent)),
        ("Sharpe", _fmt_metric(metrics.sharpe_ratio)),
        ("Sortino", _fmt_metric(metrics.sortino_ratio)),
        ("Profit Factor", _fmt_metric(metrics.profit_factor)),
        ("Benchmark Return", _fmt_pct(metrics.benchmark_return_percent)),
        ("Alpha", _fmt_pct(metrics.alpha)),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)
    return metrics

# ------------------------------------------------------------------ strategies

@app.command()
def create_strategy(
    name: Annotated[str, typer.Option(help="Strategy name")],
    code: Annotated[str, typer.Option(help="Strategy code")],
    strategy_type: Annotated[str, typer.Option(help="Strategy type")] = "scanner",
    scanner_code: Annotated[str | None, typer.Option(help="Scanner code")] = None,
    description: Annotated[str | None, typer.Option(help="Description")] = None,
    config_json: Annotated[str, typer.Option(help="Configuration JSON")] = "{}",
    is_active: Annotated[bool, typer.Option(help="Is active")] = True,
    created_by_user_id: Annotated[int | None, typer.Option(help="User ID")] = None,
) -> None:
    """Create a new backtest strategy."""
    async def _create() -> None:
        async with async_session_factory() as session:
            repo = BacktestStrategyRepository(session)
            config = json.loads(config_json)
            strategy = BacktestStrategyCreate(
                name=name,
                code=code,
                strategy_type=strategy_type,
                scanner_code=scanner_code,
                description=description,
                configuration_json=config,
                is_active=is_active,
                created_by_user_id=created_by_user_id,
            )
            created = await repo.add(strategy)
            await session.commit()
            console.print(f"Created strategy: {created.id} - {created.name}")

    asyncio.run(_create())

@app.command()
def update_strategy(
    strategy_id: Annotated[int, typer.Option(help="Strategy ID")],
    name: Annotated[str | None, typer.Option(help="Strategy name")] = None,
    description: Annotated[str | None, typer.Option(help="Description")] = None,
    strategy_type: Annotated[str | None, typer.Option(help="Strategy type")] = None,
    scanner_code: Annotated[str | None, typer.Option(help="Scanner code")] = None,
    config_json: Annotated[str, typer.Option(help="Configuration JSON")] = "{}",
    is_active: Annotated[bool | None, typer.Option(help="Is active")] = None,
) -> None:
    """Update an existing backtest strategy."""
    async def _update() -> None:
        async with async_session_factory() as session:
            repo = BacktestStrategyRepository(session)
            config = json.loads(config_json)
            update = BacktestStrategyUpdate(
                name=name,
                description=description,
                strategy_type=strategy_type,
                scanner_code=scanner_code,
                configuration_json=config,
                is_active=is_active,
            )
            updated = await repo.update(strategy_id, update)
            if updated is None:
                console.print(f"Strategy {strategy_id} not found", style="red")
                return
            await session.commit()
            console.print(f"Updated strategy: {updated.id} - {updated.name}")

    asyncio.run(_update())

@app.command()
def list_strategies(
    strategy_type: Annotated[str | None, typer.Option(help="Filter by strategy type")] = None,
    is_active: Annotated[bool | None, typer.Option(help="Filter by active status")] = None,
    limit: Annotated[int, typer.Option(help="Limit")] = 100,
) -> None:
    """List backtest strategies."""
    async def _list() -> None:
        async with async_session_factory() as session:
            repo = BacktestStrategyRepository(session)
            strategies = await repo.list(
                strategy_type=strategy_type, is_active=is_active, limit=limit
            )
            table = Table(title="Backtest Strategies")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Code", style="blue")
            table.add_column("Type", style="magenta")
            table.add_column("Active", style="yellow")
            table.add_column("Created", style="dim")
            for s in strategies:
                table.add_row(
                    str(s.id),
                    s.name,
                    s.code,
                    s.strategy_type,
                    str(s.is_active),
                    s.created_at.strftime("%Y-%m-%d"),
                )
            console.print(table)

    asyncio.run(_list())

# ------------------------------------------------------------------ runs

@app.command()
def create_run(
    strategy_id: Annotated[int, typer.Option(help="Strategy ID")],
    start_date: Annotated[str, typer.Option(help="Start date (YYYY-MM-DD)")],
    end_date: Annotated[str, typer.Option(help="End date (YYYY-MM-DD)")],
    initial_capital: Annotated[float, typer.Option(help="Initial capital")] = 100000.0,
    commission_rate: Annotated[float, typer.Option(help="Commission rate")] = 0.001,
    slippage_rate: Annotated[float, typer.Option(help="Slippage rate")] = 0.001,
    position_size_type: Annotated[str, typer.Option(help="Position size type")] = "percent_capital",
    position_size_value: Annotated[float, typer.Option(help="Position size value")] = 10.0,
    benchmark_symbol: Annotated[str | None, typer.Option(help="Benchmark symbol")] = None,
    max_positions: Annotated[int | None, typer.Option(help="Max positions")] = None,
    requested_by_user_id: Annotated[int | None, typer.Option(help="User ID")] = None,
) -> None:
    """Create a new backtest run."""
    async def _create() -> None:
        async with async_session_factory() as session:
            repo = BacktestRunRepository(session)
            run = BacktestRunCreate(
                strategy_id=strategy_id,
                requested_by_user_id=requested_by_user_id,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date),
                initial_capital=Decimal(str(initial_capital)),
                commission_rate=Decimal(str(commission_rate)),
                slippage_rate=Decimal(str(slippage_rate)),
                position_size_type=position_size_type,
                position_size_value=Decimal(str(position_size_value)),
                benchmark_symbol=benchmark_symbol,
                max_positions=max_positions,
            )
            created = await repo.create_run(**run.model_dump())
            await session.commit()
            console.print(f"Created run: {created.id} - {created.status}")

    asyncio.run(_create())

@app.command()
def list_runs(
    strategy_id: Annotated[int | None, typer.Option(help="Filter by strategy ID")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
    limit: Annotated[int, typer.Option(help="Limit")] = 100,
) -> None:
    """List backtest runs."""
    async def _list() -> None:
        async with async_session_factory() as session:
            repo = BacktestRunRepository(session)
            runs = await repo.list_runs(strategy_id=strategy_id, status=status, limit=limit)
            table = Table(title="Backtest Runs")
            table.add_column("ID", style="cyan")
            table.add_column("Strategy", style="green")
            table.add_column("Status", style="blue")
            table.add_column("Start", style="magenta")
            table.add_column("End", style="yellow")
            table.add_column("Capital", style="dim")
            table.add_column("Duration", style="dim")
            for r in runs:
                strategy = r.strategy.code if r.strategy else "N/A"
                duration = (
                    f"{r.duration_ms:.0f}ms"
                    if r.duration_ms is not None
                    else "N/A"
                )
                table.add_row(
                    str(r.id),
                    strategy,
                    r.status,
                    r.start_date.strftime("%Y-%m-%d"),
                    r.end_date.strftime("%Y-%m-%d"),
                    f"{r.initial_capital:,.2f}",
                    duration,
                )
            console.print(table)

    asyncio.run(_list())

# ------------------------------------------------------------------ simulation

@app.command()
def simulate(
    run_id: Annotated[int, typer.Option(help="Run ID")],
) -> None:
    """Simulate a backtest run and compute its performance metrics."""
    async def _simulate() -> None:
        async with async_session_factory() as session:
            simulator = BacktestSimulator(session)
            calculator = BacktestMetricCalculator(session)
            try:
                summary = await simulator.simulate_run(run_id)
                console.print(f"Simulation completed for run {run_id}", style="green")
                console.print(json.dumps(summary.as_dict(), indent=2))
            except Exception as exc:
                console.print(f"Simulation failed: {exc}", style="red")
                raise typer.Exit(1) from exc

            await _compute_and_print_metrics(run_id, calculator)

    asyncio.run(_simulate())

@app.command()
def compute_metrics(
    run_id: Annotated[int, typer.Option(help="Run ID")],
) -> None:
    """Compute metrics for a completed backtest run."""
    async def _compute() -> None:
        async with async_session_factory() as session:
            calculator = BacktestMetricCalculator(session)
            await _compute_and_print_metrics(run_id, calculator)

    asyncio.run(_compute())

@app.command()
def simulate_and_metrics(
    run_id: Annotated[int, typer.Option(help="Run ID")],
) -> None:
    """Simulate a run and compute metrics in one command."""
    async def _run() -> None:
        async with async_session_factory() as session:
            simulator = BacktestSimulator(session)
            calculator = BacktestMetricCalculator(session)
            try:
                console.print(f"Simulating run {run_id}...", style="blue")
                summary = await simulator.simulate_run(run_id)
                console.print(f"Simulation completed for run {run_id}", style="green")
                console.print(json.dumps(summary.as_dict(), indent=2))
            except Exception as exc:
                console.print(f"Simulation failed: {exc}", style="red")
                raise typer.Exit(1) from exc

            console.print(f"Computing metrics for run {run_id}...", style="blue")
            await _compute_and_print_metrics(run_id, calculator)

    asyncio.run(_run())


def main() -> None:
    app()


if __name__ == "__main__":
    main()