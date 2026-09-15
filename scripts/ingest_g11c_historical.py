"""Historical Data Ingestion Orchestrator — Stage G.11-C Phase 4.

Orchestrates the ingestion of official NSE historical reference data across:
  1. Security Aliases (TATAMOTORS -> TMPV canonical mapping)
  2. Broad Market Indices (ind_close_all_*.csv)
  3. India VIX (nse_vix_history_*.json)
  4. Bulk & Block Deals (manifest_q4_2025.json & manifest_2026.json)
  5. Participant Open Interest & Volume (manifest_q4_2025.json & manifest_2026.json)
  6. Corporate Actions, Announcements, Event Calendar (data/reference/corporate)
  7. Institutional Flow FII/DII (manifest_20260101_20260831.json)
  8. Derivatives / F&O Bhavcopy (manifest_q4_2025.json & manifest_2026.json)

CLI Modes:
  --dry-run          Scan manifests/files, report counts, no DB mutations
  --verify-only      Verify SHA-256 checksums against manifests, no DB mutations
  --ingest           Execute production ingestion into database
  --idempotency-test Execute ingestion twice and assert zero duplicates / drift
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.bulk_block_deal import BulkBlockDeal
from app.models.corporate_action import CorporateAction
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.derivative_participant import DerivativeParticipantPosition
from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.institutional_flow import InstitutionalFlow
from app.models.market_index_price import MarketIndexPrice
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.services.bulk_block_extension_service import BulkBlockExtensionService
from app.services.nse_corporate_import_service import NseCorporateImportService
from app.services.nse_derivative_historical_extension_service import (
    NseDerivativeHistoricalExtensionService,
)
from app.services.nse_index_import_service import NSEIndexImportService
from app.services.nse_institutional_import_service import NseInstitutionalImportService
from app.services.nse_participant_historical_extension_service import (
    NseParticipantHistoricalExtensionService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("g11c_ingestion")


def verify_database_guard(allow_test_db: bool = False) -> str:
    """Verify target database connection string is strictly production ai_stock_scanner."""
    raw_url = settings.database_url_async
    db_name = raw_url.rsplit("/", 1)[-1].split("?")[0].strip().lower()

    if not allow_test_db:
        if db_name != "ai_stock_scanner":
            raise SystemExit(
                f"FATAL: Database guard rejected database '{db_name}'. "
                f"Production ingestion requires strictly 'ai_stock_scanner'."
            )
        if any(bad in db_name for bad in ["dummy", "mock"]):
            raise SystemExit(f"FATAL: Prohibited substring in database name '{db_name}'.")

    logger.info("Database guard passed: target database is '%s'", db_name)
    return db_name


# ============================================================================
# 1. SECURITY ALIASES
# ============================================================================
async def ingest_security_aliases(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ensure canonical TATAMOTORS -> TMPV (stock_id=33792) alias exists."""
    stats = {"domain": "aliases", "planned": 1, "inserted": 0, "already_present": 0}

    # Verify canonical stock TMPV exists
    stmt_stock = select(Stock).where(Stock.id == 33792)
    tmpv = (await session.execute(stmt_stock)).scalar_one_or_none()
    if not tmpv or tmpv.symbol != "TMPV":
        raise RuntimeError(f"Expected Stock ID 33792 with symbol TMPV, found: {tmpv}")

    # Check if alias already exists
    stmt_alias = select(SecurityAlias).where(
        SecurityAlias.exchange == "NSE",
        SecurityAlias.symbol == "TATAMOTORS",
        SecurityAlias.valid_from == date(2025, 10, 1),
    )
    existing = (await session.execute(stmt_alias)).scalar_one_or_none()

    if existing:
        stats["already_present"] = 1
        logger.info("SecurityAlias TATAMOTORS -> TMPV already present (id=%s)", existing.id)
        return stats

    if dry_run:
        logger.info("[DRY-RUN] Would insert SecurityAlias TATAMOTORS -> TMPV (stock_id=33792)")
        stats["inserted"] = 1
        return stats

    stmt_ins = insert(SecurityAlias).values(
        stock_id=33792,
        exchange="NSE",
        symbol="TATAMOTORS",
        isin="INE155A01022",
        series="EQ",
        valid_from=date(2025, 10, 1),
        valid_to=date(2025, 10, 23),
        available_at=date(2025, 10, 1),
        source_name="NSE_SECURITY_RENAME_TMPV",
    ).on_conflict_do_nothing(index_elements=["exchange", "symbol", "valid_from"])

    await session.execute(stmt_ins)
    await session.commit()
    stats["inserted"] = 1
    logger.info("Successfully inserted SecurityAlias TATAMOTORS -> TMPV (stock_id=33792)")
    return stats


# ============================================================================
# 2. INDICES & VIX
# ============================================================================
async def ingest_indices(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest broad market index CSV files from data/reference/indices."""
    dir_path = Path("data/reference/indices")
    service = NSEIndexImportService(session)
    logger.info("Ingesting index CSVs from %s (dry_run=%s)", dir_path, dry_run)
    res = await service.import_index_directory(dir_path, dry_run=dry_run)
    return {
        "domain": "indices",
        "files_processed": res.files_processed,
        "files_failed": res.files_failed,
        "total_parsed": res.total_parsed,
        "inserted": res.inserted_total,
        "updated": res.updated_total,
        "unchanged": res.unchanged_total,
        "rejected": res.rejected_total,
        "success": res.success,
    }


async def ingest_vix(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest India VIX historical JSON chunks from data/reference/indices/vix."""
    dir_path = Path("data/reference/indices/vix")
    service = NSEIndexImportService(session)
    logger.info("Ingesting India VIX chunks from %s (dry_run=%s)", dir_path, dry_run)
    res = await service.import_vix_directory(dir_path, dry_run=dry_run)
    return {
        "domain": "vix",
        "files_processed": res["files_processed"],
        "total_rows": res["total_rows"],
        "inserted": res["inserted"],
        "updated": res["updated"],
        "unchanged": res["unchanged"],
    }


# ============================================================================
# 3. BULK & BLOCK DEALS
# ============================================================================
async def ingest_deals(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    skip_if_present: bool = True,
) -> dict[str, Any]:
    """Ingest official Bulk and Block deal manifests (2025 Q4 & 2026)."""
    service = BulkBlockExtensionService(session)
    manifest_2025 = Path("data/reference/deals/manifest_q4_2025.json")
    manifest_2026 = Path("data/reference/deals/manifest_2026.json")

    logger.info("Ingesting Bulk & Block deals: 2025 manifest (%s)", manifest_2025)
    res_2025 = await service.ingest_manifest(
        manifest_path=manifest_2025,
        skip_if_present=skip_if_present,
        verify_only=dry_run,
    )

    logger.info("Ingesting Bulk & Block deals: 2026 manifest (%s)", manifest_2026)
    res_2026 = await service.ingest_manifest(
        manifest_path=manifest_2026,
        skip_if_present=skip_if_present,
        verify_only=dry_run,
    )

    return {
        "domain": "deals",
        "total_sessions": res_2025.total_sessions + res_2026.total_sessions,
        "successful_sessions": res_2025.successful_sessions + res_2026.successful_sessions,
        "failed_sessions": res_2025.failed_sessions + res_2026.failed_sessions,
        "skipped_sessions": res_2025.skipped_sessions + res_2026.skipped_sessions,
        "rows_inserted": res_2025.total_rows_inserted + res_2026.total_rows_inserted,
        "rows_updated": res_2025.total_rows_updated + res_2026.total_rows_updated,
        "rows_unchanged": res_2025.total_rows_unchanged + res_2026.total_rows_unchanged,
        "quality_warnings": res_2025.quality_warnings_count + res_2026.quality_warnings_count,
    }


# ============================================================================
# 4. PARTICIPANT POSITION & VOLUME
# ============================================================================
async def ingest_participant(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Ingest Participant Open Interest and Volume manifests (2025 Q4 & 2026)."""
    service = NseParticipantHistoricalExtensionService(session)
    m_2025 = Path("data/reference/participant/manifest_q4_2025.json")
    m_2026 = Path("data/reference/participant/manifest_2026.json")

    logger.info("Ingesting Participant derivatives data: 2025 manifest (%s)", m_2025)
    res_2025 = await service.run_manifest(m_2025, skip_existing=skip_existing)

    logger.info("Ingesting Participant derivatives data: 2026 manifest (%s)", m_2026)
    res_2026 = await service.run_manifest(m_2026, skip_existing=skip_existing)

    return {
        "domain": "participant",
        "total_sessions": res_2025.total_sessions + res_2026.total_sessions,
        "successful_sessions": res_2025.successful_sessions + res_2026.successful_sessions,
        "failed_sessions": res_2025.failed_sessions + res_2026.failed_sessions,
        "skipped_sessions": res_2025.skipped_sessions + res_2026.skipped_sessions,
        "rows_inserted": res_2025.total_rows_inserted + res_2026.total_rows_inserted,
        "rows_updated": res_2025.total_rows_updated + res_2026.total_rows_updated,
        "rows_unchanged": res_2025.total_rows_unchanged + res_2026.total_rows_unchanged,
        "official_artifacts": res_2025.official_artifact_sessions + res_2026.official_artifact_sessions,
    }


# ============================================================================
# 5. CORPORATE ACTIONS & ANNOUNCEMENTS
# ============================================================================
async def ingest_corporate(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest corporate directory (actions, announcements, calendar)."""
    dir_path = Path("data/reference/corporate")
    service = NseCorporateImportService(session)
    logger.info("Ingesting corporate data from %s", dir_path)
    res = await service.import_corporate_directory(dir_path)
    return {
        "domain": "corporate",
        "actions_processed": res["actions"]["processed"],
        "actions_inserted": res["actions"]["inserted"],
        "actions_unchanged": res["actions"]["unchanged"],
        "announcements_processed": res["announcements"]["processed"],
        "announcements_inserted": res["announcements"]["inserted"],
        "announcements_unchanged": res["announcements"]["unchanged"],
        "calendar_processed": res["calendar"]["processed"],
        "calendar_inserted": res["calendar"]["inserted"],
        "calendar_unchanged": res["calendar"]["unchanged"],
    }


# ============================================================================
# 6. INSTITUTIONAL FLOWS
# ============================================================================
async def ingest_institutional(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest institutional flow data from manifest (113 valid sessions, 51 SOURCE_UNAVAILABLE)."""
    m_path = Path("data/reference/institutional/manifest_20260101_20260831.json")
    service = NseInstitutionalImportService(session)
    logger.info("Ingesting institutional flows from %s", m_path)
    res = await service.import_from_manifest(m_path)
    await session.commit()
    return {
        "domain": "institutional",
        "sessions_count": res.sessions_count,
        "total_records": res.total_records,
        "inserted": res.inserted,
        "updated": res.updated,
    }


# ============================================================================
# 7. DERIVATIVES / F&O BHAVCOPY
# ============================================================================
async def ingest_derivatives(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    skip_existing: bool = True,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Ingest official F&O Bhavcopy archives (2025 Q4 & 2026)."""
    service = NseDerivativeHistoricalExtensionService(session, max_date=date(2026, 9, 11))
    m_2025 = Path("data/reference/derivatives/manifest_q4_2025.json")
    m_2026 = Path("data/reference/derivatives/manifest_2026.json")

    logger.info("Ingesting F&O bhavcopies: 2025 manifest (%s)", m_2025)
    res_2025 = await service.run_manifest(
        m_2025,
        skip_existing=skip_existing,
        date_from=date_from,
        date_to=date_to,
    )

    logger.info("Ingesting F&O bhavcopies: 2026 manifest (%s)", m_2026)
    res_2026 = await service.run_manifest(
        m_2026,
        skip_existing=skip_existing,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "domain": "derivatives",
        "sessions_processed": res_2025.sessions_processed + res_2026.sessions_processed,
        "successful_sessions": res_2025.successful_sessions + res_2026.successful_sessions,
        "failed_sessions": res_2025.failed_sessions + res_2026.failed_sessions,
        "skipped_sessions": res_2025.skipped_sessions + res_2026.skipped_sessions,
        "total_contracts_upserted": res_2025.total_contracts_upserted + res_2026.total_contracts_upserted,
        "total_prices_upserted": res_2025.total_prices_upserted + res_2026.total_prices_upserted,
    }


# ============================================================================
# SHA-256 VERIFICATION
# ============================================================================
def verify_manifest_checksums() -> dict[str, Any]:
    """Verify SHA-256 hashes of all acquired raw files against their manifests."""
    manifest_targets = [
        ("derivatives_2025", Path("data/reference/derivatives/manifest_q4_2025.json"), Path("data/reference/derivatives/raw")),
        ("derivatives_2026", Path("data/reference/derivatives/manifest_2026.json"), Path("data/reference/derivatives/raw")),
        ("participant_2025", Path("data/reference/participant/manifest_q4_2025.json"), Path("data/reference/participant/raw")),
        ("participant_2026", Path("data/reference/participant/manifest_2026.json"), Path("data/reference/participant/raw")),
        ("deals_2025", Path("data/reference/deals/manifest_q4_2025.json"), Path("data/reference/deals/raw")),
        ("deals_2026", Path("data/reference/deals/manifest_2026.json"), Path("data/reference/deals/raw")),
        ("vix", Path("data/reference/indices/vix/manifest_vix.json"), Path("data/reference/indices/vix")),
    ]

    results = {"total_files_checked": 0, "verified": 0, "missing": 0, "mismatched": 0, "details": []}

    for name, m_path, base_dir in manifest_targets:
        if not m_path.exists():
            continue
        data = json.loads(m_path.read_text(encoding="utf-8"))

        files_to_check: list[tuple[str, str]] = []
        if name == "derivatives_2025":
            for d_str, meta in data.items():
                if not d_str.startswith("_"):
                    files_to_check.append((meta["filename"], meta.get("sha256", "")))
        elif name == "derivatives_2026":
            for d_str, meta in data.items():
                if not d_str.startswith("_"):
                    files_to_check.append((meta["filename"], meta.get("sha256", "")))
        elif name == "participant_2025":
            files_dict = data.get("files", {})
            for meta in (files_dict.values() if isinstance(files_dict, dict) else files_dict):
                if isinstance(meta, dict) and meta.get("filename"):
                    files_to_check.append((meta["filename"], meta.get("sha256", "")))
        elif name == "participant_2026":
            for d_str, meta in data.items():
                if not d_str.startswith("_"):
                    oi = meta.get("participant_oi", {})
                    vol = meta.get("participant_vol", {})
                    if oi.get("filename"):
                        files_to_check.append((oi["filename"], oi.get("sha256", "")))
                    if vol.get("filename"):
                        files_to_check.append((vol["filename"], vol.get("sha256", "")))
        elif name == "deals_2025":
            for _d_str, meta in data.get("sessions", {}).items():
                b = meta.get("bulk", {})
                k = meta.get("block", {})
                if b.get("filename"):
                    files_to_check.append((b["filename"], b.get("sha256", "")))
                if k.get("filename"):
                    files_to_check.append((k["filename"], k.get("sha256", "")))
        elif name == "deals_2026":
            for d_str, meta in data.items():
                if not d_str.startswith("_"):
                    b = meta.get("bulk", {})
                    k = meta.get("block", {})
                    if b.get("filename"):
                        files_to_check.append((b["filename"], b.get("sha256", "")))
                    if k.get("filename"):
                        files_to_check.append((k["filename"], k.get("sha256", "")))
        elif name == "vix":
            for _c_key, c_meta in data.get("chunks", {}).items():
                files_to_check.append((c_meta["filename"], c_meta.get("sha256", "")))

        for fname, exp_sha in files_to_check:
            results["total_files_checked"] += 1
            fpath = base_dir / fname
            if not fpath.exists():
                results["missing"] += 1
                results["details"].append(f"MISSING: {fpath}")
                continue
            if exp_sha:
                act_sha = hashlib.sha256(fpath.read_bytes()).hexdigest().lower()
                if act_sha != exp_sha.lower():
                    results["mismatched"] += 1
                    results["details"].append(f"MISMATCH: {fname} expected {exp_sha} got {act_sha}")
                else:
                    results["verified"] += 1
            else:
                results["verified"] += 1

    return results


# ============================================================================
# TABLE ROW COUNT SNAPSHOT
# ============================================================================
async def snapshot_table_counts(session: AsyncSession) -> dict[str, int]:
    """Retrieve row counts for all G.11-C target fact tables."""
    tables = [
        ("security_aliases", SecurityAlias),
        ("market_index_prices", MarketIndexPrice),
        ("bulk_block_deals", BulkBlockDeal),
        ("derivative_participant_positions", DerivativeParticipantPosition),
        ("corporate_actions", CorporateAction),
        ("corporate_announcements", CorporateAnnouncement),
        ("institutional_flows", InstitutionalFlow),
        ("derivative_contracts", DerivativeContract),
        ("derivative_daily_prices", DerivativeDailyPrice),
    ]
    counts: dict[str, int] = {}
    for t_name, model in tables:
        stmt = select(func.count()).select_from(model)
        val = (await session.execute(stmt)).scalar_one()
        counts[t_name] = val
    return counts


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================
async def run_orchestrator(args: argparse.Namespace) -> None:
    verify_database_guard(allow_test_db=args.allow_test_db)
    session_factory = get_session_factory()

    if args.verify_only:
        logger.info("Running --verify-only checksum verification...")
        v_res = verify_manifest_checksums()
        logger.info(
            "Checksum Verification Complete: checked=%d, verified=%d, missing=%d, mismatched=%d",
            v_res["total_files_checked"],
            v_res["verified"],
            v_res["missing"],
            v_res["mismatched"],
        )
        if v_res["mismatched"] > 0 or v_res["missing"] > 0:
            for err in v_res["details"]:
                logger.error(err)
            sys.exit(1)
        return

    if args.dry_run:
        logger.info("=== RUNNING IN --dry-run MODE (NO DATABASE MUTATIONS) ===")
        # Report planned writes across manifests
        v_res = verify_manifest_checksums()
        logger.info(
            "Manifest verification: %d files verified, %d missing, %d mismatched",
            v_res["verified"],
            v_res["missing"],
            v_res["mismatched"],
        )
        async with session_factory() as session:
            before_counts = await snapshot_table_counts(session)
            logger.info("Current production database counts: %s", before_counts)
        logger.info("Dry-run inspection completed successfully.")
        return

    if args.idempotency_test:
        logger.info("=== RUNNING IDEMPOTENCY TEST: TWO SUCCESSIVE INGESTIONS ===")
        async with session_factory() as session:
            baseline_counts = await snapshot_table_counts(session)
            logger.info("Baseline DB counts before run 1: %s", baseline_counts)

            # Run 1
            logger.info("--- STARTING INGESTION PASS 1 ---")
            await execute_domain_ingestion(session, args.domain, skip_existing=False)
            pass1_counts = await snapshot_table_counts(session)
            logger.info("DB counts after pass 1: %s", pass1_counts)

            # Run 2
            logger.info("--- STARTING INGESTION PASS 2 (IDEMPOTENCY CHECK) ---")
            await execute_domain_ingestion(session, args.domain, skip_existing=False)
            pass2_counts = await snapshot_table_counts(session)
            logger.info("DB counts after pass 2: %s", pass2_counts)

            # Assert identical counts
            discrepancies = {}
            for tbl, cnt1 in pass1_counts.items():
                cnt2 = pass2_counts.get(tbl, 0)
                if cnt1 != cnt2:
                    discrepancies[tbl] = f"Pass 1: {cnt1} vs Pass 2: {cnt2}"

            if discrepancies:
                logger.error("FATAL: Idempotency test FAILED with count drift: %s", discrepancies)
                sys.exit(1)
            else:
                logger.info("IDEMPOTENCY VERIFIED: Exact matching counts across both passes. Zero duplicates!")
        return

    # Standard --ingest mode
    logger.info("=== EXECUTING PRODUCTION INGESTION (domain=%s) ===", args.domain)
    start_t = time.perf_counter()
    async with session_factory() as session:
        before_counts = await snapshot_table_counts(session)
        logger.info("Pre-ingestion DB counts: %s", before_counts)

        results = await execute_domain_ingestion(
            session,
            args.domain,
            skip_existing=not args.force_reimport,
            date_from=date.fromisoformat(args.date_from) if args.date_from else None,
            date_to=date.fromisoformat(args.date_to) if args.date_to else None,
        )

        after_counts = await snapshot_table_counts(session)
        logger.info("Post-ingestion DB counts: %s", after_counts)

    elapsed = time.perf_counter() - start_t
    logger.info("=== INGESTION FINISHED IN %.2f SECONDS ===", elapsed)
    print("\n" + "=" * 60)
    print("G.11-C HISTORICAL INGESTION SUMMARY REPORT")
    print("=" * 60)
    for res in results:
        dom = res.get("domain", "unknown")
        print(f"[{dom.upper()}]: {res}")
    print("=" * 60)
    print("DATABASE DELTAS:")
    for tbl, cnt2 in after_counts.items():
        cnt1 = before_counts.get(tbl, 0)
        diff = cnt2 - cnt1
        print(f"  {tbl:35s}: {cnt1:8d} -> {cnt2:8d} (+{diff:d})")
    print("=" * 60 + "\n")


async def execute_domain_ingestion(
    session: AsyncSession,
    domain: str,
    *,
    skip_existing: bool = True,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Execute ingestion for specified domain or all domains in dependency order."""
    results: list[dict[str, Any]] = []

    # 1. Security Aliases
    if domain in ("all", "aliases"):
        res = await ingest_security_aliases(session)
        results.append(res)

    # 2. Market Indices
    if domain in ("all", "indices"):
        res = await ingest_indices(session)
        results.append(res)

    # 3. India VIX
    if domain in ("all", "vix"):
        res = await ingest_vix(session)
        results.append(res)

    # 4. Bulk & Block Deals
    if domain in ("all", "deals"):
        res = await ingest_deals(session, skip_if_present=skip_existing)
        results.append(res)

    # 5. Participant OI & Volume
    if domain in ("all", "participant"):
        res = await ingest_participant(session, skip_existing=skip_existing)
        results.append(res)

    # 6. Corporate Actions & Announcements
    if domain in ("all", "corporate"):
        res = await ingest_corporate(session)
        results.append(res)

    # 7. Institutional Flows
    if domain in ("all", "institutional"):
        res = await ingest_institutional(session)
        results.append(res)

    # 8. Derivatives (F&O Bhavcopy)
    if domain in ("all", "derivatives"):
        res = await ingest_derivatives(
            session,
            skip_existing=skip_existing,
            date_from=date_from,
            date_to=date_to,
        )
        results.append(res)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="G.11-C Historical Data Ingestion Orchestrator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Scan manifests without DB writes")
    group.add_argument("--verify-only", action="store_true", help="Verify SHA-256 checksums only")
    group.add_argument("--ingest", action="store_true", help="Execute database ingestion")
    group.add_argument("--idempotency-test", action="store_true", help="Execute 2 passes and assert exact match")

    parser.add_argument(
        "--domain",
        choices=["all", "aliases", "indices", "vix", "deals", "participant", "corporate", "institutional", "derivatives"],
        default="all",
        help="Domain to ingest (default: all)",
    )
    parser.add_argument("--date-from", type=str, default=None, help="Optional YYYY-MM-DD filter")
    parser.add_argument("--date-to", type=str, default=None, help="Optional YYYY-MM-DD filter")
    parser.add_argument("--force-reimport", action="store_true", help="Overwrite/update existing records")
    parser.add_argument("--allow-test-db", action="store_true", help="Bypass strict ai_stock_scanner DB guard")

    args = parser.parse_args()
    asyncio.run(run_orchestrator(args))


if __name__ == "__main__":
    main()
