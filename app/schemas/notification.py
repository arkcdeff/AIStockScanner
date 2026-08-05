from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    config_json: dict | None = None


class NotificationChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    enabled: bool
    config_json: dict | None
    created_at: datetime
    updated_at: datetime


class NotificationPreferenceCreate(BaseModel):
    channel_id: int
    event_type: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    minimum_priority: int | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    event_type: str
    enabled: bool
    minimum_priority: int | None
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_event_id: int | None
    channel_id: int
    notification_type: str
    subject: str | None
    rendered_body: str | None
    status: str
    attempt_count: int
    external_message_id: str | None
    error_message: str | None
    next_retry_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DailySummaryRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    summary_date: date
    status: str
    alerts_count: int
    scanner_matches: int
    top_rated_count: int
    watchlist_changes: int
    portfolio_value: Decimal | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime