from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DailyPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    series: str | None
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal | None
    last_price: Decimal | None
    volume: int
    turnover: Decimal | None
    trade_count: int | None
    delivery_quantity: int | None
    delivery_percentage: Decimal | None


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    file_name: str
    trade_date: date | None
    status: str
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_rejected: int
    error_message: str | None
    details: dict
    started_at: datetime | None
    completed_at: datetime | None


class MarketDataSummary(BaseModel):
    stocks: int
    daily_prices: int
    first_trade_date: date | None
    last_trade_date: date | None


class DownloadRequest(BaseModel):
    trade_date: date


class DownloadResponse(BaseModel):
    trade_date: date
    path: str
    size: int
    checksum: str
    downloaded: bool


class BackfillRequest(BaseModel):
    start_date: date
    end_date: date


class CoverageRow(BaseModel):
    trade_date: date
    weekday: str
    status: str
    price_count: int
    stock_count: int
    delivery_count: int
    indicator_count: int
    scan_count: int


class ScannerReadiness(BaseModel):
    latest_trade_date: date | None
    readiness: dict[str, int]
    latest_price_count: int
    latest_delivery_count: int
