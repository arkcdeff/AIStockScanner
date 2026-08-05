from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.alert import AlertEvent


class NotificationChannel(TimestampMixin, Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("name", name="uq_notification_channel_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    preferences: Mapped[list[NotificationPreference]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list[NotificationDelivery]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class NotificationPreference(TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "event_type", name="uq_notification_preference_channel_event"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    minimum_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    channel: Mapped[NotificationChannel] = relationship(back_populates="preferences")


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rendered_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[NotificationChannel] = relationship(back_populates="deliveries")
    alert_event: Mapped[AlertEvent | None] = relationship()


class DailySummaryRun(TimestampMixin, Base):
    __tablename__ = "daily_summary_runs"
    __table_args__ = (
        UniqueConstraint("summary_date", name="uq_daily_summary_run_summary_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    summary_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    alerts_count: Mapped[int] = mapped_column(Integer, default=0)
    scanner_matches: Mapped[int] = mapped_column(Integer, default=0)
    top_rated_count: Mapped[int] = mapped_column(Integer, default=0)
    watchlist_changes: Mapped[int] = mapped_column(Integer, default=0)
    portfolio_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)