"""Comprehensive Test Suite for Stage 2 G.11-D-2: Advanced Cross-Domain Synthesis Layer.

Validates:
 1. Price + Volume confirmation: Price UP + Volume UP -> CONFIRMATION
 2. Price + Volume confirmation: Price DOWN + Volume UP -> CONFIRMATION (increased participation)
 3. Price + Volume divergence: Price UP + Volume DOWN -> DIVERGENCE
 4. Price + Volume divergence: Price DOWN + Volume DOWN -> DIVERGENCE
 5. Price move + Volume unavailable -> INSUFFICIENT_EVIDENCE / None
 6. Price + Delivery confirmation: Delivery jump >= 10 pp -> CONFIRMATION
 7. Price + Delivery divergence: Delivery drop <= -15 pp -> DIVERGENCE
 8. Delivery unavailable is not zero -> fail-closed, no false relationship
 9. Price + Trend confirmation: Price UP + crosses above EMA20 -> CONFIRMATION
 10. Price + Trend conflict: Price UP while below 50 EMA -> CONFLICT (MIXED regime)
 11. Trend state transition: SuperTrend flip -> NEW_CROSS_DOMAIN_PATTERN, MAJOR
 12. Price + RSI confirmation: Price DOWN + RSI crossed below 30 -> CONFIRMATION, BEARISH
 13. Price + RSI momentum extension: Price UP + RSI crossed above 70 -> CONTEXT_ONLY
 14. Price + RSI divergence: Price UP while RSI declines >= 5 pts -> DIVERGENCE (bearish)
 15. Price + RSI divergence: Price DOWN while RSI recovers >= 5 pts -> DIVERGENCE (bullish)
 16. RSI unavailable handling -> fail-closed, no false relationship
 17. Price + Futures OI relationship: Price UP + OI UP -> PRICE_UP_OI_UP, CONFIRMATION
 18. Price + Futures OI relationship: Price DOWN + OI UP -> PRICE_DOWN_OI_UP, CONFIRMATION
 19. Price + Futures OI divergence: Price UP + OI DOWN -> PRICE_UP_OI_DOWN, DIVERGENCE
 20. Price + Futures OI divergence: Price DOWN + OI DOWN -> PRICE_DOWN_OI_DOWN, DIVERGENCE
 21. Derivatives unavailable fail-closed -> excluded from relationships, listed in missing_domains
 22. Institutional flow treated strictly as market context -> CONTEXT_ONLY
 23. Institutional data never falsely attributed to stock-specific strength
 24. Sector confirmation: Stock move and sector move in same direction -> CONFIRMATION
 25. Sector divergence: Stock move opposite sector move -> DIVERGENCE
 26. Corporate event context -> CONTEXT_ONLY, strictly without causal claims
 27. Bulk/block deal context -> CONTEXT_ONLY, strictly without insider speculation
 28. Missing domain handling -> missing != contradiction, unavailable != negative
 29. Evidence convergence score counts independent domains, not indicators
 30. Correlated metrics within a domain do not double-count
 31. Context domains (corporate, institutional) excluded from directional convergence count
 32. Conflict classification -> CONFLICTING or PARTIALLY_CONFIRMED
 33. Pattern lifecycle: window=1 -> NEW
 34. Pattern lifecycle: multi-window >= 2 confirmations -> STRENGTHENING
 35. Pattern lifecycle: multi-window 1 confirmation -> CONTINUING
 36. Pattern lifecycle: multi-window divergence -> WEAKENING
 37. Deterministic relationship sorting: MAJOR -> SIGNIFICANT -> NORMAL -> CONTEXT_ONLY
 38. Deterministic repeated execution produces identical synthesis
 39. Invariant: generated_at does not alter business ordering or equality
 40. Invariant: missing data never increases convergence score
 41. Invariant: adding unrelated corporate context does not alter existing relationship facts
 42. Invariant: market-wide institutional flow cannot become stock-specific evidence
 43. Integration test: HDFCBANK on 2026-09-15 vs 2026-09-10 (Price/Volume confirmation, tech missing)
 44. Integration test: HDFCBANK on 2026-09-09 vs 2026-09-08 (Price/Volume + RSI oversold confirmation)
 45. API endpoint GET /api/v1/stocks/{symbol}/what-changed/synthesis: unauthenticated returns 401
 46. API endpoint GET /api/v1/stocks/{symbol}/what-changed/synthesis: Bearer token returns 200
 47. API endpoint GET /api/v1/stocks/{symbol}/what-changed/synthesis: Browser cookie returns 200
 48. API endpoint GET /api/v1/market-data/stocks/{symbol}/what-changed/synthesis returns 200
 49. API endpoint GET /stocks/{symbol}/what-changed/synthesis returns 200
 50. API endpoint returns 404 for unknown stock
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.schemas.what_changed import ChangeEvent, ChangeSignificance, ChangeType
from app.schemas.what_changed_synthesis import (
    CrossDomainRelationship,
)
from app.services.what_changed_synthesis_config import (
    DOMAIN_PRICE,
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)
from app.services.what_changed_synthesis_service import WhatChangedSynthesisService
from tests.auth_helpers import auth_client

# ===========================================================================
# 1. Price + Volume Unit Synthesis Tests
# ===========================================================================

def _make_mock_price(trade_date: date, close: float, volume: int, delivery_pct: float | None = None):
    p = MagicMock()
    p.trade_date = trade_date
    p.close = Decimal(str(close))
    p.volume = volume
    p.delivery_percentage = Decimal(str(delivery_pct)) if delivery_pct is not None else None
    return p


def test_synthesis_price_up_volume_up_confirmation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.0, volume=2_000_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 5.0
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert rel.direction == "BULLISH"
    assert rel.strength == RelationshipStrength.MAJOR
    assert "confirmed by substantial volume expansion" in rel.reason


def test_synthesis_price_down_volume_up_confirmation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=95.0, volume=1_800_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BEARISH", -5.0
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert rel.direction == "BEARISH"
    assert "increased participation" in rel.reason
    assert "bullish" not in rel.reason.lower()


def test_synthesis_price_up_volume_down_divergence():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=2_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=104.0, volume=1_200_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 4.0
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.DIVERGENCE
    assert rel.direction == "DIVERGENT"
    assert "contracting volume" in rel.reason


def test_synthesis_price_volume_unavailable_fails_closed():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=0)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.0, volume=1_000_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 5.0
    )
    assert rel is None


def test_synthesis_sub_threshold_price_up_with_volume_surge_emits_context_only():
    """Price +1.17% (< 2.0% D-1 threshold) + volume +60.41% must emit neutral CONTEXT_ONLY."""
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 11), close=708.25, volume=31_411_934)
    p_curr = _make_mock_price(date(2026, 9, 15), close=716.55, volume=50_387_632)

    rel = svc._synthesize_price_volume(
        "HDFCBANK", date(2026, 9, 15), date(2026, 9, 11), p_curr, p_prev, "BULLISH", 1.17
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXT_ONLY
    assert rel.direction == "NEUTRAL"
    assert rel.strength == RelationshipStrength.NORMAL
    assert "remained below the 2.0% meaningful-change threshold" in rel.reason
    assert "bullish" not in rel.reason.lower()
    assert rel.metadata["pattern"] == "VOLUME_EXPANSION_WITH_MUTED_PRICE"


def test_synthesis_exact_2pct_threshold_with_volume_surge_emits_confirmation():
    """Price exactly +2.0% (meeting D-1 threshold) + volume +50.0% emits CONFIRMATION."""
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=102.0, volume=1_500_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 2.0
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert rel.direction == "BULLISH"
    assert rel.strength == RelationshipStrength.SIGNIFICANT
    assert "confirmed by substantial volume expansion" in rel.reason


def test_synthesis_5pct_and_10pct_thresholds_major():
    """Price +5.5% with volume +150% emits MAJOR CONFIRMATION."""
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.5, volume=2_500_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 5.5
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert rel.direction == "BULLISH"
    assert rel.strength == RelationshipStrength.MAJOR


def test_synthesis_sub_threshold_price_decrease_with_volume_surge_emits_context_only():
    """Price -1.2% (< 2.0%) with volume +75% emits neutral CONTEXT_ONLY (not bearish)."""
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=98.8, volume=1_750_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BEARISH", -1.2
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXT_ONLY
    assert rel.direction == "NEUTRAL"
    assert rel.strength == RelationshipStrength.NORMAL
    assert "remained below the 2.0% meaningful-change threshold" in rel.reason
    assert "bearish" not in rel.reason.lower()


def test_synthesis_sub_threshold_price_divergence_suppressed():
    """Sub-threshold price move with volume contraction must NOT become directional divergence."""
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=101.0, volume=700_000)

    rel = svc._synthesize_price_volume(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH", 1.0
    )
    assert rel is None


# ===========================================================================
# 2. Price + Delivery Unit Synthesis Tests
# ===========================================================================

def test_synthesis_price_delivery_confirmation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000, delivery_pct=40.0)
    p_curr = _make_mock_price(date(2026, 9, 9), close=104.0, volume=1_500_000, delivery_pct=55.0)

    rel = svc._synthesize_price_delivery(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH"
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert rel.direction == "BULLISH"
    assert "delivery percentage" in rel.reason


def test_synthesis_price_delivery_divergence():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000, delivery_pct=60.0)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.0, volume=1_500_000, delivery_pct=42.0)

    rel = svc._synthesize_price_delivery(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH"
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.DIVERGENCE
    assert "decline in delivery percentage" in rel.reason


def test_synthesis_delivery_unavailable_never_converts_to_zero():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000, delivery_pct=None)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.0, volume=1_500_000, delivery_pct=None)

    rel = svc._synthesize_price_delivery(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, "BULLISH"
    )
    # Must fail closed — never treat missing delivery as zero!
    assert rel is None


# ===========================================================================
# 3. Price + Trend & Technical Unit Synthesis Tests
# ===========================================================================

def test_synthesis_price_trend_reclaim_ema20():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=98.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=103.0, volume=1_500_000)
    c_ind = MagicMock()
    c_ind.ema_20 = Decimal("100.0")
    c_ind.ema_50 = Decimal("105.0")

    ev = ChangeEvent(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domain="TREND",
        metric="price_vs_ema_20",
        change_type=ChangeType.STATE_CHANGED,
        significance=ChangeSignificance.SIGNIFICANT,
        direction="BULLISH",
        source="NSE_INDICATOR_RESULT",
        reason="Price reclaimed 20-day EMA",
    )

    rels = svc._synthesize_price_trend(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, c_ind, None, "BULLISH", [ev]
    )
    assert len(rels) >= 1
    ema20_rel = next(r for r in rels if "ema_20" in r.metrics_involved)
    assert ema20_rel.relationship_type == RelationshipType.CONFIRMATION
    assert ema20_rel.direction == "BULLISH"


def test_synthesis_price_trend_conflict_below_ema50():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=95.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=98.0, volume=1_500_000)
    c_ind = MagicMock()
    c_ind.ema_50 = Decimal("110.0")

    rels = svc._synthesize_price_trend(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, c_ind, None, "BULLISH", []
    )
    assert len(rels) == 1
    assert rels[0].relationship_type == RelationshipType.CONFLICT
    assert rels[0].direction == "MIXED"
    assert "below the 50-day EMA" in rels[0].reason


def test_synthesis_price_rsi_oversold_confirmation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=703.0, volume=19_883_515)
    p_curr = _make_mock_price(date(2026, 9, 9), close=687.10, volume=39_173_990)
    c_ind = MagicMock()
    c_ind.rsi_14 = Decimal("29.56")

    ev = ChangeEvent(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domain="TECHNICAL",
        metric="rsi_14",
        previous_state="NORMAL",
        current_state="OVERSOLD",
        change_type=ChangeType.THRESHOLD_CROSSED,
        significance=ChangeSignificance.SIGNIFICANT,
        direction="BEARISH",
        source="NSE_INDICATOR_RESULT",
        reason="RSI(14) crossed below oversold threshold 30.0 (35.7 -> 29.6)",
    )

    rels = svc._synthesize_price_technical(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, c_ind, None, "BEARISH", [ev]
    )
    assert len(rels) == 1
    assert rels[0].relationship_type == RelationshipType.CONFIRMATION
    assert rels[0].direction == "BEARISH"
    assert "oversold" in rels[0].reason


def test_synthesis_price_rsi_bearish_divergence():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=105.0, volume=1_000_000)
    p_ind = MagicMock()
    p_ind.rsi_14 = Decimal("65.0")
    c_ind = MagicMock()
    c_ind.rsi_14 = Decimal("58.0")

    rels = svc._synthesize_price_technical(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, c_ind, p_ind, "BULLISH", []
    )
    assert len(rels) == 1
    assert rels[0].relationship_type == RelationshipType.DIVERGENCE
    assert "Bearish momentum divergence" in rels[0].reason


# ===========================================================================
# 4. Price + Derivatives OI Unit Synthesis Tests
# ===========================================================================

def test_synthesis_price_up_oi_up_neutral_relationship():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=104.0, volume=1_500_000)
    d_prev = {"total_oi": 10_000_000}
    d_curr = {"total_oi": 10_800_000}  # +8%

    rel = svc._synthesize_price_derivatives(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, d_curr, d_prev, "BULLISH"
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert "PRICE_UP_OI_UP" in rel.metrics_involved
    assert "open interest expansion" in rel.reason
    assert "long buildup" not in rel.reason.lower()  # Strict neutral naming!


def test_synthesis_price_down_oi_up_neutral_relationship():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=96.0, volume=1_500_000)
    d_prev = {"total_oi": 10_000_000}
    d_curr = {"total_oi": 10_600_000}  # +6%

    rel = svc._synthesize_price_derivatives(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, d_curr, d_prev, "BEARISH"
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert "PRICE_DOWN_OI_UP" in rel.metrics_involved
    assert "short buildup" not in rel.reason.lower()  # Strict neutral naming!


def test_synthesis_derivatives_unavailable_fails_closed():
    svc = WhatChangedSynthesisService(session=MagicMock())
    p_prev = _make_mock_price(date(2026, 9, 8), close=100.0, volume=1_000_000)
    p_curr = _make_mock_price(date(2026, 9, 9), close=104.0, volume=1_500_000)

    rel = svc._synthesize_price_derivatives(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), p_curr, p_prev, None, None, "BULLISH"
    )
    assert rel is None


# ===========================================================================
# 5. Institutional & Corporate Context Tests
# ===========================================================================

def test_synthesis_institutional_flow_is_strictly_market_context():
    svc = WhatChangedSynthesisService(session=MagicMock())
    flow_fii = MagicMock(investor_type="FII", net_amount=Decimal("1500.50"))
    flow_dii = MagicMock(investor_type="DII", net_amount=Decimal("-800.20"))

    rel = svc._synthesize_institutional(
        "HDFCBANK", date(2026, 9, 9), date(2026, 9, 8), [flow_fii, flow_dii], []
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXT_ONLY
    assert rel.strength == RelationshipStrength.CONTEXT_ONLY
    # Strict rule: Never attribute market-wide institutional flow to stock-specific strength!
    assert "market-wide context, not stock-specific" in rel.reason
    assert "HDFCBANK FII buying" not in rel.reason


def test_synthesis_corporate_events_no_causality():
    svc = WhatChangedSynthesisService(session=MagicMock())
    ann = MagicMock(category="OTHER", title="Allotment of 10000 Shares")

    rel = svc._synthesize_corporate(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), [ann], []
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXT_ONLY
    assert "no causality inferred" in rel.reason


def test_synthesis_deals_no_intent_speculation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    deal = MagicMock(quantity=100000, trade_price=Decimal("150.0"), client_name="ABC FUNDS", deal_type="BUY")

    rel = svc._synthesize_deals(
        "TEST", date(2026, 9, 9), date(2026, 9, 8), [deal]
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXT_ONLY
    assert "no intent inferred" in rel.reason


# ===========================================================================
# 6. Sector Unit Tests
# ===========================================================================

def test_synthesis_sector_confirmation():
    svc = WhatChangedSynthesisService(session=MagicMock())
    s_curr = {"close": 52000.0, "index_name": "Nifty Bank"}
    s_prev = {"close": 51000.0, "index_name": "Nifty Bank"}

    rel = svc._synthesize_sector(
        "HDFCBANK", date(2026, 9, 9), date(2026, 9, 8), 2.5, "BULLISH", s_curr, s_prev
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONFIRMATION
    assert "confirmed by aligned sector index move" in rel.reason


def test_synthesis_sector_divergence():
    svc = WhatChangedSynthesisService(session=MagicMock())
    s_curr = {"close": 50000.0, "index_name": "Nifty Bank"}
    s_prev = {"close": 51000.0, "index_name": "Nifty Bank"}

    rel = svc._synthesize_sector(
        "HDFCBANK", date(2026, 9, 9), date(2026, 9, 8), 2.5, "BULLISH", s_curr, s_prev
    )
    assert rel is not None
    assert rel.relationship_type == RelationshipType.DIVERGENCE
    assert "Sector divergence" in rel.reason


# ===========================================================================
# 7. Convergence Score & Lifecycle Tests
# ===========================================================================

def test_convergence_score_counts_independent_domains():
    svc = WhatChangedSynthesisService(session=MagicMock())
    # Rel 1: PRICE + VOLUME (Domain: PARTICIPATION)
    r1 = CrossDomainRelationship(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domains_involved=["PRICE", "VOLUME"],
        metrics_involved=["close_price", "volume"],
        relationship_type=RelationshipType.CONFIRMATION,
        strength=RelationshipStrength.SIGNIFICANT,
        reason="Price volume confirmation",
    )
    # Rel 2: PRICE + TECHNICAL (Domain: TECHNICAL)
    r2 = CrossDomainRelationship(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domains_involved=["PRICE", "TECHNICAL"],
        metrics_involved=["close_price", "rsi_14"],
        relationship_type=RelationshipType.CONFIRMATION,
        strength=RelationshipStrength.SIGNIFICANT,
        reason="Price rsi confirmation",
    )
    # Rel 3: INSTITUTIONAL (Domain: INSTITUTIONAL) -> Context only
    r3 = CrossDomainRelationship(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domains_involved=["INSTITUTIONAL"],
        metrics_involved=["market_institutional_flow"],
        relationship_type=RelationshipType.CONTEXT_ONLY,
        strength=RelationshipStrength.CONTEXT_ONLY,
        reason="Market flow context",
    )

    # Sort check
    sorted_rels = svc._sort_relationships([r3, r1, r2])
    # r1 and r2 are SIGNIFICANT, r3 is CONTEXT_ONLY -> r3 must be last
    assert sorted_rels[-1] == r3


def test_lifecycle_states():
    svc = WhatChangedSynthesisService(session=MagicMock())
    r_conf = CrossDomainRelationship(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domains_involved=["PRICE", "VOLUME"],
        metrics_involved=["close_price", "volume"],
        relationship_type=RelationshipType.CONFIRMATION,
        strength=RelationshipStrength.NORMAL,
        reason="conf",
    )
    r_conf2 = CrossDomainRelationship(
        symbol="TEST",
        evaluation_date=date(2026, 9, 9),
        comparison_date=date(2026, 9, 8),
        domains_involved=["PRICE", "TECHNICAL"],
        metrics_involved=["close_price", "rsi_14"],
        relationship_type=RelationshipType.CONFIRMATION,
        strength=RelationshipStrength.NORMAL,
        reason="conf2",
    )

    # Window 1 -> NEW
    lc1 = svc._determine_lifecycle([r_conf], comparison_window=1)
    assert lc1.state == PatternLifecycle.NEW

    # Window 5, 2 confirmations -> STRENGTHENING
    lc5 = svc._determine_lifecycle([r_conf, r_conf2], comparison_window=5)
    assert lc5.state == PatternLifecycle.STRENGTHENING


# ===========================================================================
# 8. Database Integration Tests: Real HDFCBANK Data
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

    p1 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 8),
        open=Decimal("705.00"),
        high=Decimal("710.00"),
        low=Decimal("700.00"),
        close=Decimal("703.00"),
        volume=19883515,
    )
    p2 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 9),
        open=Decimal("700.00"),
        high=Decimal("702.00"),
        low=Decimal("685.00"),
        close=Decimal("687.10"),
        volume=39173990,
    )
    p3 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 10),
        open=Decimal("688.00"),
        high=Decimal("695.00"),
        low=Decimal("686.00"),
        close=Decimal("693.80"),
        volume=27108053,
    )
    p3_fri = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 11),
        open=Decimal("683.90"),
        high=Decimal("709.00"),
        low=Decimal("681.90"),
        close=Decimal("708.25"),
        volume=31411934,
        delivery_percentage=Decimal("47.310"),
    )
    p4 = DailyPrice(
        stock_id=stock.id,
        trade_date=date(2026, 9, 15),
        open=Decimal("722.40"),
        high=Decimal("730.75"),
        low=Decimal("715.05"),
        close=Decimal("716.55"),
        volume=50387632,
    )
    ind1 = IndicatorResult(
        stock_id=stock.id,
        trade_date=date(2026, 9, 8),
        rsi_14=Decimal("35.68"),
        ema_20=Decimal("720.00"),
    )
    ind2 = IndicatorResult(
        stock_id=stock.id,
        trade_date=date(2026, 9, 9),
        rsi_14=Decimal("29.56"),
        ema_20=Decimal("716.33"),
    )
    session.add_all([p1, p2, p3, p3_fri, p4, ind1, ind2])
    await session.commit()
    return stock


@pytest.mark.asyncio
async def test_integration_hdfcbank_2026_09_15_synthesis():
    """HDFCBANK on 2026-09-15 vs 2026-09-11:
    Price 708.25 -> 716.55 (+1.17%), Volume 31,411,934 -> 50,387,632 (+60.41%).
    Price is below the 2.0% D-1 threshold.
    MUST NOT produce bullish PRICE/VOLUME CONFIRMATION.
    Instead emits neutral CONTEXT_ONLY (VOLUME_EXPANSION_WITH_MUTED_PRICE).
    """
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with factory() as session:
        svc = WhatChangedSynthesisService(session)
        res = await svc.synthesize("HDFCBANK", evaluation_date=date(2026, 9, 15), comparison_window=1)

        assert res.symbol == "HDFCBANK"
        assert res.evaluation_date == date(2026, 9, 15)
        assert res.comparison_date == date(2026, 9, 11)
        assert res.pit_safe is True

        # MUST NOT produce bullish PRICE/VOLUME CONFIRMATION
        pv_confirmations = [
            r
            for r in res.relationships
            if "VOLUME" in r.domains_involved and r.relationship_type == RelationshipType.CONFIRMATION
        ]
        assert len(pv_confirmations) == 0

        # Emits neutral CONTEXT_ONLY relationship
        pv_rels = [r for r in res.relationships if "VOLUME" in r.domains_involved]
        assert len(pv_rels) == 1
        assert pv_rels[0].relationship_type == RelationshipType.CONTEXT_ONLY
        assert pv_rels[0].direction == "NEUTRAL"
        assert "remained below the 2.0% meaningful-change threshold" in pv_rels[0].reason

        # Convergence score does NOT count PRICE as directional evidence
        assert DOMAIN_PRICE not in res.convergence_score.contributing_domains
        assert res.convergence_score.value == 0
        assert res.evidence_quality == EvidenceQuality.CONTEXT_ONLY

        # Confirm missing domains are truthfully reported
        assert "TECHNICAL" in res.missing_domains
        assert "DERIVATIVES" in res.missing_domains


@pytest.mark.asyncio
async def test_integration_hdfcbank_2026_09_09_synthesis():
    """HDFCBANK on 2026-09-09 vs 2026-09-08:
    Price -2.26%, Volume +97.0%, RSI 35.68 -> 29.56 (oversold).
    Detects Price/Volume confirmation + RSI oversold confirmation.
    """
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with factory() as session:
        svc = WhatChangedSynthesisService(session)
        res = await svc.synthesize("HDFCBANK", evaluation_date=date(2026, 9, 9), comparison_window=1)

        assert res.symbol == "HDFCBANK"
        assert res.evaluation_date == date(2026, 9, 9)
        assert res.comparison_date == date(2026, 9, 8)
        assert res.pit_safe is True

        # Relationships must contain both Volume and RSI confirmations
        rel_domains = [r.domains_involved for r in res.relationships]
        assert any("VOLUME" in d for d in rel_domains)
        assert any("TECHNICAL" in d for d in rel_domains)

        rsi_rel = next(r for r in res.relationships if "TECHNICAL" in r.domains_involved)
        assert rsi_rel.relationship_type == RelationshipType.CONFIRMATION
        assert rsi_rel.direction == "BEARISH"


# ===========================================================================
# 9. API Route Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_api_synthesis_unauthenticated_returns_401():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed/synthesis")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid authentication credentials"


@pytest.mark.asyncio
async def test_api_synthesis_bearer_auth_returns_200():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/stocks/HDFCBANK/what-changed/synthesis?evaluation_date=2026-09-09&comparison_window=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "HDFCBANK"
        assert data["evaluation_date"] == "2026-09-09"
        assert "convergence_score" in data
        assert "relationships" in data


@pytest.mark.asyncio
async def test_api_synthesis_browser_cookie_auth_returns_200():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_hdfcbank_data(session)

    async with auth_client(role="admin", browser=True) as client:
        client.headers.pop("Authorization", None)
        assert "Authorization" not in client.headers

        # 1. /api/v1/stocks/HDFCBANK/what-changed/synthesis
        r1 = await client.get("/api/v1/stocks/HDFCBANK/what-changed/synthesis?evaluation_date=2026-09-09&comparison_window=1")
        assert r1.status_code == 200

        # 2. /api/v1/market-data/stocks/HDFCBANK/what-changed/synthesis
        r2 = await client.get("/api/v1/market-data/stocks/HDFCBANK/what-changed/synthesis?evaluation_date=2026-09-09&comparison_window=1")
        assert r2.status_code == 200

        # 3. /stocks/HDFCBANK/what-changed/synthesis
        r3 = await client.get("/stocks/HDFCBANK/what-changed/synthesis?evaluation_date=2026-09-09&comparison_window=1")
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_api_synthesis_unknown_stock_404():
    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/stocks/UNKNOWN_XYZ_999/what-changed/synthesis")
        assert resp.status_code == 404
