from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.index_catalog import (
    BRENT_CRUDE_INDEX_CODE,
    INDIA_VIX_INDEX_CODE,
    USD_INR_INDEX_CODE,
)
from app.market_intelligence.providers.base import MarketProvider
from app.market_intelligence.session_dates import INDIA_TIMEZONE
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.repositories.market_intelligence_snapshot import (
    MarketIntelligenceSnapshotRepository,
)
from app.schemas.market_intelligence_snapshot import MarketIntelligenceScoreConfig
from app.services.external_market_factor_intelligence import (
    ExternalMarketFactorIntelligence,
)
from app.services.gift_nifty_intelligence import GiftNiftyIntelligence
from app.services.global_market_intelligence import GlobalMarketIntelligence
from app.services.index_price_action import IndexPriceAction
from app.services.institutional_intelligence import InstitutionalIntelligence
from app.services.macro_intelligence import MacroIntelligence
from app.services.market_breadth import MarketBreadth
from app.services.sector_intelligence import SectorIntelligence

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")

# Canonical factor names, in the same order as MarketIntelligenceScoreConfig.weights
# and the MarketIntelligenceSnapshot factor columns.
FACTOR_NAMES: tuple[str, ...] = (
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

# Index code used for the Indian price-action factor (Task 18.2 engine).
PRICE_ACTION_INDEX_CODE = "NIFTY_50"

# Centralized composite-direction thresholds (canonical +60/+25/-25/-60).
REGIME_THRESHOLDS: dict[str, Decimal] = {
    "strong_bullish": Decimal("60"),
    "bullish": Decimal("25"),
    "bearish": Decimal("-25"),
    "strong_bearish": Decimal("-60"),
}

# Classification band for bullish/bearish/neutral factor lists.
DIRECTION_BAND = {"bullish": Decimal("25"), "bearish": Decimal("-25")}

# Per-factor freshness quality factor used in confidence (0..1).
FRESHNESS_QUALITY: dict[str, Decimal] = {
    "live": Decimal("1.00"),
    "recent": Decimal("0.85"),
    "stale": Decimal("0.40"),
    "missing": Decimal("0.00"),
}

# Deterministic confidence blend weights (all sum to 1.0).
CONFIDENCE_WEIGHTS: dict[str, Decimal] = {
    "completeness": Decimal("0.30"),
    "factor_confidence": Decimal("0.25"),
    "freshness": Decimal("0.20"),
    "agreement": Decimal("0.25"),
}

CONFIDENCE_STATE_THRESHOLDS = {"high": Decimal("70"), "medium": Decimal("40")}

UNCERTAIN_CONFIDENCE_BELOW = Decimal("40")

# Deterministic risk-factor rules (thresholds centralized here).
LOW_COMPLETENESS_BELOW = Decimal("50")
EXTREME_NEGATIVE_BELOW = Decimal("-60")
HIGH_WEIGHT_STALE_ABOVE = Decimal("0.10")
HIGH_CONTRADICTION_WEIGHT = Decimal("0.10")


def _clamp(value: Decimal, low: Decimal = SCORE_MIN, high: Decimal = SCORE_MAX) -> Decimal:
    return max(low, min(high, value))


def _quantize(value: Decimal, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP) if value.is_finite() else value)


def _decimal(value: Any) -> Decimal:
    if value is None:
        raise ValueError("cannot convert None to Decimal")
    return Decimal(str(value))


class FactorInput:
    """Normalized internal factor representation consumed by the composite core.

    ``score`` is bounded -100..+100; ``None`` means the factor is unavailable
    (never treated as zero). ``configured_weight`` is the centralized weight.
    """

    __slots__ = (
        "name",
        "score",
        "configured_weight",
        "confidence",
        "completeness",
        "freshness",
        "provenance",
        "direction",
    )

    def __init__(
        self,
        name: str,
        score: float | int | None,
        configured_weight: float,
        confidence: float | int | None = None,
        completeness: float | int | None = None,
        freshness: str = "missing",
        provenance: str | None = None,
        direction: str | None = None,
    ) -> None:
        self.name = name
        self.score = float(score) if score is not None else None
        self.configured_weight = float(configured_weight)
        self.confidence = float(confidence) if confidence is not None else 0.0
        self.completeness = float(completeness) if completeness is not None else 0.0
        self.freshness = freshness
        self.provenance = provenance
        self.direction = direction

    @property
    def available(self) -> bool:
        return self.score is not None


class FactorOut(BaseModel):
    name: str
    score: float | None = None
    configured_weight: float = 0.0
    effective_weight: float = 0.0
    confidence: float = 0.0
    completeness: float = 0.0
    freshness: str = "missing"
    direction: str | None = None
    provenance: str | None = None
    contribution: float = 0.0


class Contradiction(BaseModel):
    type: str
    factor_a: str
    factor_b: str
    severity: str
    message_code: str


class RiskFactor(BaseModel):
    type: str
    severity: str
    message: str


class CompositeMarketIntelligenceResult(BaseModel):
    evaluation_time: datetime | None = None
    market_date: date | None = None
    factors: list[FactorOut] = Field(default_factory=list)
    composite_score: float | None = None
    directional_regime: str | None = None
    regime: str | None = None
    confidence_score: float | None = None
    confidence_state: str = "unknown"
    data_completeness_percent: float | None = None
    agreement_percent: float | None = None
    weighted_bullish: float = 0.0
    weighted_bearish: float = 0.0
    weighted_neutral: float = 0.0
    bullish_factors: list[FactorOut] = Field(default_factory=list)
    bearish_factors: list[FactorOut] = Field(default_factory=list)
    neutral_factors: list[FactorOut] = Field(default_factory=list)
    missing_factors: list[FactorOut] = Field(default_factory=list)
    stale_factors: list[FactorOut] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    risk_factors: list[RiskFactor] = Field(default_factory=list)
    factor_contributions: dict[str, float] = Field(default_factory=dict)
    uncertain: bool = False
    high_volatility: bool = False
    provenance_summary: list[str] = Field(default_factory=list)
    snapshot_id: int | None = None
    # Task 18.8 deterministic upcoming macro events (lifecycle == "upcoming")
    # within the evaluation lookahead boundary. Consumed by the notification
    # service for the market_macro_risk trigger. Never contains a directional
    # signal; upcoming events contribute no direction by design.
    upcoming_macro_events: list[dict[str, Any]] = Field(default_factory=list)


def classify_composite_regime(
    score: Decimal | float | int | None,
) -> str | None:
    """Deterministic directional regime from the composite score.

    Boundaries are exact: +60 -> strong_bullish, +25 -> bullish, -25 -> bearish,
    -60 -> strong_bearish, otherwise neutral. ``None`` score yields ``None``.
    """
    if score is None:
        return None
    value = Decimal(str(score))
    if value >= REGIME_THRESHOLDS["strong_bullish"]:
        return "strong_bullish"
    if value >= REGIME_THRESHOLDS["bullish"]:
        return "bullish"
    if value <= REGIME_THRESHOLDS["strong_bearish"]:
        return "strong_bearish"
    if value <= REGIME_THRESHOLDS["bearish"]:
        return "bearish"
    return "neutral"


def compute_composite_factor_result(
    factors: list[FactorInput],
    score_config: MarketIntelligenceScoreConfig | None = None,
) -> CompositeMarketIntelligenceResult:
    """Pure, deterministic composite/confidence/regime computation.

    Does not touch the database, call an engine, or read the clock. Consumes only
    the normalized ``FactorInput`` list and the centralized weight configuration.
    """
    if len(factors) != len(FACTOR_NAMES):
        raise ValueError(
            f"expected exactly {len(FACTOR_NAMES)} factors, got {len(factors)}"
        )
    named = OrderedDict((factor.name, factor) for factor in factors)
    if list(named.keys()) != list(FACTOR_NAMES):
        raise ValueError("factor names must match the canonical factor order")

    available = [factor for factor in factors if factor.available]
    configured_v = {factor.name: Decimal(str(factor.configured_weight)) for factor in factors}

    completeness = Decimal("0")
    composite: Decimal | None = None
    confidence: Decimal = Decimal("0")
    agreement_percent: Decimal = Decimal("0")
    weighted_bullish = Decimal("0")
    weighted_bearish = Decimal("0")
    weighted_neutral = Decimal("0")
    factor_conf_w = Decimal("0")
    freshness_w = Decimal("0")
    effective_weight: dict[str, Decimal] = {}

    if available:
        available_weight = sum(
            (configured_v[name] for name in (f.name for f in available)), Decimal("0")
        )
        completeness = available_weight * Decimal("100")
        effective_weight = {
            f.name: configured_v[f.name] / available_weight for f in available
        }
        composite = sum(
            (_decimal(f.score) * effective_weight[f.name] for f in available),
            Decimal("0"),
        )
        composite = _clamp(composite)

        for f in available:
            factor_conf_w += effective_weight[f.name] * _clamp(
                Decimal(str(max(0.0, min(100.0, f.confidence)))), Decimal("0"), Decimal("100")
            )
            freshness_w += effective_weight[f.name] * FRESHNESS_QUALITY.get(
                f.freshness, Decimal("0")
            )

        # Direction bands for agreement.
        band: dict[str, Decimal] = {
            "bullish": Decimal("0"),
            "bearish": Decimal("0"),
            "neutral": Decimal("0"),
        }
        for f in available:
            s = _decimal(f.score)
            if s >= DIRECTION_BAND["bullish"]:
                band["bullish"] += effective_weight[f.name]
            elif s <= DIRECTION_BAND["bearish"]:
                band["bearish"] += effective_weight[f.name]
            else:
                band["neutral"] += effective_weight[f.name]
        weighted_bullish, weighted_bearish, weighted_neutral = (
            band["bullish"],
            band["bearish"],
            band["neutral"],
        )
        if composite > 0:
            aligned = band["bullish"]
        elif composite < 0:
            aligned = band["bearish"]
        else:
            aligned = band["neutral"]
        agreement_percent = aligned * Decimal("100")

        completeness_w = completeness / Decimal("100")
        agre_w = agreement_percent / Decimal("100")
        conf_w = factor_conf_w / Decimal("100")
        # Reliability of reported freshness/agreement, damped by factor confidence:
        # low-confidence data should not inflate confidence even when it agrees.
        reliability = min(Decimal("1.0"), conf_w / Decimal("0.8"))
        confidence = _clamp(
            (
                CONFIDENCE_WEIGHTS["completeness"] * completeness_w
                + CONFIDENCE_WEIGHTS["factor_confidence"] * conf_w
                + (
                    CONFIDENCE_WEIGHTS["freshness"] * freshness_w
                    + CONFIDENCE_WEIGHTS["agreement"] * agre_w
                )
                * reliability
            )
            * Decimal("100"),
            Decimal("0"),
            Decimal("100"),
        )

    # Build factor outputs with effective weights and contributions.
    factor_outs: dict[str, FactorOut] = {}
    for factor in factors:
        eff = (
            effective_weight[factor.name]
            if factor.available and available
            else Decimal("0")
        )
        score_d = _decimal(factor.score) if factor.available else None
        contribution = (
            score_d * eff if score_d is not None else Decimal("0")
        )
        factor_outs[factor.name] = FactorOut(
            name=factor.name,
            score=factor.score,
            configured_weight=factor.configured_weight,
            effective_weight=_quantize(eff, 4),
            confidence=factor.confidence,
            completeness=factor.completeness,
            freshness=factor.freshness,
            direction=factor.direction,
            provenance=factor.provenance,
            contribution=_quantize(contribution, 4),
        )

    composite_float = _quantize(composite) if composite is not None else None
    directional_regime = classify_composite_regime(composite)
    regime = directional_regime if composite is not None else "uncertain"

    confidence_state = _confidence_state(composite, confidence)
    uncertain = composite is None or confidence < UNCERTAIN_CONFIDENCE_BELOW

    high_volatility = _detect_high_volatility(factors)

    contributions = {
        name: factor_outs[name].contribution for name in FACTOR_NAMES
    }

    bullish_low = float(DIRECTION_BAND["bullish"])
    bearish_high = float(DIRECTION_BAND["bearish"])
    bullish = [
        factor_outs[n]
        for n in FACTOR_NAMES
        if factor_outs[n].score is not None and factor_outs[n].score >= bullish_low
    ]
    bearish = [
        factor_outs[n]
        for n in FACTOR_NAMES
        if factor_outs[n].score is not None and factor_outs[n].score <= bearish_high
    ]
    neutral = [
        factor_outs[n]
        for n in FACTOR_NAMES
        if factor_outs[n].score is not None
        and bearish_high < factor_outs[n].score < bullish_low
    ]
    missing = [factor_outs[n] for n in FACTOR_NAMES if factor_outs[n].score is None]
    stale = [
        factor_outs[n]
        for n in FACTOR_NAMES
        if factor_outs[n].score is not None and factor_outs[n].freshness == "stale"
    ]

    contradictions = _detect_contradictions(available, effective_weight, factor_outs)
    risk_factors = _build_risk_factors(
        composite,
        completeness,
        confidence,
        availability={f.name: f.available for f in factors},
        factors=factor_outs,
        high_volatility=high_volatility,
        contradictions=contradictions,
    )

    return CompositeMarketIntelligenceResult(
        composite_score=composite_float,
        directional_regime=directional_regime,
        regime=regime,
        confidence_score=_quantize(confidence),
        confidence_state=confidence_state,
        data_completeness_percent=_quantize(completeness, 1),
        agreement_percent=_quantize(agreement_percent),
        weighted_bullish=_quantize(weighted_bullish, 4),
        weighted_bearish=_quantize(weighted_bearish, 4),
        weighted_neutral=_quantize(weighted_neutral, 4),
        factors=[factor_outs[n] for n in FACTOR_NAMES],
        bullish_factors=bullish,
        bearish_factors=bearish,
        neutral_factors=neutral,
        missing_factors=missing,
        stale_factors=stale,
        contradictions=contradictions,
        risk_factors=risk_factors,
        factor_contributions=contributions,
        uncertain=uncertain,
        high_volatility=high_volatility,
    )


def _confidence_state(composite: Decimal | None, confidence: Decimal) -> str:
    if composite is None:
        return "low"
    if confidence >= CONFIDENCE_STATE_THRESHOLDS["high"]:
        return "high"
    if confidence >= CONFIDENCE_STATE_THRESHOLDS["medium"]:
        return "medium"
    return "low"


def _detect_high_volatility(factors: list[FactorInput]) -> bool:
    for factor in factors:
        if factor.name == "volatility" and factor.available:
            return factor.score <= -float(DIRECTION_BAND["bullish"])
    return False


def _detect_contradictions(
    available: list[FactorInput],
    effective_weight: dict[str, Decimal],
    factor_outs: dict[str, FactorOut],
) -> list[Contradiction]:
    contradictions: list[Contradiction] = []
    pairs: list[tuple[str, str]] = [
        ("price_action", "breadth"),
        ("price_action", "sector"),
        ("gift_nifty", "global"),
        ("institutional", "macro"),
        ("volatility", "breadth"),
        ("price_action", "macro"),
        ("global", "breadth"),
        ("institutional", "price_action"),
        ("sector", "macro"),
        ("breadth", "macro"),
        ("price_action", "volatility"),
        ("global", "institutional"),
    ]
    available_names = {f.name for f in available}
    for pair in pairs:
        if pair[0] not in available_names or pair[1] not in available_names:
            continue
        a = factor_outs[pair[0]]
        b = factor_outs[pair[1]]
        av = factor_outs[pair[0]].score
        bv = factor_outs[pair[1]].score
        if av is None or bv is None:
            continue
        a_bull = av >= float(DIRECTION_BAND["bullish"])
        a_bear = av <= float(DIRECTION_BAND["bearish"])
        b_bull = bv >= float(DIRECTION_BAND["bullish"])
        b_bear = bv <= float(DIRECTION_BAND["bearish"])
        if not ((a_bull and b_bear) or (a_bear and b_bull)):
            continue
        severity = _contradiction_severity(a, b, effective_weight)
        contradictions.append(
            Contradiction(
                type="opposing_direction",
                factor_a=pair[0],
                factor_b=pair[1],
                severity=severity,
                message_code=f"{pair[0]}_vs_{pair[1]}_opposing_direction",
            )
        )
    return contradictions


def _contradiction_severity(
    a: FactorOut, b: FactorOut, effective_weight: dict[str, Decimal]
) -> str:
    aw = effective_weight.get(a.name, Decimal("0"))
    bw = effective_weight.get(b.name, Decimal("0"))
    combined = aw + bw
    a_strong = abs(a.score or 0) >= float(REGIME_THRESHOLDS["strong_bullish"])
    b_strong = abs(b.score or 0) >= float(REGIME_THRESHOLDS["strong_bullish"])
    if (a_strong and b_strong) or combined >= HIGH_CONTRADICTION_WEIGHT:
        return "high"
    if a_strong or b_strong:
        return "medium"
    return "low"


def _build_risk_factors(
    composite: Decimal | None,
    completeness: Decimal,
    confidence: Decimal,
    availability: dict[str, bool],
    factors: dict[str, FactorOut],
    high_volatility: bool,
    contradictions: list[Contradiction],
) -> list[RiskFactor]:
    risks: list[RiskFactor] = []
    if high_volatility and availability.get("volatility", False):
        risks.append(
            RiskFactor(
                type="HIGH_VIX",
                severity="medium",
                message="Indian volatility (VIX) factor is strongly adverse.",
            )
        )
    for name in ("price_action", "breadth"):
        if availability.get(name, False) and factors[name].freshness == "stale":
            risks.append(
                RiskFactor(
                    type="STALE_MAJOR_DATA",
                    severity="medium",
                    message=f"Major factor '{name}' is stale.",
                )
            )
            break
    if completeness < LOW_COMPLETENESS_BELOW:
        risks.append(
            RiskFactor(
                type="LOW_COMPLETENESS",
                severity="medium",
                message="Data completeness across factors is low.",
            )
        )
    if contradictions:
        max_severity = "high" if any(c.severity == "high" for c in contradictions) else "medium"
        risks.append(
            RiskFactor(
                type="STRONG_CONTRADICTION",
                severity=max_severity,
                message="Opposing factor signals detected.",
            )
        )
    if availability.get("breadth", False):
        bscore = factors["breadth"].score or 0
        if bscore <= float(EXTREME_NEGATIVE_BELOW):
            risks.append(
                RiskFactor(
                    type="EXTREME_NEGATIVE_BREADTH",
                    severity="high",
                    message="Market breadth is strongly negative.",
                )
            )
    if availability.get("global", False):
        gscore = factors["global"].score or 0
        if gscore <= float(EXTREME_NEGATIVE_BELOW):
            risks.append(
                RiskFactor(
                    type="EXTREME_NEGATIVE_GLOBAL",
                    severity="high",
                    message="Global market context is strongly negative.",
                )
            )
    return risks


class CompositeMarketIntelligence:
    """Deterministic market regime & composite intelligence orchestrator.

    Consumes the independent factor engines from Tasks 18.2-18.8, each evaluated
    with the *same* evaluation boundary, and never re-implements factor formulas.
    """

    _LOOKBACK_DAYS = 400

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...] = (),
        *,
        score_config: MarketIntelligenceScoreConfig | None = None,
        price_repo: MarketIndexPriceRepository | None = None,
        snapshot_repo: MarketIntelligenceSnapshotRepository | None = None,
        breadth_engine: MarketBreadth | None = None,
        sector_engine: SectorIntelligence | None = None,
        institutional_engine: InstitutionalIntelligence | None = None,
        external_factor_engine: ExternalMarketFactorIntelligence | None = None,
        global_engine: GlobalMarketIntelligence | None = None,
        gift_nifty_engine: GiftNiftyIntelligence | None = None,
        macro_engine: MacroIntelligence | None = None,
    ) -> None:
        self._session = session
        self._providers = providers
        self._score_config = score_config or MarketIntelligenceScoreConfig()
        self._price_repo = price_repo or MarketIndexPriceRepository(session)
        self._snapshot_repo = snapshot_repo or MarketIntelligenceSnapshotRepository(session)
        self._breadth_engine = breadth_engine or MarketBreadth(session)
        self._sector_engine = sector_engine or SectorIntelligence(session)
        self._institutional_engine = (
            institutional_engine or InstitutionalIntelligence(session, providers)
        )
        self._external_factor_engine = (
            external_factor_engine
            or ExternalMarketFactorIntelligence(session, providers)
        )
        self._global_engine = global_engine or GlobalMarketIntelligence(session)
        self._gift_nifty_engine = (
            gift_nifty_engine or GiftNiftyIntelligence(session, providers)
        )
        self._macro_engine = macro_engine or MacroIntelligence(session, providers)

    async def evaluate(
        self,
        evaluation_at: datetime,
        market_date: date | None = None,
        *,
        persistence: bool = False,
        factor_inputs: list[FactorInput] | None = None,
    ) -> CompositeMarketIntelligenceResult:
        """Evaluate the complete composite market intelligence at an aware instant.

        All factor engines are evaluated against the same ``evaluation_at`` /
        ``market_date`` boundary so no future data can alter a historical result.
        """
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")
        if market_date is None:
            market_date = evaluation_at.astimezone(ZoneInfo(INDIA_TIMEZONE)).date()

        if factor_inputs is None:
            factors, upcoming_macro_events = await self._collect_factors(
                evaluation_at, market_date
            )
        else:
            factors = self._apply_weights(factor_inputs)
            upcoming_macro_events = []

        result = compute_composite_factor_result(factors, self._score_config)
        result.evaluation_time = evaluation_at
        result.market_date = market_date
        result.upcoming_macro_events = upcoming_macro_events
        if persistence:
            snapshot = await self._persist(result, market_date)
            result.snapshot_id = getattr(snapshot, "id", None)
            result.provenance_summary = [f"snapshot_id={getattr(snapshot, 'id', None)}"]
        return result

    def _apply_weights(self, factor_inputs: list[FactorInput]) -> list[FactorInput]:
        weights = dict(zip(FACTOR_NAMES, self._score_config.weights, strict=True))
        output: list[FactorInput] = []
        for factor in factor_inputs:
            output.append(
                FactorInput(
                    name=factor.name,
                    score=factor.score,
                    configured_weight=weights[factor.name],
                    confidence=factor.confidence,
                    completeness=factor.completeness,
                    freshness=factor.freshness,
                    provenance=factor.provenance,
                    direction=factor.direction,
                )
            )
        return output

    async def _persist(
        self, result: CompositeMarketIntelligenceResult, market_date: date
    ):
        scores = {f.name: f.score for f in result.factors}
        snapshot = await self._snapshot_repo.upsert(
            market_date=market_date,
            generated_at=market_date,
            price_action_score=scores.get("price_action"),
            breadth_score=scores.get("breadth"),
            sector_score=scores.get("sector"),
            institutional_score=scores.get("institutional"),
            volatility_score=scores.get("volatility"),
            global_score=scores.get("global"),
            gift_nifty_score=scores.get("gift_nifty"),
            currency_score=scores.get("currency"),
            crude_score=scores.get("crude"),
            macro_score=scores.get("macro"),
            composite_score=result.composite_score,
            confidence_score=result.confidence_score,
            regime=result.regime,
            bullish_factors=[f.model_dump() for f in result.bullish_factors],
            bearish_factors=[f.model_dump() for f in result.bearish_factors],
            risk_factors=[f.model_dump() for f in result.risk_factors],
            missing_factors=[f.model_dump() for f in result.missing_factors],
            data_completeness_percent=result.data_completeness_percent,
            stale_factor_count=len(result.stale_factors),
            missing_factor_count=len(result.missing_factors),
        )
        return snapshot

    async def _collect_factors(
        self, evaluation_at: datetime, market_date: date
    ) -> tuple[list[FactorInput], list[dict[str, Any]]]:
        # Evaluate all factor engines against the SAME boundary. The three
        # external factors (VIX / USD-INR / Brent) come from one engine call.
        external = await self._evaluate_kw(self._external_factor_engine, evaluation_at)
        breadth = await self._evaluate_breadth(self._breadth_engine, market_date, evaluation_at)
        sector = await self._evaluate_kw(self._sector_engine, market_date)
        institutional = await self._evaluate_kw(self._institutional_engine, evaluation_at)
        global_result = await self._evaluate_kw(self._global_engine, evaluation_at)
        gift = await self._evaluate_kw(self._gift_nifty_engine, evaluation_at)
        macro = await self._evaluate_kw(self._macro_engine, evaluation_at)
        price_action = await self._evaluate_price_action(market_date)

        weights = dict(zip(FACTOR_NAMES, self._score_config.weights, strict=True))

        factors: list[FactorInput] = [
            self._price_action_factor(price_action, weights["price_action"]),
            FactorInput(
                name="breadth",
                score=breadth.get("breadth_score"),
                configured_weight=weights["breadth"],
                confidence=breadth.get("confidence"),
                completeness=breadth.get("completeness"),
                freshness=breadth.get("freshness", "missing"),
                provenance=_simple_provenance(breadth.get("provenance")),
                direction=breadth.get("breadth_direction"),
            ),
            FactorInput(
                name="sector",
                score=sector.get("sector_intelligence_score"),
                configured_weight=weights["sector"],
                confidence=sector.get("confidence_score"),
                completeness=sector.get("completeness_percent"),
                freshness=sector.get("freshness", "missing"),
                provenance=_simple_provenance(sector.get("provenance")),
                direction=sector.get("direction"),
            ),
            FactorInput(
                name="institutional",
                score=institutional.get("institutional_score"),
                configured_weight=weights["institutional"],
                confidence=institutional.get("confidence"),
                completeness=institutional.get("completeness"),
                freshness=institutional.get("freshness", "missing"),
                provenance=_simple_provenance(institutional.get("provenance")),
                direction=institutional.get("direction"),
            ),
            self._external_factor(
                weights["volatility"], "volatility", external, INDIA_VIX_INDEX_CODE
            ),
            FactorInput(
                name="global",
                score=global_result.get("global_score"),
                configured_weight=weights["global"],
                confidence=global_result.get("confidence"),
                completeness=global_result.get("completeness"),
                freshness="live" if global_result.get("global_score") is not None else "missing",
                provenance=_simple_provenance(_global_provenance(global_result)),
                direction=global_result.get("global_direction"),
            ),
            FactorInput(
                name="gift_nifty",
                score=gift.get("gift_nifty_score"),
                configured_weight=weights["gift_nifty"],
                confidence=gift.get("confidence_score"),
                completeness=gift.get("data_completeness_percent"),
                freshness=self._observation_freshness(gift, "gift_observation"),
                provenance=_simple_provenance(gift.get("provenance")),
                direction=gift.get("direction"),
            ),
            self._external_factor(
                weights["currency"], "currency", external, USD_INR_INDEX_CODE
            ),
            self._external_factor(
                weights["crude"], "crude", external, BRENT_CRUDE_INDEX_CODE
            ),
            FactorInput(
                name="macro",
                score=macro.get("macro_score"),
                configured_weight=weights["macro"],
                confidence=macro.get("confidence"),
                completeness=macro.get("completeness"),
                freshness=macro.get("freshness", "missing"),
                provenance=_simple_provenance(macro.get("provenance")),
                direction=macro.get("direction"),
            ),
        ]
        upcoming_macro_events = list(macro.get("upcoming_events") or [])
        return factors, upcoming_macro_events

    @staticmethod
    async def _evaluate_kw(engine: Any, boundary: Any) -> dict[str, Any]:
        return await engine.evaluate(boundary)

    @staticmethod
    async def _evaluate_breadth(
        engine: Any, market_date: date, evaluation_at: datetime
    ) -> dict[str, Any]:
        return await engine.evaluate(market_date, evaluation_time=evaluation_at)

    async def _evaluate_price_action(self, market_date: date) -> dict[str, Any]:
        start = market_date - timedelta(days=self._LOOKBACK_DAYS)
        rows = await self._price_repo.get_date_range_by_index_code(
            PRICE_ACTION_INDEX_CODE, start, market_date
        )
        memory = _PriceRepo({PRICE_ACTION_INDEX_CODE: rows})
        pa = IndexPriceAction(PRICE_ACTION_INDEX_CODE, market_date)
        pa.set_repo(memory)
        raw = await pa.price_action_score()
        return {
            "score": raw["price_action_score"],
            "breakdown": raw["score_breakdown"],
            "has_prices": bool(rows[-1].trade_date <= market_date) if rows else False,
        }

    def _price_action_factor(
        self, price_action: dict[str, Any], weight: float
    ) -> FactorInput:
        if not price_action["has_prices"]:
            return FactorInput(
                name="price_action",
                score=None,
                configured_weight=weight,
                confidence=0.0,
                completeness=0.0,
                freshness="missing",
                provenance="no price data at or before boundary",
            )
        breakdown = price_action["breakdown"]
        usable = 0
        if breakdown.get("trend") not in (None, "neutral"):
            usable += 1
        if breakdown.get("momentum") is not None:
            usable += 1
        if breakdown.get("moving_average_position") in ("above", "below"):
            usable += 1
        if breakdown.get("fifty_two_week_position") is not None:
            usable += 1
        if breakdown.get("gap") in ("gap_up", "gap_down"):
            usable += 1
        completeness = usable / 5 * 100
        confidence = completeness
        return FactorInput(
            name="price_action",
            score=price_action["score"],
            configured_weight=weight,
            confidence=confidence,
            completeness=completeness,
            freshness="live",
            provenance="NIFTY_50 bounded price-action engine",
        )

    @staticmethod
    def _external_factor(
        weight: float,
        factor_name: str,
        external: dict[str, Any],
        code: str,
    ) -> FactorInput:
        factors = external.get("factors") or {}
        key = _EXTERNAL_KEY_BY_CODE.get(code)
        entry = factors.get(key) or {}
        score = entry.get("score")
        return FactorInput(
            name=factor_name,
            score=score,
            configured_weight=weight,
            confidence=entry.get("confidence"),
            completeness=entry.get("completeness"),
            freshness=entry.get("freshness", "missing"),
            provenance=_simple_provenance(entry.get("identity")),
            direction=entry.get("direction"),
        )

    @staticmethod
    def _observation_freshness(result: dict[str, Any], key: str) -> str:
        entry = result.get(key)
        if isinstance(entry, dict):
            freshness = entry.get("freshness")
            if freshness:
                return freshness
        return result.get("fallback_level", "missing")


_EXTERNAL_KEY_BY_CODE = {
    INDIA_VIX_INDEX_CODE: "india_vix",
    USD_INR_INDEX_CODE: "usd_inr",
    BRENT_CRUDE_INDEX_CODE: "brent_crude",
}


class _PriceRepo:
    """Repository-compatible view over a single bounded bulk read."""

    def __init__(self, histories: dict[str, list[MarketIndexPrice]]) -> None:
        self._histories = histories

    async def get_date_range_by_index_code(
        self, index_code: str, start_date: date, end_date: date
    ) -> list[MarketIndexPrice]:
        return [
            row
            for row in self._histories.get(index_code, [])
            if start_date <= row.trade_date <= end_date
        ]


def _simple_provenance(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        keys = [
            k for k in ("dataset", "source", "instrument", "unit", "methodology")
            if value.get(k)
        ]
        return ", ".join(f"{k}={value[k]}" for k in keys) or None
    return str(value) or None


def _global_provenance(result: dict[str, Any]) -> Any:
    markets = result.get("markets")
    if not isinstance(markets, dict):
        return None
    sources = sorted({str(m.get("source_name")) for m in markets.values() if m.get("source_name")})
    return {"source": ",".join(sources)} if sources else None
