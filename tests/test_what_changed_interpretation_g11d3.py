"""Comprehensive Test Suite for G.11-D-3: Stock Intelligence Interpretation Layer.

Validates:
  1. State machine: bullish/bearish supported, partial bullish/bearish, mixed,
     context-only, insufficient evidence, neutral observation
  2. Evidence classification: PRIMARY (MAJOR/SIGNIFICANT+CONFIRMATION only),
     SUPPORTING, CONFLICTING (DIVERGENCE never PRIMARY), CONTEXT, MISSING
  3. Context domain protection: CORPORATE/INSTITUTIONAL/DEAL never directional
  4. Corrected directional convergence (context/missing excluded, no double count)
  5. Sector interpretation (metadata preserved, magnitude context)
  6. Derivatives interpretation (aggregate-OI terminology, structured metadata)
  7. Pattern lifecycle: NEW/CONTINUING/STRENGTHENING/WEAKENING/RESOLVED/CHANGED,
     data-gap flag, multiple simultaneous patterns, no-history fallback
  8. Pattern fingerprint stability (order-independent, strength-independent)
  9. PIT safety propagation
 10. Determinism (repeated execution, ordering, generated_at isolation)
 11. AI narrative safety (trading terms, predictions, intent, certainty,
     invented numbers) and deterministic fallback
 12. API endpoint security and schema
 13. HDFCBANK regression: 2026-09-15 muted price (CONTEXT_ONLY) and
     2026-09-09 bearish multi-domain evidence
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.base import AIProviderError, BaseAIProvider
from app.ai.interpretation_narrative_service import InterpretationNarrativeService
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.schemas.what_changed_synthesis import (
    ConvergenceScore,
    CrossDomainRelationship,
    CrossDomainSynthesisResult,
)
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)
from app.services.what_changed_synthesis_config import (
    EvidenceQuality,
    RelationshipStrength,
    RelationshipType,
)
from tests.auth_helpers import auth_client

EVAL = date(2026, 9, 15)
COMP = date(2026, 9, 14)
GENERATED = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)


# ===========================================================================
# Helpers
# ===========================================================================

def make_rel(
    domains,
    metrics,
    rtype,
    strength,
    direction=None,
    metadata=None,
    reason="deterministic test reason",
    provenance=None,
    symbol="TEST",
    eval_date=None,
    comp_date=None,
):
    return CrossDomainRelationship(
        symbol=symbol,
        evaluation_date=eval_date or EVAL,
        comparison_date=comp_date or COMP,
        domains_involved=list(domains),
        metrics_involved=list(metrics),
        relationship_type=RelationshipType(rtype),
        strength=RelationshipStrength(strength),
        direction=direction,
        evidence=[],
        conflicting_evidence=[],
        missing_evidence=[],
        provenance=provenance or ["test_source"],
        confidence="FACT_VERIFIED",
        reason=reason,
        metadata=metadata or {},
    )


def make_d2(
    relationships=(),
    supporting=(),
    conflicting=(),
    missing=(),
    context=(),
    available=(),
    conv_value=0,
    conv_max=0,
    quality=EvidenceQuality.INSUFFICIENT_DATA,
    pit=True,
    eval_date=None,
    comp_date=None,
    symbol="TEST",
    generated_at=None,
):
    return CrossDomainSynthesisResult(
        symbol=symbol,
        evaluation_date=eval_date or EVAL,
        comparison_date=comp_date or COMP,
        comparison_window_sessions=1,
        pit_safe=pit,
        evidence_quality=quality,
        convergence_score=ConvergenceScore(
            value=conv_value,
            scale="domain_count",
            max_possible=conv_max,
            contributing_domains=sorted(supporting),
            conflicting_domains=sorted(conflicting),
            excluded_domains=sorted(context),
            missing_domains=sorted(missing),
            interpretation="test",
        ),
        supporting_domains=sorted(supporting),
        conflicting_domains=sorted(conflicting),
        missing_domains=sorted(missing),
        context_domains=sorted(context),
        available_domains=sorted(available),
        relationships=list(relationships),
        lifecycle=None,
        freshness={},
        data_quality_summary={},
        generated_at=generated_at or GENERATED,
    )


async def interpret(current, previous=None, include_ai=False, narrative_service=None):
    """Run the deterministic interpretation with injected D-2 snapshots.

    When ``previous`` is None the previous-fetch path raises (no history) so
    lifecycle deterministically falls back to the no-history NEW state.
    """
    from app.services.what_changed_synthesis_service import WhatChangedSynthesisService

    synth = AsyncMock(spec=WhatChangedSynthesisService)
    synth.synthesize.side_effect = ValueError("no previous data available")
    svc = WhatChangedInterpretationService(
        MagicMock(),
        synthesis_service=synth,
        narrative_service=narrative_service,
    )
    return await svc.interpret(
        "TEST",
        current_synthesis=current,
        previous_synthesis=previous,
        include_ai_narrative=include_ai,
    )


class WhatChangedSynthesisServiceHolder:
    """Late-import holder for constructing real D-2 services in integration tests."""

    @staticmethod
    def service_cls():
        from app.services.what_changed_synthesis_service import (
            WhatChangedSynthesisService,
        )
        return WhatChangedSynthesisService


class _FakeProvider(BaseAIProvider):
    def __init__(self, output=None, fail=False):
        self._output = output
        self._fail = fail

    @property
    def name(self) -> str:
        return "fake"

    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        if self._fail:
            raise AIProviderError("provider down")
        return self._output

    async def health(self) -> bool:
        return True


FACTS = {
    "symbol": "TEST",
    "evaluation_date": "2026-09-15",
    "comparison_date": "2026-09-14",
    "overall_state": "BULLISH_EVIDENCE_SUPPORTED",
    "convergence": {
        "directional_value": 2,
        "directional_max_possible": 2,
        "supporting_domains": ["PARTICIPATION", "PRICE"],
        "conflicting_domains": [],
    },
    "metadata": {"price_change_pct": 2.5, "volume_change_pct": 60.41},
}


# ===========================================================================
# 1. Deterministic State Machine
# ===========================================================================

@pytest.mark.asyncio
async def test_state_bullish_evidence_supported():
    rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "MAJOR", "BULLISH", {"price_change_pct": 5.2, "volume_change_pct": 120.0},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert res.overall_state == "BULLISH_EVIDENCE_SUPPORTED"
    assert res.directional_bias == "BULLISH_EVIDENCE"


@pytest.mark.asyncio
async def test_state_bearish_evidence_supported():
    rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BEARISH", {"price_change_pct": -2.26, "volume_change_pct": 97.02},
    )
    rsi = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "CONFIRMATION",
        "SIGNIFICANT", "BEARISH", {},
    )
    res = await interpret(
        make_d2([rel, rsi], supporting=("PRICE", "PARTICIPATION", "TECHNICAL"),
                conv_value=3, conv_max=3, quality=EvidenceQuality.CONFIRMED)
    )
    assert res.overall_state == "BEARISH_EVIDENCE_SUPPORTED"
    assert res.directional_bias == "BEARISH_EVIDENCE"


@pytest.mark.asyncio
async def test_state_partially_supported_bullish_bias():
    rel = make_rel(
        ["PRICE", "SECTOR"], ["close_price", "sector_index_close"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"stock_change_pct": 3.0, "sector_change_pct": 0.52},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE",), conv_value=1, conv_max=1,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.overall_state == "PARTIALLY_SUPPORTED_BULLISH_BIAS"
    assert res.directional_bias == "BULLISH_EVIDENCE"


@pytest.mark.asyncio
async def test_state_partially_supported_bearish_bias():
    rel = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BEARISH", {"delivery_change_pp": 12.0},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE",), conv_value=1, conv_max=1,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.overall_state == "PARTIALLY_SUPPORTED_BEARISH_BIAS"
    assert res.directional_bias == "BEARISH_EVIDENCE"


@pytest.mark.asyncio
async def test_state_mixed_supporting_and_conflicting():
    conf_rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 2.5},
    )
    div_rel = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "DIVERGENCE",
        "SIGNIFICANT", "DIVERGENT", {},
    )
    res = await interpret(
        make_d2([conf_rel, div_rel], supporting=("PRICE", "PARTICIPATION"),
                conflicting=("TECHNICAL",), conv_value=2, conv_max=3,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.overall_state == "MIXED_EVIDENCE"
    assert res.directional_bias == "MIXED"


@pytest.mark.asyncio
async def test_state_conflicting_only_is_mixed():
    div_rel = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "DIVERGENCE",
        "SIGNIFICANT", "DIVERGENT", {},
    )
    res = await interpret(
        make_d2([div_rel], conflicting=("TECHNICAL",), conv_value=0, conv_max=1,
                quality=EvidenceQuality.CONFLICTING)
    )
    assert res.overall_state == "MIXED_EVIDENCE"
    assert res.directional_bias == "MIXED"


@pytest.mark.asyncio
async def test_state_context_only_muted_price_hdfc_case():
    """HDFCBANK 2026-09-15 semantics: muted price + volume expansion stays CONTEXT_ONLY."""
    muted = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONTEXT_ONLY",
        "NORMAL", "NEUTRAL",
        {"price_change_pct": 1.17, "volume_change_pct": 60.41,
         "pattern": "VOLUME_EXPANSION_WITH_MUTED_PRICE"},
        reason="Volume expanded 60.41% while price movement (+1.17%) remained below the 2.0% meaningful-change threshold",
    )
    res = await interpret(
        make_d2([muted], context=("PARTICIPATION",), available=("PRICE", "PARTICIPATION"),
                quality=EvidenceQuality.CONTEXT_ONLY)
    )
    assert res.overall_state == "CONTEXT_ONLY"
    assert res.directional_bias == "NEUTRAL"
    assert res.convergence.directional_value == 0
    assert res.convergence.directional_max_possible == 0
    assert res.convergence.convergence_ratio is None
    assert len(res.context_evidence) == 1
    assert res.context_evidence[0].metadata["pattern"] == "VOLUME_EXPANSION_WITH_MUTED_PRICE"
    assert "PRICE_CONFIRMATION_REQUIRED" in [w.code for w in res.watch_items]


@pytest.mark.asyncio
async def test_state_insufficient_evidence():
    res = await interpret(make_d2())
    assert res.overall_state == "INSUFFICIENT_EVIDENCE"
    assert res.directional_bias == "NO_DIRECTIONAL_SIGNAL"


@pytest.mark.asyncio
async def test_state_neutral_observation_with_relationships_only():
    insuff = make_rel(
        ["PRICE", "TREND"], ["close_price", "ema_20"], "INSUFFICIENT_EVIDENCE",
        "NORMAL", None, {},
    )
    res = await interpret(make_d2([insuff]))
    assert res.overall_state == "NEUTRAL_OBSERVATION"
    assert res.directional_bias == "NEUTRAL"


@pytest.mark.asyncio
async def test_state_never_invents_direction_without_metadata():
    """Price supporting domain present but no D-2 metadata to derive direction:
    direction must NOT be invented."""
    res = await interpret(
        make_d2(supporting=("PRICE",), conv_value=1, conv_max=1,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.overall_state == "NEUTRAL_OBSERVATION"
    assert res.directional_bias == "NEUTRAL"


@pytest.mark.asyncio
async def test_state_case_d_price_absent_institutional_context_only():
    """Case D: price absent + only institutional context → CONTEXT_ONLY, no direction."""
    inst = make_rel(
        ["INSTITUTIONAL"], ["market_institutional_flow"], "CONTEXT_ONLY",
        "CONTEXT_ONLY", "NEUTRAL", {"fii_net_crore": 100.0, "dii_net_crore": 50.0},
    )
    res = await interpret(
        make_d2([inst], context=("INSTITUTIONAL",), missing=("PRICE", "PARTICIPATION"),
                quality=EvidenceQuality.CONTEXT_ONLY)
    )
    assert res.overall_state == "CONTEXT_ONLY"
    assert res.directional_bias == "NEUTRAL"


# ===========================================================================
# 2. Evidence Classification
# ===========================================================================

@pytest.mark.asyncio
async def test_classification_major_confirmation_is_primary():
    rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "MAJOR", "BULLISH", {"price_change_pct": 6.0, "volume_change_pct": 150.0},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert len(res.primary_evidence) == 1
    assert res.primary_evidence[0].classification == "PRIMARY"


@pytest.mark.asyncio
async def test_classification_significant_confirmation_is_primary():
    rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BEARISH", {"price_change_pct": -2.26, "volume_change_pct": 97.0},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert len(res.primary_evidence) == 1


@pytest.mark.asyncio
async def test_classification_normal_confirmation_is_supporting():
    rel = make_rel(
        ["PRICE", "SECTOR"], ["close_price", "sector_index_close"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"stock_change_pct": 2.5, "sector_change_pct": 0.6},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "SECTOR"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert len(res.supporting_evidence) == 1
    assert res.supporting_evidence[0].classification == "SUPPORTING"
    assert len(res.primary_evidence) == 0


@pytest.mark.asyncio
async def test_classification_divergence_is_never_primary():
    rel = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "DIVERGENCE",
        "SIGNIFICANT", "DIVERGENT", {},
    )
    res = await interpret(
        make_d2([rel], conflicting=("TECHNICAL",), conv_value=0, conv_max=1,
                quality=EvidenceQuality.CONFLICTING)
    )
    assert len(res.conflicting_evidence) == 1
    assert res.conflicting_evidence[0].classification == "CONFLICTING"
    assert len(res.primary_evidence) == 0
    assert len(res.supporting_evidence) == 0


@pytest.mark.asyncio
async def test_classification_conflict_type_is_conflicting():
    rel = make_rel(
        ["PRICE", "TREND"], ["close_price", "ema_20"], "CONFLICT",
        "NORMAL", None, {},
    )
    res = await interpret(
        make_d2([rel], conflicting=("TREND",), conv_value=0, conv_max=1,
                quality=EvidenceQuality.CONFLICTING)
    )
    assert res.conflicting_evidence[0].classification == "CONFLICTING"


@pytest.mark.asyncio
async def test_classification_opposing_confirmation_is_conflicting():
    """Price bearish but a trend confirmation asserts BULLISH → conflicting."""
    vol = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BEARISH", {"price_change_pct": -2.26},
    )
    trend = make_rel(
        ["PRICE", "TREND"], ["close_price", "ema_20"], "CONFIRMATION",
        "MAJOR", "BULLISH", {},
    )
    res = await interpret(
        make_d2([vol, trend], supporting=("PRICE", "PARTICIPATION"),
                conflicting=("TREND",), conv_value=2, conv_max=3,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.overall_state == "MIXED_EVIDENCE"
    trend_items = [e for e in res.conflicting_evidence if "TREND" in e.domains_involved]
    assert len(trend_items) == 1
    assert trend_items[0].classification == "CONFLICTING"


@pytest.mark.asyncio
async def test_classification_context_only_type_is_context():
    rel = make_rel(
        ["CORPORATE"], ["corporate_event"], "CONTEXT_ONLY",
        "CONTEXT_ONLY", "NEUTRAL", {"announcement_count": 1},
    )
    res = await interpret(make_d2([rel], context=("CORPORATE",)))
    assert res.context_evidence[0].classification == "CONTEXT"


@pytest.mark.asyncio
async def test_classification_partial_confirmation_is_supporting():
    rel = make_rel(
        ["PRICE", "TREND"], ["close_price", "ema_20"], "PARTIAL_CONFIRMATION",
        "NORMAL", "BULLISH", {},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "TREND"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert res.supporting_evidence[0].classification == "SUPPORTING"


@pytest.mark.asyncio
async def test_classification_missing_domain_is_missing_evidence():
    res = await interpret(
        make_d2(missing=("TECHNICAL", "TREND", "DERIVATIVES", "SECTOR", "DELIVERY"))
    )
    missing_domains = [m.domain for m in res.missing_evidence]
    assert missing_domains == ["DELIVERY", "DERIVATIVES", "SECTOR", "TECHNICAL", "TREND"]
    assert all(m.classification == "MISSING" for m in res.missing_evidence)


# ===========================================================================
# 3. Context Domain Protection
# ===========================================================================

@pytest.mark.asyncio
async def test_context_corporate_never_directional():
    rel = make_rel(
        ["CORPORATE"], ["corporate_event"], "CONTEXT_ONLY",
        "CONTEXT_ONLY", "NEUTRAL", {"announcement_count": 2, "action_count": 0},
    )
    res = await interpret(make_d2([rel], context=("CORPORATE",)))
    assert res.convergence.directional_value == 0
    assert res.convergence.directional_max_possible == 0
    assert res.directional_bias == "NEUTRAL"
    assert "CORPORATE" in res.convergence.context_domains
    assert "CORPORATE" not in res.convergence.supporting_domains


@pytest.mark.asyncio
async def test_context_institutional_never_directional():
    rel = make_rel(
        ["INSTITUTIONAL"], ["market_institutional_flow"], "CONTEXT_ONLY",
        "CONTEXT_ONLY", "NEUTRAL", {"fii_net_crore": 500.0, "dii_net_crore": 100.0},
    )
    res = await interpret(make_d2([rel], context=("INSTITUTIONAL",)))
    assert res.convergence.directional_value == 0
    assert res.directional_bias == "NEUTRAL"
    inst_limitations = [limitation for limitation in res.limitations if limitation.code == "INSTITUTIONAL_CONTEXT_ONLY"]
    assert len(inst_limitations) == 1
    assert inst_limitations[0].description == "institutional evidence is market-wide context only"


@pytest.mark.asyncio
async def test_context_deal_never_directional():
    rel = make_rel(
        ["DEAL"], ["bulk_block_deal"], "CONTEXT_ONLY",
        "CONTEXT_ONLY", "NEUTRAL", {"deal_count": 1},
    )
    res = await interpret(make_d2([rel], context=("DEAL",)))
    assert res.convergence.directional_value == 0
    assert res.directional_bias == "NEUTRAL"
    assert res.context_evidence[0].domain == "DEAL"


@pytest.mark.asyncio
async def test_context_domains_excluded_from_directional_max():
    """D-2 max_possible inflation bug is NOT recreated: 3 directional + 2 context = 3, not 5."""
    rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0},
    )
    corp = make_rel(["CORPORATE"], ["corporate_event"], "CONTEXT_ONLY",
                    "CONTEXT_ONLY", "NEUTRAL", {})
    inst = make_rel(["INSTITUTIONAL"], ["market_institutional_flow"], "CONTEXT_ONLY",
                    "CONTEXT_ONLY", "NEUTRAL", {})
    res = await interpret(
        make_d2([rel, corp, inst], supporting=("PRICE", "PARTICIPATION"),
                context=("CORPORATE", "INSTITUTIONAL"),
                conv_value=2, conv_max=5, quality=EvidenceQuality.CONFIRMED)
    )
    assert res.convergence.directional_max_possible == 2
    assert res.convergence.directional_value == 2
    assert res.convergence.convergence_ratio == 1.0


@pytest.mark.asyncio
async def test_context_only_relationships_never_establish_direction():
    corp = make_rel(["CORPORATE"], ["corporate_event"], "CONTEXT_ONLY",
                    "CONTEXT_ONLY", "NEUTRAL", {})
    deal = make_rel(["DEAL"], ["bulk_block_deal"], "CONTEXT_ONLY",
                    "CONTEXT_ONLY", "NEUTRAL", {})
    res = await interpret(
        make_d2([corp, deal], context=("CORPORATE", "DEAL"),
                quality=EvidenceQuality.CONTEXT_ONLY)
    )
    assert res.overall_state == "CONTEXT_ONLY"
    assert res.directional_bias == "NEUTRAL"
    assert len(res.primary_evidence) == 0
    assert len(res.supporting_evidence) == 0


# ===========================================================================
# 4. Corrected Directional Convergence
# ===========================================================================

@pytest.mark.asyncio
async def test_convergence_unique_domain_no_double_count():
    """Volume AND delivery confirmations both map to PARTICIPATION — counted once."""
    rel_vol = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0},
    )
    rel_del = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"delivery_change_pp": 10.5},
    )
    res = await interpret(
        make_d2([rel_vol, rel_del],
                supporting=("PRICE", "PARTICIPATION", "PARTICIPATION"),
                conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    )
    assert res.convergence.directional_value == 2
    assert res.convergence.supporting_domains == ["PARTICIPATION", "PRICE"]


@pytest.mark.asyncio
async def test_convergence_conflicting_domains_counted_in_max():
    rel_vol = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0},
    )
    res = await interpret(
        make_d2([rel_vol], supporting=("PRICE", "PARTICIPATION"),
                conflicting=("TECHNICAL",), conv_value=2, conv_max=3,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert res.convergence.directional_max_possible == 3
    assert res.convergence.directional_value == 2
    assert res.convergence.convergence_ratio == round(2 / 3, 4)


@pytest.mark.asyncio
async def test_convergence_missing_domains_excluded_from_max():
    res = await interpret(
        make_d2(missing=("TECHNICAL", "TREND", "DERIVATIVES", "SECTOR"))
    )
    assert res.convergence.directional_max_possible == 0
    assert res.convergence.convergence_ratio is None
    assert res.convergence.missing_domains == ["DERIVATIVES", "SECTOR", "TECHNICAL", "TREND"]


@pytest.mark.asyncio
async def test_convergence_empty_directional_domains():
    res = await interpret(make_d2())
    assert res.convergence.directional_value == 0
    assert res.convergence.directional_max_possible == 0
    assert res.convergence.convergence_ratio is None


@pytest.mark.asyncio
async def test_convergence_interpretation_string_mentions_conflicts():
    rel_vol = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0},
    )
    res = await interpret(
        make_d2([rel_vol], supporting=("PRICE", "PARTICIPATION"),
                conflicting=("TECHNICAL",), conv_value=2, conv_max=3,
                quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert "2 of 3" in res.convergence.interpretation
    assert "conflicting" in res.convergence.interpretation


# ===========================================================================
# 5. Sector Interpretation
# ===========================================================================

@pytest.mark.asyncio
async def test_sector_meaningful_confirmation_supporting_with_metadata():
    rel = make_rel(
        ["PRICE", "SECTOR"], ["close_price", "sector_index_close"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"stock_change_pct": 5.0, "sector_change_pct": 0.52},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "SECTOR"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    sector_items = [e for e in res.supporting_evidence if "SECTOR" in e.domains_involved]
    assert len(sector_items) == 1
    assert sector_items[0].metadata["sector_change_pct"] == 0.52
    assert sector_items[0].metadata["stock_change_pct"] == 5.0
    assert sector_items[0].classification == "SUPPORTING"


@pytest.mark.asyncio
async def test_sector_small_movement_metadata_preserved_not_upgraded():
    """A tiny same-direction sector move stays NORMAL/SUPPORTING; the magnitude
    is preserved in metadata so the narrative can describe it as context."""
    rel = make_rel(
        ["PRICE", "SECTOR"], ["close_price", "sector_index_close"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"stock_change_pct": 5.0, "sector_change_pct": 0.08},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "SECTOR"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    sector_items = [e for e in res.supporting_evidence if "SECTOR" in e.domains_involved]
    assert sector_items[0].metadata["sector_change_pct"] == 0.08
    assert sector_items[0].strength == RelationshipStrength.NORMAL
    assert sector_items[0].classification == "SUPPORTING"
    assert len(res.primary_evidence) == 0


@pytest.mark.asyncio
async def test_sector_opposite_direction_is_conflicting():
    rel = make_rel(
        ["PRICE", "SECTOR"], ["close_price", "sector_index_close"], "DIVERGENCE",
        "NORMAL", "DIVERGENT", {"stock_change_pct": 2.5, "sector_change_pct": -0.7},
    )
    res = await interpret(
        make_d2([rel], conflicting=("SECTOR",), conv_value=0, conv_max=1,
                quality=EvidenceQuality.CONFLICTING)
    )
    assert res.conflicting_evidence[0].classification == "CONFLICTING"


@pytest.mark.asyncio
async def test_sector_missing_is_not_divergence():
    res = await interpret(make_d2(missing=("SECTOR",)))
    assert not any("SECTOR" in e.domains_involved for e in res.conflicting_evidence)
    assert any(m.domain == "SECTOR" for m in res.missing_evidence)
    assert "SECTOR_CONFIRMATION_REQUIRED" in [w.code for w in res.watch_items]


# ===========================================================================
# 6. Derivatives Interpretation
# ===========================================================================

@pytest.mark.asyncio
async def test_derivatives_confirmation_primary_with_structured_metadata():
    rel = make_rel(
        ["PRICE", "DERIVATIVES"], ["close_price", "open_interest", "PRICE_UP_OI_UP"],
        "CONFIRMATION", "SIGNIFICANT", "BULLISH",
        {"oi_change_pct": 5.4, "pattern_code": "PRICE_UP_OI_UP"},
        reason="Price advance coincided with open interest expansion (+5.4%), confirming participation",
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "DERIVATIVES"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert len(res.primary_evidence) == 1
    assert res.primary_evidence[0].metadata["oi_change_pct"] == 5.4
    assert res.primary_evidence[0].metadata["pattern_code"] == "PRICE_UP_OI_UP"


@pytest.mark.asyncio
async def test_derivatives_divergence_conflicting():
    rel = make_rel(
        ["PRICE", "DERIVATIVES"], ["close_price", "open_interest", "PRICE_UP_OI_DOWN"],
        "DIVERGENCE", "SIGNIFICANT", "DIVERGENT",
        {"oi_change_pct": -4.2, "pattern_code": "PRICE_UP_OI_DOWN"},
    )
    res = await interpret(
        make_d2([rel], conflicting=("DERIVATIVES",), conv_value=0, conv_max=1,
                quality=EvidenceQuality.CONFLICTING)
    )
    assert res.conflicting_evidence[0].classification == "CONFLICTING"
    assert res.conflicting_evidence[0].metadata["pattern_code"] == "PRICE_UP_OI_DOWN"


@pytest.mark.asyncio
async def test_derivatives_aggregate_oi_never_described_as_futures_positioning():
    """D-3 fallback narrative must not call aggregate OI 'futures positioning'."""
    rel = make_rel(
        ["PRICE", "DERIVATIVES"], ["close_price", "open_interest", "PRICE_UP_OI_UP"],
        "CONFIRMATION", "SIGNIFICANT", "BULLISH",
        {"oi_change_pct": 5.4, "pattern_code": "PRICE_UP_OI_UP"},
    )
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "DERIVATIVES"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    lowered = res.fallback_narrative.lower()
    assert "futures positioning" not in lowered


@pytest.mark.asyncio
async def test_derivatives_missing_is_missing_not_bearish():
    res = await interpret(make_d2(missing=("DERIVATIVES",)))
    assert not res.conflicting_evidence
    assert any(m.domain == "DERIVATIVES" for m in res.missing_evidence)
    assert res.overall_state == "INSUFFICIENT_EVIDENCE"
    assert "DERIVATIVE_DATA_REQUIRED" in [w.code for w in res.watch_items]


# ===========================================================================
# 7. Historical Lifecycle (T vs T-1)
# ===========================================================================

def _pv_rel(strength="SIGNIFICANT", rtype="CONFIRMATION", direction="BULLISH",
            metadata=None, eval_date=None, comp_date=None, symbol="TEST"):
    return make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], rtype, strength,
        direction, metadata or {"price_change_pct": 3.0},
        eval_date=eval_date, comp_date=comp_date, symbol=symbol,
    )


@pytest.mark.asyncio
async def test_lifecycle_no_previous_all_new():
    rel = _pv_rel()
    res = await interpret(make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                                  conv_value=2, conv_max=2,
                                  quality=EvidenceQuality.CONFIRMED))
    assert len(res.pattern_lifecycle_records) == 1
    assert res.pattern_lifecycle_records[0].lifecycle_state == "NEW"
    assert res.overall_lifecycle.new_count == 1
    assert res.overall_lifecycle.pattern_count == 1
    codes = [limitation.code for limitation in res.limitations]
    assert "NO_HISTORY" in codes


@pytest.mark.asyncio
async def test_lifecycle_continuing_same_pattern_same_evidence():
    current = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pattern_lifecycle_records[0].lifecycle_state == "CONTINUING"
    assert res.overall_lifecycle.continuing_count == 1
    assert "NO_HISTORY" not in [limitation.code for limitation in res.limitations]


@pytest.mark.asyncio
async def test_lifecycle_strengthening_on_strength_increase():
    current = make_d2([_pv_rel(strength="MAJOR")], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([_pv_rel(strength="NORMAL")], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    record = res.pattern_lifecycle_records[0]
    assert record.lifecycle_state == "STRENGTHENING"
    assert record.current_strength == RelationshipStrength.MAJOR
    assert record.previous_strength == RelationshipStrength.NORMAL


@pytest.mark.asyncio
async def test_lifecycle_weakening_on_strength_decrease():
    current = make_d2([_pv_rel(strength="NORMAL")], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([_pv_rel(strength="MAJOR")], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pattern_lifecycle_records[0].lifecycle_state == "WEAKENING"


@pytest.mark.asyncio
async def test_lifecycle_strengthening_on_convergence_increase():
    current = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=3, conv_max=3, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=1, conv_max=1, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pattern_lifecycle_records[0].lifecycle_state == "STRENGTHENING"


@pytest.mark.asyncio
async def test_lifecycle_weakening_on_convergence_decrease():
    current = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=1, conv_max=1, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([_pv_rel()], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=3, conv_max=3, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pattern_lifecycle_records[0].lifecycle_state == "WEAKENING"


@pytest.mark.asyncio
async def test_lifecycle_resolved_pattern_present_only_at_t1():
    prev_rel = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"delivery_change_pp": 10.0},
    )
    current = make_d2(supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                      quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([prev_rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert len(res.pattern_lifecycle_records) == 1
    record = res.pattern_lifecycle_records[0]
    assert record.lifecycle_state == "RESOLVED"
    assert record.current_strength is None
    assert record.previous_strength == RelationshipStrength.NORMAL
    assert res.overall_lifecycle.resolved_count == 1


@pytest.mark.asyncio
async def test_lifecycle_changed_descriptor_on_type_change():
    prev_rel = _pv_rel(rtype="CONFIRMATION", direction="BULLISH")
    curr_rel = _pv_rel(rtype="DIVERGENCE", direction="DIVERGENT")
    current = make_d2([curr_rel], conflicting=("PARTICIPATION",), conv_value=0,
                      conv_max=2, quality=EvidenceQuality.CONFLICTING)
    previous = make_d2([prev_rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    states = {r.lifecycle_state for r in res.pattern_lifecycle_records}
    assert states == {"RESOLVED", "NEW"}
    new_records = [r for r in res.pattern_lifecycle_records if r.lifecycle_state == "NEW"]
    assert new_records[0].change_descriptor == "CHANGED"


@pytest.mark.asyncio
async def test_lifecycle_direction_change_resolves_and_creates_new():
    prev_rel = _pv_rel(direction="BULLISH")
    curr_rel = _pv_rel(direction="BEARISH", metadata={"price_change_pct": -3.0})
    current = make_d2([curr_rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([prev_rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    states = sorted(r.lifecycle_state for r in res.pattern_lifecycle_records)
    assert states == ["NEW", "RESOLVED"]


@pytest.mark.asyncio
async def test_lifecycle_multiple_simultaneous_patterns():
    """PRICE+VOLUME continuing, PRICE+RSI new, PRICE+DELIVERY resolved — all at once."""
    curr_pv = _pv_rel()
    curr_rsi = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "DIVERGENCE",
        "SIGNIFICANT", "DIVERGENT", {},
    )
    prev_pv = _pv_rel()
    prev_del = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"delivery_change_pp": 10.0},
    )
    current = make_d2(
        [curr_pv, curr_rsi], supporting=("PRICE", "PARTICIPATION"),
        conflicting=("TECHNICAL",), conv_value=2, conv_max=3,
        quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    previous = make_d2(
        [prev_pv, prev_del], supporting=("PRICE", "PARTICIPATION"),
        conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
        eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.overall_lifecycle.pattern_count == 3
    assert res.overall_lifecycle.new_count == 1
    assert res.overall_lifecycle.continuing_count == 1
    assert res.overall_lifecycle.resolved_count == 1


@pytest.mark.asyncio
async def test_lifecycle_data_gap_flagged_not_evidence_reversal():
    """Delivery pattern disappears because delivery data is missing now → data_gap."""
    prev_del = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BULLISH", {"delivery_change_pp": 10.0},
    )
    current = make_d2(
        missing=("TECHNICAL", "TREND", "DERIVATIVES", "SECTOR", "DELIVERY"),
        supporting=("PRICE", "PARTICIPATION"), conv_value=1, conv_max=1,
        quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    previous = make_d2([prev_del], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    resolved = [r for r in res.pattern_lifecycle_records if r.lifecycle_state == "RESOLVED"]
    assert len(resolved) == 1
    assert resolved[0].data_gap is True
    assert "data gap" in resolved[0].description


@pytest.mark.asyncio
async def test_lifecycle_records_sorted_by_fingerprint():
    rel_a = _pv_rel()
    rel_b = make_rel(
        ["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "CONFIRMATION",
        "SIGNIFICANT", "BEARISH", {},
    )
    current = make_d2(
        [rel_b, rel_a], supporting=("PRICE", "PARTICIPATION", "TECHNICAL"),
        conv_value=3, conv_max=3, quality=EvidenceQuality.CONFIRMED)
    res = await interpret(current)
    fps = [r.pattern_fingerprint for r in res.pattern_lifecycle_records]
    assert fps == sorted(fps)


@pytest.mark.asyncio
async def test_lifecycle_overall_counts_aggregate_deterministically():
    curr_pv = _pv_rel()
    prev_pv = _pv_rel()
    prev_del = make_rel(
        ["PRICE", "DELIVERY"], ["close_price", "delivery_percentage"], "CONFIRMATION",
        "NORMAL", "BULLISH", {},
    )
    current = make_d2([curr_pv], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    previous = make_d2([prev_pv, prev_del], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    s = res.overall_lifecycle
    assert s.pattern_count == 2
    assert (s.new_count, s.continuing_count, s.strengthening_count,
            s.weakening_count, s.resolved_count) == (0, 1, 0, 0, 1)


# ===========================================================================
# 9. Pattern Fingerprint Stability
# ===========================================================================

def test_fingerprint_same_pattern_same_fingerprint():
    a = _pv_rel(metadata={"price_change_pct": 3.0})
    b = _pv_rel(metadata={"price_change_pct": 3.5})
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) == svc.compute_pattern_fingerprint(b)


def test_fingerprint_reordered_domains_same_fingerprint():
    a = make_rel(["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
                 "MAJOR", "BULLISH", {})
    b = make_rel(["VOLUME", "PRICE"], ["volume", "close_price"], "CONFIRMATION",
                 "MAJOR", "BULLISH", {})
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) == svc.compute_pattern_fingerprint(b)


def test_fingerprint_reordered_metrics_same_fingerprint():
    a = make_rel(["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
                 "MAJOR", "BULLISH", {})
    b = make_rel(["PRICE", "VOLUME"], ["volume", "close_price"], "CONFIRMATION",
                 "MAJOR", "BULLISH", {})
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) == svc.compute_pattern_fingerprint(b)


def test_fingerprint_strength_change_same_fingerprint():
    """Strength is NOT part of semantic identity: NORMAL → MAJOR strengthens."""
    a = _pv_rel(strength="NORMAL")
    b = _pv_rel(strength="MAJOR")
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) == svc.compute_pattern_fingerprint(b)


def test_fingerprint_direction_change_different_fingerprint():
    a = _pv_rel(direction="BULLISH")
    b = _pv_rel(direction="BEARISH")
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) != svc.compute_pattern_fingerprint(b)


def test_fingerprint_type_change_different_fingerprint():
    a = _pv_rel(rtype="CONFIRMATION")
    b = _pv_rel(rtype="DIVERGENCE", direction="DIVERGENT")
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) != svc.compute_pattern_fingerprint(b)


def test_fingerprint_metric_change_different_fingerprint():
    a = make_rel(["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "CONFIRMATION",
                 "SIGNIFICANT", "BEARISH", {})
    b = make_rel(["PRICE", "TECHNICAL"], ["close_price", "ema_20"], "CONFIRMATION",
                 "SIGNIFICANT", "BEARISH", {})
    svc = WhatChangedInterpretationService(MagicMock())
    assert svc.compute_pattern_fingerprint(a) != svc.compute_pattern_fingerprint(b)


def test_fingerprint_none_direction_normalized():
    a = make_rel(["PRICE", "TREND"], ["close_price", "ema_20"], "CONFLICT",
                 "NORMAL", None, {})
    b = make_rel(["PRICE", "TREND"], ["close_price", "ema_20"], "CONFLICT",
                 "NORMAL", None, {})
    svc = WhatChangedInterpretationService(MagicMock())
    assert "NONE" in svc.compute_pattern_fingerprint(a)
    assert svc.compute_pattern_fingerprint(a) == svc.compute_pattern_fingerprint(b)


# ===========================================================================
# 10. PIT Safety
# ===========================================================================

@pytest.mark.asyncio
async def test_pit_safe_when_both_snapshots_safe():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED, pit=True)
    previous = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       pit=True, eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pit_safe is True


@pytest.mark.asyncio
async def test_pit_unsafe_when_current_unsafe():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED, pit=False)
    previous = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       pit=True, eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pit_safe is False


@pytest.mark.asyncio
async def test_pit_unsafe_when_previous_unsafe():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED, pit=True)
    previous = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                       conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
                       pit=False, eval_date=COMP, comp_date=date(2026, 9, 13))
    res = await interpret(current, previous=previous)
    assert res.pit_safe is False


@pytest.mark.asyncio
async def test_generated_at_does_not_alter_business_fields():
    rel = _pv_rel()
    kwargs = dict(
        relationships=[rel], supporting=("PRICE", "PARTICIPATION"),
        conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED,
    )
    a = await interpret(make_d2(**kwargs, generated_at=GENERATED))
    b = await interpret(make_d2(**kwargs, generated_at=datetime(2027, 1, 1, tzinfo=UTC)))
    da = a.model_dump(mode="json")
    db_ = b.model_dump(mode="json")
    da.pop("generated_at")
    db_.pop("generated_at")
    assert da == db_


# ===========================================================================
# 11. Determinism
# ===========================================================================

@pytest.mark.asyncio
async def test_deterministic_repeated_interpretation_identical():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    a = await interpret(current)
    b = await interpret(current)
    da = a.model_dump(mode="json")
    db_ = b.model_dump(mode="json")
    da.pop("generated_at")
    db_.pop("generated_at")
    assert da == db_


@pytest.mark.asyncio
async def test_deterministic_evidence_order_preserved_from_d2():
    """D-2 relationships arrive deterministically sorted; D-3 preserves bucket order."""
    vol = make_rel(["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
                   "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0})
    rsi = make_rel(["PRICE", "TECHNICAL"], ["close_price", "rsi_14"], "CONFIRMATION",
                   "SIGNIFICANT", "BULLISH", {})
    current = make_d2([vol, rsi], supporting=("PRICE", "PARTICIPATION", "TECHNICAL"),
                      conv_value=3, conv_max=3, quality=EvidenceQuality.CONFIRMED)
    res = await interpret(current)
    # Both SIGNIFICANT confirmations → primary, in D-2 order (vol before rsi)
    assert [e.domains_involved for e in res.primary_evidence] == \
        [["PRICE", "VOLUME"], ["PRICE", "TECHNICAL"]]


@pytest.mark.asyncio
async def test_deterministic_domain_lists_sorted():
    rel = _pv_rel()
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert res.convergence.supporting_domains == ["PARTICIPATION", "PRICE"]
    assert res.convergence.supporting_domains == sorted(res.convergence.supporting_domains)


@pytest.mark.asyncio
async def test_deterministic_watch_items_sorted():
    res = await interpret(
        make_d2(missing=("SECTOR", "DERIVATIVES", "TECHNICAL", "TREND"))
    )
    codes = [w.code for w in res.watch_items]
    assert codes == sorted(codes)


# ===========================================================================
# 12. AI Narrative Safety (validator)
# ===========================================================================

@pytest.mark.parametrize("text,label", [
    ("Evidence suggests you should BUY now.", "buy"),
    ("The stock is a SELL candidate.", "sell"),
    ("Wait for the ENTRY point.", "entry"),
    ("Plan your EXIT here.", "exit"),
    ("Price TARGET is 750.", "target"),
    ("Place a STOP LOSS below 700.", "stop loss"),
])
def test_ai_forbidden_trading_terms_rejected(text, label):
    valid, errors = InterpretationNarrativeService.validate_narrative(text, FACTS)
    assert not valid, f"{label} must be rejected"
    assert any("trading" in e for e in errors)


@pytest.mark.parametrize("text,label", [
    ("The stock will rise on this evidence.", "will rise"),
    ("It is expected to fall further.", "expected to"),
    ("Likely to rise from here.", "likely to"),
    ("The probability of upside is high.", "probability of"),
    ("The forecast is constructive.", "forecast"),
])
def test_ai_predictions_rejected(text, label):
    valid, errors = InterpretationNarrativeService.validate_narrative(text, FACTS)
    assert not valid, f"{label} must be rejected"
    assert any("prediction" in e for e in errors)


def test_ai_intent_attribution_rejected():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Insider activity suggests planned accumulation.", FACTS)
    assert not valid
    assert any("intent" in e for e in errors)


def test_ai_unsupported_certainty_rejected():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "The outcome is guaranteed and definitely positive.", FACTS)
    assert not valid
    assert any("certainty" in e for e in errors)


def test_ai_invented_number_rejected():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Price moved 3.4% while volume surged 61.2%.", FACTS)
    assert not valid
    assert any("invented number" in e for e in errors)


def test_ai_valid_narrative_accepted():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Two independent directional domains confirm the observed price "
        "movement, with participation rising alongside price.", FACTS)
    assert valid, errors


def test_ai_fact_numbers_allowed():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Price moved +2.5% while volume changed 60.41%.", FACTS)
    assert valid, errors


def test_ai_date_day_component_does_not_authorize_unrelated_percentage():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Price moved 15% across the observation period.", FACTS
    )
    assert not valid
    assert any("invented number: 15" in e for e in errors)


def test_ai_date_year_component_does_not_authorize_unrelated_percentage():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "Volume surged 2026% during the session.", FACTS
    )
    assert not valid
    assert any("invented number: 2026" in e for e in errors)


def test_ai_date_month_or_day_does_not_authorize_invented_metrics():
    valid_m, errors_m = InterpretationNarrativeService.validate_narrative(
        "Price experienced a 9% decline.", FACTS
    )
    assert not valid_m
    assert any("invented number: 9" in e for e in errors_m)

    valid_d, errors_d = InterpretationNarrativeService.validate_narrative(
        "Volume shifted by 14% on the day.", FACTS
    )
    assert not valid_d
    assert any("invented number: 14" in e for e in errors_d)


def test_ai_legitimate_supplied_metrics_pass():
    rich_facts = {
        "symbol": "TEST",
        "evaluation_date": "2026-09-15",
        "comparison_date": "2026-09-14",
        "overall_state": "BULLISH_EVIDENCE_SUPPORTED",
        "convergence": {
            "directional_value": 2,
            "directional_max_possible": 2,
            "supporting_domains": ["PARTICIPATION", "PRICE"],
            "conflicting_domains": [],
            "convergence_ratio": 1.0,
        },
        "metadata": {
            "close_price": 716.55,
            "price_change_pct": 2.5,
            "volume": 50387632,
            "volume_change_pct": 60.41,
            "open_interest": 1250000,
            "oi_change_pct": 8.5,
        },
        "overall_lifecycle": {
            "pattern_count": 1,
            "new_count": 1,
            "continuing_count": 0,
            "strengthening_count": 0,
            "weakening_count": 0,
            "resolved_count": 0,
        },
    }
    narrative = (
        "Price closed at 716.55 (+2.5%) with volume reaching 50387632 (+60.41%) "
        "and open interest at 1250000 (+8.5%). Directional convergence is 2 of 2 (100%)."
    )
    valid, errors = InterpretationNarrativeService.validate_narrative(narrative, rich_facts)
    assert valid, errors


def test_ai_legitimate_full_date_in_text_passes():
    valid, errors = InterpretationNarrativeService.validate_narrative(
        "On 2026-09-15, price moved +2.5% compared to 2026-09-14.", FACTS
    )
    assert valid, errors


def test_ai_empty_narrative_rejected():
    valid, errors = InterpretationNarrativeService.validate_narrative("", FACTS)
    assert not valid


def test_ai_prompt_contains_only_structured_facts_and_rules():
    prompt = InterpretationNarrativeService.build_prompt(FACTS)
    assert "FACTS:" in prompt
    assert json.dumps(FACTS["metadata"]) in prompt
    assert "Never write BUY" in prompt
    assert "Do NOT predict" in prompt


# ===========================================================================
# 13. AI Narrative Integration & Deterministic Fallback
# ===========================================================================

SAFE_OUTPUT = json.dumps({
    "narrative": (
        "Two independent directional domains confirm the observed price "
        "movement, with participation rising alongside price."
    )
})
UNSAFE_OUTPUT = json.dumps({
    "narrative": "The stock will rise 5%. Consider BUY now."
})


@pytest.mark.asyncio
async def test_ai_valid_narrative_integrated():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    narrative_service = InterpretationNarrativeService(provider=_FakeProvider(SAFE_OUTPUT))
    res = await interpret(current, include_ai=True, narrative_service=narrative_service)
    assert res.narrative is not None
    assert res.narrative.source == "ai"
    assert res.narrative.validated is True
    assert res.interpretation_method == "deterministic+ai_narrative"


@pytest.mark.asyncio
async def test_ai_unsafe_narrative_falls_back_deterministic():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    narrative_service = InterpretationNarrativeService(provider=_FakeProvider(UNSAFE_OUTPUT))
    res = await interpret(current, include_ai=True, narrative_service=narrative_service)
    assert res.narrative is None
    assert res.fallback_narrative
    assert res.interpretation_method == "deterministic"


@pytest.mark.asyncio
async def test_ai_provider_failure_falls_back_deterministic():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    narrative_service = InterpretationNarrativeService(provider=_FakeProvider(fail=True))
    res = await interpret(current, include_ai=True, narrative_service=narrative_service)
    assert res.narrative is None
    assert res.fallback_narrative
    assert res.interpretation_method == "deterministic"


@pytest.mark.asyncio
async def test_ai_cannot_override_deterministic_state():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    without_ai = await interpret(current, include_ai=False)
    narrative_service = InterpretationNarrativeService(provider=_FakeProvider(SAFE_OUTPUT))
    with_ai = await interpret(current, include_ai=True, narrative_service=narrative_service)
    assert with_ai.overall_state == without_ai.overall_state
    assert with_ai.directional_bias == without_ai.directional_bias
    assert with_ai.convergence == without_ai.convergence
    assert with_ai.evidence_quality == without_ai.evidence_quality


@pytest.mark.asyncio
async def test_fallback_always_present_without_ai():
    rel = _pv_rel()
    current = make_d2([rel], supporting=("PRICE", "PARTICIPATION"),
                      conv_value=2, conv_max=2, quality=EvidenceQuality.CONFIRMED)
    res = await interpret(current, include_ai=False)
    assert res.fallback_narrative
    assert res.narrative is None
    assert res.interpretation_method == "deterministic"


@pytest.mark.asyncio
async def test_fallback_narrative_bullish_supported_contains_domains():
    rel = _pv_rel()
    res = await interpret(
        make_d2([rel], supporting=("PRICE", "PARTICIPATION"), conv_value=2, conv_max=2,
                quality=EvidenceQuality.CONFIRMED)
    )
    assert "bullish" in res.fallback_narrative.lower()
    assert "PARTICIPATION" in res.fallback_narrative
    lowered = res.fallback_narrative.lower()
    assert not any(t in lowered for t in ("buy", "sell", "entry", "exit", "target"))


@pytest.mark.asyncio
async def test_fallback_narrative_muted_price_preserves_d2_semantics():
    muted = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONTEXT_ONLY",
        "NORMAL", "NEUTRAL",
        {"price_change_pct": 1.17, "volume_change_pct": 60.41,
         "pattern": "VOLUME_EXPANSION_WITH_MUTED_PRICE"},
        reason="Volume expanded 60.41% while price movement (+1.17%) remained below the 2.0% meaningful-change threshold",
    )
    res = await interpret(
        make_d2([muted], context=("PARTICIPATION",), available=("PRICE", "PARTICIPATION"),
                quality=EvidenceQuality.CONTEXT_ONLY)
    )
    narrative = res.fallback_narrative
    assert "context-only" in narrative.lower()
    assert "no directional evidence is established" in narrative.lower()
    lowered = narrative.lower()
    assert not any(t in lowered for t in ("bullish", "buy", "likely to rise", "will rise"))


@pytest.mark.asyncio
async def test_fallback_narrative_conflict_and_missing_reported():
    conf_rel = make_rel(
        ["PRICE", "VOLUME"], ["close_price", "volume"], "CONFIRMATION",
        "SIGNIFICANT", "BULLISH", {"price_change_pct": 3.0},
    )
    res = await interpret(
        make_d2([conf_rel], supporting=("PRICE", "PARTICIPATION"),
                conflicting=("TECHNICAL",), missing=("SECTOR",),
                conv_value=2, conv_max=3, quality=EvidenceQuality.PARTIALLY_CONFIRMED)
    )
    assert "TECHNICAL" in res.fallback_narrative
    assert "SECTOR" in res.fallback_narrative
    assert "CONFLICT_RESOLUTION_REQUIRED" in res.fallback_narrative


# ===========================================================================
# 14. HDFCBANK Regression (real DB, mirrors certified D-2 cases)
# ===========================================================================

async def _seed_hdfcbank_data(session):
    from sqlalchemy import select

    res = await session.execute(select(Stock).where(Stock.symbol == "HDFCBANK"))
    existing = res.scalar_one_or_none()
    if existing:
        return existing

    stock = Stock(
        symbol="HDFCBANK",
        company_name="HDFC Bank Limited",
        exchange="NSE",
        isin="INE040A01034",
        is_active=True,
    )
    session.add(stock)
    await session.flush()

    session.add_all([
        DailyPrice(stock_id=stock.id, trade_date=date(2026, 9, 8),
                   open=Decimal("705.00"), high=Decimal("710.00"),
                   low=Decimal("700.00"), close=Decimal("703.00"), volume=19883515),
        DailyPrice(stock_id=stock.id, trade_date=date(2026, 9, 9),
                   open=Decimal("700.00"), high=Decimal("702.00"),
                   low=Decimal("685.00"), close=Decimal("687.10"), volume=39173990),
        DailyPrice(stock_id=stock.id, trade_date=date(2026, 9, 10),
                   open=Decimal("688.00"), high=Decimal("695.00"),
                   low=Decimal("686.00"), close=Decimal("693.80"), volume=27108053),
        DailyPrice(stock_id=stock.id, trade_date=date(2026, 9, 11),
                   open=Decimal("683.90"), high=Decimal("709.00"),
                   low=Decimal("681.90"), close=Decimal("708.25"), volume=31411934,
                   delivery_percentage=Decimal("47.310")),
        DailyPrice(stock_id=stock.id, trade_date=date(2026, 9, 15),
                   open=Decimal("722.40"), high=Decimal("730.75"),
                   low=Decimal("715.05"), close=Decimal("716.55"), volume=50387632),
        IndicatorResult(stock_id=stock.id, trade_date=date(2026, 9, 8),
                        rsi_14=Decimal("35.68"), ema_20=Decimal("720.00")),
        IndicatorResult(stock_id=stock.id, trade_date=date(2026, 9, 9),
                        rsi_14=Decimal("29.56"), ema_20=Decimal("716.33")),
    ])
    await session.commit()
    return stock


@pytest.mark.asyncio
async def test_hdfcbank_2026_09_15_muted_price_stays_context_only():
    """2026-09-15 vs 2026-09-11: +1.17% price, +60.41% volume — muted price.
    D-3 MUST preserve D-2's CONTEXT_ONLY/NEUTRAL/VOLUME_EXPANSION_WITH_MUTED_PRICE
    semantics and MUST NOT upgrade to bullish."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)
    async with factory() as session:
        synth = WhatChangedSynthesisServiceHolder.service_cls()(session)
        svc = WhatChangedInterpretationService(session, synthesis_service=synth)
        res = await svc.interpret(
            "HDFCBANK", evaluation_date=date(2026, 9, 15), comparison_window=1,
            include_ai_narrative=False,
        )
        assert res.symbol == "HDFCBANK"
        assert res.overall_state == "CONTEXT_ONLY"
        assert res.directional_bias == "NEUTRAL"
        assert res.convergence.directional_value == 0
        assert res.convergence.directional_max_possible == 0
        assert res.convergence.convergence_ratio is None
        muted = [e for e in res.context_evidence
                 if e.metadata.get("pattern") == "VOLUME_EXPANSION_WITH_MUTED_PRICE"]
        assert len(muted) == 1
        assert "PRICE_CONFIRMATION_REQUIRED" in [w.code for w in res.watch_items]
        assert res.pit_safe is True
        lowered = res.fallback_narrative.lower()
        assert not any(t in lowered for t in ("bullish", "buy", "sell"))


@pytest.mark.asyncio
async def test_hdfcbank_2026_09_09_bearish_multi_domain_supported():
    """2026-09-09 vs 2026-09-08: -2.26% price, +97.02% volume, RSI oversold cross.
    D-3 must consume D-2 structured facts (no recomputation) and classify
    BEARISH_EVIDENCE_SUPPORTED with two PRIMARY evidence items."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)
    async with factory() as session:
        synth = WhatChangedSynthesisServiceHolder.service_cls()(session)
        svc = WhatChangedInterpretationService(session, synthesis_service=synth)
        res = await svc.interpret(
            "HDFCBANK", evaluation_date=date(2026, 9, 9), comparison_window=1,
            include_ai_narrative=False,
        )
        assert res.overall_state == "BEARISH_EVIDENCE_SUPPORTED"
        assert res.directional_bias == "BEARISH_EVIDENCE"
        assert res.convergence.supporting_domains == ["PARTICIPATION", "PRICE", "TECHNICAL"]
        assert res.convergence.directional_value == 3
        assert res.convergence.directional_max_possible == 3
        assert len(res.primary_evidence) == 2
        assert res.evidence_quality == "CONFIRMED"
        assert res.pit_safe is True
        lowered = res.fallback_narrative.lower()
        assert "bearish" in lowered
        assert not any(t in lowered for t in ("buy", "entry", "target"))


# ===========================================================================
# 15. API Endpoint Security & Schema
# ===========================================================================

@pytest.mark.asyncio
async def test_api_interpretation_unauthenticated_returns_401():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed/interpretation")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid authentication credentials"


@pytest.mark.asyncio
async def test_api_interpretation_bearer_auth_returns_200():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin") as client:
        resp = await client.get(
            "/api/v1/stocks/HDFCBANK/what-changed/interpretation"
            "?evaluation_date=2026-09-09&comparison_window=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "HDFCBANK"
        assert data["evaluation_date"] == "2026-09-09"
        for key in (
            "overall_state", "directional_bias", "primary_evidence",
            "supporting_evidence", "conflicting_evidence", "context_evidence",
            "missing_evidence", "convergence", "pattern_lifecycle_records",
            "overall_lifecycle", "watch_items", "limitations",
            "fallback_narrative", "interpretation_method", "evidence_quality",
            "pit_safe", "provenance",
        ):
            assert key in data


@pytest.mark.asyncio
async def test_api_interpretation_browser_cookie_auth_returns_200():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin", browser=True) as client:
        client.headers.pop("Authorization", None)
        resp = await client.get(
            "/api/v1/stocks/HDFCBANK/what-changed/interpretation"
            "?evaluation_date=2026-09-15&comparison_window=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_state"] == "CONTEXT_ONLY"


@pytest.mark.asyncio
async def test_api_interpretation_unknown_symbol_returns_404():
    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/stocks/NOSUCHSTOCK/what-changed/interpretation")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_interpretation_invalid_date_returns_422():
    async with auth_client(role="admin") as client:
        resp = await client.get(
            "/api/v1/stocks/HDFCBANK/what-changed/interpretation"
            "?evaluation_date=not-a-date"
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_api_interpretation_response_contains_no_trading_recommendation():
    import re

    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin") as client:
        resp = await client.get(
            "/api/v1/stocks/HDFCBANK/what-changed/interpretation"
            "?evaluation_date=2026-09-09&comparison_window=1"
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert not re.search(r"\b(buy|sell|entry|exit|target|stop loss)\b", body)
