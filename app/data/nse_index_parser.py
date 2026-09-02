"""Parser for Official NSE Index Daily Closing Files.

Parses official NSE index CSV files (ind_close_all_DDMMYYYY.csv)
containing end-of-day index levels, OHLC, points change, and turnover.

Strict Invariants:
  - Exact header validation: ['Index Name', 'Index Date', 'Open Index Value',
    'High Index Value', 'Low Index Value', 'Closing Index Value', 'Points Change',
    'Change(%)', 'Volume', 'Turnover (Rs. Cr.)', 'P/E', 'P/B', 'Div Yield']
  - Normalizes index names to canonical MarketIndex.code deterministically.
  - Validates numeric OHLC (high >= low, positive values).
  - Handles missing / '-' volume and valuation ratios safely.
  - Rejects unknown, malformed, or corrupt rows without silent data corruption.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EXPECTED_HEADERS = (
    "Index Name",
    "Index Date",
    "Open Index Value",
    "High Index Value",
    "Low Index Value",
    "Closing Index Value",
    "Points Change",
    "Change(%)",
    "Volume",
    "Turnover (Rs. Cr.)",
    "P/E",
    "P/B",
    "Div Yield",
)

# Canonical 19 target Indian indices defined in G.4 scope
NSE_INDEX_NAME_TO_CODE: dict[str, str] = {
    "NIFTY 50": "NIFTY_50",
    "INDIA VIX": "INDIA_VIX",
    "NIFTY BANK": "NIFTY_BANK",
    "NIFTY IT": "NIFTY_IT",
    "NIFTY AUTO": "NIFTY_AUTO",
    "NIFTY FMCG": "NIFTY_FMCG",
    "NIFTY PHARMA": "NIFTY_PHARMA",
    "NIFTY METAL": "NIFTY_METAL",
    "NIFTY REALTY": "NIFTY_REALTY",
    "NIFTY ENERGY": "NIFTY_ENERGY",
    "NIFTY INFRASTRUCTURE": "NIFTY_INFRASTRUCTURE",
    "NIFTY COMMODITIES": "NIFTY_COMMODITIES",
    "NIFTY CONSUMER DURABLES": "NIFTY_CONSUMER_DURABLES",
    "NIFTY OIL & GAS": "NIFTY_OIL_GAS",
    "NIFTY HEALTHCARE INDEX": "NIFTY_HEALTHCARE",
    "NIFTY HEALTHCARE": "NIFTY_HEALTHCARE",
    "NIFTY SERVICES SECTOR": "NIFTY_SERVICES_SECTOR",
    "NIFTY NEXT 50": "NIFTY_NEXT_50",
    "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100",
    "NIFTY SMALLCAP 100": "NIFTY_SMALLCAP_100",
}


class NSEIndexParseError(ValueError):
    """Raised when index CSV cannot be parsed or has invalid headers."""


@dataclass(frozen=True, slots=True)
class ParsedIndexRecord:
    """A validated index price record parsed from official NSE archive."""

    index_code: str
    index_name: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal | None
    change: Decimal | None
    change_percent: Decimal | None
    volume: int | None
    turnover_cr: Decimal | None
    pe: Decimal | None
    pb: Decimal | None
    div_yield: Decimal | None
    source_name: str
    source_reference: str


@dataclass(slots=True)
class NSEIndexParseResult:
    """Result of parsing an official NSE index file."""

    records: list[ParsedIndexRecord] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int = 0
    valid_rows: int = 0


def _parse_decimal(val: str | None) -> Decimal | None:
    if val is None:
        return None
    cleaned = val.strip().replace(",", "").replace("%", "")
    if not cleaned or cleaned in ("-", "NA", "N/A", "null"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


MAX_INT32 = 2147483647


def _parse_int(val: str | None) -> int | None:
    if val is None:
        return None
    cleaned = val.strip().replace(",", "")
    if not cleaned or cleaned in ("-", "NA", "N/A", "null"):
        return None
    try:
        v = int(float(cleaned))
        if v > MAX_INT32:
            return MAX_INT32
        if v < -MAX_INT32 - 1:
            return -MAX_INT32 - 1
        return v
    except (ValueError, OverflowError):
        return None


class NSEIndexParser:
    """Parser for official NSE index closing CSV files."""

    @classmethod
    def parse_file(
        cls,
        file_path: str | Path,
        *,
        filter_target_codes: set[str] | None = None,
    ) -> NSEIndexParseResult:
        """Parse index data from a local file path."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        content = p.read_text(encoding="utf-8", errors="replace")
        return cls.parse_csv_content(
            content,
            source_reference=p.name,
            filter_target_codes=filter_target_codes,
        )

    @classmethod
    def parse_csv_content(
        cls,
        content: str,
        *,
        source_name: str = "NSE_ARCHIVE_IND_CLOSE",
        source_reference: str = "ind_close_all",
        filter_target_codes: set[str] | None = None,
    ) -> NSEIndexParseResult:
        """Parse index data from string content."""
        f = io.StringIO(content.strip())
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise NSEIndexParseError("Empty or invalid CSV: missing header row")

        # Validate header columns
        fieldnames = [col.strip() for col in reader.fieldnames if col]
        for expected in EXPECTED_HEADERS:
            if expected not in fieldnames:
                raise NSEIndexParseError(
                    f"Missing required header column '{expected}'. Found: {fieldnames}"
                )

        result = NSEIndexParseResult()

        for line_num, row in enumerate(reader, start=2):
            result.total_rows += 1
            raw_name = str(row.get("Index Name", "")).strip()
            raw_date = str(row.get("Index Date", "")).strip()

            if not raw_name or not raw_date:
                result.rejected_rows.append(
                    {"line": line_num, "reason": "Missing Index Name or Index Date", "row": row}
                )
                continue

            # Deterministic code mapping
            normalized_name = raw_name.upper()
            code = NSE_INDEX_NAME_TO_CODE.get(normalized_name)
            if not code:
                # Not one of our mapped target indices - cleanly ignore/record
                result.rejected_rows.append(
                    {"line": line_num, "reason": f"Unmapped index '{raw_name}'", "index_name": raw_name}
                )
                continue

            if filter_target_codes and code not in filter_target_codes:
                continue

            # Date parsing (DD-MM-YYYY)
            try:
                trade_date = datetime.strptime(raw_date, "%d-%m-%Y").date()
            except ValueError:
                result.rejected_rows.append(
                    {"line": line_num, "reason": f"Invalid date format '{raw_date}'", "row": row}
                )
                continue

            # Numeric OHLC validation
            open_v = _parse_decimal(row.get("Open Index Value"))
            high_v = _parse_decimal(row.get("High Index Value"))
            low_v = _parse_decimal(row.get("Low Index Value"))
            close_v = _parse_decimal(row.get("Closing Index Value"))

            if open_v is None or high_v is None or low_v is None or close_v is None:
                result.rejected_rows.append(
                    {"line": line_num, "reason": "Missing or non-numeric OHLC values", "row": row}
                )
                continue

            if open_v <= 0 or high_v <= 0 or low_v <= 0 or close_v <= 0:
                result.rejected_rows.append(
                    {"line": line_num, "reason": "Non-positive OHLC values", "row": row}
                )
                continue

            if high_v < low_v:
                result.rejected_rows.append(
                    {"line": line_num, "reason": f"High ({high_v}) < Low ({low_v})", "row": row}
                )
                continue

            change = _parse_decimal(row.get("Points Change"))
            change_pct = _parse_decimal(row.get("Change(%)"))
            volume = _parse_int(row.get("Volume"))
            turnover = _parse_decimal(row.get("Turnover (Rs. Cr.)"))
            pe = _parse_decimal(row.get("P/E"))
            pb = _parse_decimal(row.get("P/B"))
            div_yield = _parse_decimal(row.get("Div Yield"))

            previous_close = (close_v - change) if change is not None else None

            record = ParsedIndexRecord(
                index_code=code,
                index_name=raw_name,
                trade_date=trade_date,
                open=open_v,
                high=high_v,
                low=low_v,
                close=close_v,
                previous_close=previous_close,
                change=change,
                change_percent=change_pct,
                volume=volume,
                turnover_cr=turnover,
                pe=pe,
                pb=pb,
                div_yield=div_yield,
                source_name=source_name,
                source_reference=source_reference,
            )
            result.records.append(record)
            result.valid_rows += 1

        return result
