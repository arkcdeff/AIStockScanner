r"""Production Q4-2025 NSE Participant OI Historical Ingestion Script — Stage G.9.

Executes NseParticipantHistoricalExtensionService.run_manifest() against the production
database (ai_stock_scanner) for all 62 official NSE sessions (2025-10-01 to 2025-12-31).

Usage:
  .venv\Scripts\python -m scripts.ingest_participant_q4_2025
  .venv\Scripts\python -m scripts.ingest_participant_q4_2025 --skip-existing
  .venv\Scripts\python -m scripts.ingest_participant_q4_2025 --verify-only
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services.nse_participant_historical_extension_service import (
    NseParticipantHistoricalExtensionService,
)

FORBIDDEN_DB_NAMES = {"postgres", "production", "prod", "master"}
REQUIRED_DB_NAME = "ai_stock_scanner"


def verify_target_database(session: AsyncSession) -> str:
    """Verify target database is strictly ai_stock_scanner and not a test DB."""
    bind = session.get_bind()
    db_name = bind.url.database if bind and hasattr(bind, "url") and bind.url else "unknown"

    if "test" in db_name.lower():
        raise RuntimeError(f"FATAL: Connected to test database '{db_name}'. Refusing production ingestion.")
    if db_name in FORBIDDEN_DB_NAMES:
        raise RuntimeError(f"FATAL: Connected to forbidden database '{db_name}'. Refusing production ingestion.")
    if db_name != REQUIRED_DB_NAME:
        raise RuntimeError(f"FATAL: Connected to '{db_name}', expected '{REQUIRED_DB_NAME}'. Refusing production ingestion.")

    return db_name


async def get_participant_metrics(session: AsyncSession) -> dict:
    """Retrieve comprehensive participant metrics from database."""
    # 1. Total participant rows
    total_rows = (await session.execute(text("SELECT COUNT(*) FROM derivative_participant_positions"))).scalar() or 0

    # 2. Distinct report dates
    dates_res = await session.execute(
        text("SELECT MIN(report_date), MAX(report_date), COUNT(DISTINCT report_date) FROM derivative_participant_positions")
    )
    min_date, max_date, total_sessions = dates_res.fetchone()

    # 3. Q4-2025 metrics
    q4_res = await session.execute(
        text(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT report_date),
                MIN(report_date),
                MAX(report_date)
            FROM derivative_participant_positions
            WHERE report_date >= '2025-10-01' AND report_date <= '2025-12-31'
            """
        )
    )
    q4_rows, q4_sessions, q4_min, q4_max = q4_res.fetchone()

    # 4. Q4 categories and buckets
    q4_cats_res = await session.execute(
        text(
            """
            SELECT DISTINCT participant_type
            FROM derivative_participant_positions
            WHERE report_date >= '2025-10-01' AND report_date <= '2025-12-31'
            ORDER BY participant_type
            """
        )
    )
    q4_categories = [r[0] for r in q4_cats_res.fetchall()]

    q4_buckets_res = await session.execute(
        text(
            """
            SELECT DISTINCT instrument_bucket
            FROM derivative_participant_positions
            WHERE report_date >= '2025-10-01' AND report_date <= '2025-12-31'
            ORDER BY instrument_bucket
            """
        )
    )
    q4_buckets = [r[0] for r in q4_buckets_res.fetchall()]

    # 5. PIT publication check (available_at == report_date)
    pit_res = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM derivative_participant_positions
            WHERE report_date >= '2025-10-01' AND report_date <= '2025-12-31'
              AND available_at != report_date
            """
        )
    )
    pit_violations = pit_res.scalar() or 0

    # 6. Non-negative check
    neg_res = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM derivative_participant_positions
            WHERE report_date >= '2025-10-01' AND report_date <= '2025-12-31'
              AND (long_contracts < 0 OR short_contracts < 0)
            """
        )
    )
    negative_counts = neg_res.scalar() or 0

    return {
        "total_rows": total_rows,
        "total_sessions": total_sessions or 0,
        "min_date": min_date,
        "max_date": max_date,
        "q4_rows": q4_rows or 0,
        "q4_sessions": q4_sessions or 0,
        "q4_min": q4_min,
        "q4_max": q4_max,
        "q4_categories": q4_categories,
        "q4_buckets": q4_buckets,
        "pit_violations": pit_violations,
        "negative_counts": negative_counts,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Q4-2025 NSE Participant Historical Open Interest Data")
    parser.add_argument("--skip-existing", action="store_true", help="Skip sessions that already have 24 positions in the DB")
    parser.add_argument("--verify-only", action="store_true", help="Only verify database metrics without ingesting")
    parser.add_argument("--manifest", default="data/reference/participant/manifest_q4_2025.json", help="Path to manifest file")
    args = parser.parse_args()

    print("============================================================")
    print("STAGE G.9: PRODUCTION PARTICIPANT OI INGESTION")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print("============================================================")

    factory = get_session_factory()

    async with factory() as session:
        db_name = verify_target_database(session)
        print(f"Target Database Verified: {db_name}")

        print("\n--- Current Database Metrics Before Ingestion ---")
        metrics_before = await get_participant_metrics(session)
        print(f"Total Participant Rows: {metrics_before['total_rows']:,}")
        print(f"Total Distinct Sessions: {metrics_before['total_sessions']}")
        print(f"Date Span: {metrics_before['min_date']} -> {metrics_before['max_date']}")
        print(f"Q4-2025 Sessions Present: {metrics_before['q4_sessions']}")
        print(f"Q4-2025 Rows Present: {metrics_before['q4_rows']:,}")

        if args.verify_only:
            print("\n--verify-only specified. Skipping ingestion.")
            return

        print("\n============================================================")
        print(f"Starting Ingestion via Manifest: {args.manifest}")
        print(f"Skip Existing: {args.skip_existing}")
        print("============================================================")

        service = NseParticipantHistoricalExtensionService(
            session=session,
            cache_dir=Path("data/reference/participant/raw"),
        )

        start_time = time.time()
        report = await service.run_manifest(
            manifest_path=args.manifest,
            skip_existing=args.skip_existing,
        )
        elapsed = time.time() - start_time

        print("\n============================================================")
        print("INGESTION BATCH REPORT")
        print("============================================================")
        print(f"Total Sessions Processed: {report.total_sessions}")
        print(f"Successful Sessions:      {report.successful_sessions}")
        print(f"Skipped Sessions:         {report.skipped_sessions}")
        print(f"Failed Sessions:          {report.failed_sessions}")
        print(f"Total Rows Parsed:        {report.total_rows_parsed:,}")
        print(f"Total Rows Inserted:      {report.total_rows_inserted:,}")
        print(f"Total Rows Updated:       {report.total_rows_updated:,}")
        print(f"Total Rows Unchanged:     {report.total_rows_unchanged:,}")
        print(f"Perfect Balance Sessions: {report.perfect_balance_sessions}")
        print(f"Official Artifact Mismatch Sessions: {report.official_artifact_sessions}")
        print(f"Other Mismatch Sessions:  {report.other_mismatch_sessions}")
        print(f"Quality Warnings Logged:  {report.quality_warnings_count}")
        print(f"Elapsed Time:             {elapsed:.2f} seconds")

        if report.failed_sessions > 0:
            print("\nERRORS DETECTED:")
            for s in report.session_results:
                if s.status == "FAILED":
                    print(f"  {s.report_date} ({s.filename}): {s.error_message}")
            sys.exit(1)

        print("\n--- Database Metrics After Ingestion ---")
        metrics_after = await get_participant_metrics(session)
        print(f"Total Participant Rows: {metrics_after['total_rows']:,} (Delta: +{metrics_after['total_rows'] - metrics_before['total_rows']:,})")
        print(f"Total Distinct Sessions: {metrics_after['total_sessions']} (Delta: +{metrics_after['total_sessions'] - metrics_before['total_sessions']})")
        print(f"Date Span: {metrics_after['min_date']} -> {metrics_after['max_date']}")
        print(f"Q4-2025 Sessions: {metrics_after['q4_sessions']}/62")
        print(f"Q4-2025 Rows: {metrics_after['q4_rows']:,}/1,488")
        print(f"Q4-2025 Categories: {metrics_after['q4_categories']}")
        print(f"Q4-2025 Buckets: {metrics_after['q4_buckets']}")
        print(f"PIT Violations: {metrics_after['pit_violations']}")
        print(f"Negative Contract Violations: {metrics_after['negative_counts']}")

        # Validate post-conditions
        assert metrics_after["q4_sessions"] == 62, f"Expected 62 Q4 sessions, got {metrics_after['q4_sessions']}"
        assert metrics_after["q4_rows"] == 1488, f"Expected 1,488 Q4 rows, got {metrics_after['q4_rows']}"
        assert metrics_after["pit_violations"] == 0, f"Found {metrics_after['pit_violations']} PIT violations"
        assert metrics_after["negative_counts"] == 0, f"Found {metrics_after['negative_counts']} negative contracts"
        print("\nAll Post-Ingestion Assertions PASSED successfully.")


if __name__ == "__main__":
    asyncio.run(main())
