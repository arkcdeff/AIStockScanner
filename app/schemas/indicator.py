from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class IndicatorResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    symbol: str
    trade_date: date
    ema_20: Decimal | None
    ema_50: Decimal | None
    ema_100: Decimal | None
    ema_200: Decimal | None
    sma: Decimal | None
    rsi_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_hist: Decimal | None
    stochastic_k: Decimal | None
    stochastic_d: Decimal | None
    atr: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_middle: Decimal | None
    bollinger_lower: Decimal | None
    adx: Decimal | None
    supertrend: Decimal | None
    supertrend_direction: int | None
    vwap: Decimal | None
    obv: Decimal | None


class IndicatorCalculateResponse(BaseModel):
    symbol: str
    calculated: int
    skipped: int
    message: str | None = None
