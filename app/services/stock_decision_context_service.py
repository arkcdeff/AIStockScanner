"""Stock Decision Context Fusion Service — G.11-D-5 Decision Context Fusion Layer.

Fuses:
  1. Certified G.11-D-3 Stock Intelligence Interpretation (and transitively D-2/D-1)
  2. Stock 360° PIT-safe 10-dimensional research snapshot (technicals, volume, RS, fundamentals, derivatives, news)
  3. Canonical Opportunity Profiles (BREAKOUT, QUALITY_PULLBACK, SWING, INVESTMENT_QUALITY, BTST_READINESS)

Strict invariants:
  - 100% deterministic fusion; zero trade execution, zero speculative recommendations.
  - Zero recalculation of D-1, D-2, or D-3 logic; directly consumes canonical services.
  - Strict Point-in-Time safety; enforces evaluation_date and as_of_date cutoffs.
  - AI narrative is strictly optional, bounded, and fails closed to deterministic fallback.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.schemas.stock360 import StockOpportunityAssessment
from app.schemas.stock_decision_context import (
    ProfileAlignmentState,
    ProfileEvidenceAlignment,
    StockDecisionContextRead,
)
from app.services.opportunity_engine import OpportunityEngine
from app.services.stock360_service import Stock360Service
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    OverallState,
)
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)

CANONICAL_PROFILES: tuple[str, ...] = (
    "INVESTMENT_QUALITY",
    "QUALITY_PULLBACK",
    "BREAKOUT",
    "SWING",
    "BTST_READINESS",
)


def _dump_model(model_obj: Any) -> dict[str, Any]:
    """Safely convert a Pydantic model or object to a JSON-compatible dict."""
    if model_obj is None:
        return {}
    if hasattr(model_obj, "model_dump"):
        return model_obj.model_dump(mode="json")
    if hasattr(model_obj, "__dict__"):
        return {k: v for k, v in model_obj.__dict__.items() if not k.startswith("_")}
    if isinstance(model_obj, dict):
        return model_obj
    return {}


class StockDecisionContextFusionService:
    """Orchestrates single-stock research dossiers by fusing D-3 and Stock 360."""

    def __init__(
        self,
        session: AsyncSession,
        interpretation_service: WhatChangedInterpretationService | None = None,
        stock360_service: Stock360Service | None = None,
    ) -> None:
        self.session = session
        self.interpretation_service = interpretation_service or WhatChangedInterpretationService(session)
        self.stock360_service = stock360_service or Stock360Service(session)

    async def get_decision_context(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        as_of_date: date | None = None,
        comparison_window: int = 1,
        include_ai: bool = False,
    ) -> StockDecisionContextRead:
        """Produce deterministic Stock 360 & Decision Context Fusion dossier."""
        clean_symbol = symbol.upper().strip()

        # Parameter validation
        if comparison_window < 1 or comparison_window > 30:
            raise ValueError(f"comparison_window must be between 1 and 30, got {comparison_window}")

        # Validate stock exists
        stmt_stock = select(Stock).where(Stock.symbol == clean_symbol)
        stock_row = (await self.session.execute(stmt_stock)).scalar_one_or_none()
        if not stock_row:
            raise ValueError(f"Stock symbol '{symbol}' not found")

        # Determine effective dates
        eff_eval_date = evaluation_date or as_of_date
        eff_as_of = as_of_date or eff_eval_date or date.today()

        # 1. Fetch certified G.11-D-3 interpretation
        interpretation = await self.interpretation_service.interpret(
            symbol=clean_symbol,
            evaluation_date=eff_eval_date,
            comparison_window=comparison_window,
            include_ai_narrative=include_ai,
        )

        resolved_eval_date = interpretation.evaluation_date
        resolved_comp_date = interpretation.comparison_date

        # 2. Fetch PIT-safe Stock 360 Snapshot
        snapshot = await self.stock360_service.get_stock360_snapshot(
            symbol=clean_symbol,
            evaluation_date=resolved_eval_date,
            as_of_date=eff_as_of,
        )

        # 3. Evaluate Canonical Opportunity Profiles & Align Evidence
        profile_alignments: list[ProfileEvidenceAlignment] = []
        all_vetoes: list[str] = []

        for prof in CANONICAL_PROFILES:
            try:
                assessment = OpportunityEngine.evaluate_profile(snapshot, prof)
                alignment = self._evaluate_profile_alignment(assessment, interpretation, snapshot)
                profile_alignments.append(alignment)
                if alignment.veto_reasons:
                    for v in alignment.veto_reasons:
                        if v not in all_vetoes:
                            all_vetoes.append(v)
            except Exception as e:
                profile_alignments.append(
                    ProfileEvidenceAlignment(
                        profile=prof,
                        alignment_state=ProfileAlignmentState.INSUFFICIENT_DATA,
                        canonical_classification="ERROR",
                        canonical_score=0.0,
                        supporting_evidence=[],
                        conflicting_evidence=[f"Evaluation error: {e}"],
                        missing_evidence=[],
                        veto_reasons=[],
                        alignment_summary=f"Profile {prof} could not be evaluated due to insufficient inputs.",
                    )
                )

        # 4. Extract Convergence & Domains
        conv = getattr(interpretation, "convergence", None) or getattr(interpretation, "directional_convergence", None)
        if conv:
            convergence_ratio = getattr(conv, "convergence_ratio", None)
            if convergence_ratio is None:
                convergence_ratio = getattr(conv, "ratio", None)
            supporting_domains = list(conv.supporting_domains)
            conflicting_domains = list(conv.conflicting_domains)
            context_domains = list(conv.context_domains)
            missing_domains = list(conv.missing_domains)
        else:
            convergence_ratio = None
            supporting_domains = []
            conflicting_domains = []
            context_domains = []
            missing_domains = []

        if (
            interpretation.overall_state == OverallState.MIXED_EVIDENCE
            and len(conflicting_domains) > 0
        ):
            high_risk_veto = "D-3: Multi-domain contradiction detected across core indicators"
            if high_risk_veto not in all_vetoes:
                all_vetoes.append(high_risk_veto)

        lifecycle = getattr(interpretation, "overall_lifecycle", None) or getattr(interpretation, "overall_lifecycle_summary", None)
        lifecycle_summary = (
            lifecycle.description
            if lifecycle
            else None
        )

        # 5. Assemble and Return Dossier
        return StockDecisionContextRead(
            symbol=clean_symbol,
            company_name=snapshot.company_name,
            sector=snapshot.sector_name,
            sector_name=snapshot.sector_name,
            evaluation_date=resolved_eval_date,
            as_of_date=eff_as_of,
            comparison_date=resolved_comp_date,
            comparison_window=comparison_window,
            comparison_window_sessions=comparison_window,
            pit_safe=True,
            overall_state=interpretation.overall_state,
            directional_bias=interpretation.directional_bias,
            convergence_ratio=convergence_ratio,
            supporting_domains=supporting_domains,
            conflicting_domains=conflicting_domains,
            context_domains=context_domains,
            missing_domains=missing_domains,
            lifecycle_summary=lifecycle_summary,
            pattern_lifecycle_records=interpretation.pattern_lifecycle_records,
            primary_evidence=interpretation.primary_evidence,
            supporting_evidence=interpretation.supporting_evidence,
            conflicting_evidence=interpretation.conflicting_evidence,
            context_evidence=interpretation.context_evidence,
            technicals=snapshot.technicals,
            volume_delivery=snapshot.volume_delivery,
            relative_strength=_dump_model(snapshot.relative_strength),
            fundamentals=_dump_model(snapshot.fundamentals),
            derivatives=snapshot.derivatives_summary,
            news_policy=_dump_model(snapshot.news_policy),
            data_coverage=snapshot.data_coverage,
            data_completeness_pct=snapshot.data_completeness_pct,
            quality_warnings=snapshot.quality_warnings,
            profile_alignments=profile_alignments,
            active_vetoes=all_vetoes,
            watch_items=interpretation.watch_items,
            limitations=interpretation.limitations,
            narrative=interpretation.narrative,
            fallback_narrative=interpretation.fallback_narrative,
            generated_at=datetime.now(UTC),
        )

    def _evaluate_profile_alignment(
        self,
        assessment: StockOpportunityAssessment,
        interpretation: Any,
        snapshot: Any,
    ) -> ProfileEvidenceAlignment:
        """Deterministically cross-evaluate canonical profile setup against D-3 dynamic intelligence."""
        prof = assessment.profile
        score = float(assessment.score)
        classification = assessment.classification
        vetoes = list(assessment.veto_reasons)

        supporting_evidence = list(assessment.supporting_evidence)
        conflicting_evidence = list(assessment.contradicting_evidence)
        missing_evidence: list[str] = []

        conv = getattr(interpretation, "convergence", None) or getattr(interpretation, "directional_convergence", None)
        missing_domains = getattr(conv, "missing_domains", []) if conv else []
        conflicting_domains = getattr(conv, "conflicting_domains", []) if conv else []

        # Check missing domains from D-3
        for d in missing_domains:
            missing_evidence.append(f"Missing domain data: {d}")

        # Check data completeness
        if snapshot.data_completeness_pct < 50.0 or interpretation.overall_state == OverallState.INSUFFICIENT_EVIDENCE:
            alignment_state = ProfileAlignmentState.INSUFFICIENT_DATA
            summary = f"{prof} data completeness ({snapshot.data_completeness_pct:.1f}%) is insufficient for definitive alignment."
        elif vetoes:
            alignment_state = ProfileAlignmentState.BLOCKED
            summary = f"{prof} setup blocked by active canonical veto: {', '.join(vetoes)}."
        elif interpretation.overall_state == OverallState.MIXED_EVIDENCE and len(conflicting_domains) > 0 and score < 60.0:
            alignment_state = ProfileAlignmentState.BLOCKED
            vetoes.append("Multi-domain contradiction flagged by D-3 dynamic intelligence")
            summary = f"{prof} blocked due to multi-domain evidence contradictions."
        elif interpretation.overall_state in (OverallState.BEARISH_EVIDENCE_SUPPORTED, OverallState.PARTIALLY_SUPPORTED_BEARISH_BIAS):
            alignment_state = ProfileAlignmentState.BLOCKED
            conflicting_evidence.append("Multi-domain evidence confirms bearish trend; long setup contradicted.")
            summary = f"{prof} blocked by multi-domain bearish evidence divergence."
        elif classification in ("STRONG_RESEARCH_CANDIDATE", "RESEARCH_CANDIDATE") or score >= 60.0:
            if interpretation.overall_state in (OverallState.BULLISH_EVIDENCE_SUPPORTED, OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS) and interpretation.directional_bias == DirectionalBias.BULLISH_EVIDENCE:
                c_ratio = getattr(conv, "convergence_ratio", None) if conv else None
                if c_ratio is None and conv:
                    c_ratio = getattr(conv, "ratio", 1.0)
                if c_ratio is None:
                    c_ratio = 1.0
                supporting_evidence.append(f"D-3 confirms bullish multi-domain convergence (ratio: {c_ratio:.2f}).")
                alignment_state = ProfileAlignmentState.ALIGNED
                summary = f"{prof} setup aligns strongly with multi-domain bullish convergence."
            elif interpretation.overall_state in (OverallState.NEUTRAL_OBSERVATION, OverallState.CONTEXT_ONLY, OverallState.MIXED_EVIDENCE):
                alignment_state = ProfileAlignmentState.PARTIALLY_ALIGNED
                summary = f"{prof} criteria met (score: {score:.1f}), but dynamic change evidence is {interpretation.overall_state.value}."
            else:
                alignment_state = ProfileAlignmentState.PARTIALLY_ALIGNED
                summary = f"{prof} criteria met (score: {score:.1f}) with neutral/mixed multi-domain participation."
        elif classification == "WATCH" or (40.0 <= score < 60.0):
            alignment_state = ProfileAlignmentState.PARTIALLY_ALIGNED
            summary = f"{prof} shows nascent setup elements (score: {score:.1f}), pending further technical/volume confirmation."
        elif classification in ("REJECTED", "NO_TRADE") and prof == "BTST_READINESS" and not snapshot.is_fno:
            alignment_state = ProfileAlignmentState.NOT_APPLICABLE
            summary = f"{prof} is not applicable for non-F&O cash-only securities."
        elif classification in ("REJECTED", "NO_TRADE"):
            alignment_state = ProfileAlignmentState.BLOCKED
            summary = f"{prof} criteria not satisfied by underlying metrics (classification: {classification})."
        else:
            alignment_state = ProfileAlignmentState.PARTIALLY_ALIGNED
            summary = f"{prof} evaluated with score {score:.1f} and classification {classification}."

        return ProfileEvidenceAlignment(
            profile=prof,
            alignment_state=alignment_state,
            canonical_classification=classification,
            canonical_score=round(score, 1),
            supporting_evidence=supporting_evidence,
            conflicting_evidence=conflicting_evidence,
            missing_evidence=missing_evidence,
            veto_reasons=vetoes,
            alignment_summary=summary,
        )
