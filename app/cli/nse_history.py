"""CLI for historical NSE EOD data acquisition."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Annotated

import typer
from rich.console import Console

from app.db.session import get_session_factory as async_session_factory
from app.services.nse_historical import NSEHistoricalService

app = typer.Typer()
console = Console()

@app.command()
def fetch_date(
    trade_date: Annotated[str, typer.Option(help="Trade date in YYYY-MM-DD format")],
) -> None:
    """Fetch and import NSE data for a specific date."""
    async def _fetch() -> None:
        async with async_session_factory() as session:
            result = await NSEHistoricalService(session).fetch_date(
                date.fromisoformat(trade_date)
            )
        console.print(
            f"date={result.trade_date} status={result.status} message={result.message}"
        )
        if result.raw_path:
            console.print(f"raw_path={result.raw_path}")
        raise typer.Exit(0 if result.status in {"imported", "already_present"} else 1)

    try:
        asyncio.run(_fetch())
    except ValueError as exc:
        console.print(f"error={exc}", style="red")
        raise typer.Exit(1) from None

@app.command()
def fetch_range(
    from_date: Annotated[str, typer.Option(help="Start date in YYYY-MM-DD format")],
    to_date: Annotated[str, typer.Option(help="End date in YYYY-MM-DD format")],
) -> None:
    """Fetch and import NSE data for a date range."""
    async def _fetch() -> None:
        async with async_session_factory() as session:
            start = date.fromisoformat(from_date)
            end = date.fromisoformat(to_date)
            summary = await NSEHistoricalService(session).fetch_range(start, end)
        console.print(json.dumps(summary.as_dict(), indent=2))
        raise typer.Exit(0 if summary.failed == 0 else 1)

    try:
        asyncio.run(_fetch())
    except ValueError as exc:
        console.print(f"error={exc}", style="red")
        raise typer.Exit(1) from None


def main() -> None:
    app()


if __name__ == "__main__":
    main()