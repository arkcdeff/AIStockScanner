"""Pydantic & Dataclass Schemas for Sprint 19.1D-8: Coverage, Quality & Historical Replay.

Defines schemas for:
  - Derivative Dataset Coverage (Contracts, Futures, Options, Option Chain, Participants, FII Stats)
  - Data Quality Summaries
  - Historical Derivatives Replay Snapshots (Point-in-Time safe historical knowledge state)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class DatasetCoverageItem:
    """Coverage summary for a specific derivative dataset."""

    dataset_code: str  # "FO_CONTRACT_MASTER", "FO_FUTURES_EOD", "FO_OPTIONS_EOD", etc.
    dataset_name: str
    status: str  # "COMPLETE_FOR_IMPORTED_SCOPE", "PARTIAL", "NO_DATA", "UNKNOWN_COMPLETENESS"
    scope_description: str
    first_date: date | None = None
    latest_date: date | None = None
    row_count: int = 0
    contract_count: int = 0
    underlying_count: int = 0
    session_count: int = 0
    missing_session_count: str = "UNKNOWN"  # "UNKNOWN" or numeric string
    quality_issue_count: int = 0
    source_name: str = "NSE_INDIA"


@dataclass
class DerivativeCoverageSummary:
    """Overall derivatives coverage summary across all dataset dimensions."""

    as_of_date: date
    overall_status: str  # "COMPLETE_FOR_IMPORTED_SCOPE", "PARTIAL", "NO_DATA"
    datasets: list[DatasetCoverageItem]
    quality_warnings: list[str] = field(default_factory=list)


@dataclass
class ReplayProvenanceItem:
    """Provenance audit trail entry for a replay component."""

    component: str  # "FUTURES", "OPTIONS", "PARTICIPANTS", "SPOT_REFERENCE"
    source_name: str  # "NSE_INDIA", "DERIVATIVE_DAILY_PRICE", etc.
    source_reference: str
    report_or_trade_date: date
    available_at: date


@dataclass
class HistoricalDerivativesReplay:
    """Point-in-time safe historical derivatives knowledge state for symbol on date T."""

    underlying_symbol: str
    trade_date: date
    as_of_date: date
    selected_expiry_date: date | None = None
    replay_status: str = "COMPLETE_FOR_IMPORTED_SCOPE"

    # Spot Reference
    spot_price: Decimal | None = None
    spot_price_source: str | None = None

    # Reconstructed Components (Dict representations of snapshot objects for API/UI serialization)
    futures_snapshot: dict[str, Any] | None = None
    futures_rollover: dict[str, Any] | None = None
    option_chain: dict[str, Any] | None = None
    options_analytics: dict[str, Any] | None = None
    expiry_options_analytics: dict[str, Any] | None = None
    participant_positions: dict[str, Any] | None = None

    # Metadata & Quality
    provenance: list[ReplayProvenanceItem] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    computed_warnings: list[str] = field(default_factory=list)
