from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    stock_id: int | None = None
    watchlist_id: int | None = None
    rule_type: str
    operator: str
    value: float | None = None
    notification_type: str = "in_app"
    cooldown_minutes: int = 0


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    stock_id: int | None = None
    watchlist_id: int | None = None
    rule_type: str | None = None
    operator: str | None = None
    value: float | None = None
    notification_type: str | None = None
    cooldown_minutes: int | None = None


class AlertRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    enabled: bool
    stock_id: int | None
    watchlist_id: int | None
    rule_type: str
    operator: str
    value: float | None
    notification_type: str
    cooldown_minutes: int
    last_triggered: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_rule_id: int
    stock_id: int
    trade_date: date | None
    trigger_value: float | None
    message: str
    status: str
    created_at: datetime
    rule_name: str | None = None
    symbol: str | None = None


class AlertEventUpdate(BaseModel):
    status: str


class AlertRunResult(BaseModel):
    checked: int
    triggered: int
