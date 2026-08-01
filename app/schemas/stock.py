from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StockCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    company_name: str = Field(min_length=1, max_length=255)
    exchange: str = Field(default="NSE", max_length=16)
    series: str | None = Field(default="EQ", max_length=8)
    isin: str | None = Field(default=None, max_length=20)
    sector_id: int | None = None
    industry: str | None = Field(default=None, max_length=120)


class StockRead(StockCreate):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
