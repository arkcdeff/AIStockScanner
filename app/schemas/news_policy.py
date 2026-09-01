"""Pydantic and dataclass schemas for News + Government Policy Intelligence — Sprint 19.1F."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NewsEntityLinkRead(BaseModel):
    """Read schema for a normalized news entity impact link."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    news_event_id: int
    stock_id: int | None = None
    sector_id: int | None = None
    impact_relation: str
    deterministic_context: str


class NewsEventRead(BaseModel):
    """Read schema for a single NewsEvent record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    headline: str
    summary: str | None = None
    source_name: str
    source_type: str
    source_reference: str | None = None
    published_at: datetime
    available_at: datetime
    effective_at: date | None = None
    fetched_at: datetime | None = None
    event_type: str
    category: str
    scope_type: str
    government_body: str | None = None
    regulator: str | None = None
    language: str = "en"
    deterministic_context: str
    event_severity: str
    event_risk_flag: str | None = None
    confidence_score: float | None = 1.0
    reason_code: str | None = None
    stock_id: int | None = None
    sector_id: int | None = None
    market_index_id: int | None = None
    corporate_announcement_id: int | None = None
    econ_event_id: int | None = None
    idempotency_hash: str


@dataclass
class UpcomingEventRiskRead:
    """Read schema for an upcoming risk event (earnings, board meeting, RBI MPC, budget, etc.)."""

    event_type: str
    category: str
    title: str
    scheduled_at: datetime | date
    stock_id: int | None = None
    symbol: str | None = None
    event_risk_flag: str = "MAJOR_ANNOUNCEMENT"
    overnight_risk: bool = False


@dataclass
class CompanyNewsPolicySnapshot:
    """Point-in-time news and policy intelligence snapshot for a single stock."""

    symbol: str
    stock_id: int
    evaluation_datetime: datetime
    as_of_date: date
    recent_events: list[NewsEventRead] = Field(default_factory=list)
    positive_context_count: int = 0
    negative_context_count: int = 0
    mixed_context_count: int = 0
    neutral_context_count: int = 0
    upcoming_event_risks: list[UpcomingEventRiskRead] = Field(default_factory=list)
    latest_results_event: NewsEventRead | None = None
    overnight_event_risk: bool = False
    policy_headwind: bool = False
    policy_tailwind: bool = False
    regulatory_flag: bool = False
    quality_warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


@dataclass
class SectorNewsPolicySnapshot:
    """Point-in-time news and policy intelligence snapshot for a sector."""

    sector_name: str
    sector_id: int
    evaluation_datetime: datetime
    as_of_date: date
    recent_events: list[NewsEventRead] = Field(default_factory=list)
    policy_changes_count: int = 0
    regulatory_changes_count: int = 0
    positive_events_count: int = 0
    negative_events_count: int = 0
    impacted_stocks_count: int = 0
    quality_warnings: list[str] = Field(default_factory=list)


@dataclass
class MarketNewsPolicySnapshot:
    """Point-in-time news and policy intelligence snapshot for the entire market."""

    evaluation_datetime: datetime
    as_of_date: date
    recent_market_events: list[NewsEventRead] = Field(default_factory=list)
    rbi_monetary_events: list[NewsEventRead] = Field(default_factory=list)
    sebi_regulatory_events: list[NewsEventRead] = Field(default_factory=list)
    government_policy_events: list[NewsEventRead] = Field(default_factory=list)
    upcoming_market_risks: list[UpcomingEventRiskRead] = Field(default_factory=list)
    overall_sentiment_context: str = "UNKNOWN"
    quality_warnings: list[str] = Field(default_factory=list)


class NewsCoverageRead(BaseModel):
    """Read schema for news and government policy dataset coverage summary."""

    model_config = ConfigDict(from_attributes=True)

    total_events_count: int
    company_events_count: int
    government_policy_events_count: int
    rbi_monetary_events_count: int
    sebi_regulatory_events_count: int
    macro_events_count: int
    unique_stocks_covered: int
    unique_sectors_covered: int
    earliest_published_at: datetime | None = None
    latest_published_at: datetime | None = None
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    unresolved_entities_count: int = 0
    coverage_status: str = "PARTIAL"
