"""NSE Participant Historical Extension Service — Stage G.9.

Extends real NSE historical derivatives participant open interest coverage
backwards through Q4-2025 (2025-10-01 through 2025-12-31, 62 actual trading sessions)
using official NSE/NSCCL Participant-wise Open Interest archives.

Key Policies & Invariants:
  1. Strict Source Provenance:
     - source_name = 'NSE_NSCCL'
     - source_reference = exact filename (fao_participant_oi_DDMMYYYY.csv)
     - source_checksum = SHA-256 hash verified against manifest
  2. Point-in-Time Integrity:
     - available_at = report_date (published post-market on trade date T)
     - No future look-ahead (never date.today() / datetime.now() for historical dates)
  3. Strict Category & Bucket Normalization:
     - Categories: CLIENT, DII, FII, PRO (TOTAL row strictly excluded)
     - Buckets: INDEX_FUTURE, STOCK_FUTURE, INDEX_OPTION_CALL, INDEX_OPTION_PUT,
       STOCK_OPTION_CALL, STOCK_OPTION_PUT
     - Normalized facts: exactly 24 rows per session (4 categories × 6 buckets)
  4. Non-Negative Fact Assertion:
     - Gross long and short contract counts must be >= 0.
  5. Official Source Artifact Tolerance:
     - 50/62 official NSE files contain a +/- 1-2 contract index-option imbalance
       inherent to the exchange disclosure.
     - Preserved as DQ_PARTICIPANT_MARKET_BALANCE_MISMATCH warning without data mutation.
  6. Atomic Session Ingestion & Idempotency:
     - Per-session database transaction with rollback on unexpected errors.
     - Conflict upsert on (report_date, dataset_type, participant_type, instrument_bucket).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_participant_parser import ParticipantOpenInterestParser
from app.models.data_quality_issue import DataQualityIssue
from app.models.derivative_participant import DerivativeParticipantPosition
from app.repositories.derivative_participant import DerivativeParticipantRepository

logger = logging.getLogger(__name__)

EXPECTED_PARTICIPANT_TYPES = frozenset({"CLIENT", "DII", "FII", "PRO"})
EXPECTED_INSTRUMENT_BUCKETS = frozenset({
    "INDEX_FUTURE",
    "STOCK_FUTURE",
    "INDEX_OPTION_CALL",
    "INDEX_OPTION_PUT",
    "STOCK_OPTION_CALL",
    "STOCK_OPTION_PUT",
})


@dataclass
class ParticipantSessionResult:
    """Ingestion result for a single participant open interest session."""

    report_date: date
    filename: str
    rows_parsed: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    is_balanced: bool = True
    is_official_artifact: bool = False
    quality_warnings: list[str] = field(default_factory=list)
    status: str = "SUCCESS"  # SUCCESS, SKIPPED_ALREADY_PRESENT, FAILED
    error_message: str | None = None


@dataclass
class ParticipantBatchReport:
    """Aggregate report of batch participant historical extension run."""

    total_sessions: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    skipped_sessions: int = 0
    total_rows_parsed: int = 0
    total_rows_inserted: int = 0
    total_rows_updated: int = 0
    total_rows_unchanged: int = 0
    perfect_balance_sessions: int = 0
    official_artifact_sessions: int = 0
    other_mismatch_sessions: int = 0
    quality_warnings_count: int = 0
    session_results: list[ParticipantSessionResult] = field(default_factory=list)


class NseParticipantHistoricalExtensionService:
    """Service to ingest historical NSE participant open interest datasets for Q4-2025."""

    def __init__(
        self,
        session: AsyncSession,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.session = session
        self.cache_dir = Path(cache_dir or "data/reference/participant/raw")
        self.parser = ParticipantOpenInterestParser()
        self.repo = DerivativeParticipantRepository(session)

    async def ingest_session(
        self,
        report_date: date,
        filename: str | None = None,
        expected_sha256: str | None = None,
        skip_if_present: bool = False,
    ) -> ParticipantSessionResult:
        """Ingest a single historical participant open interest trading session."""
        if not filename:
            d_str = report_date.strftime("%d%m%Y")
            filename = f"fao_participant_oi_{d_str}.csv"

        # 1. Skip-if-present check
        if skip_if_present:
            existing_cnt = (
                await self.session.execute(
                    select(func.count(DerivativeParticipantPosition.id))
                    .where(DerivativeParticipantPosition.report_date == report_date)
                    .where(DerivativeParticipantPosition.dataset_type == "OPEN_INTEREST")
                )
            ).scalar() or 0

            if existing_cnt >= 24:
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    rows_parsed=existing_cnt,
                    rows_inserted=0,
                    rows_updated=0,
                    rows_unchanged=existing_cnt,
                    is_balanced=True,
                    status="SKIPPED_ALREADY_PRESENT",
                )

        # 2. Resolve source file path
        source_path = self.cache_dir / filename
        if not source_path.exists():
            # Fallback check directly in relative path
            source_path = Path("data/reference/participant/raw") / filename
            if not source_path.exists():
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Participant source file not found: {source_path}",
                )

        # 3. Read and verify SHA256 checksum
        try:
            content_bytes = await asyncio.to_thread(source_path.read_bytes)
        except OSError as e:
            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                status="FAILED",
                error_message=f"Failed reading participant file: {e}",
            )

        actual_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                status="FAILED",
                error_message=f"SHA-256 mismatch for {filename}: expected {expected_sha256}, got {actual_sha256}",
            )

        # 4. Parse content using official ParticipantOpenInterestParser
        try:
            parsed = self.parser.parse(
                content=content_bytes,
                report_date=report_date,
                filename=filename,
            )
        except Exception as e:
            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                status="FAILED",
                error_message=f"Parse failure: {e}",
            )

        # 5. Strict structure and invariant validations
        if len(parsed.rows) != 24:
            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                status="FAILED",
                error_message=f"Expected exactly 24 normalized rows, got {len(parsed.rows)}",
            )

        seen_identities: set[tuple[str, str]] = set()
        orm_positions: list[DerivativeParticipantPosition] = []

        for r in parsed.rows:
            # Validate categories and instrument buckets
            if r.participant_type not in EXPECTED_PARTICIPANT_TYPES:
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Unknown participant category: {r.participant_type}",
                )
            if r.instrument_bucket not in EXPECTED_INSTRUMENT_BUCKETS:
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Unknown instrument bucket: {r.instrument_bucket}",
                )

            # Validate non-negative quantities
            if r.long_contracts < 0 or r.short_contracts < 0:
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Negative contracts detected for {r.participant_type}:{r.instrument_bucket}",
                )

            ident = (r.participant_type, r.instrument_bucket)
            if ident in seen_identities:
                return ParticipantSessionResult(
                    report_date=report_date,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Duplicate logical identity in session: {ident}",
                )
            seen_identities.add(ident)

            # Build ORM entity with strict PIT and provenance
            orm_positions.append(
                DerivativeParticipantPosition(
                    report_date=report_date,
                    dataset_type="OPEN_INTEREST",
                    participant_type=r.participant_type,
                    instrument_bucket=r.instrument_bucket,
                    long_contracts=r.long_contracts,
                    short_contracts=r.short_contracts,
                    reported_total_long=r.reported_total_long,
                    reported_total_short=r.reported_total_short,
                    available_at=report_date,  # Strict PIT publication boundary
                    source_name="NSE_NSCCL",
                    source_reference=filename,
                    source_checksum=actual_sha256,
                    fetched_at=datetime.now(UTC),
                )
            )

        # 6. Check official source artifact classification
        # Imbalance is official source artifact if warnings only contain balance mismatch on index options
        is_official_artifact = False
        if not parsed.is_market_balanced:
            non_balance_warnings = [
                w for w in parsed.quality_warnings if "DQ_PARTICIPANT_MARKET_BALANCE_MISMATCH" not in w
            ]
            if not non_balance_warnings:
                is_official_artifact = True

        # 7. Upsert into database
        try:
            # Query existing count before upsert to determine inserted vs updated
            existing_before = (
                await self.session.execute(
                    select(func.count(DerivativeParticipantPosition.id))
                    .where(DerivativeParticipantPosition.report_date == report_date)
                    .where(DerivativeParticipantPosition.dataset_type == "OPEN_INTEREST")
                )
            ).scalar() or 0

            inserted_cnt, updated_cnt = await self.repo.upsert_positions(orm_positions)

            # Calculate inserted vs unchanged
            if existing_before == 0:
                final_inserted = len(orm_positions)
                final_unchanged = 0
                final_updated = 0
            else:
                final_inserted = max(0, len(orm_positions) - existing_before)
                final_updated = updated_cnt
                final_unchanged = len(orm_positions) - final_inserted - final_updated

            # 8. Record DataQualityIssue warnings
            for w in parsed.quality_warnings:
                dq = DataQualityIssue(
                    issue_code=w.split(":")[0],
                    severity="warning",
                    exchange="NSE",
                    trade_date=report_date,
                    description=f"Participant Q4-2025 extension: {w}",
                    detected_at=datetime.now(UTC),
                )
                self.session.add(dq)

            await self.session.commit()

            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                rows_parsed=len(orm_positions),
                rows_inserted=final_inserted,
                rows_updated=final_updated,
                rows_unchanged=final_unchanged,
                is_balanced=parsed.is_market_balanced,
                is_official_artifact=is_official_artifact,
                quality_warnings=parsed.quality_warnings,
                status="SUCCESS",
            )

        except Exception as e:
            await self.session.rollback()
            logger.exception("Failed ingesting participant session for %s", report_date)
            return ParticipantSessionResult(
                report_date=report_date,
                filename=filename,
                status="FAILED",
                error_message=f"Database transaction failure: {e}",
            )

    async def run_manifest(
        self,
        manifest_path: Path | str | None = None,
        skip_existing: bool = False,
    ) -> ParticipantBatchReport:
        """Process all sessions in the Q4-2025 participant manifest."""
        m_path = Path(manifest_path or "data/reference/participant/manifest_q4_2025.json")
        if not m_path.exists():
            raise FileNotFoundError(f"Participant manifest not found at: {m_path}")

        manifest_raw = await asyncio.to_thread(m_path.read_text, encoding="utf-8")
        manifest_data = json.loads(manifest_raw)

        files_map: dict[str, Any] = manifest_data.get("files", {})
        sorted_dates = sorted(files_map.keys())

        report = ParticipantBatchReport(total_sessions=len(sorted_dates))

        for d_str in sorted_dates:
            entry = files_map[d_str]
            rep_d = date.fromisoformat(entry["report_date"])
            filename = entry["filename"]
            sha256 = entry.get("sha256")

            res = await self.ingest_session(
                report_date=rep_d,
                filename=filename,
                expected_sha256=sha256,
                skip_if_present=skip_existing,
            )

            report.session_results.append(res)
            report.total_rows_parsed += res.rows_parsed
            report.total_rows_inserted += res.rows_inserted
            report.total_rows_updated += res.rows_updated
            report.total_rows_unchanged += res.rows_unchanged
            report.quality_warnings_count += len(res.quality_warnings)

            if res.status == "SUCCESS":
                report.successful_sessions += 1
                if res.is_balanced:
                    report.perfect_balance_sessions += 1
                elif res.is_official_artifact:
                    report.official_artifact_sessions += 1
                else:
                    report.other_mismatch_sessions += 1
            elif res.status == "SKIPPED_ALREADY_PRESENT":
                report.skipped_sessions += 1
            elif res.status == "FAILED":
                report.failed_sessions += 1

        return report
