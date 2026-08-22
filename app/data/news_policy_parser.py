"""Parsers for News and Government Policy Intelligence datasets — Sprint 19.1F.

Classes:
  - NewsPolicyParser: Normalizes news items, government press releases (PIB),
    RBI monetary policy announcements, and SEBI circulars.
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from typing import Any

from app.models.news_policy import NewsEvent


class NewsPolicyParser:
    """Parser and normalizer for news and government policy disclosures."""

    VALID_EVENT_TYPES = {"COMPANY", "GOVERNMENT_POLICY", "RBI_MONETARY", "SEBI_REGULATORY", "MACRO"}
    VALID_SOURCE_TYPES = {"OFFICIAL_PRIMARY", "OFFICIAL_SECONDARY", "LICENSED_NEWS", "VALIDATED_LOCAL", "UNVERIFIED"}
    VALID_CONTEXTS = {"POSITIVE_CONTEXT", "NEGATIVE_CONTEXT", "MIXED", "NEUTRAL", "UNKNOWN"}
    VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    @classmethod
    def parse_dict(cls, row: dict[str, Any], default_source_type: str = "OFFICIAL_PRIMARY") -> NewsEvent:
        """Parse a raw dictionary representation of a news or policy event."""
        headline = str(row.get("headline", "")).strip()
        if not headline:
            raise ValueError("headline is required for NewsEvent")

        source_name = str(row.get("source_name", "UNKNOWN_SOURCE")).strip()
        source_type = str(row.get("source_type", default_source_type)).strip().upper()
        if source_type not in cls.VALID_SOURCE_TYPES:
            source_type = default_source_type

        # Parse published_at
        pub_raw = row.get("published_at")
        published_at = cls._parse_datetime(pub_raw)

        # Parse available_at (defaults to published_at if not provided)
        avail_raw = row.get("available_at")
        available_at = cls._parse_datetime(avail_raw) if avail_raw else published_at

        # Parse effective_at date
        eff_raw = row.get("effective_at")
        effective_at = cls._parse_date(eff_raw) if eff_raw else None

        event_type = str(row.get("event_type", "COMPANY")).strip().upper()
        if event_type not in cls.VALID_EVENT_TYPES:
            event_type = "COMPANY"

        category = str(row.get("category", "OTHER")).strip().upper()
        scope_type = str(row.get("scope_type", "STOCK")).strip().upper()

        context = str(row.get("deterministic_context", "UNKNOWN")).strip().upper()
        if context not in cls.VALID_CONTEXTS:
            context = "UNKNOWN"

        severity = str(row.get("event_severity", "MEDIUM")).strip().upper()
        if severity not in cls.VALID_SEVERITIES:
            severity = "MEDIUM"

        stock_id = int(row["stock_id"]) if row.get("stock_id") is not None else None
        sector_id = int(row["sector_id"]) if row.get("sector_id") is not None else None
        market_index_id = int(row["market_index_id"]) if row.get("market_index_id") is not None else None

        event = NewsEvent(
            headline=headline,
            summary=str(row.get("summary", "")).strip() or None,
            source_name=source_name,
            source_type=source_type,
            source_reference=row.get("source_reference"),
            published_at=published_at,
            available_at=available_at,
            effective_at=effective_at,
            fetched_at=datetime.now(UTC),
            event_type=event_type,
            category=category,
            scope_type=scope_type,
            government_body=row.get("government_body"),
            regulator=row.get("regulator"),
            language=str(row.get("language", "en")),
            deterministic_context=context,
            event_severity=severity,
            event_risk_flag=row.get("event_risk_flag"),
            confidence_score=float(row.get("confidence_score", 1.0)) if row.get("confidence_score") else 1.0,
            reason_code=row.get("reason_code"),
            stock_id=stock_id,
            sector_id=sector_id,
            market_index_id=market_index_id,
            corporate_announcement_id=int(row["corporate_announcement_id"]) if row.get("corporate_announcement_id") else None,
            econ_event_id=int(row["econ_event_id"]) if row.get("econ_event_id") else None,
            idempotency_hash="",
        )
        event.idempotency_hash = event.compute_fingerprint()
        return event

    @classmethod
    def parse_csv(cls, content: str, default_source_type: str = "OFFICIAL_PRIMARY") -> list[NewsEvent]:
        """Parse CSV string of news or policy events."""
        reader = csv.DictReader(io.StringIO(content.strip()))
        events: list[NewsEvent] = []
        for row in reader:
            try:
                evt = cls.parse_dict(row, default_source_type=default_source_type)
                events.append(evt)
            except Exception:
                continue
        return events

    @staticmethod
    def _parse_datetime(val: Any) -> datetime:
        """Helper to parse ISO datetime strings or datetime objects."""
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=UTC)
            return val
        if isinstance(val, date):
            return datetime(val.year, val.month, val.day, tzinfo=UTC)
        if isinstance(val, str):
            val_str = val.strip()
            # ISO parse
            try:
                dt = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                pass
        return datetime.now(UTC)

    @staticmethod
    def _parse_date(val: Any) -> date | None:
        """Helper to parse date strings or date objects."""
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            try:
                return date.fromisoformat(val.strip())
            except ValueError:
                pass
        return None
