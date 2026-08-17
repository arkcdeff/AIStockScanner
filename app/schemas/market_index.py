from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, validator


class MarketIndexCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=255)
    country: str = Field(default="INDIA", min_length=1, max_length=16)
    exchange: str | None = Field(default=None, max_length=32)
    category: str = Field(default="indian_index", min_length=1, max_length=32)
    currency: str | None = Field(default=None, max_length=3)
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=40)
    provider_symbol: str | None = Field(default=None, max_length=64)
    is_active: bool = Field(default=True)

    @validator("code")
    def code_must_be_uppercase(cls, v: str) -> str:
        return v.upper()

    model_config = {"from_attributes": True}


class MarketIndexUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    exchange: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, min_length=1, max_length=32)
    currency: str | None = Field(default=None, max_length=3)
    timezone: str | None = Field(default=None, min_length=1, max_length=40)
    provider_symbol: str | None = Field(default=None, max_length=64)
    is_active: bool | None = Field(default=None)

    model_config = {"from_attributes": True}


class MarketIndexResponse(BaseModel):
    id: int
    code: str
    name: str
    country: str
    exchange: str | None = None
    category: str
    currency: str | None = None
    timezone: str
    provider_symbol: str | None = None
    is_active: bool
    created_at: date
    updated_at: date

    model_config = {"from_attributes": True}

    @property
    def display_name(self) -> str:
        return f"{self.code} ({self.name})"