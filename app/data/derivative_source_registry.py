"""Derivative Source Registry — Sprint 19.1D-1.

Manages metadata and access policies for NSE F&O datasets.

Access Classifications:
  - OFFICIAL_PUBLIC: Directly available from official public portals (e.g. NSE archives).
  - OFFICIAL_RESTRICTED: Requires official credentials/licensing or portal login.
  - LOCAL_VALIDATED_FILE: Official files uploaded/provided locally after validation.
  - LICENSED_PROVIDER: Authorized commercial vendor feed.

Data Rights Policy:
  - NO scraping of OIData or commercial vendor websites.
  - NO bypassing of anti-bot, 403, or authentication restrictions.
  - Official public data and validated local files are preferred.
"""
from __future__ import annotations

from dataclasses import dataclass

# Access Classification Constants
ACCESS_OFFICIAL_PUBLIC = "OFFICIAL_PUBLIC"
ACCESS_OFFICIAL_RESTRICTED = "OFFICIAL_RESTRICTED"
ACCESS_LOCAL_VALIDATED_FILE = "LOCAL_VALIDATED_FILE"
ACCESS_LICENSED_PROVIDER = "LICENSED_PROVIDER"

# Automation Status Constants
STATUS_AUTOMATED = "AUTOMATED"
STATUS_MANUAL_FALLBACK = "MANUAL_FALLBACK"
STATUS_LOCAL_ONLY = "LOCAL_ONLY"
STATUS_UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class DerivativeSourceMetadata:
    """Metadata registry entry for a derivative dataset."""

    dataset_code: str
    source_name: str
    official_owner: str
    format_type: str
    access_classification: str
    automation_status: str
    historical_support: bool
    point_in_time_granularity: str  # EOD, INTRADAY, DAILY_SNAPSHOT
    notes: str


#: Canonical Registry of NSE Derivative Datasets
DERIVATIVE_SOURCE_REGISTRY: dict[str, DerivativeSourceMetadata] = {
    "FO_CONTRACT_MASTER": DerivativeSourceMetadata(
        dataset_code="FO_CONTRACT_MASTER",
        source_name="NSE F&O Contract Master",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_LOCAL_VALIDATED_FILE,
        automation_status=STATUS_LOCAL_ONLY,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Master specifications for active and expired futures/options contracts.",
    ),
    "FO_UDIFF_BHAVCOPY": DerivativeSourceMetadata(
        dataset_code="FO_UDIFF_BHAVCOPY",
        source_name="F&O UDiFF Common Bhavcopy Final",
        official_owner="NSE India",
        format_type="CSV / ZIP",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Unified Data Interchange Format for end-of-day prices, volumes, and open interest.",
    ),
    "FO_SETTLEMENT": DerivativeSourceMetadata(
        dataset_code="FO_SETTLEMENT",
        source_name="Daily Settlement Prices",
        official_owner="NSE Clearing (NCL)",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Official daily mark-to-market settlement prices for futures and options.",
    ),
    "FO_NCL_OI": DerivativeSourceMetadata(
        dataset_code="FO_NCL_OI",
        source_name="NCL Open Interest Report",
        official_owner="NSE Clearing (NCL)",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Clearing corporation open interest statistics.",
    ),
    "FO_COMBINED_OI": DerivativeSourceMetadata(
        dataset_code="FO_COMBINED_OI",
        source_name="Combined Open Interest Report",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Combined futures and options open interest across exchanges.",
    ),
    "FO_PARTICIPANT_OI": DerivativeSourceMetadata(
        dataset_code="FO_PARTICIPANT_OI",
        source_name="Participant-wise Open Interest",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Open interest broken down by FII, DII, Proprietary, and Client categories.",
    ),
    "FO_PARTICIPANT_VOLUME": DerivativeSourceMetadata(
        dataset_code="FO_PARTICIPANT_VOLUME",
        source_name="Participant-wise Trading Volumes",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Trading volume broken down by market participant categories.",
    ),
    "FO_FII_DERIVATIVES": DerivativeSourceMetadata(
        dataset_code="FO_FII_DERIVATIVES",
        source_name="FII Derivatives Statistics",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Daily buy/sell contracts and turnover figures for Foreign Institutional Investors.",
    ),
    "FO_BAN": DerivativeSourceMetadata(
        dataset_code="FO_BAN",
        source_name="Securities in Ban Period",
        official_owner="NSE India",
        format_type="TXT / CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Securities crossing 95% MWPL restricted from fresh derivative positioning.",
    ),
    "FO_MWPL": DerivativeSourceMetadata(
        dataset_code="FO_MWPL",
        source_name="Market-Wide Position Limits (MWPL)",
        official_owner="NSE India",
        format_type="CSV",
        access_classification=ACCESS_OFFICIAL_PUBLIC,
        automation_status=STATUS_MANUAL_FALLBACK,
        historical_support=True,
        point_in_time_granularity="EOD",
        notes="Open interest limits and utilization percentages per eligible underlying.",
    ),
}


def get_dataset_metadata(dataset_code: str) -> DerivativeSourceMetadata | None:
    """Retrieve metadata for a derivative dataset code."""
    return DERIVATIVE_SOURCE_REGISTRY.get(dataset_code)


def list_registered_datasets() -> list[DerivativeSourceMetadata]:
    """Return all registered derivative dataset metadata entries."""
    return list(DERIVATIVE_SOURCE_REGISTRY.values())
