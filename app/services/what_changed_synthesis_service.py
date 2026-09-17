"""What Changed Synthesis Service — G.11-D-2 Advanced Cross-Domain Synthesis Engine.

Evaluates how deterministic changes across independent market domains relate to each other:
confirming, conflicting, diverging, or providing context, with strict fail-closed handling
for unpopulated or unavailable domains.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.what_changed_repository import WhatChangedRepository
from app.schemas.what_changed import ChangeEvent, WhatChangedResult
from app.schemas.what_changed_synthesis import (
    ConvergenceScore,
    CrossDomainRelationship,
    CrossDomainSynthesisResult,
    LifecycleState,
)
from app.services.exchange_calendar_service import ExchangeCalendarService
from app.services.what_changed_config import DEFAULT_COMPARISON_WINDOW, PRICE_CHANGE_NORMAL_PCT
from app.services.what_changed_service import WhatChangedService
from app.services.what_changed_synthesis_config import (
    DOMAIN_CORPORATE,
    DOMAIN_DERIVATIVES,
    DOMAIN_INSTITUTIONAL,
    DOMAIN_PARTICIPATION,
    DOMAIN_PRICE,
    DOMAIN_SECTOR,
    DOMAIN_TECHNICAL,
    DOMAIN_TREND,
    FUTURES_OI_CHANGE_SIGNIFICANT_PCT,
    SYNTHESIS_RELATIONSHIP_SORT_ORDER,
    SYNTHESIS_STRENGTH_SORT_ORDER,
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)


class WhatChangedSynthesisService:
    """Deterministic cross-domain synthesis engine for AIStockScanner."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WhatChangedRepository | None = None,
        calendar: ExchangeCalendarService | None = None,
        what_changed_service: WhatChangedService | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or WhatChangedRepository(session)
        self.calendar = calendar or ExchangeCalendarService()
        self.what_changed_service = what_changed_service or WhatChangedService(
            session=self.session,
            repository=self.repository,
            calendar=self.calendar,
        )

    async def synthesize(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        comparison_window: int = DEFAULT_COMPARISON_WINDOW,
    ) -> CrossDomainSynthesisResult:
        """Execute deterministic cross-domain synthesis for symbol between evaluation and comparison dates."""
        clean_symbol = symbol.upper().strip()
        stock = await self.repository.get_stock_by_symbol(clean_symbol)
        if not stock:
            raise ValueError(f"Stock symbol '{symbol}' not found")

        # 1. Obtain certified G.11-D-1 foundational change result
        d1_result: WhatChangedResult = await self.what_changed_service.detect_changes(
            symbol=clean_symbol,
            evaluation_date=evaluation_date,
            comparison_window=comparison_window,
        )

        eval_d = d1_result.evaluation_date
        comp_d = d1_result.comparison_date

        # 2. Gather domain facts (PIT-safe <= eval_d)
        curr_price = await self.repository.get_daily_price(stock.id, eval_d)
        prev_price = await self.repository.get_daily_price(stock.id, comp_d)
        curr_ind = await self.repository.get_indicator_result(stock.id, eval_d)
        prev_ind = await self.repository.get_indicator_result(stock.id, comp_d)

        deals = await self.repository.get_bulk_block_deals(
            clean_symbol, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )
        announcements = await self.repository.get_corporate_announcements(
            stock.id, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )
        actions = await self.repository.get_corporate_actions(
            stock.id, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )

        curr_flows = await self.repository.get_institutional_flows(eval_d)
        prev_flows = await self.repository.get_institutional_flows(comp_d)

        curr_deriv = await self.repository.get_derivatives_oi_summary(stock.id, eval_d, pit_cutoff=eval_d)
        prev_deriv = await self.repository.get_derivatives_oi_summary(stock.id, comp_d, pit_cutoff=eval_d)

        curr_sector = await self.repository.get_sector_benchmark_price(stock.id, eval_d)
        prev_sector = await self.repository.get_sector_benchmark_price(stock.id, comp_d)

        # 3. Track domain availability
        available_domains: list[str] = []
        missing_domains: list[str] = []

        if curr_price is not None and prev_price is not None:
            available_domains.append(DOMAIN_PRICE)
            available_domains.append(DOMAIN_PARTICIPATION)
        else:
            missing_domains.append(DOMAIN_PRICE)
            missing_domains.append(DOMAIN_PARTICIPATION)

        if curr_ind is not None and prev_ind is not None:
            available_domains.append(DOMAIN_TECHNICAL)
            available_domains.append(DOMAIN_TREND)
        else:
            missing_domains.append(DOMAIN_TECHNICAL)
            missing_domains.append(DOMAIN_TREND)

        if curr_deriv is not None and prev_deriv is not None:
            available_domains.append(DOMAIN_DERIVATIVES)
        else:
            missing_domains.append(DOMAIN_DERIVATIVES)

        if announcements or actions:
            available_domains.append(DOMAIN_CORPORATE)

        if curr_flows or prev_flows:
            available_domains.append(DOMAIN_INSTITUTIONAL)

        if curr_sector is not None and prev_sector is not None:
            available_domains.append(DOMAIN_SECTOR)
        else:
            missing_domains.append(DOMAIN_SECTOR)

        # 4. Evaluate Cross-Domain Relationships
        relationships: list[CrossDomainRelationship] = []
        supporting_domains: set[str] = set()
        conflicting_domains: set[str] = set()
        context_domains: set[str] = set()

        price_direction: str | None = None
        price_change_pct: float | None = None
        has_meaningful_price: bool = False
        if curr_price and prev_price:
            c_val = float(curr_price.close)
            p_val = float(prev_price.close)
            price_change_pct = round(((c_val - p_val) / p_val) * 100.0, 2)
            has_meaningful_price = (abs(price_change_pct) >= PRICE_CHANGE_NORMAL_PCT) or any(
                c.domain == "PRICE" for c in d1_result.changes
            )
            if price_change_pct > 0:
                price_direction = "BULLISH"
                if has_meaningful_price:
                    supporting_domains.add(DOMAIN_PRICE)
            elif price_change_pct < 0:
                price_direction = "BEARISH"
                if has_meaningful_price:
                    supporting_domains.add(DOMAIN_PRICE)
            else:
                price_direction = "NEUTRAL"

        # A. PRICE + VOLUME
        rel_pv = self._synthesize_price_volume(
            clean_symbol,
            eval_d,
            comp_d,
            curr_price,
            prev_price,
            price_direction,
            price_change_pct,
            d1_result.changes,
        )
        if rel_pv:
            relationships.append(rel_pv)
            if rel_pv.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_PARTICIPATION)
            elif rel_pv.relationship_type == RelationshipType.DIVERGENCE:
                conflicting_domains.add(DOMAIN_PARTICIPATION)
            elif rel_pv.relationship_type == RelationshipType.CONTEXT_ONLY:
                context_domains.add(DOMAIN_PARTICIPATION)

        # B. PRICE + DELIVERY
        rel_pd = self._synthesize_price_delivery(
            clean_symbol, eval_d, comp_d, curr_price, prev_price, price_direction
        )
        if rel_pd:
            relationships.append(rel_pd)
            if rel_pd.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_PARTICIPATION)
            elif rel_pd.relationship_type == RelationshipType.DIVERGENCE:
                conflicting_domains.add(DOMAIN_PARTICIPATION)
        else:
            if "DELIVERY" not in missing_domains:
                missing_domains.append("DELIVERY")

        # C. PRICE + TREND
        rel_pt = self._synthesize_price_trend(
            clean_symbol, eval_d, comp_d, curr_price, prev_price, curr_ind, prev_ind, price_direction, d1_result.changes
        )
        for r in rel_pt:
            relationships.append(r)
            if r.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_TREND)
            elif r.relationship_type in (RelationshipType.CONFLICT, RelationshipType.DIVERGENCE):
                conflicting_domains.add(DOMAIN_TREND)

        # D. PRICE + TECHNICAL (MOMENTUM / RSI)
        rel_ptech = self._synthesize_price_technical(
            clean_symbol, eval_d, comp_d, curr_price, prev_price, curr_ind, prev_ind, price_direction, d1_result.changes
        )
        for r in rel_ptech:
            relationships.append(r)
            if r.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_TECHNICAL)
            elif r.relationship_type == RelationshipType.DIVERGENCE:
                conflicting_domains.add(DOMAIN_TECHNICAL)
            elif r.relationship_type == RelationshipType.CONTEXT_ONLY:
                context_domains.add(DOMAIN_TECHNICAL)

        # E. PRICE + DERIVATIVES OI
        rel_pderiv = self._synthesize_price_derivatives(
            clean_symbol, eval_d, comp_d, curr_price, prev_price, curr_deriv, prev_deriv, price_direction
        )
        if rel_pderiv:
            relationships.append(rel_pderiv)
            if rel_pderiv.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_DERIVATIVES)
            elif rel_pderiv.relationship_type == RelationshipType.DIVERGENCE:
                conflicting_domains.add(DOMAIN_DERIVATIVES)

        # F. INSTITUTIONAL FLOW (Market-wide context only)
        rel_inst = self._synthesize_institutional(
            clean_symbol, eval_d, comp_d, curr_flows, prev_flows
        )
        if rel_inst:
            relationships.append(rel_inst)
            context_domains.add(DOMAIN_INSTITUTIONAL)

        # G. SECTOR + STOCK
        rel_sec = self._synthesize_sector(
            clean_symbol, eval_d, comp_d, price_change_pct, price_direction, curr_sector, prev_sector
        )
        if rel_sec:
            relationships.append(rel_sec)
            if rel_sec.relationship_type == RelationshipType.CONFIRMATION:
                supporting_domains.add(DOMAIN_SECTOR)
            elif rel_sec.relationship_type == RelationshipType.DIVERGENCE:
                conflicting_domains.add(DOMAIN_SECTOR)

        # H. CORPORATE EVENTS (Context only, no causation)
        rel_corp = self._synthesize_corporate(
            clean_symbol, eval_d, comp_d, announcements, actions
        )
        if rel_corp:
            relationships.append(rel_corp)
            context_domains.add(DOMAIN_CORPORATE)

        # I. BULK / BLOCK DEALS (Context only, no speculation)
        rel_deals = self._synthesize_deals(
            clean_symbol, eval_d, comp_d, deals
        )
        if rel_deals:
            relationships.append(rel_deals)
            context_domains.add("DEAL")

        # 5. Deterministic Relationship Ranking & Sorting
        relationships = self._sort_relationships(relationships)

        # 6. Evidence Convergence Score & Evidence Quality Calculation
        supp_list = sorted(supporting_domains)
        conf_list = sorted(conflicting_domains)
        ctx_list = sorted(context_domains)
        miss_list = sorted(set(missing_domains) - supporting_domains - conflicting_domains)

        convergence_value = len(supp_list)
        evaluated_domains_count = len(supp_list) + len(conf_list)
        if evaluated_domains_count == 0 and len(ctx_list) > 0:
            evidence_quality = EvidenceQuality.CONTEXT_ONLY
        elif len(conf_list) > 0:
            evidence_quality = EvidenceQuality.PARTIALLY_CONFIRMED if len(supp_list) > 0 else EvidenceQuality.CONFLICTING
        elif len(supp_list) >= 2:
            evidence_quality = EvidenceQuality.CONFIRMED
        elif len(supp_list) == 1:
            evidence_quality = EvidenceQuality.PARTIALLY_CONFIRMED if len(miss_list) >= 2 else EvidenceQuality.CONFIRMED
        else:
            evidence_quality = EvidenceQuality.INSUFFICIENT_DATA

        score_interp = f"{convergence_value} independent domains provide confirming evidence for the observed state"

        convergence_score = ConvergenceScore(
            value=convergence_value,
            scale="domain_count",
            max_possible=len(available_domains),
            contributing_domains=supp_list,
            conflicting_domains=conf_list,
            excluded_domains=ctx_list,
            missing_domains=miss_list,
            interpretation=score_interp,
        )

        # 7. Lifecycle Determination (if multi-window context exists)
        lifecycle = self._determine_lifecycle(relationships, comparison_window)

        return CrossDomainSynthesisResult(
            symbol=clean_symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            comparison_window_sessions=comparison_window,
            pit_safe=True,
            evidence_quality=evidence_quality,
            convergence_score=convergence_score,
            supporting_domains=supp_list,
            conflicting_domains=conf_list,
            missing_domains=miss_list,
            context_domains=ctx_list,
            available_domains=sorted(available_domains),
            relationships=relationships,
            lifecycle=lifecycle,
            freshness=d1_result.freshness,
            data_quality_summary=d1_result.data_quality_summary,
            generated_at=datetime.now(UTC),
        )

    # -----------------------------------------------------------------------
    # Domain Synthesis Methods (Deterministic Confirmation Matrix)
    # -----------------------------------------------------------------------

    def _synthesize_price_volume(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_price: Any,
        prev_price: Any,
        price_direction: str | None,
        price_change_pct: float | None,
        changes: list[ChangeEvent] | None = None,
    ) -> CrossDomainRelationship | None:
        """Evaluate Price + Volume relationship."""
        if not curr_price or not prev_price or price_direction is None or price_change_pct is None:
            return None

        c_vol = int(curr_price.volume)
        p_vol = int(prev_price.volume)
        if p_vol <= 0:
            return None

        vol_change_pct = round(((c_vol - p_vol) / p_vol) * 100.0, 2)

        # Check whether meaningful price movement occurred per D-1 criteria
        has_meaningful_price = (abs(price_change_pct) >= PRICE_CHANGE_NORMAL_PCT) or (
            changes is not None and any(c.domain == "PRICE" for c in changes)
        )

        if has_meaningful_price:
            if price_direction != "NEUTRAL" and vol_change_pct >= 50.0:
                strength = (
                    RelationshipStrength.MAJOR
                    if (abs(price_change_pct) >= 5.0 and vol_change_pct >= 100.0)
                    else RelationshipStrength.SIGNIFICANT
                )
                if price_direction == "BULLISH":
                    reason = f"Price advance (+{price_change_pct}%) confirmed by substantial volume expansion (+{vol_change_pct}%)"
                else:
                    reason = f"Price decline ({price_change_pct}%) confirmed by increased participation (volume +{vol_change_pct}%)"

                return CrossDomainRelationship(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domains_involved=["PRICE", "VOLUME"],
                    metrics_involved=["close_price", "volume"],
                    relationship_type=RelationshipType.CONFIRMATION,
                    strength=strength,
                    direction=price_direction,
                    evidence=[
                        f"close_price changed {price_change_pct}% ({float(prev_price.close):.2f} -> {float(curr_price.close):.2f})",
                        f"volume surged {vol_change_pct}% ({p_vol:,} -> {c_vol:,})",
                    ],
                    provenance=["daily_prices"],
                    reason=reason,
                    metadata={"price_change_pct": price_change_pct, "volume_change_pct": vol_change_pct},
                )
            elif price_direction != "NEUTRAL" and vol_change_pct <= -20.0:
                return CrossDomainRelationship(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domains_involved=["PRICE", "VOLUME"],
                    metrics_involved=["close_price", "volume"],
                    relationship_type=RelationshipType.DIVERGENCE,
                    strength=RelationshipStrength.NORMAL,
                    direction="DIVERGENT",
                    evidence=[
                        f"close_price changed {price_change_pct}% ({float(prev_price.close):.2f} -> {float(curr_price.close):.2f})",
                        f"volume contracted {vol_change_pct}% ({p_vol:,} -> {c_vol:,})",
                    ],
                    provenance=["daily_prices"],
                    reason=f"Price move ({price_change_pct}%) occurred on contracting volume ({vol_change_pct}%), indicating divergence/weak participation",
                    metadata={"price_change_pct": price_change_pct, "volume_change_pct": vol_change_pct},
                )
        else:
            # Sub-threshold price movement: do NOT create directional CONFIRMATION or DIVERGENCE
            if vol_change_pct >= 50.0:
                return CrossDomainRelationship(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domains_involved=["PRICE", "VOLUME"],
                    metrics_involved=["close_price", "volume"],
                    relationship_type=RelationshipType.CONTEXT_ONLY,
                    strength=RelationshipStrength.NORMAL,
                    direction="NEUTRAL",
                    evidence=[
                        f"close_price changed {price_change_pct}% ({float(prev_price.close):.2f} -> {float(curr_price.close):.2f})",
                        f"volume surged {vol_change_pct}% ({p_vol:,} -> {c_vol:,})",
                    ],
                    provenance=["daily_prices"],
                    reason=f"Volume expanded {vol_change_pct}% while price movement ({price_change_pct:+.2f}%) remained below the {PRICE_CHANGE_NORMAL_PCT}% meaningful-change threshold",
                    metadata={
                        "price_change_pct": price_change_pct,
                        "volume_change_pct": vol_change_pct,
                        "pattern": "VOLUME_EXPANSION_WITH_MUTED_PRICE",
                    },
                )

        return None

    def _synthesize_price_delivery(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_price: Any,
        prev_price: Any,
        price_direction: str | None,
    ) -> CrossDomainRelationship | None:
        """Evaluate Price + Delivery percentage relationship (fail-closed if missing)."""
        if not curr_price or not prev_price or price_direction is None:
            return None

        c_deliv = getattr(curr_price, "delivery_percentage", None)
        p_deliv = getattr(prev_price, "delivery_percentage", None)
        if c_deliv is None or p_deliv is None:
            return None  # Never treat missing delivery as zero!

        c_d = float(c_deliv)
        p_d = float(p_deliv)
        d_diff = round(c_d - p_d, 2)

        if d_diff >= 10.0:
            return CrossDomainRelationship(
                symbol=symbol,
                evaluation_date=eval_d,
                comparison_date=comp_d,
                domains_involved=["PRICE", "DELIVERY"],
                metrics_involved=["close_price", "delivery_percentage"],
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.NORMAL,
                direction=price_direction,
                evidence=[
                    f"delivery_percentage jumped +{d_diff} pp ({p_d:.1f}% -> {c_d:.1f}%)",
                ],
                provenance=["daily_prices"],
                reason=f"Price move accompanied by marked expansion in delivery percentage (+{d_diff} percentage points), reflecting high-conviction delivery participation",
                metadata={"delivery_change_pp": d_diff, "current_delivery_pct": c_d},
            )
        elif d_diff <= -15.0:
            return CrossDomainRelationship(
                symbol=symbol,
                evaluation_date=eval_d,
                comparison_date=comp_d,
                domains_involved=["PRICE", "DELIVERY"],
                metrics_involved=["close_price", "delivery_percentage"],
                relationship_type=RelationshipType.DIVERGENCE,
                strength=RelationshipStrength.NORMAL,
                direction="DIVERGENT",
                evidence=[
                    f"delivery_percentage fell {d_diff} pp ({p_d:.1f}% -> {c_d:.1f}%)",
                ],
                provenance=["daily_prices"],
                reason=f"Price move accompanied by sharp decline in delivery percentage ({d_diff} pp), indicating speculative rather than delivery-backed participation",
                metadata={"delivery_change_pp": d_diff, "current_delivery_pct": c_d},
            )

        return None

    def _synthesize_price_trend(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_price: Any,
        prev_price: Any,
        curr_ind: Any,
        prev_ind: Any,
        price_direction: str | None,
        changes: list[ChangeEvent],
    ) -> list[CrossDomainRelationship]:
        """Evaluate Price + Trend moving averages / SuperTrend relationships."""
        results: list[CrossDomainRelationship] = []
        if not curr_price or not prev_price or not curr_ind:
            return results

        c_close = float(curr_price.close)
        ema50 = float(curr_ind.ema_50) if curr_ind.ema_50 is not None else None

        # Check for EMA crosses in D-1 changes
        trend_events = [c for c in changes if c.domain == "TREND"]
        for te in trend_events:
            if "ema_20" in te.metric:
                rel_type = RelationshipType.CONFIRMATION if te.direction == price_direction else RelationshipType.CONFLICT
                results.append(
                    CrossDomainRelationship(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domains_involved=["PRICE", "TREND"],
                        metrics_involved=["close_price", "ema_20"],
                        relationship_type=rel_type,
                        strength=RelationshipStrength.SIGNIFICANT,
                        direction=te.direction,
                        evidence=[te.reason],
                        provenance=["daily_prices", "indicator_results"],
                        reason=f"Price move coincided with moving average state transition ({te.reason})",
                    )
                )
            elif "supertrend" in te.metric:
                results.append(
                    CrossDomainRelationship(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domains_involved=["PRICE", "TREND"],
                        metrics_involved=["close_price", "supertrend"],
                        relationship_type=RelationshipType.NEW_CROSS_DOMAIN_PATTERN,
                        strength=RelationshipStrength.MAJOR,
                        direction=te.direction,
                        evidence=[te.reason],
                        provenance=["indicator_results"],
                        reason=f"High-significance trend-state transition: {te.reason}",
                    )
                )

        # Conflict check: Price UP while remaining below 50 EMA
        if price_direction == "BULLISH" and ema50 is not None and c_close < ema50:
            results.append(
                CrossDomainRelationship(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domains_involved=["PRICE", "TREND"],
                    metrics_involved=["close_price", "ema_50"],
                    relationship_type=RelationshipType.CONFLICT,
                    strength=RelationshipStrength.NORMAL,
                    direction="MIXED",
                    evidence=[
                        f"Close price is {c_close:.2f}, remaining below 50-day EMA ({ema50:.2f})",
                    ],
                    provenance=["daily_prices", "indicator_results"],
                    reason="Counter-trend condition: Close price advanced but remains below the 50-day EMA, indicating advance within a broader bearish trend context",
                )
            )

        return results

    def _synthesize_price_technical(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_price: Any,
        prev_price: Any,
        curr_ind: Any,
        prev_ind: Any,
        price_direction: str | None,
        changes: list[ChangeEvent],
    ) -> list[CrossDomainRelationship]:
        """Evaluate Price + Momentum / RSI relationships."""
        results: list[CrossDomainRelationship] = []
        if not curr_price or not prev_price or not curr_ind:
            return results

        # Check for RSI events in D-1
        rsi_events = [c for c in changes if c.domain == "TECHNICAL" and "rsi" in c.metric]
        for re in rsi_events:
            if re.change_type == "THRESHOLD_CROSSED":
                if price_direction == "BEARISH" and re.current_state == "OVERSOLD":
                    results.append(
                        CrossDomainRelationship(
                            symbol=symbol,
                            evaluation_date=eval_d,
                            comparison_date=comp_d,
                            domains_involved=["PRICE", "TECHNICAL"],
                            metrics_involved=["close_price", "rsi_14"],
                            relationship_type=RelationshipType.CONFIRMATION,
                            strength=RelationshipStrength.SIGNIFICANT,
                            direction="BEARISH",
                            evidence=[re.reason],
                            provenance=["indicator_results"],
                            reason=f"Price decline confirmed by momentum indicator crossing into oversold territory ({re.reason})",
                        )
                    )
                elif price_direction == "BULLISH" and re.current_state == "OVERBOUGHT":
                    results.append(
                        CrossDomainRelationship(
                            symbol=symbol,
                            evaluation_date=eval_d,
                            comparison_date=comp_d,
                            domains_involved=["PRICE", "TECHNICAL"],
                            metrics_involved=["close_price", "rsi_14"],
                            relationship_type=RelationshipType.CONTEXT_ONLY,
                            strength=RelationshipStrength.NORMAL,
                            direction="NEUTRAL",
                            evidence=[re.reason],
                            provenance=["indicator_results"],
                            reason=f"Price advance accompanied by momentum entering overbought zone ({re.reason}), representing momentum extension context",
                        )
                    )

        # Divergence check between Price and RSI
        if prev_ind and curr_ind.rsi_14 is not None and prev_ind.rsi_14 is not None:
            c_rsi = float(curr_ind.rsi_14)
            p_rsi = float(prev_ind.rsi_14)
            rsi_diff = round(c_rsi - p_rsi, 2)
            c_close = float(curr_price.close)
            p_close = float(prev_price.close)

            if c_close > p_close and rsi_diff <= -5.0:
                results.append(
                    CrossDomainRelationship(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domains_involved=["PRICE", "TECHNICAL"],
                        metrics_involved=["close_price", "rsi_14"],
                        relationship_type=RelationshipType.DIVERGENCE,
                        strength=RelationshipStrength.SIGNIFICANT,
                        direction="DIVERGENT",
                        evidence=[
                            f"Close price increased ({p_close:.2f} -> {c_close:.2f}) while RSI(14) declined ({p_rsi:.1f} -> {c_rsi:.1f})",
                        ],
                        provenance=["daily_prices", "indicator_results"],
                        reason="Bearish momentum divergence: Price advanced while momentum indicator declined",
                    )
                )
            elif c_close < p_close and rsi_diff >= 5.0:
                results.append(
                    CrossDomainRelationship(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domains_involved=["PRICE", "TECHNICAL"],
                        metrics_involved=["close_price", "rsi_14"],
                        relationship_type=RelationshipType.DIVERGENCE,
                        strength=RelationshipStrength.SIGNIFICANT,
                        direction="DIVERGENT",
                        evidence=[
                            f"Close price decreased ({p_close:.2f} -> {c_close:.2f}) while RSI(14) recovered ({p_rsi:.1f} -> {c_rsi:.1f})",
                        ],
                        provenance=["daily_prices", "indicator_results"],
                        reason="Bullish momentum divergence: Price declined while momentum indicator recovered",
                    )
                )

        return results

    def _synthesize_price_derivatives(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_price: Any,
        prev_price: Any,
        curr_deriv: dict[str, Any] | None,
        prev_deriv: dict[str, Any] | None,
        price_direction: str | None,
    ) -> CrossDomainRelationship | None:
        """Evaluate Price + Futures OI relationships with neutral naming."""
        if not curr_price or not prev_price or not curr_deriv or not prev_deriv or price_direction is None:
            return None

        c_oi = curr_deriv.get("total_oi", 0)
        p_oi = prev_deriv.get("total_oi", 0)
        if p_oi <= 0:
            return None

        oi_change_pct = round(((c_oi - p_oi) / p_oi) * 100.0, 2)
        if abs(oi_change_pct) < FUTURES_OI_CHANGE_SIGNIFICANT_PCT:
            return None

        if price_direction == "BULLISH":
            if oi_change_pct > 0:
                rel_name = "PRICE_UP_OI_UP"
                reason = f"Price advance (+{curr_price.close}) coincided with open interest expansion (+{oi_change_pct}%), confirming participation in the upward price move"
                rel_type = RelationshipType.CONFIRMATION
            else:
                rel_name = "PRICE_UP_OI_DOWN"
                reason = f"Price advance (+{curr_price.close}) coincided with open interest contraction ({oi_change_pct}%), indicating short-covering / position liquidation"
                rel_type = RelationshipType.DIVERGENCE
        elif price_direction == "BEARISH":
            if oi_change_pct > 0:
                rel_name = "PRICE_DOWN_OI_UP"
                reason = f"Price decline ({curr_price.close}) coincided with open interest expansion (+{oi_change_pct}%), confirming participation in the downward price move"
                rel_type = RelationshipType.CONFIRMATION
            else:
                rel_name = "PRICE_DOWN_OI_DOWN"
                reason = f"Price decline ({curr_price.close}) coincided with open interest contraction ({oi_change_pct}%), indicating long liquidation"
                rel_type = RelationshipType.DIVERGENCE
        else:
            return None

        return CrossDomainRelationship(
            symbol=symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            domains_involved=["PRICE", "DERIVATIVES"],
            metrics_involved=["close_price", "open_interest", rel_name],
            relationship_type=rel_type,
            strength=RelationshipStrength.SIGNIFICANT,
            direction=price_direction if rel_type == RelationshipType.CONFIRMATION else "DIVERGENT",
            evidence=[
                f"total open interest shifted {oi_change_pct}% ({p_oi:,} -> {c_oi:,})",
            ],
            provenance=["derivative_daily_prices"],
            reason=reason,
            metadata={"oi_change_pct": oi_change_pct, "pattern_code": rel_name},
        )

    def _synthesize_institutional(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        curr_flows: list[Any],
        prev_flows: list[Any],
    ) -> CrossDomainRelationship | None:
        """Evaluate Institutional flow (Market-wide context only, never stock-specific!)."""
        if not curr_flows:
            return None

        fii_flow = next((f for f in curr_flows if f.investor_type == "FII"), None)
        dii_flow = next((f for f in curr_flows if f.investor_type == "DII"), None)
        if not fii_flow and not dii_flow:
            return None

        fii_net = float(fii_flow.net_amount) if fii_flow and fii_flow.net_amount is not None else 0.0
        dii_net = float(dii_flow.net_amount) if dii_flow and dii_flow.net_amount is not None else 0.0

        return CrossDomainRelationship(
            symbol=symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            domains_involved=["INSTITUTIONAL"],
            metrics_involved=["market_institutional_flow"],
            relationship_type=RelationshipType.CONTEXT_ONLY,
            strength=RelationshipStrength.CONTEXT_ONLY,
            direction="NEUTRAL",
            evidence=[
                f"Market-wide FII net flow: ₹{fii_net:,.2f} Cr, DII net flow: ₹{dii_net:,.2f} Cr on {eval_d}",
            ],
            provenance=["institutional_flows"],
            reason=f"Market-wide institutional cash flow context: FII net ₹{fii_net:,.2f} Cr, DII net ₹{dii_net:,.2f} Cr (market-wide context, not stock-specific)",
            metadata={"fii_net_crore": fii_net, "dii_net_crore": dii_net},
        )

    def _synthesize_sector(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        stock_change_pct: float | None,
        price_direction: str | None,
        curr_sector: dict[str, Any] | None,
        prev_sector: dict[str, Any] | None,
    ) -> CrossDomainRelationship | None:
        """Evaluate Sector index vs stock relative movement."""
        if stock_change_pct is None or not curr_sector or not prev_sector:
            return None

        c_idx = curr_sector.get("close")
        p_idx = prev_sector.get("close")
        if not c_idx or not p_idx or p_idx <= 0:
            return None

        idx_change_pct = round(((c_idx - p_idx) / p_idx) * 100.0, 2)
        idx_name = curr_sector.get("index_name", curr_sector.get("index_code", "Sector"))

        if (stock_change_pct > 0 and idx_change_pct > 0) or (stock_change_pct < 0 and idx_change_pct < 0):
            return CrossDomainRelationship(
                symbol=symbol,
                evaluation_date=eval_d,
                comparison_date=comp_d,
                domains_involved=["PRICE", "SECTOR"],
                metrics_involved=["close_price", "sector_index_close"],
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.NORMAL,
                direction=price_direction,
                evidence=[
                    f"Stock moved {stock_change_pct}% while sector index {idx_name} moved {idx_change_pct}%",
                ],
                provenance=["market_index_prices"],
                reason=f"Stock movement ({stock_change_pct}%) confirmed by aligned sector index move in {idx_name} ({idx_change_pct}%)",
                metadata={"stock_change_pct": stock_change_pct, "sector_change_pct": idx_change_pct},
            )
        elif (stock_change_pct > 0 and idx_change_pct < 0) or (stock_change_pct < 0 and idx_change_pct > 0):
            return CrossDomainRelationship(
                symbol=symbol,
                evaluation_date=eval_d,
                comparison_date=comp_d,
                domains_involved=["PRICE", "SECTOR"],
                metrics_involved=["close_price", "sector_index_close"],
                relationship_type=RelationshipType.DIVERGENCE,
                strength=RelationshipStrength.NORMAL,
                direction="DIVERGENT",
                evidence=[
                    f"Stock moved {stock_change_pct}% while sector index {idx_name} moved opposingly {idx_change_pct}%",
                ],
                provenance=["market_index_prices"],
                reason=f"Sector divergence: Stock move ({stock_change_pct}%) moved counter to sector index {idx_name} ({idx_change_pct}%)",
                metadata={"stock_change_pct": stock_change_pct, "sector_change_pct": idx_change_pct},
            )

        return None

    def _synthesize_corporate(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        announcements: list[Any],
        actions: list[Any],
    ) -> CrossDomainRelationship | None:
        """Evaluate Corporate events (Context only, no causation!)."""
        if not announcements and not actions:
            return None

        events_summary = []
        for a in announcements[:2]:
            events_summary.append(f"Announcement ({getattr(a, 'category', 'OTHER')}): {a.title[:80]}")
        for ca in actions[:2]:
            events_summary.append(f"Corporate Action ({getattr(ca, 'action_type', 'ACTION')}) ex-date: {ca.ex_date}")

        return CrossDomainRelationship(
            symbol=symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            domains_involved=["CORPORATE"],
            metrics_involved=["corporate_event"],
            relationship_type=RelationshipType.CONTEXT_ONLY,
            strength=RelationshipStrength.CONTEXT_ONLY,
            direction="NEUTRAL",
            evidence=events_summary,
            provenance=["corporate_announcements", "corporate_actions"],
            reason="Price/volume changes occurred within an evaluation window containing corporate announcements/actions (event context, no causality inferred)",
            metadata={"announcement_count": len(announcements), "action_count": len(actions)},
        )

    def _synthesize_deals(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        deals: list[Any],
    ) -> CrossDomainRelationship | None:
        """Evaluate Bulk/Block deals (Context only, no speculation!)."""
        if not deals:
            return None

        deal_summaries = []
        for d in deals[:3]:
            qty = getattr(d, "quantity", 0)
            price = getattr(d, "trade_price", 0)
            client = getattr(d, "client_name", "UNKNOWN")
            deal_type = getattr(d, "deal_type", "DEAL")
            deal_summaries.append(f"{deal_type}: {client} traded {qty:,} shares at ₹{price:.2f}")

        return CrossDomainRelationship(
            symbol=symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            domains_involved=["DEAL"],
            metrics_involved=["bulk_block_deal"],
            relationship_type=RelationshipType.CONTEXT_ONLY,
            strength=RelationshipStrength.CONTEXT_ONLY,
            direction="NEUTRAL",
            evidence=deal_summaries,
            provenance=["bulk_block_deals"],
            reason="Price/volume changes occurred within an evaluation window containing bulk/block transaction disclosures (deal context, no intent inferred)",
            metadata={"deal_count": len(deals)},
        )

    # -----------------------------------------------------------------------
    # Sorting & Lifecycle
    # -----------------------------------------------------------------------

    def _sort_relationships(self, rels: list[CrossDomainRelationship]) -> list[CrossDomainRelationship]:
        """Sort relationships deterministically by strength, relationship type, domains, and reason."""
        sorted_rels = list(rels)
        sorted_rels.sort(
            key=lambda r: (
                SYNTHESIS_STRENGTH_SORT_ORDER.get(r.strength, 99),
                SYNTHESIS_RELATIONSHIP_SORT_ORDER.get(r.relationship_type, 99),
                "-".join(r.domains_involved),
                "-".join(r.metrics_involved),
                r.reason,
            )
        )
        return sorted_rels

    def _determine_lifecycle(
        self,
        relationships: list[CrossDomainRelationship],
        comparison_window: int,
    ) -> LifecycleState | None:
        """Determine pattern lifecycle state across window scales."""
        if not relationships:
            return None

        # Check for confirmed relationships
        confirmations = [r for r in relationships if r.relationship_type == RelationshipType.CONFIRMATION]
        divergences = [r for r in relationships if r.relationship_type == RelationshipType.DIVERGENCE]

        if comparison_window == 1:
            state = PatternLifecycle.NEW
            desc = "Pattern newly emerged in the immediate single-session evaluation window"
        elif len(confirmations) >= 2:
            state = PatternLifecycle.STRENGTHENING
            desc = f"Pattern strengthening across multi-session comparison window ({comparison_window} sessions)"
        elif len(confirmations) == 1 and len(divergences) == 0:
            state = PatternLifecycle.CONTINUING
            desc = f"Pattern continuing consistently across multi-session comparison window ({comparison_window} sessions)"
        elif len(divergences) > 0:
            state = PatternLifecycle.WEAKENING
            desc = f"Cross-domain divergence emerging across {comparison_window}-session window"
        else:
            state = PatternLifecycle.CONTINUING
            desc = f"Relationship observed across {comparison_window}-session window"

        return LifecycleState(
            state=state,
            current_window=comparison_window,
            description=desc,
        )
