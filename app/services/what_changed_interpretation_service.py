"""What Changed Interpretation Service — G.11-D-3 Stock Intelligence Interpretation Engine.

Deterministic interpretation of the certified G.11-D-2 ``CrossDomainSynthesisResult``:

    Evidence (D-1/D-2) → Interpretation (D-3) → Narrative (optional AI)

Hard rules enforced by this module:

* D-3 consumes ONLY ``CrossDomainSynthesisResult`` — it never calls D-1 and
  never recalculates any market evidence (RSI, EMA, price/volume changes, OI…).
* DIVERGENCE is always CONFLICTING evidence — never PRIMARY.
* Context domains (CORPORATE, INSTITUTIONAL, DEAL) never contribute to
  directional convergence and never establish direction or intent.
* D-2's ``max_possible`` may include context-only domains; D-3 exposes a
  corrected directional-only convergence representation.
* Lifecycle is computed per pattern by comparing the current (T) and previous
  (T-1) D-2 snapshots. A missing previous snapshot deterministically yields
  NEW (no-history state) — never invented history.
* Missing data is never treated as negative evidence.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.what_changed_interpretation import (
    DirectionalConvergence,
    EvidenceItem,
    Limitation,
    NarrativeResult,
    OverallLifecycleSummary,
    PatternLifecycleRecord,
    StockIntelligenceInterpretation,
    WatchItem,
)
from app.schemas.what_changed_synthesis import CrossDomainSynthesisResult
from app.services.what_changed_interpretation_config import (
    CONTEXT_ONLY_DOMAINS,
    DOMAIN_LIMITATION_TEXT,
    DOMAIN_WATCH_MAP,
    INSTITUTIONAL_LIMITATION,
    METHOD_AI_ASSISTED,
    METHOD_DETERMINISTIC,
    MUTED_PRICE_PATTERN,
    NO_HISTORY_LIMITATION,
    PRICE_DIRECTION_METADATA_KEYS,
    STRENGTH_RANK,
    DirectionalBias,
    EvidenceClassification,
    LifecycleChangeDescriptor,
    OverallState,
)
from app.services.what_changed_synthesis_config import (
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)
from app.services.what_changed_synthesis_service import WhatChangedSynthesisService

_DIRECTIONAL_DIRECTIONS = ("BULLISH", "BEARISH")

_WATCH_DESCRIPTIONS: dict[str, str] = {
    "PRICE_CONFIRMATION_REQUIRED": (
        "A meaningful directional price move would provide directional evidence "
        "for the current observation."
    ),
    "VOLUME_CONFIRMATION_REQUIRED": (
        "Volume or delivery participation data would clarify whether the price "
        "move was supported by participation."
    ),
    "TREND_ALIGNMENT_REQUIRED": (
        "Trend indicator data would clarify alignment between price and trend structure."
    ),
    "DERIVATIVE_DATA_REQUIRED": (
        "Derivatives open-interest data would clarify participation in the derivatives market."
    ),
    "SECTOR_CONFIRMATION_REQUIRED": (
        "Sector index data would clarify whether the move was sector-wide or stock-specific."
    ),
    "CONFLICT_RESOLUTION_REQUIRED": (
        "Additional or updated evidence would help resolve the currently conflicting "
        "domain signals."
    ),
}


class WhatChangedInterpretationService:
    """Deterministic G.11-D-3 interpretation engine (no AI required)."""

    def __init__(
        self,
        session: AsyncSession,
        synthesis_service: WhatChangedSynthesisService | None = None,
        narrative_service: Any | None = None,
    ) -> None:
        self.session = session
        self.synthesis_service = synthesis_service or WhatChangedSynthesisService(session)
        self._injected_narrative_service = narrative_service

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def interpret(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        comparison_window: int = 1,
        *,
        current_synthesis: CrossDomainSynthesisResult | None = None,
        previous_synthesis: CrossDomainSynthesisResult | None = None,
        include_ai_narrative: bool | None = None,
    ) -> StockIntelligenceInterpretation:
        """Produce the deterministic stock intelligence interpretation.

        Args:
            symbol: Stock symbol (case-insensitive).
            evaluation_date: Evaluation date (defaults to latest available).
            comparison_window: D-2 comparison window in trading sessions.
            current_synthesis: Optionally inject a pre-computed current D-2
                result (cache-compatible; avoids recomputation).
            previous_synthesis: Optionally inject a pre-computed previous D-2
                snapshot (T-1). When omitted it is computed via the certified
                D-2 service at ``current.comparison_date``.
            include_ai_narrative: When ``None`` follows ``settings.ai_enabled``.
        """
        clean_symbol = symbol.upper().strip()

        current = current_synthesis
        if current is None:
            current = await self.synthesis_service.synthesize(
                symbol=clean_symbol,
                evaluation_date=evaluation_date,
                comparison_window=comparison_window,
            )

        previous, history_available = await self._resolve_previous_synthesis(
            clean_symbol, current, previous_synthesis
        )

        price_direction = self._derive_price_direction(current)

        # 1. Evidence classification
        primary, supporting, conflicting, context, missing = self._classify_relationships(
            current, price_direction
        )

        # 2. Corrected directional convergence
        convergence = self._build_convergence(current)

        # 3. Deterministic state machine
        has_context = bool(context) or bool(current.context_domains)
        overall_state, directional_bias = self._determine_state(
            supporting_domains=convergence.supporting_domains,
            conflicting_domains=convergence.conflicting_domains,
            price_direction=price_direction,
            has_context=has_context,
            has_relationships=bool(current.relationships),
        )

        # 4. Lifecycle (per-pattern + aggregate)
        pattern_records, overall_lifecycle = self._build_lifecycle(current, previous)

        # 5. Watch items & limitations
        watch_items = self._build_watch_items(
            convergence=convergence,
            missing_domains=convergence.missing_domains,
            has_directional=bool(
                convergence.supporting_domains or convergence.conflicting_domains
            ),
            has_context=has_context,
        )
        limitations = self._build_limitations(
            missing_domains=convergence.missing_domains,
            context_domains=sorted(current.context_domains),
            history_available=history_available,
        )

        # 6. Provenance (from D-2 relationships + the D-1 layer)
        provenance = self._build_provenance(current)

        # 7. Deterministic fallback narrative (always available)
        fallback_narrative = self._build_fallback_narrative(
            symbol=clean_symbol,
            d2=current,
            overall_state=overall_state,
            directional_bias=directional_bias,
            convergence=convergence,
            context_evidence=context,
            missing_evidence=missing,
            watch_items=watch_items,
        )

        # 8. Optional AI narrative (never authoritative)
        narrative: NarrativeResult | None = None
        method = METHOD_DETERMINISTIC
        include_ai = (
            settings.ai_enabled if include_ai_narrative is None else include_ai_narrative
        )
        if include_ai:
            facts = self._build_ai_facts(
                symbol=clean_symbol,
                d2=current,
                overall_state=overall_state,
                directional_bias=directional_bias,
                convergence=convergence,
                primary=primary,
                supporting=supporting,
                conflicting=conflicting,
                context=context,
                missing=missing,
                overall_lifecycle=overall_lifecycle,
                pattern_records=pattern_records,
                watch_items=watch_items,
                limitations=limitations,
            )
            narrative = await self._generate_narrative(facts)
            if narrative is not None:
                method = METHOD_AI_ASSISTED

        pit_safe = bool(current.pit_safe) and (
            bool(previous.pit_safe) if previous is not None else True
        )

        return StockIntelligenceInterpretation(
            symbol=clean_symbol,
            evaluation_date=current.evaluation_date,
            comparison_date=current.comparison_date,
            generated_at=datetime.now(UTC),
            overall_state=overall_state,
            directional_bias=directional_bias,
            primary_evidence=primary,
            supporting_evidence=supporting,
            conflicting_evidence=conflicting,
            context_evidence=context,
            missing_evidence=missing,
            convergence=convergence,
            overall_lifecycle=overall_lifecycle,
            pattern_lifecycle_records=pattern_records,
            watch_items=watch_items,
            limitations=limitations,
            narrative=narrative,
            fallback_narrative=fallback_narrative,
            interpretation_method=method,
            evidence_quality=current.evidence_quality,
            pit_safe=pit_safe,
            provenance=provenance,
        )

    # -------------------------------------------------------------------
    # Previous-snapshot resolution (T-1)
    # -------------------------------------------------------------------

    async def _resolve_previous_synthesis(
        self,
        symbol: str,
        current: CrossDomainSynthesisResult,
        previous_synthesis: CrossDomainSynthesisResult | None,
    ) -> tuple[CrossDomainSynthesisResult | None, bool]:
        """Obtain the previous (T-1) D-2 snapshot via the certified D-2 service.

        The previous snapshot is evaluated at ``current.comparison_date`` so it
        uses only data available as of that historical date (PIT-safe). Returns
        ``(previous, history_available)``.
        """
        if previous_synthesis is not None:
            if (
                previous_synthesis.symbol == current.symbol
                and previous_synthesis.evaluation_date < current.evaluation_date
            ):
                return previous_synthesis, True
            return None, False

        try:
            previous = await self.synthesis_service.synthesize(
                symbol=symbol,
                evaluation_date=current.comparison_date,
                comparison_window=1,
            )
        except ValueError:
            return None, False

        if previous.evaluation_date >= current.evaluation_date:
            return None, False
        return previous, True

    # -------------------------------------------------------------------
    # Pattern fingerprint (deterministic semantic identity)
    # -------------------------------------------------------------------

    @staticmethod
    def compute_pattern_fingerprint(relationship: Any) -> str:
        """Deterministic fingerprint: sorted(domains) + sorted(metrics) + type + direction.

        Strength is deliberately EXCLUDED so that a strength change
        (e.g. NORMAL → MAJOR) represents STRENGTHENING rather than
        RESOLVED + NEW. Dates, generated_at, free-form reasons and raw
        numeric values are also excluded.
        """
        direction = relationship.direction if relationship.direction else "NONE"
        return "|".join(
            [
                "-".join(sorted(relationship.domains_involved)),
                "-".join(sorted(relationship.metrics_involved)),
                str(relationship.relationship_type.value),
                str(direction),
            ]
        )

    # -------------------------------------------------------------------
    # Price direction derivation (metadata only — never recalculated)
    # -------------------------------------------------------------------

    @staticmethod
    def _derive_price_direction(d2: CrossDomainSynthesisResult) -> str | None:
        """Derive the price direction from certified D-2 structured data.

        A price direction exists only when D-2 treated the price move as
        directionally meaningful (``PRICE`` in supporting_domains). The sign is
        read from D-2-computed metadata values — D-3 never recomputes
        percentages from raw prices.
        """
        if "PRICE" not in set(d2.supporting_domains):
            return None

        for rel in d2.relationships:
            if "PRICE" not in rel.domains_involved:
                continue
            for key in PRICE_DIRECTION_METADATA_KEYS:
                value = rel.metadata.get(key)
                if value is None:
                    continue
                pct = float(value)
                if pct > 0:
                    return "BULLISH"
                if pct < 0:
                    return "BEARISH"
                return "NEUTRAL"

        for rel in d2.relationships:
            if (
                "PRICE" in rel.domains_involved
                and rel.relationship_type == RelationshipType.CONFIRMATION
                and rel.direction in _DIRECTIONAL_DIRECTIONS
            ):
                return rel.direction
        return None

    @staticmethod
    def _extract_price_change_pct(d2: CrossDomainSynthesisResult) -> float | None:
        """Read the D-2-computed price change percentage from metadata (if any)."""
        for rel in d2.relationships:
            if "PRICE" not in rel.domains_involved:
                continue
            value = rel.metadata.get("price_change_pct")
            if value is not None:
                return float(value)
        return None

    # -------------------------------------------------------------------
    # Evidence classification
    # -------------------------------------------------------------------

    @staticmethod
    def _classify_relationship(
        rel: Any, price_direction: str | None
    ) -> EvidenceClassification:
        """Deterministically classify a D-2 relationship.

        CRITICAL: DIVERGENCE is always CONFLICTING — never PRIMARY.
        Context-only relationships are always CONTEXT.
        """
        # 1. Context-only relationships (type or strength)
        if (
            rel.relationship_type == RelationshipType.CONTEXT_ONLY
            or rel.strength == RelationshipStrength.CONTEXT_ONLY
        ):
            return EvidenceClassification.CONTEXT

        # 2. Explicit conflict relationship types
        if rel.relationship_type in (RelationshipType.DIVERGENCE, RelationshipType.CONFLICT):
            return EvidenceClassification.CONFLICTING

        # 3. Opposing directional confirmation: a relationship asserting a
        #    directional direction opposite to the price direction conflicts
        #    with the observed move.
        if (
            rel.direction in _DIRECTIONAL_DIRECTIONS
            and price_direction in _DIRECTIONAL_DIRECTIONS
            and rel.direction != price_direction
        ):
            return EvidenceClassification.CONFLICTING

        # 4. Confirmation family
        if rel.relationship_type == RelationshipType.CONFIRMATION:
            if rel.strength in (RelationshipStrength.MAJOR, RelationshipStrength.SIGNIFICANT):
                return EvidenceClassification.PRIMARY
            return EvidenceClassification.SUPPORTING

        if rel.relationship_type == RelationshipType.PARTIAL_CONFIRMATION:
            return EvidenceClassification.SUPPORTING

        if rel.relationship_type == RelationshipType.NEW_CROSS_DOMAIN_PATTERN:
            if rel.strength in (RelationshipStrength.MAJOR, RelationshipStrength.SIGNIFICANT):
                return EvidenceClassification.PRIMARY
            return EvidenceClassification.SUPPORTING

        if rel.relationship_type == RelationshipType.RESOLVED_CROSS_DOMAIN_PATTERN:
            return EvidenceClassification.CONTEXT

        # INSUFFICIENT_EVIDENCE or any unhandled type — fail closed.
        return EvidenceClassification.MISSING

    def _classify_relationships(
        self, d2: CrossDomainSynthesisResult, price_direction: str | None
    ) -> tuple[
        list[EvidenceItem], list[EvidenceItem], list[EvidenceItem],
        list[EvidenceItem], list[EvidenceItem],
    ]:
        """Classify all D-2 relationships into the five evidence buckets.

        D-2 relationships arrive deterministically sorted; that order is
        preserved inside each bucket.
        """
        primary: list[EvidenceItem] = []
        supporting: list[EvidenceItem] = []
        conflicting: list[EvidenceItem] = []
        context: list[EvidenceItem] = []
        missing: list[EvidenceItem] = []

        for rel in d2.relationships:
            classification = self._classify_relationship(rel, price_direction)
            item = EvidenceItem(
                domain=rel.domains_involved[0] if rel.domains_involved else "UNKNOWN",
                domains_involved=list(rel.domains_involved),
                metrics_involved=list(rel.metrics_involved),
                relationship_type=rel.relationship_type,
                strength=rel.strength,
                direction=rel.direction,
                classification=classification,
                reason=rel.reason,
                metadata=dict(rel.metadata),
                provenance=list(rel.provenance),
                pattern_fingerprint=self.compute_pattern_fingerprint(rel),
            )
            if classification == EvidenceClassification.PRIMARY:
                primary.append(item)
            elif classification == EvidenceClassification.SUPPORTING:
                supporting.append(item)
            elif classification == EvidenceClassification.CONFLICTING:
                conflicting.append(item)
            elif classification == EvidenceClassification.CONTEXT:
                context.append(item)
            else:  # MISSING — fail closed
                missing.append(item)

        # Missing directional evidence: D-2's missing_domains are unavailable
        # data domains. Missing data is never treated as negative evidence.
        for domain in sorted(set(d2.missing_domains)):
            if domain in CONTEXT_ONLY_DOMAINS:
                continue
            missing.append(
                EvidenceItem(
                    domain=domain,
                    domains_involved=[domain],
                    metrics_involved=[],
                    relationship_type=RelationshipType.INSUFFICIENT_EVIDENCE,
                    strength=RelationshipStrength.NORMAL,
                    direction=None,
                    classification=EvidenceClassification.MISSING,
                    reason=f"{domain} data unavailable for this evaluation window",
                    metadata={},
                    provenance=[],
                    pattern_fingerprint="",
                )
            )

        return primary, supporting, conflicting, context, missing

    # -------------------------------------------------------------------
    # Corrected directional convergence
    # -------------------------------------------------------------------

    @staticmethod
    def _build_convergence(d2: CrossDomainSynthesisResult) -> DirectionalConvergence:
        """Directional-only convergence, corrected against D-2's max_possible.

        D-2's ``max_possible`` may include context-only domains
        (CORPORATE / INSTITUTIONAL / DEAL) that can never contribute to
        directional convergence. D-3 therefore exposes:

            directional_domains   = unique(supporting + conflicting)  [directional only]
            directional_max_possible = len(directional_domains)

        Context and missing domains are excluded; domains are never
        double-counted regardless of how many indicators belong to them.
        """
        supporting = sorted(
            {d for d in d2.supporting_domains if d not in CONTEXT_ONLY_DOMAINS}
        )
        conflicting = sorted(
            {d for d in d2.conflicting_domains if d not in CONTEXT_ONLY_DOMAINS}
        )
        directional_domains = sorted(set(supporting) | set(conflicting))
        value = len(supporting)
        max_possible = len(directional_domains)
        ratio = round(value / max_possible, 4) if max_possible > 0 else None

        context_domains = sorted(set(d2.context_domains))
        missing_domains = sorted(
            {d for d in d2.missing_domains if d not in CONTEXT_ONLY_DOMAINS}
        )

        interpretation = (
            f"{value} of {max_possible} evaluated directional domains provide "
            "supporting evidence"
        )
        if conflicting:
            interpretation += f"; {len(conflicting)} conflicting"

        return DirectionalConvergence(
            directional_value=value,
            directional_max_possible=max_possible,
            convergence_ratio=ratio,
            supporting_domains=supporting,
            conflicting_domains=conflicting,
            context_domains=context_domains,
            missing_domains=missing_domains,
            interpretation=interpretation,
        )

    # -------------------------------------------------------------------
    # Deterministic state machine
    # -------------------------------------------------------------------

    @staticmethod
    def _determine_state(
        *,
        supporting_domains: list[str],
        conflicting_domains: list[str],
        price_direction: str | None,
        has_context: bool,
        has_relationships: bool,
    ) -> tuple[OverallState, DirectionalBias]:
        """Deterministic overall-state / directional-bias decision table.

        Edge cases handled explicitly:
        * supporting AND conflicting coexist → MIXED_EVIDENCE (no winner chosen)
        * conflicting-only (no support) → MIXED_EVIDENCE (D-2 quality CONFLICTING)
        * muted/absent price (no derivable direction) → never invent a direction
        * context evidence only → CONTEXT_ONLY
        * nothing usable → INSUFFICIENT_EVIDENCE
        """
        supp = supporting_domains
        conf = conflicting_domains

        # Mixed: supporting and conflicting directional evidence coexist
        # (also conflicting-only — the evidence picture has no single reading).
        if supp and conf:
            return OverallState.MIXED_EVIDENCE, DirectionalBias.MIXED
        if conf and not supp:
            return OverallState.MIXED_EVIDENCE, DirectionalBias.MIXED

        # Directional support present — requires a derivable price direction.
        if supp:
            if price_direction == "BULLISH":
                if len(supp) >= 2:
                    return (
                        OverallState.BULLISH_EVIDENCE_SUPPORTED,
                        DirectionalBias.BULLISH_EVIDENCE,
                    )
                return (
                    OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS,
                    DirectionalBias.BULLISH_EVIDENCE,
                )
            if price_direction == "BEARISH":
                if len(supp) >= 2:
                    return (
                        OverallState.BEARISH_EVIDENCE_SUPPORTED,
                        DirectionalBias.BEARISH_EVIDENCE,
                    )
                return (
                    OverallState.PARTIALLY_SUPPORTED_BEARISH_BIAS,
                    DirectionalBias.BEARISH_EVIDENCE,
                )
            # Supporting domains exist but no direction is derivable from D-2
            # structured data — never invent a direction.
            return OverallState.NEUTRAL_OBSERVATION, DirectionalBias.NEUTRAL

        # No directional evidence at all.
        if has_context:
            return OverallState.CONTEXT_ONLY, DirectionalBias.NEUTRAL
        if has_relationships:
            return OverallState.NEUTRAL_OBSERVATION, DirectionalBias.NEUTRAL
        return OverallState.INSUFFICIENT_EVIDENCE, DirectionalBias.NO_DIRECTIONAL_SIGNAL

    # -------------------------------------------------------------------
    # Historical lifecycle (T vs T-1, per pattern)
    # -------------------------------------------------------------------

    def _build_lifecycle(
        self,
        current: CrossDomainSynthesisResult,
        previous: CrossDomainSynthesisResult | None,
    ) -> tuple[list[PatternLifecycleRecord], OverallLifecycleSummary | None]:
        """Compare current (T) D-2 patterns against the previous (T-1) snapshot.

        Deterministic rules:
        * fingerprint absent at T-1 (or no snapshot)      → NEW
        * same fingerprint, higher evidence               → STRENGTHENING
        * same fingerprint, lower evidence                → WEAKENING
        * same fingerprint, equivalent evidence           → CONTINUING
        * previous fingerprint absent at T                → RESOLVED (with a
          data-gap flag when disappearance is explained by data unavailability
          rather than evidence reversal)

        Evidence comparison is strength-first (the fingerprint excludes
        strength so NORMAL → MAJOR strengthens rather than resolving), then
        the overall D-2 convergence value.
        """
        current_rels = list(current.relationships)
        prev_map: dict[str, Any] = {}
        if previous is not None:
            for rel in previous.relationships:
                prev_map[self.compute_pattern_fingerprint(rel)] = rel

        current_fp_set = {self.compute_pattern_fingerprint(rel) for rel in current_rels}
        resolved_prev: dict[tuple[tuple[str, ...], tuple[str, ...]], Any] = {}
        for fp, prev_rel in prev_map.items():
            if fp not in current_fp_set:
                key = (
                    tuple(sorted(prev_rel.domains_involved)),
                    tuple(sorted(prev_rel.metrics_involved)),
                )
                resolved_prev[key] = prev_rel

        records: list[PatternLifecycleRecord] = []

        for rel in current_rels:
            fp = self.compute_pattern_fingerprint(rel)
            prev = prev_map.get(fp)

            if previous is None or prev is None:
                state = PatternLifecycle.NEW
                prev_strength = None
                description = (
                    "No previous synthesis snapshot available; pattern classified "
                    "as NEW (no-history state)."
                    if previous is None
                    else "Pattern not present in the previous snapshot."
                )
            else:
                prev_strength = prev.strength
                cur_rank = STRENGTH_RANK.get(rel.strength.value, 0)
                prev_rank = STRENGTH_RANK.get(prev.strength.value, 0)
                cur_conv = current.convergence_score.value
                prev_conv = previous.convergence_score.value
                if cur_rank > prev_rank:
                    state = PatternLifecycle.STRENGTHENING
                elif cur_rank < prev_rank:
                    state = PatternLifecycle.WEAKENING
                elif cur_conv > prev_conv:
                    state = PatternLifecycle.STRENGTHENING
                elif cur_conv < prev_conv:
                    state = PatternLifecycle.WEAKENING
                else:
                    state = PatternLifecycle.CONTINUING
                description = (
                    f"Same semantic pattern present at both {previous.evaluation_date} "
                    f"and {current.evaluation_date}."
                )

            # Changed-semantics descriptor: same domains+metrics as a resolved
            # pattern but a different fingerprint (type or direction changed).
            change_descriptor = None
            key = (
                tuple(sorted(rel.domains_involved)),
                tuple(sorted(rel.metrics_involved)),
            )
            if state == PatternLifecycle.NEW and key in resolved_prev:
                change_descriptor = LifecycleChangeDescriptor.CHANGED
                description = (
                    "Pattern semantics changed relative to the previous snapshot "
                    "(relationship type or direction changed); previous variant resolved."
                )

            records.append(
                PatternLifecycleRecord(
                    pattern_fingerprint=fp,
                    domains_involved=list(rel.domains_involved),
                    metrics_involved=list(rel.metrics_involved),
                    relationship_type=rel.relationship_type,
                    direction=rel.direction,
                    current_strength=rel.strength,
                    previous_strength=prev_strength,
                    lifecycle_state=state,
                    change_descriptor=change_descriptor,
                    data_gap=False,
                    description=description,
                )
            )

        # Resolved patterns: present at T-1, absent at T.
        for fp in sorted(prev_map):
            if fp in current_fp_set:
                continue
            prev_rel = prev_map[fp]
            missing_now = set(current.missing_domains)
            data_gap = any(
                d not in CONTEXT_ONLY_DOMAINS and d in missing_now
                for d in prev_rel.domains_involved
            )
            if data_gap:
                description = (
                    "Pattern no longer observable because its underlying data is "
                    "unavailable in the current snapshot (data gap, not an "
                    "evidence reversal)."
                )
            else:
                description = "Pattern no longer present in the current snapshot (resolved)."
            records.append(
                PatternLifecycleRecord(
                    pattern_fingerprint=fp,
                    domains_involved=list(prev_rel.domains_involved),
                    metrics_involved=list(prev_rel.metrics_involved),
                    relationship_type=prev_rel.relationship_type,
                    direction=prev_rel.direction,
                    current_strength=None,
                    previous_strength=prev_rel.strength,
                    lifecycle_state=PatternLifecycle.RESOLVED,
                    change_descriptor=None,
                    data_gap=data_gap,
                    description=description,
                )
            )

        records.sort(key=lambda r: r.pattern_fingerprint)

        if not records:
            return [], None

        summary = OverallLifecycleSummary(
            pattern_count=len(records),
            new_count=sum(1 for r in records if r.lifecycle_state == PatternLifecycle.NEW),
            continuing_count=sum(
                1 for r in records if r.lifecycle_state == PatternLifecycle.CONTINUING
            ),
            strengthening_count=sum(
                1 for r in records if r.lifecycle_state == PatternLifecycle.STRENGTHENING
            ),
            weakening_count=sum(
                1 for r in records if r.lifecycle_state == PatternLifecycle.WEAKENING
            ),
            resolved_count=sum(
                1 for r in records if r.lifecycle_state == PatternLifecycle.RESOLVED
            ),
            description=(
                "No previous synthesis snapshot available; all current patterns "
                "classified as NEW (no-history state)."
                if previous is None
                else (
                    f"{len(records)} patterns tracked between "
                    f"{previous.evaluation_date} and {current.evaluation_date}."
                )
            ),
        )
        return records, summary

    # -------------------------------------------------------------------
    # Watch items & limitations
    # -------------------------------------------------------------------

    @staticmethod
    def _build_watch_items(
        *,
        convergence: DirectionalConvergence,
        missing_domains: list[str],
        has_directional: bool,
        has_context: bool,
    ) -> list[WatchItem]:
        """Deterministic watch items describing what evidence would clarify
        the interpretation. NEVER trading instructions."""
        codes: set[str] = set()

        for domain in missing_domains:
            watch = DOMAIN_WATCH_MAP.get(domain)
            if watch:
                codes.add(watch)

        # Muted / unconfirmed price with no directional evidence
        if not has_directional and not any(
            d in convergence.supporting_domains for d in ("PRICE",)
        ):
            codes.add("PRICE_CONFIRMATION_REQUIRED")

        if convergence.conflicting_domains:
            codes.add("CONFLICT_RESOLUTION_REQUIRED")

        return sorted(
            (
                WatchItem(code=code, description=_WATCH_DESCRIPTIONS[code])
                for code in codes
            ),
            key=lambda w: w.code,
        )

    @staticmethod
    def _build_limitations(
        *,
        missing_domains: list[str],
        context_domains: list[str],
        history_available: bool,
    ) -> list[Limitation]:
        limitations: list[Limitation] = []
        for domain in missing_domains:
            text = DOMAIN_LIMITATION_TEXT.get(domain)
            if text:
                limitations.append(Limitation(code=f"MISSING_{domain}", description=text))
        if "INSTITUTIONAL" in context_domains:
            limitations.append(
                Limitation(code="INSTITUTIONAL_CONTEXT_ONLY", description=INSTITUTIONAL_LIMITATION)
            )
        if not history_available:
            limitations.append(
                Limitation(code="NO_HISTORY", description=NO_HISTORY_LIMITATION)
            )
        return sorted(limitations, key=lambda limitation: limitation.code)

    @staticmethod
    def _build_provenance(d2: CrossDomainSynthesisResult) -> list[str]:
        provenance: set[str] = {"what_changed"}
        for rel in d2.relationships:
            provenance.update(rel.provenance)
        return sorted(provenance)

    # -------------------------------------------------------------------
    # Deterministic fallback narrative
    # -------------------------------------------------------------------

    def _build_fallback_narrative(
        self,
        *,
        symbol: str,
        d2: CrossDomainSynthesisResult,
        overall_state: OverallState,
        directional_bias: DirectionalBias,
        convergence: DirectionalConvergence,
        context_evidence: list[EvidenceItem],
        missing_evidence: list[EvidenceItem],
        watch_items: list[WatchItem],
    ) -> str:
        """Deterministic plain-language narrative built ONLY from structured D-3 facts."""
        parts: list[str] = []

        price_pct = self._extract_price_change_pct(d2)
        if price_pct is not None:
            parts.append(
                f"{symbol} moved {price_pct:+.2f}% between "
                f"{d2.comparison_date} and {d2.evaluation_date}."
            )
        else:
            parts.append(
                f"{symbol} was evaluated for {d2.evaluation_date} versus "
                f"{d2.comparison_date}."
            )

        # Preserve D-2's muted-price semantic verbatim (deterministic D-2 prose).
        muted_rel = next(
            (
                item
                for item in context_evidence
                if item.metadata.get("pattern") == MUTED_PRICE_PATTERN
            ),
            None,
        )

        supp = convergence.supporting_domains
        conf = convergence.conflicting_domains

        if overall_state == OverallState.BULLISH_EVIDENCE_SUPPORTED:
            parts.append(
                f"{convergence.directional_value} independent directional domains "
                f"({', '.join(supp)}) confirm the observed bullish price movement."
            )
        elif overall_state == OverallState.BEARISH_EVIDENCE_SUPPORTED:
            parts.append(
                f"{convergence.directional_value} independent directional domains "
                f"({', '.join(supp)}) confirm the observed bearish price movement."
            )
        elif overall_state == OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS:
            parts.append(
                f"A single directional domain ({', '.join(supp)}) supports the "
                "observed bullish price movement; additional confirming evidence "
                "would strengthen this interpretation."
            )
        elif overall_state == OverallState.PARTIALLY_SUPPORTED_BEARISH_BIAS:
            parts.append(
                f"A single directional domain ({', '.join(supp)}) supports the "
                "observed bearish price movement; additional confirming evidence "
                "would strengthen this interpretation."
            )
        elif overall_state == OverallState.MIXED_EVIDENCE:
            parts.append(
                f"Supporting domains ({', '.join(supp) if supp else 'none'}) and "
                f"conflicting domains ({', '.join(conf)}) coexist; the evidence "
                "does not establish a single directional reading."
            )
        elif overall_state == OverallState.CONTEXT_ONLY:
            if muted_rel is not None:
                parts.append(muted_rel.reason + ".")
            parts.append(
                "The available evidence remains context-only; no directional "
                "evidence is established."
            )
        elif overall_state == OverallState.NEUTRAL_OBSERVATION:
            parts.append(
                "Evidence is available for the evaluation window, but no "
                "meaningful directional relationship is established."
            )
        else:  # INSUFFICIENT_EVIDENCE
            parts.append(
                "Insufficient evidence is available to establish a directional "
                "interpretation for this evaluation window."
            )

        if conf:
            parts.append(
                f"Conflicting evidence from {', '.join(conf)} remains unresolved."
            )
        if missing_evidence:
            parts.append(
                f"Evidence unavailable: "
                f"{', '.join(item.domain for item in missing_evidence)}."
            )
        if watch_items:
            parts.append(f"Watch items: {', '.join(item.code for item in watch_items)}.")

        return " ".join(parts)

    # -------------------------------------------------------------------
    # Optional AI narrative (never authoritative)
    # -------------------------------------------------------------------

    @staticmethod
    def _build_ai_facts(
        *,
        symbol: str,
        d2: CrossDomainSynthesisResult,
        overall_state: OverallState,
        directional_bias: DirectionalBias,
        convergence: DirectionalConvergence,
        primary: list[EvidenceItem],
        supporting: list[EvidenceItem],
        conflicting: list[EvidenceItem],
        context: list[EvidenceItem],
        missing: list[EvidenceItem],
        overall_lifecycle: OverallLifecycleSummary | None,
        pattern_records: list[PatternLifecycleRecord],
        watch_items: list[WatchItem],
        limitations: list[Limitation],
    ) -> dict[str, Any]:
        """Structured deterministic facts handed to the LLM. No DB access."""

        def items(evidence: list[EvidenceItem]) -> list[dict[str, Any]]:
            return [
                {
                    "domain": item.domain,
                    "relationship_type": item.relationship_type.value,
                    "strength": item.strength.value,
                    "direction": item.direction,
                    "reason": item.reason,
                    "metadata": item.metadata,
                }
                for item in evidence
            ]

        return {
            "symbol": symbol,
            "evaluation_date": d2.evaluation_date.isoformat(),
            "comparison_date": d2.comparison_date.isoformat(),
            "overall_state": overall_state.value,
            "directional_bias": directional_bias.value,
            "evidence_quality": d2.evidence_quality.value,
            "pit_safe": bool(d2.pit_safe),
            "convergence": convergence.model_dump(mode="json"),
            "primary_evidence": items(primary),
            "supporting_evidence": items(supporting),
            "conflicting_evidence": items(conflicting),
            "context_evidence": items(context),
            "missing_evidence": [
                {"domain": item.domain, "reason": item.reason} for item in missing
            ],
            "overall_lifecycle": (
                overall_lifecycle.model_dump(mode="json")
                if overall_lifecycle is not None
                else None
            ),
            "pattern_lifecycle_records": [
                record.model_dump(mode="json") for record in pattern_records
            ],
            "watch_items": [item.code for item in watch_items],
            "limitations": [item.description for item in limitations],
        }

    async def _generate_narrative(self, facts: dict[str, Any]) -> NarrativeResult | None:
        """Generate a validated AI narrative. Returns None on any failure —
        the deterministic fallback always remains available."""
        from app.ai.interpretation_narrative_service import InterpretationNarrativeService

        service = self._injected_narrative_service
        if service is None:
            try:
                service = InterpretationNarrativeService()
            except Exception:  # provider misconfiguration — fail closed to fallback
                return None
        try:
            result = await service.generate(facts)
        except Exception:
            return None
        if result is None or not result.get("validated"):
            # Validation failure ⇒ deterministic fallback (fail-closed).
            return None
        return NarrativeResult(
            text=result["text"],
            source="ai",
            validated=True,
            validation_errors=[],
            provider=result.get("provider"),
            model=result.get("model"),
        )
