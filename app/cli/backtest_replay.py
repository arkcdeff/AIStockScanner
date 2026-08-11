"""CLI for historical scanner replay."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Annotated

import typer
from rich.console import Console

from app.db.session import get_session_factory as async_session_factory
from app.services.backtest_replay import BacktestReplayService

app = typer.Typer()
console = Console()

@app.command()
def replay(
    scanner_code: Annotated[
        str | None, typer.Option(help="Scanner code; omit to replay all")
    ] = None,
    from_date: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = ...,
    to_date: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = ...,
    symbols: Annotated[list[str] | None, typer.Option(help="Optional symbol filter")] = None,
) -> None:
    """Replay scanner signals for a date range."""
    async def _replay() -> None:
        async with async_session_factory() as session:
            service = BacktestReplayService(session)
            start_date = date.fromisoformat(from_date)
            end_date = date.fromisoformat(to_date)

            if scanner_code:
                summary = await service.replay_scanner(
                    scanner_code, start_date, end_date, symbols=symbols
                )
            else:
                summary = await service.replay_all(start_date, end_date, symbols=symbols)

            console.print(json.dumps(summary.as_dict(), indent=2))
            for signal in summary.signals:
                console.print(
                    f"signal date={signal.trade_date} symbol={signal.symbol} "
                    f"scanner={signal.scanner_code} score={signal.score} reason={signal.reason}"
                )

    try:
        asyncio.run(_replay())
    except ValueError as exc:
        console.print(f"error={exc}", style="red")
        raise typer.Exit(1) from None


def main() -> None:
    app()


if __name__ == "__main__":
    main()