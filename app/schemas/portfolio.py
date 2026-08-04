from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreate(BaseModel):
    broker: str | None = Field(default=None, max_length=120)
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class PortfolioUpdate(BaseModel):
    broker: str | None = Field(default=None, max_length=120)
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class PortfolioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker: str | None
    account_name: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PortfolioSummaryRead(PortfolioRead):
    holdings_count: int = 0
    total_invested: float = 0.0
    total_value: float = 0.0
    total_pl: float = 0.0
    total_roi_pct: float = 0.0
    today_pl: float = 0.0


class HoldingCreate(BaseModel):
    stock_id: int
    quantity: Decimal = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    purchase_date: date | None = None
    broker_order_id: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class HoldingUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    average_price: Decimal | None = Field(default=None, gt=0)
    purchase_date: date | None = None
    broker_order_id: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class HoldingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    stock_id: int
    quantity: float
    average_price: float
    purchase_date: date | None
    broker_order_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class HoldingValuationRead(HoldingRead):
    symbol: str
    company_name: str
    latest_close: float | None
    previous_close: float | None
    invested: float
    current_value: float | None
    pl: float | None
    roi_pct: float | None
    today_pl: float | None
    allocation_pct: float


class PortfolioDetailRead(PortfolioRead):
    holdings: list[HoldingValuationRead] = []
    holdings_count: int = 0
    total_invested: float = 0.0
    total_value: float = 0.0
    total_pl: float = 0.0
    total_roi_pct: float = 0.0
    today_pl: float = 0.0
