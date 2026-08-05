from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NotificationProviderName = Literal["mock", "telegram", "email", "in_app"]

SUPPORTED_NOTIFICATION_PROVIDERS = frozenset({"mock", "telegram", "email", "in_app"})
SUPPORTED_DELIVERY_STATUSES = frozenset(
    {"pending", "sending", "sent", "failed", "retry_scheduled", "skipped", "read"}
)

_PROVIDER_CONFIG_FIELDS: dict[str, frozenset[str]] = {
    "mock": frozenset({"recipient", "fail_with", "external_id_prefix"}),
    "telegram": frozenset(
        {
            "recipient",
            "bot_token",
            "chat_id",
            "html_mode",
            "max_message_length",
            "timeout",
        }
    ),
    "email": frozenset(
        {
            "recipient",
            "host",
            "port",
            "username",
            "password",
            "from",
            "use_tls",
            "use_starttls",
            "timeout",
        }
    ),
    "in_app": frozenset({"recipient", "external_id_prefix", "fail_if_not_configured"}),
}

_SENSITIVE_CONFIG_KEYS = frozenset(
    {"api_key", "bot_token", "chat_id", "password", "recipient", "smtp_password", "token"}
)
_MASK = "********"


def normalize_notification_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value not in SUPPORTED_NOTIFICATION_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_NOTIFICATION_PROVIDERS))
        raise ValueError(f"Unsupported notification provider: {value!r}. Supported: {supported}")
    return value


def validate_provider_config(provider: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the provider-specific, API-safe channel configuration fields."""
    provider = normalize_notification_provider(provider)
    if config is None:
        return {}
    unknown = set(config) - _PROVIDER_CONFIG_FIELDS[provider]
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"Unsupported configuration field(s) for {provider}: {fields}")

    cleaned = dict(config)
    if "port" in cleaned and not 1 <= int(cleaned["port"]) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if "timeout" in cleaned and not 1 <= float(cleaned["timeout"]) <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    if "max_message_length" in cleaned and not 1 <= int(cleaned["max_message_length"]) <= 4096:
        raise ValueError("max_message_length must be between 1 and 4096")
    if provider == "mock" and cleaned.get("fail_with") not in {
        None,
        "timeout",
        "transient",
        "permanent",
    }:
        raise ValueError("mock fail_with must be timeout, transient, or permanent")
    return cleaned


def sanitize_notification_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a response-safe copy of a provider configuration."""
    if config is None:
        return None
    return {
        key: _MASK if key.lower() in _SENSITIVE_CONFIG_KEYS and value else value
        for key, value in config.items()
    }


def sanitize_notification_error(
    message: str | None, *, secrets: list[str] | tuple[str, ...] = ()
) -> str | None:
    """Remove known values and credential-shaped key/value pairs from an error."""
    if not message:
        return None
    safe = str(message).strip()[:500]
    for secret in secrets:
        if secret:
            safe = safe.replace(str(secret), _MASK)
    safe = re.sub(
        r"(?i)(api[_-]?key|bot[_-]?token|chat[_-]?id|password|recipient|token)"
        r"\s*[=:]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}={_MASK}",
        safe,
    )
    safe = re.sub(r"(?i)bearer\s+[^\s,;]+", f"Bearer {_MASK}", safe)
    return safe or "Notification provider error"


def validate_quiet_hours(start: time | None, end: time | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("quiet_hours_start and quiet_hours_end must be provided together")
    if start is not None and start == end:
        raise ValueError("quiet hours start and end must be different")


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    config_json: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        return normalize_notification_provider(value)


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = Field(default=None, min_length=1, max_length=40)
    enabled: bool | None = None
    config_json: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("name must not be blank")
        return value

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        return normalize_notification_provider(value) if value is not None else None

    @model_validator(mode="after")
    def validate_channel(self) -> NotificationChannelUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one channel field must be supplied")
        return self


class NotificationChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    enabled: bool
    config_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class NotificationPreferenceCreate(BaseModel):
    channel_id: int = Field(gt=0)
    event_type: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    minimum_priority: int | None = Field(default=None, ge=0, le=100)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None

    @model_validator(mode="after")
    def validate_preference(self) -> NotificationPreferenceCreate:
        self.event_type = self.event_type.strip().lower()
        if not self.event_type:
            raise ValueError("event_type must not be blank")
        validate_quiet_hours(self.quiet_hours_start, self.quiet_hours_end)
        return self


class NotificationPreferenceUpdate(BaseModel):
    channel_id: int | None = Field(default=None, gt=0)
    event_type: str | None = Field(default=None, min_length=1, max_length=40)
    enabled: bool | None = None
    minimum_priority: int | None = Field(default=None, ge=0, le=100)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None

    @model_validator(mode="after")
    def validate_preference(self) -> NotificationPreferenceUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one preference field must be supplied")
        if self.event_type is not None:
            self.event_type = self.event_type.strip().lower()
            if not self.event_type:
                raise ValueError("event_type must not be blank")
        if {"quiet_hours_start", "quiet_hours_end"}.issubset(self.model_fields_set):
            validate_quiet_hours(self.quiet_hours_start, self.quiet_hours_end)
        return self


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


class NotificationStatusRead(BaseModel):
    notifications_enabled: bool
    provider_availability: dict[str, bool]
    telegram_enabled: bool
    email_enabled: bool
    in_app_enabled: bool
    pending_count: int
    sending_count: int
    retry_scheduled_count: int
    failed_count: int
    sent_today: int
    unread_count: int
    last_delivery_at: datetime | None
    stale_sending_count: int


class NotificationDeliveryHistoryItem(BaseModel):
    id: int
    alert_event_id: int | None
    channel_id: int
    channel_name: str
    provider: str
    notification_type: str
    subject: str | None
    status: str
    attempts: int
    sent_at: datetime | None
    created_at: datetime
    unread: bool


class NotificationAlertSummary(BaseModel):
    id: int
    rule_id: int
    rule_name: str | None
    trade_date: date | None
    trigger_value: float | None
    message: str


class NotificationStockSummary(BaseModel):
    id: int
    symbol: str
    company_name: str
    exchange: str


class NotificationDeliveryDetail(NotificationDeliveryHistoryItem):
    rendered_body: str | None
    next_retry_at: datetime | None
    sanitized_error: str | None
    alert: NotificationAlertSummary | None
    stock: NotificationStockSummary | None


class DeliverAlertResponse(BaseModel):
    alert_event_id: int
    channels_evaluated: int
    sent: int
    failed: int
    retry_scheduled: int
    skipped: int
    delivery_ids: list[int]


class DeliverPendingRequest(BaseModel):
    batch_size: int | None = Field(default=None, ge=1)


class DeliverPendingResponse(BaseModel):
    batch_size: int
    dispatched: int


class NotificationRetryResponse(BaseModel):
    delivery_id: int
    status: str
    attempt_count: int
    sent_at: datetime | None
    next_retry_at: datetime | None


class NotificationMarkReadResponse(BaseModel):
    delivery_id: int
    status: str
    read: bool


class ProviderTestRequest(BaseModel):
    recipient: str | None = Field(default=None, min_length=1, max_length=320)
    config_json: dict[str, Any] | None = None

    @field_validator("recipient")
    @classmethod
    def strip_recipient(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProviderTestResponse(BaseModel):
    provider: str
    success: bool
    external_message_id: str | None
    tried_at: datetime
    retry_after: int | None


class NotificationDeliveryRead(BaseModel):
    """Backward-compatible ORM schema retained for service/model consumers."""

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


class DailySummaryRunRequest(BaseModel):
    summary_date: date | None = None


class DailySummaryExecutionResponse(BaseModel):
    run: DailySummaryRunRead
    idempotent: bool
    delivery_ids: list[int]


class DailySummaryHistoryResponse(BaseModel):
    items: list[DailySummaryRunRead]
    total: int
    limit: int
    offset: int


class SchedulerJobStatusRead(BaseModel):
    job_id: str
    enabled: bool
    registered: bool
    next_run_time: str | None
    last_execution: dict[str, Any] | None = None


class NotificationSchedulerStatusRead(BaseModel):
    running: bool
    alert_delivery_job_enabled: bool
    daily_summary_job_enabled: bool
    registered_job_ids: list[str]
    jobs: dict[str, SchedulerJobStatusRead]


class DeliverySchedulerRunResponse(BaseModel):
    status: str
    recovered: int = 0
    dispatched: int = 0
    events_processed: int = 0
    duration_ms: float = 0
    error: str | None = None
    finished_at: str | None = None
