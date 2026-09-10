"""Repository for Participant-wise Positions & FII Derivatives Statistics.

Provides point-in-time safe queries, bounded range fetching, and idempotent upserts:
  - DerivativeParticipantPosition (dataset_type = OPEN_INTEREST | TRADING_VOLUME)
  - FiiDerivativeStatistic
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_participant import (
    DerivativeParticipantPosition,
    FiiDerivativeStatistic,
)


class DerivativeParticipantRepository:
    """Repository managing participant positions and FII statistics persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_positions(
        self,
        positions: list[DerivativeParticipantPosition],
    ) -> tuple[int, int]:
        """Bulk upsert participant positions idempotently on unique logical identity key."""
        if not positions:
            return 0, 0

        inserted_count = 0
        updated_count = 0

        for pos in positions:
            stmt = insert(DerivativeParticipantPosition).values(
                report_date=pos.report_date,
                dataset_type=pos.dataset_type,
                participant_type=pos.participant_type,
                instrument_bucket=pos.instrument_bucket,
                long_contracts=pos.long_contracts,
                short_contracts=pos.short_contracts,
                reported_total_long=pos.reported_total_long,
                reported_total_short=pos.reported_total_short,
                available_at=pos.available_at,
                source_name=pos.source_name,
                source_reference=pos.source_reference,
                source_checksum=pos.source_checksum,
                fetched_at=pos.fetched_at,
            )

            # On conflict update non-key facts
            stmt = stmt.on_conflict_do_update(
                constraint="uq_derivative_participant_positions_identity",
                set_={
                    "long_contracts": stmt.excluded.long_contracts,
                    "short_contracts": stmt.excluded.short_contracts,
                    "reported_total_long": stmt.excluded.reported_total_long,
                    "reported_total_short": stmt.excluded.reported_total_short,
                    "available_at": stmt.excluded.available_at,
                    "source_reference": stmt.excluded.source_reference,
                    "source_checksum": stmt.excluded.source_checksum,
                    "fetched_at": stmt.excluded.fetched_at,
                },
                where=or_(
                    DerivativeParticipantPosition.long_contracts.is_distinct_from(stmt.excluded.long_contracts),
                    DerivativeParticipantPosition.short_contracts.is_distinct_from(stmt.excluded.short_contracts),
                    DerivativeParticipantPosition.reported_total_long.is_distinct_from(stmt.excluded.reported_total_long),
                    DerivativeParticipantPosition.reported_total_short.is_distinct_from(stmt.excluded.reported_total_short),
                ),
            )

            res = await self.session.execute(stmt)
            if res.rowcount > 0:
                inserted_count += 1

        return inserted_count, updated_count

    async def upsert_fii_statistics(
        self,
        stats: list[FiiDerivativeStatistic],
    ) -> tuple[int, int]:
        """Bulk upsert FII derivatives statistics idempotently."""
        if not stats:
            return 0, 0

        inserted_count = 0
        for stat in stats:
            stmt = insert(FiiDerivativeStatistic).values(
                report_date=stat.report_date,
                instrument_category=stat.instrument_category,
                buy_contracts=stat.buy_contracts,
                buy_amount=stat.buy_amount,
                sell_contracts=stat.sell_contracts,
                sell_amount=stat.sell_amount,
                open_interest_contracts=stat.open_interest_contracts,
                open_interest_amount=stat.open_interest_amount,
                available_at=stat.available_at,
                source_name=stat.source_name,
                source_reference=stat.source_reference,
                source_checksum=stat.source_checksum,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="uq_fii_derivative_statistics_identity",
                set_={
                    "buy_contracts": stmt.excluded.buy_contracts,
                    "buy_amount": stmt.excluded.buy_amount,
                    "sell_contracts": stmt.excluded.sell_contracts,
                    "sell_amount": stmt.excluded.sell_amount,
                    "open_interest_contracts": stmt.excluded.open_interest_contracts,
                    "open_interest_amount": stmt.excluded.open_interest_amount,
                    "available_at": stmt.excluded.available_at,
                    "source_reference": stmt.excluded.source_reference,
                    "source_checksum": stmt.excluded.source_checksum,
                },
                where=or_(
                    FiiDerivativeStatistic.buy_contracts.is_distinct_from(stmt.excluded.buy_contracts),
                    FiiDerivativeStatistic.buy_amount.is_distinct_from(stmt.excluded.buy_amount),
                    FiiDerivativeStatistic.sell_contracts.is_distinct_from(stmt.excluded.sell_contracts),
                    FiiDerivativeStatistic.sell_amount.is_distinct_from(stmt.excluded.sell_amount),
                    FiiDerivativeStatistic.open_interest_contracts.is_distinct_from(stmt.excluded.open_interest_contracts),
                    FiiDerivativeStatistic.open_interest_amount.is_distinct_from(stmt.excluded.open_interest_amount),
                ),
            )

            res = await self.session.execute(stmt)
            if res.rowcount > 0:
                inserted_count += 1

        return inserted_count, 0

    async def get_positions_by_date(
        self,
        report_date: date,
        dataset_type: str = "OPEN_INTEREST",
        as_of_date: date | None = None,
    ) -> list[DerivativeParticipantPosition]:
        """Fetch participant position facts for a report_date (PIT safe available_at <= as_of_date)."""
        as_of = as_of_date or report_date
        stmt = (
            select(DerivativeParticipantPosition)
            .where(DerivativeParticipantPosition.report_date == report_date)
            .where(DerivativeParticipantPosition.dataset_type == dataset_type)
            .where(DerivativeParticipantPosition.available_at <= as_of)
            .order_by(
                DerivativeParticipantPosition.participant_type.asc(),
                DerivativeParticipantPosition.instrument_bucket.asc(),
            )
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_fii_statistics_by_date(
        self,
        report_date: date,
        as_of_date: date | None = None,
    ) -> list[FiiDerivativeStatistic]:
        """Fetch FII derivatives statistics for a report_date (PIT safe available_at <= as_of_date)."""
        as_of = as_of_date or report_date
        stmt = (
            select(FiiDerivativeStatistic)
            .where(FiiDerivativeStatistic.report_date == report_date)
            .where(FiiDerivativeStatistic.available_at <= as_of)
            .order_by(FiiDerivativeStatistic.instrument_category.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_participant_history(
        self,
        participant_type: str,
        instrument_bucket: str | None = None,
        dataset_type: str = "OPEN_INTEREST",
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
    ) -> list[DerivativeParticipantPosition]:
        """Fetch historical time-series for a specific participant and optional instrument bucket."""
        as_of = as_of_date or (end_date or date.today())
        stmt = (
            select(DerivativeParticipantPosition)
            .where(DerivativeParticipantPosition.participant_type == participant_type)
            .where(DerivativeParticipantPosition.dataset_type == dataset_type)
            .where(DerivativeParticipantPosition.available_at <= as_of)
        )
        if instrument_bucket:
            stmt = stmt.where(DerivativeParticipantPosition.instrument_bucket == instrument_bucket)
        if start_date:
            stmt = stmt.where(DerivativeParticipantPosition.report_date >= start_date)
        if end_date:
            stmt = stmt.where(DerivativeParticipantPosition.report_date <= end_date)

        stmt = stmt.order_by(
            DerivativeParticipantPosition.report_date.asc(),
            DerivativeParticipantPosition.instrument_bucket.asc(),
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
