from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analytics import AnalyticsListItemRead, TechnicalAnalysisRead


class AIStockContext(BaseModel):
    symbol: str
    company_name: str
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    trade_date: date
    latest_close: float | None = None
    price_change_pct: float | None = None
    volume: int | None = None
    avg_volume_20d: float | None = None
    delivery_percentage: float | None = None


class AIAnalyticsContext(BaseModel):
    overall_score: int
    trend_score: int = 0
    momentum_score: int = 0
    volume_score: int = 0
    delivery_score: int = 0
    volatility_score: int = 0
    support: float | None = None
    resistance: float | None = None
    pivot: float | None = None
    trend_rating: str | None = None
    momentum_rating: str | None = None
    volatility_rating: str | None = None
    is_trending: bool = False
    is_breakout: bool = False


class AIIndicatorContext(BaseModel):
    trade_date: date | None = None
    ema_20: float | None = None
    ema_50: float | None = None
    ema_100: float | None = None
    ema_200: float | None = None
    sma: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr: float | None = None
    adx: float | None = None
    supertrend: float | None = None
    supertrend_direction: str | None = None
    vwap: float | None = None
    obv: float | None = None
    bollinger_upper: float | None = None
    bollinger_middle: float | None = None
    bollinger_lower: float | None = None
    stochastic_k: float | None = None
    stochastic_d: float | None = None


class AIScannerMatchContext(BaseModel):
    scanner_name: str
    category: str | None = None
    score: float | None = None
    reason: str | None = None


class AIPipelineContext(BaseModel):
    has_run: bool = False
    status: str | None = None
    trade_date: date | None = None
    imported_rows: int = 0
    indicators_calculated: int = 0
    scanner_matches: int = 0
    analytics_calculated: int = 0


class AIExplanationPayload(BaseModel):
    """Validated AI output for a single stock."""

    summary: str = ""
    medium_summary: str = ""
    detailed_summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    support: list[str] = Field(default_factory=list)
    resistance: list[str] = Field(default_factory=list)
    scanner_explanation: str = ""
    trend_explanation: str = ""
    risk_explanation: str = ""
    confidence: int = Field(default=0, ge=0, le=100)


class AICollectionPayload(BaseModel):
    """Validated AI output for a group of stocks (scanner/watchlist summary)."""

    summary: str = ""
    explanation: str = ""
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)


class AIMarketPayload(BaseModel):
    """Validated AI output for the market summary."""

    summary: str = ""
    explanation: str = ""
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)


class AIStockExplanationResponse(AIExplanationPayload):
    symbol: str
    company_name: str
    trade_date: date
    provider: str
    model: str
    source: Literal["generated", "cache", "fallback"]
    technical_score: TechnicalAnalysisRead


class AICollectionExplanationResponse(AICollectionPayload):
    trade_date: date | None = None
    provider: str
    model: str
    source: Literal["generated", "cache", "fallback"]
    stocks: list[AnalyticsListItemRead]


class AIMarketSummaryResponse(AIMarketPayload):
    trade_date: date | None = None
    provider: str
    model: str
    source: Literal["generated", "cache", "fallback"]
    stock_count: int = 0
    top_rated: list[AnalyticsListItemRead]
