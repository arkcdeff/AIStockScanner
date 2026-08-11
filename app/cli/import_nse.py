from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from app.db.session import get_session_factory as async_session_factory
from app.services.nse_downloader import NSEDownloader, NSEDownloadError
from app.services.nse_import import NSEImportService

app = typer.Typer()
console = Console()

@app.command()
def file(
    path: Annotated[str, typer.Argument(help="Path to bhavcopy CSV or ZIP")],
) -> None:
    """Import local NSE bhavcopy CSV/ZIP."""
    async def _import_file() -> None:
        async with async_session_factory() as session:
            job = await NSEImportService(session).import_files(path)
        console.print(
            f"status={job.status} read={job.rows_read} inserted={job.rows_inserted} "
            f"updated={job.rows_updated} rejected={job.rows_rejected}"
        )
        if job.error_message:
            console.print(f"error={job.error_message}", style="red")
        raise typer.Exit(0 if job.status == "completed" else 1)

    asyncio.run(_import_file())

@app.command()
def date(
    trade_date: Annotated[str, typer.Option(help="Trade date in YYYY-MM-DD format")],
) -> None:
    """Download and import NSE bhavcopy for a date."""
    async def _download_and_import() -> None:
        target_date: datetime | None = None
        raw_path: Path | None = None

        try:
            from datetime import date

            trade_date_val = date.fromisoformat(trade_date)
            target_date = datetime.combine(trade_date_val, datetime.min.time())

            with NSEDownloader() as downloader:
                raw_path, _ = downloader.download(trade_date_val)

            async with async_session_factory() as session:
                job = await NSEImportService(session).import_files(raw_path)
            console.print(
                f"status={job.status} date={target_date.date()} read={job.rows_read} "
                f"inserted={job.rows_inserted} updated={job.rows_updated} "
                f"rejected={job.rows_rejected}"
            )
            if job.error_message:
                console.print(f"error={job.error_message}", style="red")
            raise typer.Exit(0 if job.status == "completed" else 1)
        except NSEDownloadError as exc:
            console.print(f"download_error={exc}", style="red")
            raise typer.Exit(1) from exc
        except ValueError as exc:
            console.print(f"error={exc}", style="red")
            raise typer.Exit(1) from exc

    asyncio.run(_download_and_import())

@app.command()
def latest() -> None:
    """Download and import latest available NSE bhavcopy."""
    async def _download_and_import() -> None:
        target_date: datetime | None = None
        raw_path: Path | None = None

        try:
            with NSEDownloader() as downloader:
                trade_date_val, raw_path, _ = downloader.download_latest()
                target_date = datetime.combine(trade_date_val, datetime.min.time())

            async with async_session_factory() as session:
                job = await NSEImportService(session).import_files(raw_path)
            console.print(
                f"status={job.status} date={target_date.date()} read={job.rows_read} "
                f"inserted={job.rows_inserted} updated={job.rows_updated} "
                f"rejected={job.rows_rejected}"
            )
            if job.error_message:
                console.print(f"error={job.error_message}", style="red")
            raise typer.Exit(0 if job.status == "completed" else 1)
        except NSEDownloadError as exc:
            console.print(f"download_error={exc}", style="red")
            raise typer.Exit(1) from exc

    asyncio.run(_download_and_import())


def main() -> None:
    app()


if __name__ == "__main__":
    main()