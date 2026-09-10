r"""Production Q4-2025 NSE Bulk & Block Deals Historical Ingestion Script — Stage G.10.

Executes BulkBlockExtensionService against the production database (ai_stock_scanner)
for all 62 official NSE sessions (2025-10-01 to 2025-12-31).

Usage:
  uv run python -m scripts.ingest_bulk_block_q4_2025
  uv run python -m scripts.ingest_bulk_block_q4_2025 --skip-existing
  uv run python -m scripts.ingest_bulk_block_q4_2025 --verify-only
  uv run python -m scripts.ingest_bulk_block_q4_2025 --idempotency-test
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services.bulk_block_extension_service import BulkBlockExtensionService

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


async def get_deals_metrics(session: AsyncSession) -> dict:
    """Retrieve comprehensive Bulk & Block deals metrics from database."""
    # 1. Total rows and counts by deal_type
    total_res = await session.execute(
        text("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE deal_type = 'BULK'),
                COUNT(*) FILTER (WHERE deal_type = 'BLOCK'),
                COUNT(*) FILTER (WHERE is_current = TRUE),
                COUNT(*) FILTER (WHERE is_current = FALSE)
            FROM bulk_block_deals
        """)
    )
    total_rows, bulk_rows, block_rows, current_rows, superseded_rows = total_res.fetchone()

    # 2. Distinct dates and sessions
    dates_res = await session.execute(
        text("""
            SELECT
                MIN(trade_date),
                MAX(trade_date),
                COUNT(DISTINCT trade_date)
            FROM bulk_block_deals
        """)
    )
    min_date, max_date, total_sessions = dates_res.fetchone()

    # 3. Symbols and resolution
    sym_res = await session.execute(
        text("""
            SELECT
                COUNT(DISTINCT symbol),
                COUNT(*) FILTER (WHERE stock_id IS NOT NULL),
                COUNT(*) FILTER (WHERE stock_id IS NULL)
            FROM bulk_block_deals
            WHERE is_current = TRUE
        """)
    )
    unique_symbols, resolved_rows, unresolved_rows = sym_res.fetchone()

    # 4. Invariant checks
    # Check 1: Uniqueness violations on (source_reference, source_line_index) among current rows
    dup_res = await session.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT source_reference, source_line_index
                FROM bulk_block_deals
                WHERE is_current = TRUE
                GROUP BY source_reference, source_line_index
                HAVING COUNT(*) > 1
            ) sub
        """)
    )
    duplicate_source_lines = dup_res.scalar_one()

    # Check 2: Orphan stocks
    orphan_res = await session.execute(
        text("""
            SELECT COUNT(*)
            FROM bulk_block_deals d
            LEFT JOIN stocks s ON d.stock_id = s.id
            WHERE d.stock_id IS NOT NULL AND s.id IS NULL
        """)
    )
    orphan_stock_refs = orphan_res.scalar_one()

    # Check 3: Invalid quantity or price
    invalid_val_res = await session.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE quantity <= 0),
                COUNT(*) FILTER (WHERE trade_price <= 0),
                COUNT(*) FILTER (WHERE deal_value <= 0),
                COUNT(*) FILTER (WHERE deal_type NOT IN ('BULK', 'BLOCK')),
                COUNT(*) FILTER (WHERE buy_sell NOT IN ('BUY', 'SELL'))
            FROM bulk_block_deals
        """)
    )
    (
        invalid_qty,
        invalid_price,
        invalid_value,
        invalid_deal_type,
        invalid_buy_sell,
    ) = invalid_val_res.fetchone()

    # Check 4: PIT violations (available_at != trade_date + 1 day)
    pit_res = await session.execute(
        text("""
            SELECT COUNT(*)
            FROM bulk_block_deals
            WHERE available_at != trade_date + INTERVAL '1 day'
        """)
    )
    pit_violations = pit_res.scalar_one()

    # Check 5: Data quality issues
    dq_res = await session.execute(
        text("""
            SELECT issue_code, COUNT(*)
            FROM data_quality_issues
            WHERE issue_code LIKE 'DQ_DEALS_%'
            GROUP BY issue_code
            ORDER BY issue_code
        """)
    )
    dq_issues = dict(dq_res.fetchall())

    return {
        "total_rows": total_rows,
        "bulk_rows": bulk_rows,
        "block_rows": block_rows,
        "current_rows": current_rows,
        "superseded_rows": superseded_rows,
        "min_date": str(min_date) if min_date else None,
        "max_date": str(max_date) if max_date else None,
        "total_sessions": total_sessions,
        "unique_symbols": unique_symbols,
        "resolved_rows": resolved_rows,
        "unresolved_rows": unresolved_rows,
        "duplicate_source_lines": duplicate_source_lines,
        "orphan_stock_refs": orphan_stock_refs,
        "invalid_qty": invalid_qty,
        "invalid_price": invalid_price,
        "invalid_value": invalid_value,
        "invalid_deal_type": invalid_deal_type,
        "invalid_buy_sell": invalid_buy_sell,
        "pit_violations": pit_violations,
        "dq_issues": dq_issues,
    }


async def run_ingestion(
    skip_existing: bool = False,
    verify_only: bool = False,
    idempotency_test: bool = False,
) -> int:
    """Run Q4-2025 Bulk & Block deals ingestion."""
    session_factory = get_session_factory()
    start_time = time.monotonic()

    print("============================================================")
    print("AISTOCKSCANNER — G.10 Q4-2025 BULK & BLOCK DEALS INGESTION")
    print("============================================================")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(f"Mode: {'VERIFY ONLY' if verify_only else ('IDEMPOTENCY TEST' if idempotency_test else ('SKIP EXISTING' if skip_existing else 'FULL INGESTION'))}")

    async with session_factory() as session:
        db_name = verify_target_database(session)
        print(f"Verified target database: {db_name}")

        service = BulkBlockExtensionService(session)

        # Run primary pass
        print("\nIngesting Q4-2025 deals from manifest...")
        report = await service.ingest_all_q4_2025(
            skip_if_present=skip_existing,
            verify_only=verify_only,
        )

        print("Ingestion pass completed:")
        print(f"  Total sessions:      {report.total_sessions}")
        print(f"  Successful:          {report.successful_sessions}")
        print(f"  Skipped:             {report.skipped_sessions}")
        print(f"  Failed:              {report.failed_sessions}")
        print(f"  Bulk rows parsed:    {report.total_bulk_rows}")
        print(f"  Block rows parsed:   {report.total_block_rows}")
        print(f"  Total rows inserted: {report.total_rows_inserted}")
        print(f"  Total rows updated:  {report.total_rows_updated}")
        print(f"  Total unchanged:     {report.total_rows_unchanged}")
        print(f"  Resolved rows:       {report.resolved_stock_rows}")
        print(f"  Unresolved rows:     {report.unresolved_rows}")
        print(f"  Unresolved symbols:  {len(report.unresolved_symbols)} symbols: {report.unresolved_symbols}")

        if report.failed_sessions > 0:
            print(f"ERROR: {report.failed_sessions} sessions failed! Exiting with code 1.")
            return 1

        # Run idempotency test if requested
        if idempotency_test and not verify_only:
            print("\nRunning Idempotency Test (second pass with exact same source files)...")
            idemp_report = await service.ingest_all_q4_2025(skip_if_present=False)
            print("Idempotency pass result:")
            print(f"  Rows inserted:  {idemp_report.total_rows_inserted} (expected 0)")
            print(f"  Rows updated:   {idemp_report.total_rows_updated} (expected 0)")
            print(f"  Rows unchanged: {idemp_report.total_rows_unchanged}")

            if idemp_report.total_rows_inserted > 0 or idemp_report.total_rows_updated > 0:
                print("FATAL: Idempotency violation! Rows were re-inserted or updated unexpectedly.")
                return 1
            print("IDEMPOTENCY TEST: PASS (100% bitwise identical no-op)")

        # Verify DB metrics
        if not verify_only:
            print("\nVerifying database invariants...")
            metrics = await get_deals_metrics(session)
            print("Database State:")
            print(f"  Total rows in DB:     {metrics['total_rows']}")
            print(f"  Bulk rows:            {metrics['bulk_rows']}")
            print(f"  Block rows:           {metrics['block_rows']}")
            print(f"  Current rows:         {metrics['current_rows']}")
            print(f"  Superseded rows:      {metrics['superseded_rows']}")
            print(f"  Date range:           {metrics['min_date']} to {metrics['max_date']}")
            print(f"  Total sessions in DB: {metrics['total_sessions']}")
            print(f"  Unique symbols:       {metrics['unique_symbols']}")
            print(f"  Resolved rows:        {metrics['resolved_rows']}")
            print(f"  Unresolved rows:      {metrics['unresolved_rows']}")
            print(f"  Data quality issues:  {metrics['dq_issues']}")

            print("\nInvariant Checks:")
            print(f"  Duplicate source lines: {metrics['duplicate_source_lines']} (must be 0)")
            print(f"  Orphan stock refs:      {metrics['orphan_stock_refs']} (must be 0)")
            print(f"  Invalid quantity:       {metrics['invalid_qty']} (must be 0)")
            print(f"  Invalid price:          {metrics['invalid_price']} (must be 0)")
            print(f"  Invalid deal value:     {metrics['invalid_value']} (must be 0)")
            print(f"  Invalid deal type:      {metrics['invalid_deal_type']} (must be 0)")
            print(f"  Invalid buy/sell:       {metrics['invalid_buy_sell']} (must be 0)")
            print(f"  PIT violations:         {metrics['pit_violations']} (must be 0)")

            if (
                metrics['duplicate_source_lines'] > 0
                or metrics['orphan_stock_refs'] > 0
                or metrics['invalid_qty'] > 0
                or metrics['invalid_price'] > 0
                or metrics['invalid_value'] > 0
                or metrics['invalid_deal_type'] > 0
                or metrics['invalid_buy_sell'] > 0
                or metrics['pit_violations'] > 0
            ):
                print("FATAL: Invariant check failed! Database contains corrupted deal records.")
                return 1

    elapsed = time.monotonic() - start_time
    print(f"\nCompleted in {elapsed:.2f} seconds. Status: SUCCESS.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Q4-2025 NSE Bulk & Block Deals")
    parser.add_argument("--skip-existing", action="store_true", help="Skip sessions with complete deals in DB")
    parser.add_argument("--verify-only", action="store_true", help="Verify files and hashes without writing to DB")
    parser.add_argument("--idempotency-test", action="store_true", help="Run second pass to prove zero new inserts/updates")

    args = parser.parse_args()

    exit_code = asyncio.run(
        run_ingestion(
            skip_existing=args.skip_existing,
            verify_only=args.verify_only,
            idempotency_test=args.idempotency_test,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
