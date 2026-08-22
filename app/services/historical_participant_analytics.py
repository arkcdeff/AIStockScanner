"""Historical Participant Analytics Service — Sprint 19.1D-7.

Provides point-in-time safe historical participant analytics:
  - Participant Open Interest & Trading Volume snapshots
  - FII Index Futures Summary (Long, Short, Net, Long/Short Ratio)
  - Day-over-Day position changes (current - previous available report)
  - Participant concentration shares (Long share %, Short share %)
  - FII Derivatives Statistics summary
  - Quality checks & market balance validation

Strict Invariants:
  - NO AI calculates these numbers; all metrics are deterministic
  - NO contract-level participant ownership claims
  - OPEN_INTEREST is explicitly distinguished from TRADING_VOLUME
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_participant import DerivativeParticipantPosition
from app.repositories.derivative_participant import DerivativeParticipantRepository
from app.schemas.derivative_participant import (
    FiiDerivativeStatisticItem,
    FiiIndexFuturesSummary,
    ParticipantPositionItem,
    ParticipantSnapshot,
)


class HistoricalParticipantAnalyticsService:
    """Service for historical participant positioning and volume analytics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DerivativeParticipantRepository(session)

    async def get_participant_snapshot(
        self,
        report_date: date,
        dataset_type: str = "OPEN_INTEREST",
        as_of_date: date | None = None,
    ) -> ParticipantSnapshot:
        """Fetch participant snapshot for a report date with FII summary and share metrics."""
        as_of = as_of_date or report_date
        positions = await self.repo.get_positions_by_date(
            report_date=report_date,
            dataset_type=dataset_type,
            as_of_date=as_of,
        )

        quality_warnings: list[str] = []

        if not positions:
            quality_warnings.append(f"PARTICIPANT_DATA_UNAVAILABLE:{dataset_type}:{report_date}")

        # Bucket total longs and shorts for share calculations
        bucket_long_totals: dict[str, int] = {}
        bucket_short_totals: dict[str, int] = {}

        for pos in positions:
            b = pos.instrument_bucket
            bucket_long_totals[b] = bucket_long_totals.get(b, 0) + pos.long_contracts
            bucket_short_totals[b] = bucket_short_totals.get(b, 0) + pos.short_contracts

        items: list[ParticipantPositionItem] = []
        is_balanced = True

        for pos in positions:
            b = pos.instrument_bucket
            tot_l = bucket_long_totals.get(b, 0)
            tot_s = bucket_short_totals.get(b, 0)

            l_share = (
                (Decimal(str(pos.long_contracts)) / Decimal(str(tot_l))) * Decimal("100")
                if tot_l > 0
                else Decimal("0")
            )
            s_share = (
                (Decimal(str(pos.short_contracts)) / Decimal(str(tot_s))) * Decimal("100")
                if tot_s > 0
                else Decimal("0")
            )

            items.append(
                ParticipantPositionItem(
                    participant_type=pos.participant_type,
                    instrument_bucket=b,
                    long_contracts=pos.long_contracts,
                    short_contracts=pos.short_contracts,
                    net_contracts=pos.net_contracts,
                    reported_total_long=pos.reported_total_long,
                    reported_total_short=pos.reported_total_short,
                    long_share_pct=l_share,
                    short_share_pct=s_share,
                )
            )

        # Check market balance
        for b in bucket_long_totals:
            if bucket_long_totals[b] != bucket_short_totals[b]:
                is_balanced = False
                quality_warnings.append(f"DQ_PARTICIPANT_MARKET_BALANCE_MISMATCH:{b}")

        # FII Index Futures summary
        fii_idx_fut = [
            p for p in positions if p.participant_type == "FII" and p.instrument_bucket == "INDEX_FUTURE"
        ]
        fii_summary = FiiIndexFuturesSummary(report_date=report_date)

        if fii_idx_fut:
            p = fii_idx_fut[0]
            fii_summary.long_contracts = p.long_contracts
            fii_summary.short_contracts = p.short_contracts
            fii_summary.net_contracts = p.net_contracts

            if p.short_contracts > 0:
                fii_summary.long_short_ratio = Decimal(str(p.long_contracts)) / Decimal(
                    str(p.short_contracts)
                )

            # Query previous available report date for day-over-day changes
            prev_stmt = (
                select(DerivativeParticipantPosition)
                .where(DerivativeParticipantPosition.participant_type == "FII")
                .where(DerivativeParticipantPosition.instrument_bucket == "INDEX_FUTURE")
                .where(DerivativeParticipantPosition.dataset_type == dataset_type)
                .where(DerivativeParticipantPosition.report_date < report_date)
                .where(DerivativeParticipantPosition.available_at <= as_of)
                .order_by(DerivativeParticipantPosition.report_date.desc())
                .limit(1)
            )
            prev_res = await self.session.execute(prev_stmt)
            prev_p = prev_res.scalar_one_or_none()

            if prev_p:
                fii_summary.day_over_day_long_change = p.long_contracts - prev_p.long_contracts
                fii_summary.day_over_day_short_change = p.short_contracts - prev_p.short_contracts
                fii_summary.day_over_day_net_change = p.net_contracts - prev_p.net_contracts

        # Fetch FII derivatives statistics if available
        fii_stats_rows = await self.repo.get_fii_statistics_by_date(
            report_date=report_date, as_of_date=as_of
        )
        fii_stat_items: list[FiiDerivativeStatisticItem] = []

        for st in fii_stats_rows:
            net_cnt = (
                (st.buy_contracts - st.sell_contracts)
                if (st.buy_contracts is not None and st.sell_contracts is not None)
                else None
            )
            net_amt = (
                (st.buy_amount - st.sell_amount)
                if (st.buy_amount is not None and st.sell_amount is not None)
                else None
            )
            fii_stat_items.append(
                FiiDerivativeStatisticItem(
                    instrument_category=st.instrument_category,
                    buy_contracts=st.buy_contracts,
                    buy_amount=st.buy_amount,
                    sell_contracts=st.sell_contracts,
                    sell_amount=st.sell_amount,
                    open_interest_contracts=st.open_interest_contracts,
                    open_interest_amount=st.open_interest_amount,
                    net_buy_sell_contracts=net_cnt,
                    net_buy_sell_amount=net_amt,
                )
            )

        return ParticipantSnapshot(
            report_date=report_date,
            dataset_type=dataset_type,
            as_of_date=as_of,
            fii_index_futures=fii_summary,
            items=items,
            fii_statistics=fii_stat_items,
            quality_warnings=quality_warnings,
            is_market_balanced=is_balanced,
        )

    async def get_participant_history(
        self,
        participant_type: str,
        instrument_bucket: str,
        dataset_type: str = "OPEN_INTEREST",
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
    ) -> list[ParticipantPositionItem]:
        """Fetch historical time-series items for a specific participant and instrument bucket."""
        as_of = as_of_date or (end_date or date.today())
        rows = await self.repo.get_participant_history(
            participant_type=participant_type,
            instrument_bucket=instrument_bucket,
            dataset_type=dataset_type,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of,
        )

        return [
            ParticipantPositionItem(
                participant_type=r.participant_type,
                instrument_bucket=r.instrument_bucket,
                long_contracts=r.long_contracts,
                short_contracts=r.short_contracts,
                net_contracts=r.net_contracts,
                reported_total_long=r.reported_total_long,
                reported_total_short=r.reported_total_short,
            )
            for r in rows
        ]
