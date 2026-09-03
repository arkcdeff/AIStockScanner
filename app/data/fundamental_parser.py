"""Parsers for Fundamental Financial Statements, Corporate Announcements, and Shareholding Patterns.

Strict Invariants:
  - Normalizes amounts to INR Crore.
  - Preserves None for missing/unreported metrics (MISSING != ZERO).
  - Enforces explicit period types ("QUARTERLY" / "ANNUAL") and statement bases ("CONSOLIDATED" / "STANDALONE").
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class FundamentalParseError(ValueError):
    """Raised when fundamental parser encounters unrecoverable syntax or validation errors."""


def parse_decimal_or_none(value: Any) -> Decimal | None:
    """Parse numeric string to Decimal, returning None if empty or missing (MISSING != ZERO)."""
    if value is None:
        return None
    val_str = str(value).strip()
    if not val_str or val_str.upper() in ("NONE", "NULL", "N/A", "-", ""):
        return None
    # Remove commas
    val_clean = val_str.replace(",", "")
    try:
        return Decimal(val_clean)
    except InvalidOperation:
        return None


def parse_date_strict(val_str: str) -> date:
    """Parse ISO date string YYYY-MM-DD."""
    val_clean = val_str.strip()
    try:
        return datetime.strptime(val_clean, "%Y-%m-%d").date()
    except ValueError as exc:
        raise FundamentalParseError(f"Invalid date format '{val_str}', expected YYYY-MM-DD") from exc


class FundamentalStatementParser:
    """Parser for financial statements (Income Statement, Balance Sheet, Cash Flow)."""

    VALID_PERIOD_TYPES = {"QUARTERLY", "ANNUAL"}
    VALID_STATEMENT_BASES = {"CONSOLIDATED", "STANDALONE"}
    VALID_REVISIONS = {"AS_REPORTED", "LATEST_RESTATED"}

    @classmethod
    def parse_csv_content(cls, content: str) -> list[dict[str, Any]]:
        """Parse raw CSV string containing financial statements."""
        f = io.StringIO(content.strip())
        reader = csv.DictReader(f)
        records: list[dict[str, Any]] = []

        for idx, row in enumerate(reader, start=1):
            symbol = row.get("symbol", "").strip().upper()
            if not symbol:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'symbol'")

            period_type = row.get("period_type", "").strip().upper()
            if period_type not in cls.VALID_PERIOD_TYPES:
                raise FundamentalParseError(
                    f"Row {idx}: invalid period_type '{period_type}', expected one of {cls.VALID_PERIOD_TYPES}"
                )

            statement_basis = row.get("statement_basis", "CONSOLIDATED").strip().upper()
            if statement_basis not in cls.VALID_STATEMENT_BASES:
                raise FundamentalParseError(
                    f"Row {idx}: invalid statement_basis '{statement_basis}', expected one of {cls.VALID_STATEMENT_BASES}"
                )

            period_end_str = row.get("period_end", "").strip()
            if not period_end_str:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'period_end'")
            period_end = parse_date_strict(period_end_str)

            available_at_str = row.get("available_at", "").strip() or row.get("reported_at", "").strip() or period_end_str
            available_at = parse_date_strict(available_at_str)

            reported_at_str = row.get("reported_at", "").strip()
            reported_at = parse_date_strict(reported_at_str) if reported_at_str else available_at

            revision_type = row.get("revision_type", "AS_REPORTED").strip().upper()
            if revision_type not in cls.VALID_REVISIONS:
                revision_type = "AS_REPORTED"

            records.append({
                "symbol": symbol,
                "period_type": period_type,
                "statement_basis": statement_basis,
                "period_end": period_end,
                "reported_at": reported_at,
                "available_at": available_at,
                "revision_type": revision_type,
                "revenue": parse_decimal_or_none(row.get("revenue")),
                "operating_income": parse_decimal_or_none(row.get("operating_income")),
                "ebit": parse_decimal_or_none(row.get("ebit")),
                "pbt": parse_decimal_or_none(row.get("pbt")),
                "pat": parse_decimal_or_none(row.get("pat")),
                "eps": parse_decimal_or_none(row.get("eps")),
                "total_assets": parse_decimal_or_none(row.get("total_assets")),
                "total_liabilities": parse_decimal_or_none(row.get("total_liabilities")),
                "total_equity": parse_decimal_or_none(row.get("total_equity")),
                "total_debt": parse_decimal_or_none(row.get("total_debt")),
                "cash_and_equivalents": parse_decimal_or_none(row.get("cash_and_equivalents")),
                "operating_cash_flow": parse_decimal_or_none(row.get("operating_cash_flow")),
                "investing_cash_flow": parse_decimal_or_none(row.get("investing_cash_flow")),
                "financing_cash_flow": parse_decimal_or_none(row.get("financing_cash_flow")),
                "free_cash_flow": parse_decimal_or_none(row.get("free_cash_flow")),
                "shares_outstanding": parse_decimal_or_none(row.get("shares_outstanding")),
                "source_name": row.get("source_name", "OFFICIAL_DISCLOSURE").strip(),
                "source_reference": row.get("source_reference", None),
            })

        return records


class CorporateAnnouncementParser:
    """Parser for corporate announcement records."""

    VALID_CATEGORIES = {
        "FINANCIAL_RESULTS", "BOARD_MEETING", "DIVIDEND", "BONUS", "STOCK_SPLIT",
        "RIGHTS", "BUYBACK", "MERGER", "DEMERGER", "ACQUISITION", "ORDER_WIN", "CAPEX",
        "FUND_RAISE", "MANAGEMENT_CHANGE", "CREDIT_RATING", "LITIGATION", "REGULATORY", "OTHER"
    }

    @classmethod
    def normalize_category(cls, desc: str | None) -> str:
        """Map raw exchange announcement description or purpose to canonical category."""
        if not desc:
            return "OTHER"
        d = desc.upper()
        if "BOARD MEETING" in d or "OUTCOME OF BOARD" in d:
            return "BOARD_MEETING"
        if "FINANCIAL RESULT" in d or "FINANCIALS" in d:
            return "FINANCIAL_RESULTS"
        if "DIVIDEND" in d:
            return "DIVIDEND"
        if "BONUS" in d:
            return "BONUS"
        if "SPLIT" in d or "SUB-DIVISION" in d:
            return "STOCK_SPLIT"
        if "RIGHTS" in d:
            return "RIGHTS"
        if "BUYBACK" in d:
            return "BUYBACK"
        if "MERGER" in d or "AMALGAMATION" in d:
            return "MERGER"
        if "DEMERGER" in d:
            return "DEMERGER"
        if "ACQUISITION" in d:
            return "ACQUISITION"
        if "ORDER" in d or "CONTRACT" in d or "BAGGING" in d:
            return "ORDER_WIN"
        if "CAPEX" in d or "EXPANSION" in d:
            return "CAPEX"
        if "FUND RAISE" in d or "ALLOTMENT OF SECURITIES" in d:
            return "FUND_RAISE"
        if any(k in d for k in ["MANAGEMENT", "DIRECTOR", "RESIGNATION", "APPOINTMENT", "CESSATION"]):
            return "MANAGEMENT_CHANGE"
        if "CREDIT RATING" in d:
            return "CREDIT_RATING"
        if "LITIGATION" in d or "DISPUTE" in d:
            return "LITIGATION"
        if "REGULATORY" in d:
            return "REGULATORY"
        if d in cls.VALID_CATEGORIES:
            return d
        return "OTHER"

    @classmethod
    def parse_csv_content(cls, content: str) -> list[dict[str, Any]]:
        """Parse raw CSV string containing corporate announcements."""
        f = io.StringIO(content.strip())
        reader = csv.DictReader(f)
        records: list[dict[str, Any]] = []

        for idx, row in enumerate(reader, start=1):
            symbol = row.get("symbol", "").strip().upper()
            if not symbol:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'symbol'")

            category = cls.normalize_category(row.get("category", "OTHER"))

            announcement_date_str = row.get("announcement_date", "").strip()
            if not announcement_date_str:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'announcement_date'")
            ann_date = parse_date_strict(announcement_date_str)

            avail_str = row.get("available_at", "").strip() or announcement_date_str
            avail_date = parse_date_strict(avail_str)

            title = row.get("title", "").strip()
            if not title:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'title'")

            records.append({
                "symbol": symbol,
                "category": category,
                "announcement_date": ann_date,
                "available_at": avail_date,
                "title": title,
                "summary": row.get("summary", None),
                "corporate_action_id": parse_decimal_or_none(row.get("corporate_action_id")),
                "source_name": row.get("source_name", "NSE_ANNOUNCEMENT").strip(),
                "source_reference": row.get("source_reference", None),
            })

        return records


class ShareholdingPatternParser:
    """Parser for quarter-end shareholding disclosures."""

    @classmethod
    def parse_csv_content(cls, content: str) -> list[dict[str, Any]]:
        """Parse raw CSV string containing shareholding patterns."""
        f = io.StringIO(content.strip())
        reader = csv.DictReader(f)
        records: list[dict[str, Any]] = []

        for idx, row in enumerate(reader, start=1):
            symbol = row.get("symbol", "").strip().upper()
            if not symbol:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'symbol'")

            period_end_str = row.get("period_end", "").strip()
            if not period_end_str:
                raise FundamentalParseError(f"Row {idx}: missing mandatory 'period_end'")
            period_end = parse_date_strict(period_end_str)

            avail_str = row.get("available_at", "").strip() or row.get("reported_at", "").strip() or period_end_str
            available_at = parse_date_strict(avail_str)

            reported_at_str = row.get("reported_at", "").strip()
            reported_at = parse_date_strict(reported_at_str) if reported_at_str else available_at

            promoter = parse_decimal_or_none(row.get("promoter_holding_pct"))
            fii = parse_decimal_or_none(row.get("fii_holding_pct"))
            dii = parse_decimal_or_none(row.get("dii_holding_pct"))
            public = parse_decimal_or_none(row.get("public_holding_pct"))

            if promoter is None or fii is None or dii is None or public is None:
                raise FundamentalParseError(f"Row {idx}: missing mandatory shareholding % fields")

            records.append({
                "symbol": symbol,
                "period_end": period_end,
                "reported_at": reported_at,
                "available_at": available_at,
                "promoter_holding_pct": promoter,
                "fii_holding_pct": fii,
                "dii_holding_pct": dii,
                "public_holding_pct": public,
                "promoter_pledged_pct": parse_decimal_or_none(row.get("promoter_pledged_pct")),
                "promoter_holding_change": parse_decimal_or_none(row.get("promoter_holding_change")),
                "source_name": row.get("source_name", "OFFICIAL_SHAREHOLDING").strip(),
                "source_reference": row.get("source_reference", None),
            })

        return records
