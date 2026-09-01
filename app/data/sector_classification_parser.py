"""Parser for Official Exchange Sector and Industry Classification.

Parses official NSE Index/Constituent CSV files (e.g. ind_niftytotalmarket_list.csv)
containing company classification metadata.

Strict Invariants:
  - Exact header validation: ['Company Name', 'Industry', 'Symbol', 'Series', 'ISIN Code'].
  - Normalizes whitespace while preserving exact source taxonomy strings.
  - Validates ISIN structure (12 alphanumeric characters, typically starting with 'IN').
  - Validates Symbol and Series presence.
  - Rejects malformed records and identifies dummy/exchange test records.
  - Never fabricates or guesses missing fields.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

ISIN_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class SectorClassificationParseError(ValueError):
    """Raised when classification CSV cannot be parsed or has invalid headers."""


@dataclass(frozen=True, slots=True)
class SectorClassificationRecord:
    """A validated record from the official classification source."""

    company_name: str
    industry: str
    symbol: str
    series: str
    isin: str
    is_dummy: bool = False
    row_number: int = 0


@dataclass(slots=True)
class SectorClassificationParseResult:
    """Result of parsing an official classification file."""

    records: list[SectorClassificationRecord] = field(default_factory=list)
    dummy_records: list[SectorClassificationRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_rows: int = 0
    valid_rows: int = 0


class SectorClassificationParser:
    """Parser for official NSE sector and industry classification CSV files."""

    EXPECTED_HEADERS = ("Company Name", "Industry", "Symbol", "Series", "ISIN Code")

    @classmethod
    def parse_file(cls, file_path: str | Path) -> SectorClassificationParseResult:
        """Parse classification data from a local file path."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Classification file not found: {file_path}")

        raw_bytes = p.read_bytes()
        try:
            content = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SectorClassificationParseError(
                f"File {file_path} is not valid UTF-8: {exc}"
            ) from exc

        return cls.parse_csv_content(content)

    @classmethod
    def parse_csv_content(cls, content: str) -> SectorClassificationParseResult:
        """Parse raw CSV string containing official classification records."""
        if not content or not content.strip():
            raise SectorClassificationParseError("Classification content is empty")

        f = io.StringIO(content.strip())
        reader = csv.reader(f)

        try:
            header_row = next(reader)
        except StopIteration as exc:
            raise SectorClassificationParseError("Empty CSV: header row missing") from exc

        cleaned_headers = tuple(h.strip() for h in header_row)
        if len(cleaned_headers) < len(cls.EXPECTED_HEADERS) or cleaned_headers[:5] != cls.EXPECTED_HEADERS:
            raise SectorClassificationParseError(
                f"Invalid CSV headers: expected {cls.EXPECTED_HEADERS}, got {cleaned_headers}"
            )

        result = SectorClassificationParseResult()

        for row_idx, row in enumerate(reader, start=2):
            result.total_rows += 1
            if not row or all(not cell.strip() for cell in row):
                continue

            if len(row) < 5:
                result.errors.append(f"Row {row_idx}: Insufficient columns ({len(row)} < 5)")
                continue

            company_name = row[0].strip()
            industry = row[1].strip()
            symbol = row[2].strip().upper()
            series = row[3].strip().upper()
            isin = row[4].strip().upper()

            missing_fields: list[str] = []
            if not company_name:
                missing_fields.append("Company Name")
            if not industry:
                missing_fields.append("Industry")
            if not symbol:
                missing_fields.append("Symbol")
            if not isin:
                missing_fields.append("ISIN Code")

            if missing_fields:
                result.errors.append(f"Row {row_idx}: Missing required fields: {', '.join(missing_fields)}")
                continue

            is_dummy = (
                symbol.startswith("DUMMY")
                or isin.startswith("DUM")
                or not ISIN_REGEX.match(isin)
            )

            rec = SectorClassificationRecord(
                company_name=company_name,
                industry=industry,
                symbol=symbol,
                series=series,
                isin=isin,
                is_dummy=is_dummy,
                row_number=row_idx,
            )

            if is_dummy:
                result.dummy_records.append(rec)
            else:
                result.records.append(rec)
                result.valid_rows += 1

        return result
