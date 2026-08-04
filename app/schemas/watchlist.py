from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    sort_order: int = 0
    is_favorite: bool = False
    is_default: bool = False


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    sort_order: int | None = None
    is_favorite: bool | None = None
    is_default: bool | None = None


class WatchlistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    color: str | None
    icon: str | None
    sort_order: int
    is_favorite: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    stock_count: int = 0


class WatchlistStockCreate(BaseModel):
    stock_id: int
    notes: str | None = None
    priority: int = 0


class WatchlistStockUpdate(BaseModel):
    notes: str | None = None
    priority: int | None = None


class WatchlistStockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    watchlist_id: int
    stock_id: int
    notes: str | None
    priority: int
    added_at: datetime


class WatchlistStockWithPriceRead(WatchlistStockRead):
    symbol: str
    company_name: str
    latest_close: float | None
    trade_date: date | None


class WatchlistDetailRead(WatchlistRead):
    stocks: list[WatchlistStockWithPriceRead] = []
