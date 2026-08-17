from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, validator


class MarketIndexPriceCreate(BaseModel):
    market_index_id: int = Field(..., ge=1)
    trade_date: date = Field(..., description="YYYY-MM-DD")
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    previous_close: float | None = Field(default=None, gt=0)
    change: float | None = Field(default=None)
    change_percent: float | None = Field(default=None, ge=-100, le=100)
    volume: int | None = Field(default=None, ge=0)
    observed_at: date | None = Field(default=None)
    fetched_at: date | None = Field(default=None)
    source_name: str | None = Field(default=None, max_length=128)
    source_reference: str | None = Field(default=None, max_length=256)
    freshness_status: str = Field(default="stale", min_length=1, max_length=20)

    @validator("freshness_status")
    def status_must_be_valid(cls, v: str) -> str:
        allowed = {"live", "recent", "stale", "missing"}
        if v not in allowed:
            raise ValueError(f"freshness_status must be one of {allowed}")
        return v


class MarketIndexPriceUpdate(BaseModel):
    open: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    close: float | None = Field(default=None, gt=0)
    previous_close: float | None = Field(default=None, gt=0)
    change: float | None = Field(default=None)
    change_percent: float | None = Field(default=None, ge=-100, le=100)
    volume: int | None = Field(default=None, ge=0)
    observed_at: date | None = Field(default=None)
    fetched_at: date | None = Field(default=None)
    source_name: str | None = Field(default=None, max_length=128)
    source_reference: str | None = Field(default=None, max_length=256)
    freshness_status: str | None = Field(default=None, min_length=1, max_length=20)

    @validator("freshness_status")
    def status_must_be_valid(cls, v: str) -> str | None:
        if v is not None:
            allowed = {"live", "recent", "stale", "missing"}
            if v not in allowed:
                raise ValueError(f"freshness_status must be one of {allowed}")
        return v


class MarketIndexPriceResponse(BaseModel):
    id: int
    market_index_id: int
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: int | None = None
    observed_at: date | None = None
    fetched_at: date | None = None
    source_name: str | None = None
    source_reference: str | None = None
    freshness_status: str
    created_at: date
    updated_at: date

    model_config = {"from_attributes": True}

    @property
    def change_summary(self) -> str:
        if self.change is None:
            return "No change data"
        if self.change_percent is None:
            return f"Change: {self.change:.2f}"
        return f"Change: {self.change:.2f} ({self.change_percent:.2f}%)"