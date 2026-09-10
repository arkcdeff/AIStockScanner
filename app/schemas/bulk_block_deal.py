"""Pydantic schemas for Bulk and Block Deals."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BulkBlockDealRead(BaseModel):
    """Public read schema for a bulk or block deal transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_date: date
    deal_type: Literal["BULK", "BLOCK"]
    symbol: str
    security_name: str
    client_name: str
    buy_sell: Literal["BUY", "SELL"]
    quantity: int
    trade_price: Decimal
    deal_value: Decimal
    remarks: str | None = None
    stock_id: int | None = None
    available_at: date
    source_name: str
    source_reference: str
    deal_row_hash: str
    is_current: bool
    created_at: datetime


class BulkBlockDealSummary(BaseModel):
    """Aggregated deal summary statistics for a given stock or query filter."""

    model_config = ConfigDict(populate_by_name=True)

    total_deals: int = 0
    total_value: Decimal = Decimal("0.00")
    buy_deals_count: int = Field(default=0, alias="buy_deals")
    buy_quantity: int = 0
    buy_value: Decimal = Decimal("0.00")
    sell_deals_count: int = Field(default=0, alias="sell_deals")
    sell_quantity: int = 0
    sell_value: Decimal = Decimal("0.00")
    net_value: Decimal = Decimal("0.00")
    bulk_deals_count: int = Field(default=0, alias="bulk_deals")
    block_deals_count: int = Field(default=0, alias="block_deals")


class BulkBlockDealPage(BaseModel):
    """Paginated list of bulk/block deals with query metadata and summary."""

    model_config = ConfigDict(populate_by_name=True)

    deals: list[BulkBlockDealRead] = Field(default_factory=list, alias="items")
    total_count: int = Field(default=0, alias="total")
    page: int = 1
    page_size: int = 50
    total_pages: int = Field(default=1, alias="pages")
    summary: BulkBlockDealSummary | None = None
