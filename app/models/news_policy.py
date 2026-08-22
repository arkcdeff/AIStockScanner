"""News and Government Policy Intelligence ORM models — Sprint 19.1F.

Models:
  - NewsEvent: Persisted news disclosure, government policy, RBI monetary policy,
    and SEBI regulatory events with point-in-time (PIT) timestamps and source trust hierarchy.
  - NewsEntityLink: Normalized mapping linking policy/macro news events to affected stocks or sectors.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.fundamental_corporate import CorporateAnnouncement
    from app.models.macro_event import EconEvent
    from app.models.market_index import MarketIndex
    from app.models.sector import Sector
    from app.models.stock import Stock


class NewsEvent(Base):
    """Persisted news disclosure, government policy, RBI, or SEBI regulatory event."""

    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Core Headline and Excerpt
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source Provenance and Trust Model
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OFFICIAL_PRIMARY")
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Key Timestamps (PIT Semantics)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Event Categorization and Scope
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPANY")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="OTHER")
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="STOCK")

    # Regulatory / Government Body Details
    government_body: Mapped[str | None] = mapped_column(String(64), nullable=True)
    regulator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    # Deterministic Context & Severity
    deterministic_context: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    event_severity: Mapped[str] = mapped_column(String(32), nullable=False, default="MEDIUM")
    event_risk_flag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True, default=1.0)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Entity Foreign Keys (Nullable)
    stock_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True
    )
    sector_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True
    )
    market_index_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_indices.id", ondelete="SET NULL"), nullable=True
    )

    # Existing Architecture Links (Reuse without duplication)
    corporate_announcement_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corporate_announcements.id", ondelete="SET NULL"), nullable=True
    )
    econ_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("econ_events.id", ondelete="SET NULL"), nullable=True
    )

    # Idempotency
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Relationships
    stock: Mapped[Stock | None] = relationship("Stock", backref="news_events")
    sector: Mapped[Sector | None] = relationship("Sector", backref="news_events")
    market_index: Mapped[MarketIndex | None] = relationship("MarketIndex", backref="news_events")
    corporate_announcement: Mapped[CorporateAnnouncement | None] = relationship("CorporateAnnouncement")
    econ_event: Mapped[EconEvent | None] = relationship("EconEvent")
    entity_links: Mapped[list[NewsEntityLink]] = relationship("NewsEntityLink", back_populates="news_event", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('OFFICIAL_PRIMARY', 'OFFICIAL_SECONDARY', 'LICENSED_NEWS', 'VALIDATED_LOCAL', 'UNVERIFIED')",
            name="ck_news_events_source_type",
        ),
        CheckConstraint(
            "event_type IN ('COMPANY', 'GOVERNMENT_POLICY', 'RBI_MONETARY', 'SEBI_REGULATORY', 'MACRO')",
            name="ck_news_events_event_type",
        ),
        CheckConstraint(
            "scope_type IN ('STOCK', 'MULTI_STOCK', 'SECTOR', 'MARKET', 'MACRO')",
            name="ck_news_events_scope_type",
        ),
        CheckConstraint(
            "deterministic_context IN ('POSITIVE_CONTEXT', 'NEGATIVE_CONTEXT', 'MIXED', 'NEUTRAL', 'UNKNOWN')",
            name="ck_news_events_context",
        ),
        CheckConstraint(
            "event_severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_news_events_severity",
        ),
        Index("ix_news_events_stock_pub", "stock_id", "published_at"),
        Index("ix_news_events_sector_pub", "sector_id", "published_at"),
        Index("ix_news_events_pit", "available_at"),
        Index("ix_news_events_event_type", "event_type"),
        Index("ix_news_events_category", "category"),
    )

    def compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint for deduplication and idempotency."""
        parts = [
            self.source_name,
            self.headline.strip().lower(),
            self.published_at.isoformat() if self.published_at else "",
            str(self.stock_id or ""),
        ]
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class NewsEntityLink(Base):
    """Normalized multi-entity link mapping policy/government events to impacted stocks or sectors."""

    __tablename__ = "news_entity_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("news_events.id", ondelete="CASCADE"), nullable=False
    )
    stock_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=True
    )
    sector_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True
    )

    impact_relation: Mapped[str] = mapped_column(String(32), nullable=False, default="DIRECT")
    deterministic_context: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")

    news_event: Mapped[NewsEvent] = relationship("NewsEvent", back_populates="entity_links")
    stock: Mapped[Stock | None] = relationship("Stock")
    sector: Mapped[Sector | None] = relationship("Sector")

    __table_args__ = (
        CheckConstraint(
            "impact_relation IN ('DIRECT', 'INDIRECT', 'UNKNOWN')",
            name="ck_news_entity_links_impact_relation",
        ),
        CheckConstraint(
            "deterministic_context IN ('POSITIVE_CONTEXT', 'NEGATIVE_CONTEXT', 'MIXED', 'NEUTRAL', 'UNKNOWN')",
            name="ck_news_entity_links_context",
        ),
        Index("ix_news_entity_links_stock", "stock_id"),
        Index("ix_news_entity_links_sector", "sector_id"),
    )
