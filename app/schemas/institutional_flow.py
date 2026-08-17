from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, validator


class InstitutionalFlowCreate(BaseModel):
    trade_date: date = Field(..., description="YYYY-MM-DD")
    investor_type: str = Field(..., min_length=1, max_length=16)
    buy_value: float | None = Field(default=None, ge=0)
    sell_value: float | None = Field(default=None, ge=0)
    net_value: float | None = Field(default=None)

    @validator("investor_type")
    def type_must_be_valid(cls, v: str) -> str:
        allowed = ["FII", "DII"]
        if v not in allowed:
            raise ValueError(f"investor_type must be one of {allowed}")
        return v.upper()


class InstitutionalFlowUpdate(BaseModel):
    buy_value: float | None = Field(default=None, ge=0)
    sell_value: float | None = Field(default=None, ge=0)
    net_value: float | None = Field(default=None)
    one_day_cumulative: float | None = Field(default=None)
    three_day_cumulative: float | None = Field(default=None)
    five_day_cumulative: float | None = Field(default=None)
    twenty_day_cumulative: float | None = Field(default=None)


class InstitutionalFlowResponse(BaseModel):
    id: int
    trade_date: date
    investor_type: str
    buy_value: float | None = None
    sell_value: float | None = None
    net_value: float | None = None
    one_day_cumulative: float | None = None
    three_day_cumulative: float | None = None
    five_day_cumulative: float | None = None
    twenty_day_cumulative: float | None = None
    source_name: str | None = None
    observed_at: date | None = None
    fetched_at: date | None = None
    freshness_status: str
    created_at: date
    updated_at: date

    model_config = {"from_attributes": True}

    @property
    def is_net_positive(self) -> bool | None:
        return self.net_value is not None and self.net_value > 0