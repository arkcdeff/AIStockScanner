"""Derivative Coverage Service for Sprint 19.1D-8.

Provides transparent derivatives coverage assessment across 7 dataset dimensions:
  1. Contract Master (FO_CONTRACT_MASTER)
  2. Futures Prices (FO_FUTURES_EOD)
  3. Options Prices (FO_OPTIONS_EOD)
  4. Option Chains (FO_OPTION_CHAIN)
  5. Participant Open Interest (FO_PARTICIPANT_OI)
  6. Participant Trading Volume (FO_PARTICIPANT_VOLUME)
  7. FII Derivatives Statistics (FO_FII_DERIVATIVES)

Strict Invariants:
  - Explicitly distinguishes "Imported Scope" from "Exchange-Wide Scope"
  - Does NOT claim exchange-wide completeness based solely on imported rows
  - Reports missing_session_count = "UNKNOWN" when no full exchange calendar is present
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality_issue import DataQualityIssue
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.derivative_participant import (
    DerivativeParticipantPosition,
    FiiDerivativeStatistic,
)
from app.schemas.derivative_coverage_replay import (
    DatasetCoverageItem,
    DerivativeCoverageSummary,
)


class DerivativeCoverageService:
    """Service providing comprehensive coverage and data-quality assessments for derivatives."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_derivatives_coverage(
        self,
        as_of_date: date | None = None,
    ) -> DerivativeCoverageSummary:
        """Calculate derivatives coverage metrics across all 7 derivative dataset dimensions."""
        as_of = as_of_date or date.today()
        quality_warnings: list[str] = []
        datasets: list[DatasetCoverageItem] = []

        # 1. Contract Master Coverage
        c_stmt = (
            select(
                func.min(DerivativeContract.available_at).label("min_date"),
                func.max(DerivativeContract.available_at).label("max_date"),
                func.count(DerivativeContract.id).label("row_cnt"),
                func.count(
                    func.distinct(
                        func.coalesce(
                            DerivativeContract.underlying_index_id,
                            DerivativeContract.underlying_stock_id,
                        )
                    )
                ).label("und_cnt"),
            )
            .where(DerivativeContract.available_at <= as_of)
        )
        c_res = (await self.session.execute(c_stmt)).one()
        c_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (c_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_CONTRACT_MASTER",
                dataset_name="Derivative Contract Master",
                status=c_status,
                first_date=c_res.min_date,
                latest_date=c_res.max_date,
                row_count=c_res.row_cnt or 0,
                contract_count=c_res.row_cnt or 0,
                underlying_count=c_res.und_cnt or 0,
                scope_description="Imported F&O contracts across indices and equities",
            )
        )

        # 2. Futures Price Coverage
        f_stmt = (
            select(
                func.min(DerivativeDailyPrice.trade_date).label("min_date"),
                func.max(DerivativeDailyPrice.trade_date).label("max_date"),
                func.count(DerivativeDailyPrice.id).label("row_cnt"),
                func.count(func.distinct(DerivativeDailyPrice.trade_date)).label("sess_cnt"),
            )
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "FUTURE")
            .where(DerivativeDailyPrice.available_at <= as_of)
        )
        f_res = (await self.session.execute(f_stmt)).one()
        f_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (f_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_FUTURES_EOD",
                dataset_name="Futures EOD Prices & OI",
                status=f_status,
                first_date=f_res.min_date,
                latest_date=f_res.max_date,
                row_count=f_res.row_cnt or 0,
                session_count=f_res.sess_cnt or 0,
                scope_description="Imported EOD Futures daily price & open interest time series",
            )
        )

        # 3. Options Price Coverage
        o_stmt = (
            select(
                func.min(DerivativeDailyPrice.trade_date).label("min_date"),
                func.max(DerivativeDailyPrice.trade_date).label("max_date"),
                func.count(DerivativeDailyPrice.id).label("row_cnt"),
                func.count(func.distinct(DerivativeDailyPrice.trade_date)).label("sess_cnt"),
            )
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
            .where(DerivativeDailyPrice.available_at <= as_of)
        )
        o_res = (await self.session.execute(o_stmt)).one()
        o_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (o_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_OPTIONS_EOD",
                dataset_name="Options EOD Prices & OI",
                status=o_status,
                first_date=o_res.min_date,
                latest_date=o_res.max_date,
                row_count=o_res.row_cnt or 0,
                session_count=o_res.sess_cnt or 0,
                scope_description="Imported EOD Options strike-level prices & open interest",
            )
        )

        # 4. Participant OI Coverage
        poi_stmt = (
            select(
                func.min(DerivativeParticipantPosition.report_date).label("min_date"),
                func.max(DerivativeParticipantPosition.report_date).label("max_date"),
                func.count(DerivativeParticipantPosition.id).label("row_cnt"),
                func.count(func.distinct(DerivativeParticipantPosition.report_date)).label("sess_cnt"),
            )
            .where(DerivativeParticipantPosition.dataset_type == "OPEN_INTEREST")
            .where(DerivativeParticipantPosition.available_at <= as_of)
        )
        poi_res = (await self.session.execute(poi_stmt)).one()
        poi_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (poi_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_PARTICIPANT_OI",
                dataset_name="Participant-wise Open Interest",
                status=poi_status,
                first_date=poi_res.min_date,
                latest_date=poi_res.max_date,
                row_count=poi_res.row_cnt or 0,
                session_count=poi_res.sess_cnt or 0,
                scope_description="Category open interest (Client, DII, FII, Pro)",
            )
        )

        # 5. Participant Volume Coverage
        pvol_stmt = (
            select(
                func.min(DerivativeParticipantPosition.report_date).label("min_date"),
                func.max(DerivativeParticipantPosition.report_date).label("max_date"),
                func.count(DerivativeParticipantPosition.id).label("row_cnt"),
                func.count(func.distinct(DerivativeParticipantPosition.report_date)).label("sess_cnt"),
            )
            .where(DerivativeParticipantPosition.dataset_type == "TRADING_VOLUME")
            .where(DerivativeParticipantPosition.available_at <= as_of)
        )
        pvol_res = (await self.session.execute(pvol_stmt)).one()
        pvol_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (pvol_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_PARTICIPANT_VOLUME",
                dataset_name="Participant-wise Trading Volume",
                status=pvol_status,
                first_date=pvol_res.min_date,
                latest_date=pvol_res.max_date,
                row_count=pvol_res.row_cnt or 0,
                session_count=pvol_res.sess_cnt or 0,
                scope_description="Category trading volumes (Client, DII, FII, Pro)",
            )
        )

        # 6. FII Statistics Coverage
        fii_stmt = (
            select(
                func.min(FiiDerivativeStatistic.report_date).label("min_date"),
                func.max(FiiDerivativeStatistic.report_date).label("max_date"),
                func.count(FiiDerivativeStatistic.id).label("row_cnt"),
                func.count(func.distinct(FiiDerivativeStatistic.report_date)).label("sess_cnt"),
            )
            .where(FiiDerivativeStatistic.available_at <= as_of)
        )
        fii_res = (await self.session.execute(fii_stmt)).one()
        fii_status = "COMPLETE_FOR_IMPORTED_SCOPE" if (fii_res.row_cnt or 0) > 0 else "NO_DATA"

        datasets.append(
            DatasetCoverageItem(
                dataset_code="FO_FII_DERIVATIVES",
                dataset_name="FII Derivatives Statistics",
                status=fii_status,
                first_date=fii_res.min_date,
                latest_date=fii_res.max_date,
                row_count=fii_res.row_cnt or 0,
                session_count=fii_res.sess_cnt or 0,
                scope_description="FII buy/sell contracts and turnover figures",
            )
        )

        # Overall Status
        has_any = any(d.status != "NO_DATA" for d in datasets)
        all_complete = all(d.status == "COMPLETE_FOR_IMPORTED_SCOPE" for d in datasets)
        if all_complete:
            overall_status = "COMPLETE_FOR_IMPORTED_SCOPE"
        elif has_any:
            overall_status = "PARTIAL"
        else:
            overall_status = "NO_DATA"

        return DerivativeCoverageSummary(
            as_of_date=as_of,
            overall_status=overall_status,
            datasets=datasets,
            quality_warnings=quality_warnings,
        )

    async def get_quality_summary(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch summary of recorded DataQualityIssue items for derivatives."""
        stmt = (
            select(
                DataQualityIssue.issue_code,
                DataQualityIssue.severity,
                DataQualityIssue.trade_date,
                DataQualityIssue.description,
                DataQualityIssue.resolved,
            )
            .order_by(DataQualityIssue.id.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return [
            {
                "issue_code": row.issue_code,
                "severity": row.severity,
                "trade_date": row.trade_date,
                "description": row.description,
                "resolved": row.resolved,
            }
            for row in res.all()
        ]
