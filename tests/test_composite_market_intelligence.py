from __future__ import annotations

import pytest

from app.schemas.market_intelligence_snapshot import MarketIntelligenceScoreConfig
from app.services.composite_market_intelligence import (
    FACTOR_NAMES,
    CompositeMarketIntelligenceResult,
    FactorInput,
    classify_composite_regime,
    compute_composite_factor_result,
)

DEFAULT_WEIGHTS = MarketIntelligenceScoreConfig().weights
WEIGHTS = dict(zip(FACTOR_NAMES, DEFAULT_WEIGHTS, strict=True))


def _fi(
    name: str,
    score,
    *,
    conf: float = 80.0,
    comp: float = 80.0,
    freshness: str = "live",
) -> FactorInput:
    return FactorInput(
        name=name,
        score=score,
        configured_weight=WEIGHTS[name],
        confidence=conf,
        completeness=comp,
        freshness=freshness,
    )


def _factors(scores: dict, *, conf=80.0, comp=80.0, freshness="live"):
    return [
        _fi(n, scores.get(n), conf=conf, comp=comp, freshness=freshness)
        for n in FACTOR_NAMES
    ]


def test_factor_names_canonical_order():
    assert FACTOR_NAMES == (
        "price_action",
        "breadth",
        "sector",
        "institutional",
        "volatility",
        "global",
        "gift_nifty",
        "currency",
        "crude",
        "macro",
    )


def test_default_weights_match_expected():
    assert DEFAULT_WEIGHTS == [
        0.25,
        0.15,
        0.10,
        0.10,
        0.10,
        0.10,
        0.10,
        0.04,
        0.03,
        0.03,
    ]
    assert abs(sum(DEFAULT_WEIGHTS) - 1.0) < 1e-6


# ============================================================
# WEIGHTS / MISSING RENORMALIZATION
# ============================================================


def test_missing_factor_renormalizes_available_weight():
    # All factors present except macro (weight .03). Composite must ignore the
    # missing factor and renormalize the remaining weights to sum to 1.
    scores = {n: 80 for n in FACTOR_NAMES}
    scores["macro"] = None
    factors = _factors(scores)
    result: CompositeMarketIntelligenceResult = compute_composite_factor_result(factors)
    # available weight = 1.0 - 0.03 = 0.97 -> completeness 97
    assert result.data_completeness_percent == pytest.approx(97.0)
    assert result.composite_score == pytest.approx(80.0)
    assert (len(result.missing_factors), result.missing_factors[0].name) == (1, "macro")


def test_high_weight_missing_lowers_completeness_more_than_low_weight():
    # price_action weight .25
    missing_high = compute_composite_factor_result(
        _factors({**{n: 80 for n in FACTOR_NAMES}, "price_action": None})
    )
    # crude weight .03
    missing_low = compute_composite_factor_result(
        _factors({**{n: 80 for n in FACTOR_NAMES}, "crude": None})
    )
    assert missing_high.data_completeness_percent < missing_low.data_completeness_percent
    assert missing_high.data_completeness_percent == pytest.approx(75.0)
    assert missing_low.data_completeness_percent == pytest.approx(97.0)


def test_only_one_factor_available_renormalizes_to_100pct_weight():
    scores = {n: None for n in FACTOR_NAMES}
    scores["price_action"] = 50
    result = compute_composite_factor_result(_factors(scores))
    assert result.composite_score == pytest.approx(50.0)
    assert result.data_completeness_percent == pytest.approx(25.0)
    # effective weight of the only factor is 1.0
    pa = next(f for f in result.factors if f.name == "price_action")
    assert pa.effective_weight == pytest.approx(1.0)
    assert pa.contribution == pytest.approx(50.0)


def test_none_available_produces_none_composite_and_uncertain():
    result = compute_composite_factor_result(_factors({}))
    assert result.composite_score is None
    assert result.regime == "uncertain"
    assert result.directional_regime is None
    assert result.confidence_score == 0
    assert result.data_completeness_percent == 0
    assert result.uncertain is True
    assert result.confidence_state == "low"


# ============================================================
# COMPOSITE SCORE
# ============================================================


def test_all_strongly_bullish_consensus():
    scores = {n: 80 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    assert result.composite_score == pytest.approx(80.0)
    assert result.directional_regime == "strong_bullish"
    assert result.agreement_percent == pytest.approx(100.0)
    assert result.confidence_score == pytest.approx(95.0)
    assert result.weighted_bullish == pytest.approx(1.0)
    assert result.weighted_bearish == pytest.approx(0.0)


def test_all_strongly_bearish_consensus():
    scores = {n: -80 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    assert result.composite_score == pytest.approx(-80.0)
    assert result.directional_regime == "strong_bearish"
    assert result.confidence_score == pytest.approx(95.0)
    assert result.weighted_bearish == pytest.approx(1.0)


def test_weighted_composite_prefers_high_weight_factor():
    # +80 price_action (0.25) & breadth (0.15) vs -60 crude (0.03), rest neutral/missing
    scores = {n: 0 for n in FACTOR_NAMES}
    scores["price_action"] = 80
    scores["breadth"] = 80
    scores["crude"] = -60
    scores["macro"] = None
    scores["global"] = None
    scores["gift_nifty"] = None
    result = compute_composite_factor_result(_factors(scores))
    # available weight = 0.25+0.15+0.10+0.10+0.10(sector,inst,vol) ... plus crude .03
    # Neutral factors still available (score 0) -> contribute 0.
    # available = all except macro, global, gift -> weights .25+.15+.10+.10+.10+.04+.03 = .77
    avail = 0.25 + 0.15 + 0.10 + 0.10 + 0.10 + 0.04 + 0.03
    composite = (80 * 0.25 + 80 * 0.15 - 60 * 0.03) / avail
    assert result.composite_score == pytest.approx(composite, abs=0.01)
    # positive bias because high-weight factors dominate
    assert result.composite_score > 0


def test_bounds_clamp_to_100():
    scores = {n: 200 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    assert result.composite_score == pytest.approx(100.0)
    scores = {n: -200 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    assert result.composite_score == pytest.approx(-100.0)


# ============================================================
# REGIME THRESHOLDS
# ============================================================


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (60, "strong_bullish"),
        (59.999, "bullish"),
        (25, "bullish"),
        (24.999, "neutral"),
        (-24.999, "neutral"),
        (-25, "bearish"),
        (-59.999, "bearish"),
        (-60, "strong_bearish"),
        (None, None),
    ],
)
def test_classify_composite_regime_exact_boundaries(score, expected):
    assert classify_composite_regime(score) == expected


def test_regime_from_single_factor_at_boundaries():
    for score, regime in [(60, "strong_bullish"), (59.99, "bullish"), (25, "bullish"),
                          (24.99, "neutral"), (-24.99, "neutral"), (-25, "bearish"),
                          (-60, "strong_bearish")]:
        scores = {n: None for n in FACTOR_NAMES}
        scores["price_action"] = score
        result = compute_composite_factor_result(_factors(scores))
        assert result.directional_regime == regime


# ============================================================
# CONFIDENCE
# ============================================================


def test_confidence_high_on_complete_fresh_agreement():
    scores = {n: 60 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    assert result.confidence_score == pytest.approx(95.0)
    assert result.confidence_state == "high"


def test_confidence_falls_when_incomplete():
    full = compute_composite_factor_result(_factors({n: 60 for n in FACTOR_NAMES}))
    scores = {n: 60 for n in FACTOR_NAMES}
    scores["price_action"] = None
    scores["breadth"] = None
    partial = compute_composite_factor_result(_factors(scores))
    assert partial.confidence_score < full.confidence_score


def test_confidence_falls_when_stale():
    fresh = compute_composite_factor_result(_factors({n: 60 for n in FACTOR_NAMES}))
    stale_scores = {n: 60 for n in FACTOR_NAMES}
    stale = _factors(stale_scores)
    for factor in stale:
        if factor.name == "breadth":
            object.__setattr__(factor, "freshness", "stale")
    result = compute_composite_factor_result(stale)
    assert result.confidence_score < fresh.confidence_score
    assert len(result.stale_factors) == 1
    assert result.stale_factors[0].name == "breadth"


def test_confidence_falls_when_conflicting():
    scores = {n: None for n in FACTOR_NAMES}
    scores["price_action"] = 80
    scores["breadth"] = -80
    scores["crude"] = None
    scores["macro"] = None
    scores["global"] = None
    scores["gift_nifty"] = None
    scores["currency"] = None
    scores["sector"] = None
    scores["institutional"] = None
    scores["volatility"] = None
    result = compute_composite_factor_result(_factors(scores))
    assert len(result.contradictions) == 1
    assert result.contradictions[0].severity == "high"
    # confidence is bounded and reduced relative to a same-weight consensus
    assert 0 <= result.confidence_score < 95


# ============================================================
# COMPLETENESS (WEIGHTED)
# ============================================================


def test_completeness_full_partial_zero():
    full = compute_composite_factor_result(_factors({n: 30 for n in FACTOR_NAMES}))
    assert full.data_completeness_percent == pytest.approx(100.0)

    scores = {n: 30 for n in FACTOR_NAMES}
    scores["price_action"] = None  # 25 weight
    partial = compute_composite_factor_result(_factors(scores))
    assert partial.data_completeness_percent == pytest.approx(75.0)

    none = compute_composite_factor_result(_factors({}))
    assert none.data_completeness_percent == 0


# ============================================================
# FACTOR LISTS & CONTRIBUTIONS
# ============================================================


def test_bullish_bearish_neutral_classification():
    scores = {
        "price_action": 60,
        "breadth": -60,
        "sector": 0,
        "institutional": None,
        "volatility": 30,
        "global": -20,
        "gift_nifty": 5,
        "currency": -5,
        "crude": None,
        "macro": -30,
    }
    result = compute_composite_factor_result(_factors(scores))
    assert [f.name for f in result.bullish_factors] == ["price_action", "volatility"]
    assert [f.name for f in result.bearish_factors] == ["breadth", "macro"]
    assert [f.name for f in result.neutral_factors] == [
        "sector",
        "global",
        "gift_nifty",
        "currency",
    ]
    assert [f.name for f in result.missing_factors] == ["institutional", "crude"]


def test_factor_lists_have_deterministic_order():
    scores = {"price_action": 60, "breadth": -60}
    r1 = compute_composite_factor_result(_factors(scores))
    r2 = compute_composite_factor_result(_factors(scores))
    assert [f.name for f in r1.factors] == [f.name for f in r2.factors]
    assert [f.name for f in r1.bullish_factors] == [f.name for f in r2.bullish_factors]


def test_contribution_accounting_reconciles_to_composite():
    scores = {
        "price_action": 80,
        "breadth": -20,
        "sector": 40,
        "institutional": -10,
        "volatility": 0,
        "global": 30,
        "gift_nifty": 100,
        "currency": -60,
        "crude": 200,
        "macro": None,
    }
    result = compute_composite_factor_result(_factors(scores))
    total = sum(result.factor_contributions.values())
    assert total == pytest.approx(result.composite_score, abs=0.02)


def test_factor_items_include_weight_and_contribution():
    result = compute_composite_factor_result(_factors({n: 50 for n in FACTOR_NAMES}))
    pa = next(f for f in result.factors if f.name == "price_action")
    assert pa.configured_weight == pytest.approx(0.25)
    assert pa.effective_weight == pytest.approx(0.25)
    assert pa.contribution == pytest.approx(50 * 0.25, abs=0.01)


# ============================================================
# QUERY-PATH / DETERMINISM
# ============================================================


def test_repeated_evaluation_identical():
    scores = {n: ((n.count("a") % 5) * 10 - 20) for n in FACTOR_NAMES}
    factors = _factors(scores)
    r1 = compute_composite_factor_result(factors)
    r2 = compute_composite_factor_result(factors)
    assert r1.composite_score == r2.composite_score
    assert r1.confidence_score == r2.confidence_score
    assert r1.agreement_percent == r2.agreement_percent
    assert r1.factor_contributions == r2.factor_contributions
    assert [c.model_dump() for c in r1.contradictions] == [
        c.model_dump() for c in r2.contradictions
    ]


def test_more_than_10_or_less_than_10_factors_rejected():
    # passing 11 factors (duplicate causes an extra item)
    factors = [_fi(n, 50) for n in list(FACTOR_NAMES) + ["price_action"]]
    with pytest.raises(ValueError):
        compute_composite_factor_result(factors)
    # passing 9 factors
    factors = [_fi(n, 50) for n in FACTOR_NAMES[:9]]
    with pytest.raises(ValueError):
        compute_composite_factor_result(factors)


def test_wrong_factor_names_rejected():
    bad = list(FACTOR_NAMES)
    bad[0], bad[1] = bad[1], bad[0]
    with pytest.raises(ValueError):
        compute_composite_factor_result([_fi(n, 50) for n in bad])


# ============================================================
# HIGH VOLATILITY / UNCERTAINTY POLICY
# ============================================================


def test_high_volatility_when_volatility_factor_strongly_adverse():
    scores = {n: 0 for n in FACTOR_NAMES}
    scores["volatility"] = -60
    result = compute_composite_factor_result(_factors(scores))
    assert result.high_volatility is True


def test_not_high_volatility_when_volatility_factor_mild():
    scores = {n: 0 for n in FACTOR_NAMES}
    scores["volatility"] = -10
    result = compute_composite_factor_result(_factors(scores))
    assert result.high_volatility is False


def test_direction_not_replaced_by_low_confidence():
    # A bearish composite stays bearish even with low confidence (quality is separate).
    scores = {n: -70 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores, comp=30.0, conf=20.0))
    assert result.directional_regime == "strong_bearish"
    assert result.regime == "strong_bearish"
    assert result.confidence_state in ("low", "medium")


# ============================================================
# RISK FACTORS
# ============================================================


def test_risk_factors_surface_extreme_negative_conditions():
    scores = {n: -70 for n in FACTOR_NAMES}
    result = compute_composite_factor_result(_factors(scores))
    codes = [r.type for r in result.risk_factors]
    assert "EXTREME_NEGATIVE_BREADTH" in codes
    assert "EXTREME_NEGATIVE_GLOBAL" in codes
    assert "STRONG_CONTRADICTION" not in codes


def test_risk_factors_low_completeness_and_contradiction():
    scores = {n: None for n in FACTOR_NAMES}
    scores["price_action"] = 80
    scores["breadth"] = -80
    result = compute_composite_factor_result(_factors(scores))
    codes = [r.type for r in result.risk_factors]
    assert "LOW_COMPLETENESS" in codes
    assert "STRONG_CONTRADICTION" in codes


def test_healthcheck_presence_of_evaluation_fields():
    result = compute_composite_factor_result(_factors({n: 40 for n in FACTOR_NAMES}))
    assert result.evaluation_time is None
    assert result.market_date is None
    assert result.snapshot_id is None
