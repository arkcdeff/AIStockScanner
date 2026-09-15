#!/usr/bin/env python
"""CLI tool to safely calculate technical indicators for the eligible broad-market universe."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.db.session import get_session_factory
from app.services.indicator_batch_service import IndicatorBatchService


async def main_async(args: argparse.Namespace) -> int:
    target_date: date | None = None
    if args.as_of_date:
        target_date = date.fromisoformat(args.as_of_date)

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    factory = get_session_factory()
    async with factory() as session:
        service = IndicatorBatchService(session)

        def progress_cb(current: int, total: int, sym: str) -> None:
            pct = (current / total) * 100.0
            print(f"Progress: [{current}/{total}] ({pct:.1f}%) — Processing {sym}...")

        summary = await service.run_batch(
            as_of_date=target_date,
            dry_run=args.dry_run,
            min_sessions=args.min_sessions,
            limit=args.limit,
            symbols=symbols,
            progress_callback=progress_cb if not args.quiet else None,
        )

        print("\n" + "=" * 60)
        print("BROAD-MARKET INDICATOR COMPUTATION SUMMARY")
        print("=" * 60)
        print(f"As-of Date:                   {summary.as_of_date}")
        print(f"Dry Run:                      {summary.dry_run}")
        print(f"Total Eligible Stocks:        {summary.eligible_count}")
        print(f"Total Excluded Stocks:        {summary.excluded_count}")
        print(f"Stocks Processed:             {summary.processed_count}")
        print(f"Stocks Succeeded:             {summary.successful_count}")
        print(f"Stocks Failed:                {summary.failed_count}")
        print(f"New Rows Inserted:            {summary.inserted_rows}")
        print(f"Existing Rows Updated:        {summary.updated_rows}")
        print(f"Existing Rows Unchanged:      {summary.unchanged_rows}")
        print(f"Total Indicator Rows:         {summary.total_indicator_rows_produced}")
        print(f"Total Price Rows Read:        {summary.total_daily_prices_read}")
        print(f"Elapsed Time:                 {summary.elapsed_seconds:.2f}s")
        print(f"Average Time / Stock:         {summary.avg_seconds_per_stock:.4f}s")

        if summary.failures:
            print("\nFAILURES:")
            for f in summary.failures:
                print(f"  - {f['symbol']}: {f['reason']} (status: {f['status']})")

        print("=" * 60)
        return 0 if summary.failed_count == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute broad-market indicators for eligible cash equities."
    )
    parser.add_argument(
        "--as-of-date",
        type=str,
        default=None,
        help="Target as-of date (YYYY-MM-DD). Defaults to latest production weekday date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run without inserting or updating database records.",
    )
    parser.add_argument(
        "--min-sessions",
        type=int,
        default=200,
        help="Minimum required weekday sessions for eligibility (default: 200).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit calculation to the first N eligible stocks.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated list of symbols to process (e.g. RELIANCE,TCS).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress intermediate progress logging.",
    )

    args = parser.parse_args()
    ret = asyncio.run(main_async(args))
    raise SystemExit(ret)


if __name__ == "__main__":
    main()
