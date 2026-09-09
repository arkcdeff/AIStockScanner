r"""Production Q4-2025 NSE F&O Historical Ingestion Script — Stage G.8.

Executes NseDerivativeHistoricalExtensionService.run_manifest() against the production
database (ai_stock_scanner) for all 62 official NSE sessions (2025-10-01 to 2025-12-31).

Usage:
  .venv\Scripts\python -m scripts.ingest_q4_2025_derivatives
  .venv\Scripts\python -m scripts.ingest_q4_2025_derivatives --skip-existing
  .venv\Scripts\python -m scripts.ingest_q4_2025_derivatives --verify-only
"""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services.nse_derivative_historical_extension_service import (
    NseDerivativeHistoricalExtensionService,
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


async def get_database_metrics(session: AsyncSession) -> dict:
    """Retrieve comprehensive derivative metrics from database."""
    # 1. Total contracts & daily prices
    c_count = (await session.execute(text("SELECT COUNT(*) FROM derivative_contracts"))).scalar() or 0
    p_count = (await session.execute(text("SELECT COUNT(*) FROM derivative_daily_prices"))).scalar() or 0

    # 2. Date ranges
    dates_res = await session.execute(
        text("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) FROM derivative_daily_prices")
    )
    min_date, max_date, total_sessions = dates_res.fetchone()

    # 3. Q4-2025 metrics
    q4_res = await session.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT trade_date) as q4_sessions,
                COUNT(*) as q4_daily_prices
            FROM derivative_daily_prices
            WHERE trade_date >= '2025-10-01' AND trade_date <= '2025-12-31'
            """
        )
    )
    q4_sessions, q4_prices = q4_res.fetchone()

    # 4. Contracts breakdown
    q4_c_res = await session.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT dc.id) as q4_contracts,
                COUNT(DISTINCT CASE WHEN dc.instrument_type = 'OPTION' THEN dc.id END) as q4_options,
                COUNT(DISTINCT CASE WHEN dc.instrument_type = 'FUTURE' THEN dc.id END) as q4_futures
            FROM derivative_contracts dc
            JOIN derivative_daily_prices ddp ON ddp.contract_id = dc.id
            WHERE ddp.trade_date >= '2025-10-01' AND ddp.trade_date <= '2025-12-31'
            """
        )
    )
    q4_contracts, q4_options, q4_futures = q4_c_res.fetchone()

    # 5. Data Quality checks
    dq_dups = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                    SELECT contract_id, trade_date FROM derivative_daily_prices
                    GROUP BY contract_id, trade_date HAVING COUNT(*) > 1
                ) s
                """
            )
        )
    ).scalar() or 0

    dq_orphans = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM derivative_daily_prices ddp
                LEFT JOIN derivative_contracts dc ON dc.id = ddp.contract_id
                WHERE dc.id IS NULL
                """
            )
        )
    ).scalar() or 0

    dq_weekends = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM derivative_daily_prices
                WHERE EXTRACT(DOW FROM trade_date) IN (0, 6)
                """
            )
        )
    ).scalar() or 0

    dq_gift_nifty = (
        await session.execute(
            text("SELECT COUNT(*) FROM derivative_contracts WHERE trading_symbol LIKE '%GIFT%'")
        )
    ).scalar() or 0

    dq_pit_violations = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM derivative_daily_prices
                WHERE available_at > trade_date
                """
            )
        )
    ).scalar() or 0

    dq_option_lot_not_1 = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM derivative_contracts
                WHERE instrument_type = 'OPTION' AND lot_size != 1
                """
            )
        )
    ).scalar() or 0

    dq_option_source_not_none = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM derivative_contracts
                WHERE instrument_type = 'OPTION' AND source_name IS NOT NULL
                """
            )
        )
    ).scalar() or 0

    return {
        "total_contracts": c_count,
        "total_daily_prices": p_count,
        "earliest_date": str(min_date) if min_date else "None",
        "latest_date": str(max_date) if max_date else "None",
        "total_distinct_sessions": total_sessions or 0,
        "q4_sessions": q4_sessions or 0,
        "q4_daily_prices": q4_prices or 0,
        "q4_contracts": q4_contracts or 0,
        "q4_options": q4_options or 0,
        "q4_futures": q4_futures or 0,
        "dup_contract_date": dq_dups,
        "orphan_ddp": dq_orphans,
        "weekend_ddp": dq_weekends,
        "gift_nifty_contracts": dq_gift_nifty,
        "pit_violations": dq_pit_violations,
        "option_lot_not_1": dq_option_lot_not_1,
        "option_source_not_none": dq_option_source_not_none,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Production Q4-2025 NSE F&O Ingestion")
    parser.add_argument("--skip-existing", action="store_true", help="Skip sessions that already have prices in DB")
    parser.add_argument("--verify-only", action="store_true", help="Run read-only database verification")
    args = parser.parse_args()

    session_factory = get_session_factory()

    async with session_factory() as session:
        # Step 1: Pre-Execution Target Verification
        db_name = verify_target_database(session)
        bind = session.get_bind()
        host = bind.url.host or "localhost"
        port = bind.url.port or 5432
        manifest_path = Path("data/reference/derivatives/manifest_q4_2025.json")

        print("=" * 70)
        print("STAGE G.8 — PRODUCTION Q4-2025 NSE F&O HISTORICAL INGESTION")
        print("=" * 70)
        print(f"  Target Database: {db_name}")
        print(f"  Host:Port:       {host}:{port}")
        print(f"  Manifest:        {manifest_path}")
        print(f"  Skip Existing:   {args.skip_existing}")
        print(f"  Timestamp:       {datetime.now(UTC).isoformat()}")
        print("=" * 70)

        # Step 2: Baseline Metrics Before Ingestion
        print("\n[STEP 1/3] Reading Pre-Ingestion Baseline Metrics...")
        pre_metrics = await get_database_metrics(session)
        print(f"  Total Derivative Contracts:    {pre_metrics['total_contracts']:,}")
        print(f"  Total Derivative Daily Prices: {pre_metrics['total_daily_prices']:,}")
        print(f"  Total Distinct Sessions:       {pre_metrics['total_distinct_sessions']}")
        print(f"  Date Range:                    {pre_metrics['earliest_date']} to {pre_metrics['latest_date']}")
        print(f"  Q4-2025 Sessions Present:      {pre_metrics['q4_sessions']}")
        print(f"  Q4-2025 Daily Prices:          {pre_metrics['q4_daily_prices']:,}")

        if args.verify_only:
            print("\n[VERIFY ONLY] Skipping ingestion phase as requested.")
            return

        # Step 3: Execute Manifest Ingestion
        print("\n[STEP 2/3] Executing NseDerivativeHistoricalExtensionService.run_manifest()...")
        extension_service = NseDerivativeHistoricalExtensionService(session)

        start_time = time.perf_counter()
        report = await extension_service.run_manifest(
            manifest_path=manifest_path,
            skip_existing=args.skip_existing,
            commit=True,
        )
        elapsed_sec = time.perf_counter() - start_time

        print("\n" + "=" * 70)
        print(f"MANIFEST INGESTION COMPLETE in {elapsed_sec:.2f}s")
        print(f"  Sessions Processed: {report.sessions_processed}")
        print(f"  Successful:         {report.successful_sessions}")
        print(f"  Skipped (Existing): {report.skipped_sessions}")
        print(f"  Failed:             {report.failed_sessions}")
        print(f"  Source Rows Read:   {report.total_source_rows:,}")
        print(f"  Contracts Upserted: {report.total_contracts_upserted:,}")
        print(f"  Prices Upserted:    {report.total_prices_upserted:,}")
        print("=" * 70)

        # Print per-session status table
        print("\nPER-SESSION INGESTION RESULTS:")
        print(f"{'#':<3} {'Date':<12} {'Status':<24} {'Source Rows':<12} {'Prices':<10} {'Contracts':<10} {'Notes'}")
        print("-" * 85)
        for idx, res in enumerate(report.session_results, 1):
            notes = res.error_message or ""
            print(
                f"{idx:<3} {str(res.trade_date):<12} {res.status:<24} {res.total_rows:<12} {res.prices_upserted:<10} {res.contracts_upserted:<10} {notes}"
            )

        # Step 4: Post-Ingestion Verification
        print("\n[STEP 3/3] Running Post-Ingestion Production Database Verification...")
        post_metrics = await get_database_metrics(session)

        contracts_added = post_metrics["total_contracts"] - pre_metrics["total_contracts"]
        prices_added = post_metrics["total_daily_prices"] - pre_metrics["total_daily_prices"]

        print(f"  Total Derivative Contracts:    {post_metrics['total_contracts']:,} (+{contracts_added:,})")
        print(f"  Total Derivative Daily Prices: {post_metrics['total_daily_prices']:,} (+{prices_added:,})")
        print(f"  Total Distinct Sessions:       {post_metrics['total_distinct_sessions']}")
        print(f"  Earliest Trade Date:           {post_metrics['earliest_date']}")
        print(f"  Latest Trade Date:             {post_metrics['latest_date']}")
        print(f"  Q4-2025 Sessions:              {post_metrics['q4_sessions']} (expected 62)")
        print(f"  Q4-2025 Daily Prices:          {post_metrics['q4_daily_prices']:,}")
        print(f"  Q4-2025 Active Contracts:      {post_metrics['q4_contracts']:,}")
        print(f"    - Option Contracts:          {post_metrics['q4_options']:,}")
        print(f"    - Futures Contracts:         {post_metrics['q4_futures']:,}")

        print("\nDATA QUALITY ASSERTIONS:")
        checks = [
            ("Duplicate (contract_id, trade_date) = 0", post_metrics["dup_contract_date"] == 0, post_metrics["dup_contract_date"]),
            ("Orphan Daily Prices = 0", post_metrics["orphan_ddp"] == 0, post_metrics["orphan_ddp"]),
            ("Weekend Daily Prices = 0", post_metrics["weekend_ddp"] == 0, post_metrics["weekend_ddp"]),
            ("GIFT NIFTY Contracts = 0", post_metrics["gift_nifty_contracts"] == 0, post_metrics["gift_nifty_contracts"]),
            ("Point-in-Time Violations = 0", post_metrics["pit_violations"] == 0, post_metrics["pit_violations"]),
            ("Option Lot Size != 1 Violations = 0", post_metrics["option_lot_not_1"] == 0, post_metrics["option_lot_not_1"]),
            ("Option Source != None Violations = 0", post_metrics["option_source_not_none"] == 0, post_metrics["option_source_not_none"]),
            ("Q4-2025 Sessions == 62", post_metrics["q4_sessions"] == 62, post_metrics["q4_sessions"]),
        ]

        all_passed = True
        for name, passed, val in checks:
            status_str = "[PASS]" if passed else "[FAIL]"
            if not passed:
                all_passed = False
            print(f"  {status_str} {name} (current value: {val})")

        print("=" * 70)
        if all_passed and report.failed_sessions == 0:
            print("PRODUCTION Q4-2025 INGESTION COMPLETED SUCCESSFULLY.")
        else:
            print("WARNING: Ingestion completed with one or more failures or failed checks.")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
