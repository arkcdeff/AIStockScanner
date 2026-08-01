from __future__ import annotations

import argparse
import asyncio

from app.db.session import get_session_factory
from app.services.nse_import import NSEImportService


async def _import_nse(bhavcopy: str, delivery: str | None) -> int:
    async with get_session_factory()() as session:
        job = await NSEImportService(session).import_files(bhavcopy, delivery)
    print(
        f"status={job.status} read={job.rows_read} inserted={job.rows_inserted} "
        f"updated={job.rows_updated} rejected={job.rows_rejected}"
    )
    if job.error_message:
        print(f"error={job.error_message}")
    return 0 if job.status == "completed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="aistock")
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import-nse", help="Import NSE bhavcopy and delivery CSV/ZIP")
    importer.add_argument("bhavcopy")
    importer.add_argument("--delivery")
    args = parser.parse_args()

    if args.command == "import-nse":
        raise SystemExit(asyncio.run(_import_nse(args.bhavcopy, args.delivery)))


if __name__ == "__main__":
    main()
