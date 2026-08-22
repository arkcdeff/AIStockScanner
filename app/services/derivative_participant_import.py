"""Import Orchestration Service for Sprint 19.1D-7: Participant Derivatives.

Orchestrates parsing, file archiving, checksum calculation, DB upserts, quality checks,
and audit job logging for:
  - Participant-wise Open Interest (FO_PARTICIPANT_OI)
  - Participant-wise Trading Volume (FO_PARTICIPANT_VOLUME)
  - FII Derivatives Statistics (FO_FII_DERIVATIVES)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.nse_participant_parser import (
    FiiDerivativesStatisticsParser,
    ParticipantOpenInterestParser,
    ParticipantTradingVolumeParser,
)
from app.models.data_quality_issue import DataQualityIssue
from app.models.derivative_participant import (
    DerivativeParticipantPosition,
    FiiDerivativeStatistic,
)
from app.models.import_job import ImportJob
from app.repositories.derivative_participant import DerivativeParticipantRepository


@dataclass
class ParticipantImportSummary:
    """Summary statistics returned by DerivativeParticipantImportService."""

    job_id: int | None
    dataset_code: str
    report_date: date
    raw_row_count: int
    parsed_record_count: int
    inserted_count: int
    updated_count: int
    source_reference: str
    source_checksum: str
    quality_warnings: list[str] = field(default_factory=list)
    status: str = "COMPLETED"


class DerivativeParticipantImportService:
    """Import orchestration service for participant derivative datasets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DerivativeParticipantRepository(session)

    async def import_participant_oi(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str = "fao_participant_oi.csv",
        source_name: str = "NSE_INDIA",
        available_at: date | None = None,
    ) -> ParticipantImportSummary:
        """Parse and store Participant-wise Open Interest (FO_PARTICIPANT_OI)."""
        return await self._import_participant_file(
            content=content,
            dataset_code="FO_PARTICIPANT_OI",
            dataset_type="OPEN_INTEREST",
            override_date=report_date,
            filename=filename,
            source_name=source_name,
            override_available_at=available_at,
        )

    async def import_participant_volume(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str = "fao_participant_vol.csv",
        source_name: str = "NSE_INDIA",
        available_at: date | None = None,
    ) -> ParticipantImportSummary:
        """Parse and store Participant-wise Trading Volume (FO_PARTICIPANT_VOLUME)."""
        return await self._import_participant_file(
            content=content,
            dataset_code="FO_PARTICIPANT_VOLUME",
            dataset_type="TRADING_VOLUME",
            override_date=report_date,
            filename=filename,
            source_name=source_name,
            override_available_at=available_at,
        )

    async def _import_participant_file(
        self,
        content: str | bytes,
        dataset_code: str,
        dataset_type: str,
        override_date: date | None = None,
        filename: str = "fao_participant.csv",
        source_name: str = "NSE_INDIA",
        override_available_at: date | None = None,
    ) -> ParticipantImportSummary:
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content

        checksum = hashlib.sha256(raw_bytes).hexdigest()

        if dataset_type == "TRADING_VOLUME":
            parser = ParticipantTradingVolumeParser()
        else:
            parser = ParticipantOpenInterestParser()

        parsed = parser.parse(content=content, report_date=override_date, filename=filename)
        rep_date = parsed.report_date
        avail_date = override_available_at or rep_date

        # Archive raw file
        archive_path = self._archive_raw_file(raw_bytes, rep_date, filename)

        # Create ImportJob record
        job = ImportJob(
            source=source_name,
            file_name=filename,
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        self.session.add(job)
        await self.session.flush()

        # Build ORM objects
        orm_positions: list[DerivativeParticipantPosition] = []
        for r in parsed.rows:
            orm_positions.append(
                DerivativeParticipantPosition(
                    report_date=rep_date,
                    dataset_type=dataset_type,
                    participant_type=r.participant_type,
                    instrument_bucket=r.instrument_bucket,
                    long_contracts=r.long_contracts,
                    short_contracts=r.short_contracts,
                    reported_total_long=r.reported_total_long,
                    reported_total_short=r.reported_total_short,
                    available_at=avail_date,
                    source_name=source_name,
                    source_reference=str(archive_path),
                    source_checksum=checksum,
                    fetched_at=datetime.now(UTC),
                )
            )

        inserted_cnt, updated_cnt = await self.repo.upsert_positions(orm_positions)

        # Record quality warnings
        for w in parsed.quality_warnings:
            dq = DataQualityIssue(
                issue_code=w.split(":")[0],
                severity="warning",
                exchange="NSE",
                trade_date=rep_date,
                description=f"Participant import quality warning: {w}",
                detected_at=datetime.now(UTC),
            )
            self.session.add(dq)

        job.status = "SUCCESS"
        job.completed_at = datetime.now(UTC)
        job.records_processed = len(orm_positions)
        job.records_inserted = inserted_cnt

        await self.session.commit()

        return ParticipantImportSummary(
            job_id=job.id,
            dataset_code=dataset_code,
            report_date=rep_date,
            raw_row_count=parsed.raw_row_count,
            parsed_record_count=len(orm_positions),
            inserted_count=inserted_cnt,
            updated_count=updated_cnt,
            source_reference=str(archive_path),
            source_checksum=checksum,
            quality_warnings=parsed.quality_warnings,
            status="COMPLETED",
        )

    async def import_fii_derivatives_statistics(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str = "fii_derivatives.csv",
        source_name: str = "NSE_INDIA",
        available_at: date | None = None,
    ) -> ParticipantImportSummary:
        """Parse and store FII Derivatives Statistics (FO_FII_DERIVATIVES)."""
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        parser = FiiDerivativesStatisticsParser()
        parsed = parser.parse(content=content, report_date=report_date, filename=filename)

        rep_date = parsed.report_date
        avail_date = available_at or rep_date

        archive_path = self._archive_raw_file(raw_bytes, rep_date, filename)

        job = ImportJob(
            source=source_name,
            file_name=filename,
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        self.session.add(job)
        await self.session.flush()

        orm_stats: list[FiiDerivativeStatistic] = []
        for r in parsed.rows:
            orm_stats.append(
                FiiDerivativeStatistic(
                    report_date=rep_date,
                    instrument_category=r.instrument_category,
                    buy_contracts=r.buy_contracts,
                    buy_amount=r.buy_amount,
                    sell_contracts=r.sell_contracts,
                    sell_amount=r.sell_amount,
                    open_interest_contracts=r.open_interest_contracts,
                    open_interest_amount=r.open_interest_amount,
                    available_at=avail_date,
                    source_name=source_name,
                    source_reference=str(archive_path),
                    source_checksum=checksum,
                )
            )

        ins_cnt, upd_cnt = await self.repo.upsert_fii_statistics(orm_stats)

        job.status = "SUCCESS"
        job.completed_at = datetime.now(UTC)
        job.records_processed = len(orm_stats)
        job.records_inserted = ins_cnt

        await self.session.commit()

        return ParticipantImportSummary(
            job_id=job.id,
            dataset_code="FO_FII_DERIVATIVES",
            report_date=rep_date,
            raw_row_count=parsed.raw_row_count,
            parsed_record_count=len(orm_stats),
            inserted_count=ins_cnt,
            updated_count=upd_cnt,
            source_reference=str(archive_path),
            source_checksum=checksum,
            quality_warnings=parsed.quality_warnings,
            status="COMPLETED",
        )

    def _archive_raw_file(self, raw_bytes: bytes, report_date: date, filename: str) -> Path:
        """Store original report file under data/raw/nse/fo/YYYY/MM/ for audit provenance."""
        data_dir = Path(getattr(settings, "DATA_DIR", "data"))
        target_dir = data_dir / "raw" / "nse" / "fo" / f"{report_date.year:04d}" / f"{report_date.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_file = target_dir / f"{report_date.strftime('%Y%m%d')}_{filename}"
        archive_file.write_bytes(raw_bytes)
        return archive_file
