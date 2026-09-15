"""Data Coverage State representation — Stage 2 G.11-C.

Provides enum and minimal backend capability to distinguish dataset availability
states without silently defaulting missing data to zeros.
"""
from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path


class CoverageState(StrEnum):
    """Canonical availability state for a dataset / session."""

    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_STALE_CONTENT = "SOURCE_STALE_CONTENT"
    NO_DATA = "NO_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


DEFAULT_INSTITUTIONAL_MANIFEST = Path("data/reference/institutional/manifest_20260101_20260831.json")


def evaluate_institutional_coverage(
    trade_date: date,
    manifest_path: Path | str | None = None,
) -> CoverageState:
    """Evaluate coverage state for a trade date in institutional flow data."""
    path = Path(manifest_path or DEFAULT_INSTITUTIONAL_MANIFEST)
    if not path.exists():
        return CoverageState.NO_DATA

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return CoverageState.NO_DATA

    d_str = trade_date.isoformat()
    if d_str not in data:
        return CoverageState.NO_DATA

    entry = data[d_str]
    status = entry.get("source_status")
    if status in ("SOURCE_UNAVAILABLE", "UNAVAILABLE"):
        return CoverageState.SOURCE_UNAVAILABLE
    elif status in ("SOURCE_STALE_CONTENT", "STALE_CONTENT"):
        return CoverageState.SOURCE_STALE_CONTENT
    elif status in ("VALID", "GENUINE", "GENUINE_PRIOR_TRUSTED", "GENUINE_ACQUIRED"):
        return CoverageState.AVAILABLE

    return CoverageState.NO_DATA
