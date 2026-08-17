from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EconEvent(TimestampMixin, Base):
    """A scheduled macroeconomic/central-bank event and its recorded values.

    ``event_code`` references the declarative catalog in
    ``app/market_intelligence/macro_event_catalog.py``. Each row is unique per
    event code and its scheduled reference time. The actual/forecast/previous
    values are only ever populated by a validated provider or validated import;
    the model itself never invents values.
    """

    __tablename__ = "econ_events"

    __table_args__ = (
        UniqueConstraint(
            "event_code",
            "reference_time",
            name="uq_econ_event_code_reference_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    reference_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )

    actual: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    forecast: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    previous: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)

    source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freshness_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="stale"
    )

    def __repr__(self) -> str:
        return (
            f"<EconEvent code={self.event_code} "
            f"reference_time={self.reference_time} actual={self.actual}>"
        )
