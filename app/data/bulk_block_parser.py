"""Parser for official NSE Cash Market Bulk Deals and Block Deals disclosures.

Parses official exchange-published daily and consolidated Bulk/Block deal reports:
  Date, Symbol, Security Name, Client Name, Buy / Sell, Quantity Traded, Trade Price / Wght. Avg. Price, Remarks

Handles:
  - Trailing spaces in header column names ("Date ", "Symbol ", etc.)
  - Uppercase and mixed-case DD-Mon-YYYY dates (e.g. 01-OCT-2025)
  - Indian and standard number grouping with commas ("82,00,000", "70,000")
  - Empty remarks, "-" remarks, and whitespace normalization
  - Rejection of non-positive quantity or price
  - Deterministic 1-based data-row line indexing (source_line_index)
  - Normalized 8-field SHA-256 deal_row_hash computation (non-unique content hash)
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


class DealsParseError(Exception):
    """Raised when CSV structure or mandatory validation fails."""


@dataclass(frozen=True)
class ParsedDealRow:
    """Normalized deal transaction parsed from official CSV source."""

    source_line_index: int
    trade_date: date
    deal_type: str
    symbol: str
    security_name: str
    client_name: str
    buy_sell: str
    quantity: int
    trade_price: Decimal
    deal_value: Decimal
    remarks: str | None
    deal_row_hash: str


@dataclass
class BulkBlockParseResult:
    """Complete parse result for a bulk or block deals report."""

    deal_type: str
    report_date: date | None = None
    rows: list[ParsedDealRow] = field(default_factory=list)
    raw_row_count: int = 0
    quality_warnings: list[str] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)


class BulkBlockDealsParser:
    """Parser for NSE Bulk and Block deal CSV reports."""

    @staticmethod
    def clean_number_str(val: str) -> str:
        """Strip commas and surrounding whitespace."""
        return val.replace(",", "").strip()

    @staticmethod
    def parse_date(date_str: str) -> date:
        """Parse date string in DD-Mon-YYYY, DD-MM-YYYY, or YYYY-MM-DD format."""
        s = date_str.strip()
        if not s:
            raise DealsParseError("Empty date string")

        # Try DD-Mon-YYYY (e.g. 01-OCT-2025)
        m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
        if m:
            day = int(m.group(1))
            mon_str = m.group(2).upper()
            year = int(m.group(3))
            month = MONTH_MAP.get(mon_str)
            if not month:
                raise DealsParseError(f"Invalid month name '{mon_str}' in date '{s}'")
            return date(year, month, day)

        # Try DD-MM-YYYY
        m2 = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", s)
        if m2:
            return date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))

        # Try YYYY-MM-DD
        m3 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        if m3:
            return date(int(m3.group(1)), int(m3.group(2)), int(m3.group(3)))

        # Fallback to standard strptime
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue

        raise DealsParseError(f"Unrecognized date format: '{date_str}'")

    @staticmethod
    def compute_deal_row_hash(
        trade_date: date,
        deal_type: str,
        symbol: str,
        client_name: str,
        buy_sell: str,
        quantity: int,
        trade_price: Decimal,
        remarks: str | None,
    ) -> str:
        """Compute SHA-256 hash of normalized 8-field content.

        Content fields:
          trade_date (YYYY-MM-DD), deal_type (BULK/BLOCK), symbol (uppercase),
          client_name (normalized single spaces), buy_sell (BUY/SELL),
          quantity (int), trade_price (standard string representation), remarks (or empty string).
        """
        norm_date = trade_date.isoformat()
        norm_type = deal_type.strip().upper()
        norm_symbol = symbol.strip().upper()
        norm_client = re.sub(r"\s+", " ", client_name.strip()).upper()
        norm_side = buy_sell.strip().upper()
        norm_qty = str(quantity)
        # Standardize price formatting: remove trailing zeros after decimal point up to 4 places
        norm_price = f"{trade_price:.4f}".rstrip("0").rstrip(".") if "." in f"{trade_price:.4f}" else str(trade_price)
        norm_rem = remarks.strip() if remarks else ""

        payload = f"{norm_date}|{norm_type}|{norm_symbol}|{norm_client}|{norm_side}|{norm_qty}|{norm_price}|{norm_rem}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def parse_csv(
        self,
        content: str | bytes,
        deal_type: str,
        trade_date: date | None = None,
        source_reference: str | None = None,
    ) -> BulkBlockParseResult:
        """Parse raw CSV content into structured BulkBlockParseResult.

        Args:
            content: Raw string or bytes of the CSV file.
            deal_type: 'BULK' or 'BLOCK'.
            trade_date: Optional fallback trade date.
            source_reference: Optional source reference filename.
        """
        deal_type_clean = deal_type.strip().upper()
        if deal_type_clean not in ("BULK", "BLOCK"):
            raise DealsParseError(f"Invalid deal_type '{deal_type}'. Must be 'BULK' or 'BLOCK'.")

        if isinstance(content, bytes):
            # Decode with UTF-8 fallback to latin-1
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("latin-1")
        else:
            text_content = content

        result = BulkBlockParseResult(deal_type=deal_type_clean)
        lines = [ln.strip() for ln in text_content.splitlines() if ln.strip()]
        if not lines:
            return result

        reader = csv.reader(io.StringIO(text_content))
        try:
            raw_header = next(reader)
        except StopIteration:
            return result

        header = [col.strip() for col in raw_header]
        if not header or not header[0]:
            return result

        # Check for NO RECORDS indicator
        if len(header) >= 1 and "NO RECORDS" in header[0].upper():
            return result

        # Validate header structure
        # Expected: Date, Symbol, Security Name, Client Name, Buy / Sell, Quantity Traded, Trade Price / Wght. Avg. Price, Remarks
        header_lower = [c.lower() for c in header]
        if not any("date" in c for c in header_lower):
            raise DealsParseError(f"Invalid deals CSV: 'Date' column not found in header: {header}")
        if not any("symbol" in c for c in header_lower):
            raise DealsParseError(f"Invalid deals CSV: 'Symbol' column not found in header: {header}")

        data_row_index = 0
        for raw_row in reader:
            if not raw_row or not any(raw_row):
                continue

            # Check for "NO RECORDS" row
            if "NO RECORDS" in raw_row[0].upper():
                continue

            data_row_index += 1
            result.raw_row_count += 1

            if len(raw_row) < 7:
                result.rejected_rows.append({
                    "line": data_row_index,
                    "reason": f"Insufficient columns ({len(raw_row)} < 7)",
                    "raw": raw_row,
                })
                continue

            try:
                # 0: Date
                row_date = self.parse_date(raw_row[0])

                # 1: Symbol
                symbol = raw_row[1].strip().upper()
                if not symbol:
                    raise DealsParseError("Empty symbol")

                # 2: Security Name
                security_name = raw_row[2].strip()
                if not security_name:
                    security_name = symbol

                # 3: Client Name
                client_name = re.sub(r"\s+", " ", raw_row[3].strip())
                if not client_name:
                    raise DealsParseError("Empty client name")

                # 4: Buy / Sell
                raw_side = raw_row[4].strip().upper()
                if raw_side in ("BUY", "B"):
                    buy_sell = "BUY"
                elif raw_side in ("SELL", "S"):
                    buy_sell = "SELL"
                else:
                    raise DealsParseError(f"Invalid Buy/Sell value: '{raw_row[4]}'")

                # 5: Quantity Traded
                clean_qty = self.clean_number_str(raw_row[5])
                quantity = int(clean_qty)
                if quantity <= 0:
                    raise DealsParseError(f"Quantity must be positive, got {quantity}")

                # 6: Trade Price / Wght. Avg. Price
                clean_price = self.clean_number_str(raw_row[6])
                trade_price = Decimal(clean_price)
                if trade_price <= Decimal("0"):
                    raise DealsParseError(f"Price must be positive, got {trade_price}")

                # 7: Remarks
                raw_remarks = raw_row[7].strip() if len(raw_row) > 7 else ""
                if raw_remarks in ("", "-"):
                    remarks = None
                else:
                    remarks = raw_remarks

                # Deal value calculation (quantity * price, rounded to 2 decimals)
                deal_value = (Decimal(quantity) * trade_price).quantize(Decimal("0.01"))

                # Deal row content hash
                deal_row_hash = self.compute_deal_row_hash(
                    trade_date=row_date,
                    deal_type=deal_type_clean,
                    symbol=symbol,
                    client_name=client_name,
                    buy_sell=buy_sell,
                    quantity=quantity,
                    trade_price=trade_price,
                    remarks=remarks,
                )

                parsed_row = ParsedDealRow(
                    source_line_index=data_row_index,
                    trade_date=row_date,
                    deal_type=deal_type_clean,
                    symbol=symbol,
                    security_name=security_name,
                    client_name=client_name,
                    buy_sell=buy_sell,
                    quantity=quantity,
                    trade_price=trade_price,
                    deal_value=deal_value,
                    remarks=remarks,
                    deal_row_hash=deal_row_hash,
                )
                result.rows.append(parsed_row)

                if result.report_date is None:
                    result.report_date = row_date

            except (DealsParseError, InvalidOperation, ValueError) as exc:
                result.rejected_rows.append({
                    "line": data_row_index,
                    "reason": str(exc),
                    "raw": raw_row,
                })
                result.quality_warnings.append(f"Line {data_row_index} rejected: {exc}")

        return result


# Module-level aliases
BulkBlockParser = BulkBlockDealsParser
compute_deal_row_hash = BulkBlockDealsParser.compute_deal_row_hash
