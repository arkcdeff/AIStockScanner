from datetime import date

from pydantic import BaseModel, ConfigDict


class TechnicalAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    trade_date: date
    trend_score: int
    momentum_score: int
    volume_score: int
    delivery_score: int
    volatility_score: int
    overall_score: int
    support: float | None
    resistance: float | None
    pivot: float | None
    pivot_r1: float | None
    pivot_r2: float | None
    pivot_r3: float | None
    pivot_s1: float | None
    pivot_s2: float | None
    pivot_s3: float | None
    fib_pivot: float | None
    fib_r1: float | None
    fib_r2: float | None
    fib_r3: float | None
    fib_s1: float | None
    fib_s2: float | None
    fib_s3: float | None
    relative_volume: float | None
    delivery_strength: float | None
    trend_rating: str | None
    momentum_rating: str | None
    volatility_rating: str | None
    is_trending: bool
    is_breakout: bool


class AnalyticsListItemRead(TechnicalAnalysisRead):
    symbol: str
    company_name: str
    latest_close: float | None
