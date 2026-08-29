from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class StockOverviewResponse(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    series: str | None
    isin: str | None
    sector: str | None
    industry: str | None
    latest_trade_date: date | None
    latest_close: float | None
    previous_close: float | None
    price_change: float | None
    price_change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int | None
    avg_volume_20d: float | None
    volume_multiple: float | None
    delivery_quantity: int | None
    delivery_percentage: float | None
    avg_delivery_20d: float | None
    history_sessions: int = 0
    history_first_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class IndicatorCardResponse(BaseModel):
    trade_date: date | None
    ema_20: float | None
    ema_50: float | None
    ema_100: float | None
    ema_200: float | None
    sma: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    atr: float | None
    adx: float | None
    supertrend: float | None
    supertrend_direction: int | None
    vwap: float | None
    obv: float | None
    bollinger_upper: float | None
    bollinger_middle: float | None
    bollinger_lower: float | None
    stochastic_k: float | None
    stochastic_d: float | None

    model_config = ConfigDict(from_attributes=True)


class ScannerMatchResponse(BaseModel):
    id: int
    scan_date: date
    matched: bool
    score: float | None
    metrics: dict
    scanner_name: str | None
    category: str | None
    reason: str | None

    model_config = ConfigDict(from_attributes=True)


class ChartDataResponse(BaseModel):
    symbol: str
    candles: list[dict[str, Any]]
    volume: list[dict[str, Any]]
    ema20: list[dict[str, Any]]
    ema50: list[dict[str, Any]]
    ema200: list[dict[str, Any]]
    scanner_markers: list[dict[str, Any]]
