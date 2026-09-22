"""Sector Evidence Aggregator Service — G.11-D-6 Sector Breadth & Evidence Consensus.

Stateless on-demand aggregator that evaluates sector constituent universes
using bounded async concurrency (max 5 workers via semaphore), isolated worker AsyncSession
ownership, and certified G.11-D-3 interpretations.

Calculates discrete 5-state distributions, canonical denominators, domain participation rates,
equal-weight concentration proxies, deterministic contradiction flags, and market-wide aggregates.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.market_intelligence.sector_universe import (
    SECTOR_DEFINITIONS_BY_CODE,
    SECTOR_INDEX_CODES,
    SectorDefinition,
)
from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.index_membership import IndexMembership
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.sector_evidence import (
    CoverageStatus,
    SectorContradictionDetail,
    SectorContradictionFlag,
    SectorEvidenceBreadthRequest,
    SectorEvidenceBreadthResponse,
    SectorEvidenceSummary,
)
from app.schemas.what_changed_interpretation import (
    StockIntelligenceInterpretation,
)
from app.services.historical_query import HistoricalQueryService
from app.services.what_changed_interpretation_config import (
    OverallState,
)
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)
from app.services.what_changed_screening_config import SCREENING_MAX_WORKERS
from app.services.what_changed_synthesis_config import RelationshipStrength

logger = logging.getLogger(__name__)

# Canonical mapping from Sector Index Code to DB Sector.name keywords for live taxonomy fallback
_SECTOR_CODE_TO_DB_NAME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "NIFTY_AUTO": ("Automobile and Auto Components", "Auto"),
    "NIFTY_BANK": ("Financial Services", "Bank"),
    "NIFTY_FIN_SERV": ("Financial Services",),
    "NIFTY_FMCG": ("Fast Moving Consumer Goods", "FMCG"),
    "NIFTY_IT": ("Information Technology", "IT"),
    "NIFTY_MEDIA": ("Media Entertainment & Publication", "Media"),
    "NIFTY_METAL": ("Metals & Mining", "Metal"),
    "NIFTY_PHARMA": ("Healthcare", "Pharma"),
    "NIFTY_PSU_BANK": ("Financial Services", "Bank"),
    "NIFTY_PRIVATE_BANK": ("Financial Services", "Bank"),
    "NIFTY_REALTY": ("Realty", "Real Estate"),
    "NIFTY_HEALTHCARE": ("Healthcare",),
    "NIFTY_CONSUMER_DURABLES": ("Consumer Durables",),
    "NIFTY_OIL_GAS": ("Oil Gas & Consumable Fuels", "Oil"),
    "NIFTY_ENERGY": ("Oil Gas & Consumable Fuels", "Power", "Energy"),
    "NIFTY_INFRASTRUCTURE": ("Construction", "Capital Goods", "Infrastructure"),
    "NIFTY_COMMODITIES": ("Metals & Mining", "Chemicals", "Commodities"),
}


@dataclass(frozen=True, slots=True)
class _ConstituentEvaluationResult:
    symbol: str
    stock_id: int
    company_name: str | None = None
    status: str = "SUCCESS"  # "SUCCESS", "NO_DATA", "ERROR"
    interpretation: StockIntelligenceInterpretation | None = None
    error_message: str | None = None


class SectorEvidenceAggregatorService:
    """G.11-D-6 Sector Breadth & Market Evidence Consensus Aggregator.

    Parameters
    ----------
    session:
        The coordinator session used for index, sector, membership, and price resolution.
    session_factory:
        Factory for worker coroutines to open isolated AsyncSession instances.
    """

    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session = session
        self._session_factory = session_factory

    async def get_sector_evidence_breadth(
        self,
        request: SectorEvidenceBreadthRequest,
    ) -> SectorEvidenceBreadthResponse:
        """Execute sector breadth aggregation across the requested sector universes."""
        # 1. Resolve date semantics
        eval_date = request.evaluation_date or request.as_of_date or date.today()
        as_of = request.as_of_date
        window = request.comparison_window
        effective_cutoff = min(eval_date, as_of) if as_of is not None else eval_date

        # 2. Resolve requested sector universe codes
        requested_codes = self._resolve_requested_sectors(request.sectors)

        # 3. Pre-fetch F&O eligible stock IDs knowable at as_of
        fno_eligible_stock_ids = await self._fetch_fno_eligible_stock_ids(as_of)

        # 4. Process sectors sequentially while constituents fan out with bounded concurrency
        semaphore = asyncio.Semaphore(SCREENING_MAX_WORKERS)
        sector_summaries: list[SectorEvidenceSummary] = []

        for code in requested_codes:
            summary = await self._evaluate_single_sector(
                sector_code=code,
                evaluation_date=eval_date,
                as_of_date=as_of,
                effective_cutoff=effective_cutoff,
                comparison_window=window,
                min_coverage_pct=request.min_data_coverage_pct,
                fno_eligible_stock_ids=fno_eligible_stock_ids,
                semaphore=semaphore,
            )
            sector_summaries.append(summary)

        # 5. Deterministic sort: net_directional_breadth_pct DESC, mean_convergence DESC, sector_name ASC
        sector_summaries.sort(
            key=lambda s: (
                -s.net_directional_breadth_pct,
                -(s.mean_convergence_ratio if s.mean_convergence_ratio is not None else -999.0),
                s.sector_name,
            )
        )

        # 6. Compute market-wide aggregate metrics across confirmed sectors
        confirmed_sectors = [
            s for s in sector_summaries if s.coverage_status == CoverageStatus.CONFIRMED
        ]
        confirmed_count = len(confirmed_sectors)

        if confirmed_count > 0:
            sectors_bullish_pct = round(
                len([s for s in confirmed_sectors if s.net_directional_breadth_pct > 0.0])
                / confirmed_count
                * 100.0,
                2,
            )
            sectors_bearish_pct = round(
                len([s for s in confirmed_sectors if s.net_directional_breadth_pct < 0.0])
                / confirmed_count
                * 100.0,
                2,
            )
            sectors_neutral_pct = round(
                len([s for s in confirmed_sectors if s.net_directional_breadth_pct == 0.0])
                / confirmed_count
                * 100.0,
                2,
            )
            conv_ratios = [
                s.mean_convergence_ratio
                for s in confirmed_sectors
                if s.mean_convergence_ratio is not None
            ]
            market_mean_convergence = (
                round(sum(conv_ratios) / len(conv_ratios), 4) if conv_ratios else None
            )
        else:
            sectors_bullish_pct = 0.0
            sectors_bearish_pct = 0.0
            sectors_neutral_pct = 0.0
            market_mean_convergence = None

        return SectorEvidenceBreadthResponse(
            request_echo=request,
            evaluation_date=eval_date,
            as_of_date=as_of,
            comparison_window=window,
            total_sectors_evaluated=len(sector_summaries),
            confirmed_sectors_count=confirmed_count,
            sectors_bullish_pct=sectors_bullish_pct,
            sectors_bearish_pct=sectors_bearish_pct,
            sectors_neutral_pct=sectors_neutral_pct,
            market_mean_convergence_ratio=market_mean_convergence,
            sectors=sector_summaries,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Single Sector Evaluation
    # ------------------------------------------------------------------

    async def _evaluate_single_sector(
        self,
        sector_code: str,
        evaluation_date: date,
        as_of_date: date | None,
        effective_cutoff: date,
        comparison_window: int,
        min_coverage_pct: float,
        fno_eligible_stock_ids: set[int],
        semaphore: asyncio.Semaphore,
    ) -> SectorEvidenceSummary:
        """Evaluate breadth and contradictions for one sector universe."""
        definition = SECTOR_DEFINITIONS_BY_CODE.get(
            sector_code,
            SectorDefinition(sector_code, sector_code, group="other", cyclicality=None),
        )

        # 1. Resolve constituents
        constituents, tier_used, is_unsupported_tax = await self._resolve_sector_constituents(
            sector_code=sector_code,
            as_of_date=as_of_date,
            effective_date=effective_cutoff,
        )

        logger.info(
            "Sector constituent resolution: sector=%s, total=%d, tier=%s, as_of=%s",
            sector_code,
            len(constituents),
            tier_used,
            as_of_date,
        )

        # Handle unsupported historical taxonomy failure
        if is_unsupported_tax:
            return SectorEvidenceSummary(
                sector_code=sector_code,
                sector_name=definition.name,
                category=definition.group,
                evaluation_date=evaluation_date,
                as_of_date=as_of_date,
                comparison_window=comparison_window,
                total_eligible_constituents=0,
                evaluated_constituents=0,
                analytical_valid_constituents=0,
                insufficient_evidence_count=0,
                no_data_count=0,
                error_count=0,
                data_coverage_pct=0.0,
                insufficient_evidence_pct=0.0,
                coverage_status=CoverageStatus.UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY,
                bullish_count=0,
                bearish_count=0,
                mixed_count=0,
                neutral_count=0,
                bullish_pct=0.0,
                bearish_pct=0.0,
                mixed_pct=0.0,
                neutral_pct=0.0,
                net_directional_breadth_pct=0.0,
                mean_convergence_ratio=None,
                median_convergence_ratio=None,
                volume_breakout_pct=0.0,
                delivery_breakout_pct=0.0,
                derivatives_eligible_count=0,
                derivatives_valid_count=0,
                derivatives_active_count=0,
                derivatives_active_pct=0.0,
                institutional_active_pct=0.0,
                contradictions=[],
                quality_warnings=[
                    f"Historical sector replay for {sector_code} requires Point-in-Time IndexMembership. "
                    "Fallback to unversioned Stock.sector_id is rejected to prevent survivorship bias."
                ],
            )

        total_eligible = len(constituents)
        if total_eligible == 0:
            return SectorEvidenceSummary(
                sector_code=sector_code,
                sector_name=definition.name,
                category=definition.group,
                evaluation_date=evaluation_date,
                as_of_date=as_of_date,
                comparison_window=comparison_window,
                total_eligible_constituents=0,
                evaluated_constituents=0,
                analytical_valid_constituents=0,
                insufficient_evidence_count=0,
                no_data_count=0,
                error_count=0,
                data_coverage_pct=0.0,
                insufficient_evidence_pct=0.0,
                coverage_status=CoverageStatus.INSUFFICIENT_DATA,
                bullish_count=0,
                bearish_count=0,
                mixed_count=0,
                neutral_count=0,
                bullish_pct=0.0,
                bearish_pct=0.0,
                mixed_pct=0.0,
                neutral_pct=0.0,
                net_directional_breadth_pct=0.0,
                mean_convergence_ratio=None,
                median_convergence_ratio=None,
                volume_breakout_pct=0.0,
                delivery_breakout_pct=0.0,
                derivatives_eligible_count=0,
                derivatives_valid_count=0,
                derivatives_active_count=0,
                derivatives_active_pct=0.0,
                institutional_active_pct=0.0,
                contradictions=[],
                quality_warnings=[f"No eligible constituents resolved for sector {sector_code}."],
            )

        # 2. Fan out constituent evaluations with bounded concurrency
        tasks = [
            self._evaluate_constituent(
                stock=stock,
                effective_eval_date=effective_cutoff,
                comparison_window=comparison_window,
                semaphore=semaphore,
            )
            for stock in constituents
        ]
        results: list[_ConstituentEvaluationResult] = list(
            await asyncio.gather(*tasks, return_exceptions=False)
        )

        # 3. Partition results into 5 analytical states + error / no_data
        no_data_count = len([r for r in results if r.status == "NO_DATA"])
        error_count = len([r for r in results if r.status == "ERROR"])
        evaluated_results = [r for r in results if r.status == "SUCCESS" and r.interpretation is not None]
        evaluated_count = len(evaluated_results)

        bullish_count = 0
        bearish_count = 0
        mixed_count = 0
        neutral_count = 0
        insufficient_evidence_count = 0

        convergence_ratios: list[float] = []
        vol_breakout_count = 0
        deliv_breakout_count = 0
        inst_count = 0

        derivatives_eligible_count = 0
        derivatives_valid_count = 0
        derivatives_active_count = 0

        for r in evaluated_results:
            interp = r.interpretation
            assert interp is not None

            # 5-state partitioning
            st = interp.overall_state
            if st in (OverallState.BULLISH_EVIDENCE_SUPPORTED, OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS):
                bullish_count += 1
            elif st in (OverallState.BEARISH_EVIDENCE_SUPPORTED, OverallState.PARTIALLY_SUPPORTED_BEARISH_BIAS):
                bearish_count += 1
            elif st == OverallState.MIXED_EVIDENCE:
                mixed_count += 1
            elif st in (OverallState.NEUTRAL_OBSERVATION, OverallState.CONTEXT_ONLY):
                neutral_count += 1
            elif st == OverallState.INSUFFICIENT_EVIDENCE:
                insufficient_evidence_count += 1

            # Convergence ratio
            if interp.convergence is not None and interp.convergence.convergence_ratio is not None:
                convergence_ratios.append(interp.convergence.convergence_ratio)

            # Domain participation
            all_evidence = (
                interp.primary_evidence
                + interp.supporting_evidence
                + interp.conflicting_evidence
                + interp.context_evidence
            )

            # Volume breakout check
            if self._has_volume_breakout(interp, all_evidence):
                vol_breakout_count += 1

            # Delivery breakout check
            if self._has_delivery_breakout(interp, all_evidence):
                deliv_breakout_count += 1

            # Institutional confirmation check
            if self._has_institutional_activity(interp, all_evidence):
                inst_count += 1

            # Derivatives participation check
            is_fno = r.stock_id in fno_eligible_stock_ids
            if is_fno:
                derivatives_eligible_count += 1
                # Was derivatives domain validly evaluated (not in missing_domains)?
                missing = interp.convergence.missing_domains if interp.convergence else []
                if "DERIVATIVES" not in missing:
                    derivatives_valid_count += 1
                    if self._has_derivatives_activity(interp, all_evidence):
                        derivatives_active_count += 1

        # 4. Canonical Denominators
        analytical_valid_count = bullish_count + bearish_count + mixed_count + neutral_count

        data_coverage_pct = round(
            (analytical_valid_count / total_eligible) * 100.0, 2
        )
        insufficient_evidence_pct = round(
            (insufficient_evidence_count / total_eligible) * 100.0, 2
        )

        # Coverage status determination
        quality_warnings: list[str] = []
        if data_coverage_pct >= 70.0:
            coverage_status = CoverageStatus.CONFIRMED
        elif data_coverage_pct >= min_coverage_pct:
            coverage_status = CoverageStatus.PARTIAL_DATA
            quality_warnings.append(
                f"Data coverage is partial ({data_coverage_pct:.1f}% < 70.0%). "
                "Some constituents had missing or incomplete price/domain history."
            )
        else:
            coverage_status = CoverageStatus.INSUFFICIENT_DATA
            quality_warnings.append(
                f"Data coverage is insufficient ({data_coverage_pct:.1f}% < {min_coverage_pct:.1f}%). "
                "Directional metrics have been suppressed to prevent misleading signals."
            )

        # Directional percentages computed strictly over N_ANALYTICAL_VALID
        if analytical_valid_count > 0 and coverage_status != CoverageStatus.INSUFFICIENT_DATA:
            bullish_pct = round((bullish_count / analytical_valid_count) * 100.0, 2)
            bearish_pct = round((bearish_count / analytical_valid_count) * 100.0, 2)
            mixed_pct = round((mixed_count / analytical_valid_count) * 100.0, 2)
            neutral_pct = round((neutral_count / analytical_valid_count) * 100.0, 2)
            net_breadth_pct = round(bullish_pct - bearish_pct, 2)
        else:
            bullish_pct = 0.0
            bearish_pct = 0.0
            mixed_pct = 0.0
            neutral_pct = 0.0
            net_breadth_pct = 0.0

        # Convergence statistics
        if convergence_ratios and coverage_status != CoverageStatus.INSUFFICIENT_DATA:
            mean_convergence = round(sum(convergence_ratios) / len(convergence_ratios), 4)
            median_convergence = round(statistics.median(convergence_ratios), 4)
        else:
            mean_convergence = None
            median_convergence = None

        # Domain participation percentages
        volume_breakout_pct = (
            round((vol_breakout_count / evaluated_count) * 100.0, 2)
            if evaluated_count > 0
            else 0.0
        )
        delivery_breakout_pct = (
            round((deliv_breakout_count / evaluated_count) * 100.0, 2)
            if evaluated_count > 0
            else 0.0
        )
        institutional_active_pct = (
            round((inst_count / evaluated_count) * 100.0, 2)
            if evaluated_count > 0
            else 0.0
        )
        derivatives_active_pct = (
            round((derivatives_active_count / derivatives_valid_count) * 100.0, 2)
            if derivatives_valid_count > 0
            else 0.0
        )

        # 5. Fetch sector absolute return and constituent returns for contradiction engine
        sector_absolute_return = await self._fetch_sector_index_return(
            sector_code=sector_code,
            effective_cutoff=effective_cutoff,
            window=comparison_window,
        )
        if sector_absolute_return is None:
            quality_warnings.append(
                f"Sector index price data not available for {sector_code}; price-based contradiction rules skipped."
            )

        # 6. Evaluate deterministic contradiction rules
        contradictions = await self._evaluate_contradictions(
            sector_return=sector_absolute_return,
            bearish_pct=bearish_pct,
            delivery_breakout_pct=delivery_breakout_pct,
            mixed_pct=mixed_pct,
            net_breadth_pct=net_breadth_pct,
            analytical_valid_count=analytical_valid_count,
            constituents=constituents,
            effective_cutoff=effective_cutoff,
            window=comparison_window,
        )

        logger.info(
            "Sector breadth evaluated: sector=%s, valid=%d, net_breadth=%.1f%%, mean_conv=%s, contradictions=%d",
            sector_code,
            analytical_valid_count,
            net_breadth_pct,
            mean_convergence,
            len(contradictions),
        )

        return SectorEvidenceSummary(
            sector_code=sector_code,
            sector_name=definition.name,
            category=definition.group,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
            comparison_window=comparison_window,
            total_eligible_constituents=total_eligible,
            evaluated_constituents=evaluated_count,
            analytical_valid_constituents=analytical_valid_count,
            insufficient_evidence_count=insufficient_evidence_count,
            no_data_count=no_data_count,
            error_count=error_count,
            data_coverage_pct=data_coverage_pct,
            insufficient_evidence_pct=insufficient_evidence_pct,
            coverage_status=coverage_status,
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            mixed_count=mixed_count,
            neutral_count=neutral_count,
            bullish_pct=bullish_pct,
            bearish_pct=bearish_pct,
            mixed_pct=mixed_pct,
            neutral_pct=neutral_pct,
            net_directional_breadth_pct=net_breadth_pct,
            mean_convergence_ratio=mean_convergence,
            median_convergence_ratio=median_convergence,
            volume_breakout_pct=volume_breakout_pct,
            delivery_breakout_pct=delivery_breakout_pct,
            derivatives_eligible_count=derivatives_eligible_count,
            derivatives_valid_count=derivatives_valid_count,
            derivatives_active_count=derivatives_active_count,
            derivatives_active_pct=derivatives_active_pct,
            institutional_active_pct=institutional_active_pct,
            contradictions=contradictions,
            quality_warnings=quality_warnings,
        )

    # ------------------------------------------------------------------
    # Worker Coroutine: Constituent Evaluation
    # ------------------------------------------------------------------

    async def _evaluate_constituent(
        self,
        stock: Stock,
        effective_eval_date: date,
        comparison_window: int,
        semaphore: asyncio.Semaphore,
    ) -> _ConstituentEvaluationResult:
        """Evaluate a single constituent using its own isolated AsyncSession."""
        async with semaphore:
            try:
                async with self._session_factory() as worker_session:
                    d3_svc = WhatChangedInterpretationService(worker_session)
                    interpretation = await d3_svc.interpret(
                        symbol=stock.symbol,
                        evaluation_date=effective_eval_date,
                        comparison_window=comparison_window,
                        include_ai_narrative=False,
                    )
                    return _ConstituentEvaluationResult(
                        symbol=stock.symbol,
                        stock_id=stock.id,
                        company_name=getattr(stock, "company_name", None),
                        status="SUCCESS",
                        interpretation=interpretation,
                    )
            except ValueError as e:
                logger.debug("Constituent %s evaluation no data: %s", stock.symbol, e)
                return _ConstituentEvaluationResult(
                    symbol=stock.symbol,
                    stock_id=stock.id,
                    company_name=getattr(stock, "company_name", None),
                    status="NO_DATA",
                    error_message=str(e),
                )
            except Exception as e:
                logger.warning("Constituent %s evaluation error: %s", stock.symbol, e)
                return _ConstituentEvaluationResult(
                    symbol=stock.symbol,
                    stock_id=stock.id,
                    company_name=getattr(stock, "company_name", None),
                    status="ERROR",
                    error_message=str(e),
                )

    # ------------------------------------------------------------------
    # Constituent Resolution & PIT
    # ------------------------------------------------------------------

    async def _resolve_sector_constituents(
        self,
        sector_code: str,
        as_of_date: date | None,
        effective_date: date,
    ) -> tuple[list[Stock], str, bool]:
        """Resolve constituents for a sector universe.

        Returns (stocks, tier_description, is_unsupported_historical_taxonomy).
        """
        # Look up MarketIndex by code
        idx_res = await self._session.execute(
            select(MarketIndex.id).where(MarketIndex.code == sector_code)
        )
        index_id = idx_res.scalar_one_or_none()

        # Historical Mode
        if as_of_date is not None:
            if index_id is None:
                # No MarketIndex found for sector code historically
                return [], "NONE", True

            # Query IndexMembership via HistoricalQueryService or direct PIT query
            hist_svc = HistoricalQueryService(self._session)
            memberships = await hist_svc.get_index_members(index_id, as_of_date)
            if not memberships:
                # No PIT index memberships exist for this historical date
                return [], "NONE", True

            stock_ids = [m.stock_id for m in memberships]
            stocks_res = await self._session.execute(
                select(Stock).where(Stock.id.in_(stock_ids)).order_by(Stock.symbol)
            )
            stocks = list(stocks_res.scalars().all())
            return stocks, "TIER_1_INDEX_MEMBERSHIP", False

        # Live Mode (as_of_date is None)
        if index_id is not None:
            # Check current memberships (valid_to is null or valid_to >= today)
            today = date.today()
            stmt = (
                select(IndexMembership.stock_id)
                .where(IndexMembership.index_id == index_id)
                .where(
                    or_(
                        IndexMembership.valid_to.is_(None),
                        IndexMembership.valid_to >= today,
                    )
                )
            )
            res = await self._session.execute(stmt)
            stock_ids = [row[0] for row in res.all()]
            if stock_ids:
                stocks_res = await self._session.execute(
                    select(Stock).where(Stock.id.in_(stock_ids)).order_by(Stock.symbol)
                )
                return list(stocks_res.scalars().all()), "LIVE_INDEX_MEMBERSHIP", False

        # Live Taxonomy Fallback via Stock.sector_id
        keywords = _SECTOR_CODE_TO_DB_NAME_KEYWORDS.get(sector_code, ())
        sector_clauses = [Sector.name.ilike(f"%{kw}%") for kw in keywords]
        if not sector_clauses:
            return [], "NONE", False

        sec_res = await self._session.execute(
            select(Sector.id).where(or_(*sector_clauses))
        )
        sector_ids = [row[0] for row in sec_res.all()]
        if not sector_ids:
            return [], "NONE", False

        # Query active stocks belonging to these sectors with recent price data
        stocks_res = await self._session.execute(
            select(Stock)
            .where(Stock.sector_id.in_(sector_ids))
            .where(Stock.is_active.is_(True))
            .order_by(Stock.symbol)
        )
        stocks = list(stocks_res.scalars().all())
        return stocks, "LIVE_TAXONOMY_FALLBACK", False

    # ------------------------------------------------------------------
    # Domain Participation Checks
    # ------------------------------------------------------------------

    def _has_volume_breakout(self, interp: StockIntelligenceInterpretation, all_evidence: list[Any]) -> bool:
        if interp.convergence and "PARTICIPATION" in interp.convergence.supporting_domains:
            # Verify volume involvement
            for e in interp.supporting_evidence:
                if any("VOLUME" in str(m).upper() for m in e.metrics_involved) or "volume" in e.reason.lower():
                    return True
        for e in all_evidence:
            dom = getattr(e, "domain", "").upper()
            doms_inv = [str(d).upper() for d in getattr(e, "domains_involved", [])]
            mets_inv = [str(m).upper() for m in getattr(e, "metrics_involved", [])]
            meta = getattr(e, "metadata", {}) or {}
            reason = getattr(e, "reason", "").lower()
            str_val = getattr(e, "strength", None)

            is_vol = dom in ("VOLUME", "PARTICIPATION") or any("VOLUME" in d for d in doms_inv) or any("VOLUME" in m for m in mets_inv) or "volume" in reason
            if is_vol:
                if (
                    str_val in (RelationshipStrength.MAJOR, RelationshipStrength.SIGNIFICANT)
                    or meta.get("significance", 0) >= 70.0
                    or meta.get("volume_surge", False)
                    or "volume" in reason
                ):
                    return True
        return False

    def _has_delivery_breakout(self, interp: StockIntelligenceInterpretation, all_evidence: list[Any]) -> bool:
        for e in all_evidence:
            dom = getattr(e, "domain", "").upper()
            doms_inv = [str(d).upper() for d in getattr(e, "domains_involved", [])]
            mets_inv = [str(m).upper() for m in getattr(e, "metrics_involved", [])]
            meta = getattr(e, "metadata", {}) or {}
            reason = getattr(e, "reason", "").lower()
            str_val = getattr(e, "strength", None)

            is_deliv = "DELIVERY" in dom or any("DELIVERY" in d for d in doms_inv) or any("DELIVERY" in m for m in mets_inv) or "delivery" in reason
            if is_deliv:
                if (
                    str_val in (RelationshipStrength.MAJOR, RelationshipStrength.SIGNIFICANT)
                    or meta.get("significance", 0) >= 70.0
                    or meta.get("delivery_surge", False)
                    or "delivery" in reason
                ):
                    return True
        return False

    def _has_derivatives_activity(self, interp: StockIntelligenceInterpretation, all_evidence: list[Any]) -> bool:
        if interp.convergence:
            if "DERIVATIVES" in interp.convergence.supporting_domains or "DERIVATIVES" in interp.convergence.conflicting_domains:
                return True
        for e in all_evidence:
            dom = getattr(e, "domain", "").upper()
            doms_inv = [str(d).upper() for d in getattr(e, "domains_involved", [])]
            mets_inv = [str(m).upper() for m in getattr(e, "metrics_involved", [])]
            if dom == "DERIVATIVES" or any("DERIVATIVE" in d for d in doms_inv) or any("OI" in m for m in mets_inv):
                return True
        return False

    def _has_institutional_activity(self, interp: StockIntelligenceInterpretation, all_evidence: list[Any]) -> bool:
        if interp.convergence:
            ctx = [str(c).upper() for c in interp.convergence.context_domains]
            if any(c in ("INSTITUTIONAL", "DEAL", "CORPORATE") for c in ctx):
                return True
        for e in all_evidence:
            dom = getattr(e, "domain", "").upper()
            doms_inv = [str(d).upper() for d in getattr(e, "domains_involved", [])]
            if dom in ("INSTITUTIONAL", "DEAL", "CORPORATE") or any(d in ("INSTITUTIONAL", "DEAL", "CORPORATE") for d in doms_inv):
                return True
        return False

    # ------------------------------------------------------------------
    # Contradiction Engine & Sector Returns
    # ------------------------------------------------------------------

    async def _fetch_sector_index_return(
        self,
        sector_code: str,
        effective_cutoff: date,
        window: int,
    ) -> float | None:
        """Calculate sector index absolute return over window w."""
        idx_res = await self._session.execute(
            select(MarketIndex.id).where(MarketIndex.code == sector_code)
        )
        index_id = idx_res.scalar_one_or_none()
        if index_id is None:
            return None

        # Fetch up to window + 2 prices on or before effective_cutoff
        # NOTE (P1 F-01 remediation): the model column is `close`, not `close_price`
        # (app/models/market_index_price.py). Referencing the wrong attribute raises
        # AttributeError at query-construction time and crashed every live sector
        # evaluation with HTTP 500 (independent audit F-01).
        stmt = (
            select(MarketIndexPrice.close, MarketIndexPrice.trade_date)
            .where(MarketIndexPrice.market_index_id == index_id)
            .where(MarketIndexPrice.trade_date <= effective_cutoff)
            .order_by(MarketIndexPrice.trade_date.desc())
            .limit(window + 1)
        )
        res = await self._session.execute(stmt)
        prices = list(res.all())
        if len(prices) < 2:
            return None

        current_close = float(prices[0][0])
        base_close = float(prices[-1][0])
        if base_close <= 0:
            return None

        return round(((current_close - base_close) / base_close) * 100.0, 2)

    async def _evaluate_contradictions(
        self,
        sector_return: float | None,
        bearish_pct: float,
        delivery_breakout_pct: float,
        mixed_pct: float,
        net_breadth_pct: float,
        analytical_valid_count: int,
        constituents: list[Stock],
        effective_cutoff: date,
        window: int,
    ) -> list[SectorContradictionDetail]:
        """Evaluate the 4 deterministic contradiction rules."""
        contradictions: list[SectorContradictionDetail] = []

        # Rule 1: Price-Breadth Divergence
        if sector_return is not None and sector_return >= 0.5:
            if bearish_pct >= 50.0:
                contradictions.append(
                    SectorContradictionDetail(
                        flag=SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION,
                        description=(
                            f"Sector index absolute return is +{sector_return:.2f}% "
                            f"despite {bearish_pct:.1f}% of constituents displaying bearish evidence (>= 50.0%)."
                        ),
                    )
                )

            # Rule 2: Delivery Confirmation Deficit
            if delivery_breakout_pct <= 10.0:
                contradictions.append(
                    SectorContradictionDetail(
                        flag=SectorContradictionFlag.DELIVERY_CONFIRMATION_DEFICIT,
                        description=(
                            f"Sector index absolute return is +{sector_return:.2f}% "
                            f"with only {delivery_breakout_pct:.1f}% delivery breakout participation (<= 10.0%)."
                        ),
                    )
                )

            # Rule 3: High Concentration Risk (Equal-weight proxy)
            if analytical_valid_count >= 5 and net_breadth_pct <= 0.0:
                is_concentrated, c_sum = await self._check_concentration_risk(
                    constituents=constituents,
                    effective_cutoff=effective_cutoff,
                    window=window,
                )
                if is_concentrated:
                    contradictions.append(
                        SectorContradictionDetail(
                            flag=SectorContradictionFlag.HIGH_CONCENTRATION_RISK,
                            description=(
                                f"Sector index absolute return is +{sector_return:.2f}% but gains are heavily concentrated: "
                                f"top 2 contributors account for {c_sum * 100.0:.1f}% of positive returns "
                                f"while net breadth is {net_breadth_pct:.1f}% (<= 0.0%)."
                            ),
                        )
                    )

        # Rule 4: Multi-Domain Sector Conflict
        if mixed_pct >= 40.0:
            contradictions.append(
                SectorContradictionDetail(
                    flag=SectorContradictionFlag.SECTOR_EVIDENCE_CONFLICTED,
                    description=(
                        f"{mixed_pct:.1f}% of sector constituents exhibit conflicted multi-domain evidence (>= 40.0%)."
                    ),
                )
            )

        return contradictions

    async def _check_concentration_risk(
        self,
        constituents: list[Stock],
        effective_cutoff: date,
        window: int,
    ) -> tuple[bool, float]:
        """Check equal-weight constituent return concentration proxy."""
        stock_ids = [s.id for s in constituents]
        if not stock_ids:
            return False, 0.0

        # Pre-fetch prices for all constituents around window
        lookback_days = max(10, window * 4 + 10)
        start_date = effective_cutoff - timedelta(days=lookback_days)

        stmt = (
            select(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close)
            .where(DailyPrice.stock_id.in_(stock_ids))
            .where(DailyPrice.trade_date >= start_date)
            .where(DailyPrice.trade_date <= effective_cutoff)
            .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        )
        res = await self._session.execute(stmt)
        rows = res.all()

        # Group by stock_id
        prices_by_stock: dict[int, list[float]] = {}
        for s_id, _t_date, c_price in rows:
            prices_by_stock.setdefault(s_id, []).append(float(c_price))

        positive_returns: list[float] = []
        for p_list in prices_by_stock.values():
            if len(p_list) >= window + 1:
                cur = p_list[0]
                base = p_list[window]
                if base > 0:
                    ret = (cur - base) / base
                    if ret > 0.0:
                        positive_returns.append(ret)

        if len(positive_returns) < 2:
            return False, 0.0

        sigma_pos = sum(positive_returns)
        if sigma_pos <= 0.0:
            return False, 0.0

        contributions = [r / sigma_pos for r in positive_returns]
        contributions.sort(reverse=True)
        top2_sum = contributions[0] + contributions[1]

        if top2_sum >= 0.80:
            return True, round(top2_sum, 4)

        return False, round(top2_sum, 4)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_requested_sectors(self, requested: list[str]) -> list[str]:
        """Validate and resolve requested sector codes."""
        if not requested or "ALL_SECTORS" in requested or requested == ["ALL_SECTORS"]:
            return list(SECTOR_INDEX_CODES)

        result: list[str] = []
        valid_set = set(SECTOR_INDEX_CODES)
        for r in requested:
            code = r.strip().upper()
            if code == "ALL_SECTORS":
                return list(SECTOR_INDEX_CODES)
            if code not in valid_set:
                raise ValueError(
                    f"Unsupported sector code {r!r}. Must be one of {sorted(valid_set)} or 'ALL_SECTORS'."
                )
            if code not in result:
                result.append(code)
        return result

    async def _fetch_fno_eligible_stock_ids(self, as_of_date: date | None) -> set[int]:
        """Fetch set of stock IDs with active F&O contracts at as_of."""
        stmt = select(DerivativeContract.underlying_stock_id).where(
            DerivativeContract.underlying_stock_id.is_not(None)
        )
        if as_of_date is not None:
            stmt = stmt.where(DerivativeContract.available_at <= as_of_date)
        res = await self._session.execute(stmt.distinct())
        return {row[0] for row in res.all()}
