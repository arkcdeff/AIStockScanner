from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.db.session import get_session_factory
from app.services.nse_downloader import NSEDownloadError, NSEDownloader
from app.services.nse_import import NSEImportService


async def _import_file(file_path: str) -> int:
    async with get_session_factory()() as session:
        job = await NSEImportService(session).import_files(file_path)
    print(
        f"status={job.status} read={job.rows_read} inserted={job.rows_inserted} "
        f"updated={job.rows_updated} rejected={job.rows_rejected}"
    )
    if job.error_message:
        print(f"error={job.error_message}")
    return 0 if job.status == "completed" else 1


async def _download_and_import(trade_date: str | None = None, latest: bool = False) -> int:
    target_date: datetime | None = None
    raw_path: Path | None = None

    try:
        with NSEDownloader() as downloader:
            if latest:
                trade_date_val, raw_path, _ = downloader.download_latest()
                target_date = datetime.combine(trade_date_val, datetime.min.time())
            elif trade_date:
                from datetime import date

                trade_date_val = date.fromisoformat(trade_date)
                raw_path, _ = downloader.download(trade_date_val)
                target_date = datetime.combine(trade_date_val, datetime.min.time())
            else:
                raise ValueError("Either --date or --latest must be provided")

        async with get_session_factory()() as session:
            job = await NSEImportService(session).import_files(raw_path)
        print(
            f"status={job.status} date={target_date.date()} read={job.rows_read} "
            f"inserted={job.rows_inserted} updated={job.rows_updated} rejected={job.rows_rejected}"
        )
        if job.error_message:
            print(f"error={job.error_message}")
        return 0 if job.status == "completed" else 1
    except NSEDownloadError as exc:
        print(f"download_error={exc}")
        return 1
    except ValueError as exc:
        print(f"error={exc}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="aistock-import-nse")
    subparsers = parser.add_subparsers(dest="command", required=True)

    file_parser = subparsers.add_parser("file", help="Import local NSE bhavcopy CSV/ZIP")
    file_parser.add_argument("path", help="Path to bhavcopy CSV or ZIP")

    date_parser = subparsers.add_parser("date", help="Download and import NSE bhavcopy for a date")
    date_parser.add_argument("--date", required=True, help="Trade date in YYYY-MM-DD format")

    subparsers.add_parser("latest", help="Download and import latest available NSE bhavcopy")

    args = parser.parse_args()

    if args.command == "file":
        raise SystemExit(asyncio.run(_import_file(args.path)))
    elif args.command == "date":
        raise SystemExit(asyncio.run(_download_and_import(trade_date=args.date)))
    elif args.command == "latest":
        raise SystemExit(asyncio.run(_download_and_import(latest=True)))


if __name__ == "__main__":
    main()
