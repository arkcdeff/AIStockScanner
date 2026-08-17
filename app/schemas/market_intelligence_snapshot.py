from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MarketIntelligenceSnapshotCreate(BaseModel):
    market_date: date = Field(..., description="YYYY-MM-DD")
    generated_at: date | None = Field(default=None)
    price_action_score: Decimal | None = Field(default=None, ge=-100, le=100)
    breadth_score: Decimal | None = Field(default=None, ge=-100, le=100)
    sector_score: Decimal | None = Field(default=None, ge=-100, le=100)
    institutional_score: Decimal | None = Field(default=None, ge=-100, le=100)
    volatility_score: Decimal | None = Field(default=None, ge=-100, le=100)
    global_score: Decimal | None = Field(default=None, ge=-100, le=100)
    gift_nifty_score: Decimal | None = Field(default=None, ge=-100, le=100)
    currency_score: Decimal | None = Field(default=None, ge=-100, le=100)
    crude_score: Decimal | None = Field(default=None, ge=-100, le=100)
    macro_score: Decimal | None = Field(default=None, ge=-100, le=100)
    composite_score: Decimal | None = Field(default=None, ge=-100, le=100)
    confidence_score: Decimal | None = Field(default=None, ge=0, le=100)
    regime: str | None = Field(default=None, max_length=32)
    bullish_factors_json: dict[str, Any] | None = Field(default=None)
    bearish_factors_json: dict[str, Any] | None = Field(default=None)
    risk_factors_json: dict[str, Any] | None = Field(default=None)
    missing_factors_json: dict[str, Any] | None = Field(default=None)
    data_completeness_percent: Decimal | None = Field(
        default=None, ge=0, le=100
    )
    stale_factor_count: int = Field(default=0, ge=0)
    missing_factor_count: int = Field(default=0, ge=0)


class MarketIntelligenceSnapshotUpdate(BaseModel):
    generated_at: date | None = Field(default=None)
    price_action_score: Decimal | None = Field(
        default=None, ge=-100, le=100
    )
    breadth_score: Decimal | None = Field(default=None, ge=-100, le=100)
    sector_score: Decimal | None = Field(default=None, ge=-100, le=100)
    institutional_score: Decimal | None = Field(default=None, ge=-100, le=100)
    volatility_score: Decimal | None = Field(default=None, ge=-100, le=100)
    global_score: Decimal | None = Field(default=None, ge=-100, le=100)
    gift_nifty_score: Decimal | None = Field(default=None, ge=-100, le=100)
    currency_score: Decimal | None = Field(default=None, ge=-100, le=100)
    crude_score: Decimal | None = Field(default=None, ge=-100, le=100)
    macro_score: Decimal | None = Field(default=None, ge=-100, le=100)
    composite_score: Decimal | None = Field(default=None, ge=-100, le=100)
    confidence_score: Decimal | None = Field(
        default=None, ge=0, le=100
    )
    regime: str | None = Field(default=None, max_length=32)
    bullish_factors_json: dict[str, Any] | None = Field(default=None)
    bearish_factors_json: dict[str, Any] | None = Field(default=None)
    risk_factors_json: dict[str, Any] | None = Field(default=None)
    missing_factors_json: dict[str, Any] | None = Field(default=None)
    data_completeness_percent: Decimal | None = Field(
        default=None, ge=0, le=100
    )
    stale_factor_count: int | None = Field(default=None, ge=0)
    missing_factor_count: int | None = Field(default=None, ge=0)


class MarketIntelligenceSnapshotResponse(BaseModel):
    id: int
    market_date: date
    generated_at: date
    price_action_score: Decimal | None = None
    breadth_score: Decimal | None = None
    sector_score: Decimal | None = None
    institutional_score: Decimal | None = None
    volatility_score: Decimal | None = None
    global_score: Decimal | None = None
    gift_nifty_score: Decimal | None = None
    currency_score: Decimal | None = None
    crude_score: Decimal | None = None
    macro_score: Decimal | None = None
    composite_score: Decimal | None = None
    confidence_score: Decimal | None = None
    regime: str | None = None
    bullish_factors_json: dict[str, Any] | None = None
    bearish_factors_json: dict[str, Any] | None = None
    risk_factors_json: dict[str, Any] | None = None
    missing_factors_json: dict[str, Any] | None = None
    data_completeness_percent: Decimal | None = None
    stale_factor_count: int = 0
    missing_factor_count: int = 0
    created_at: date
    updated_at: date

    model_config = {"from_attributes": True}

    @property
    def is_bullish(self) -> bool | None:
        return self.composite_score is not None and self.composite_score > 0

    @property
    def is_bearish(self) -> bool | None:
        return self.composite_score is not None and self.composite_score < 0

    @property
    def is_neutral(self) -> bool | None:
        return self.composite_score is not None and -24 <= self.composite_score <= 24


class MarketIntelligenceScoreConfig(BaseModel):
    """Centralized scoring configuration.

    ``weights`` holds exactly 10 factor weights. Each weight must be in
    ``[0, 1]`` and the weights must sum to exactly ``1.0``.
    """

    weights: list[float] = Field(
        default_factory=lambda: [
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
    )

    @model_validator(mode="after")
    def _validate_weights(self) -> MarketIntelligenceScoreConfig:
        weights = self.weights
        if len(weights) != 10:
            raise ValueError(
                f"Scoring config must have exactly 10 weights, got {len(weights)}"
            )
        for i, weight in enumerate(weights):
            if weight < 0 or weight > 1:
                raise ValueError(f"Weight {i} must be in [0, 1], got {weight}")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Scoring weights must sum to 1.0, got {total}")
        return self