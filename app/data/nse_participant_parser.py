"""Parsers for NSE Participant-wise Open Interest, Trading Volume, and FII Derivatives Statistics.

Supports official NSE report layouts:
  1. Participant-wise Open Interest (dataset_type = "OPEN_INTEREST")
  2. Participant-wise Trading Volume (dataset_type = "TRADING_VOLUME")
  3. FII Derivatives Statistics

Strict Invariants:
  - Participant types normalized: CLIENT, DII, FII, PRO
  - Instrument buckets normalized: INDEX_FUTURE, STOCK_FUTURE, INDEX_OPTION_CALL, INDEX_OPTION_PUT, STOCK_OPTION_CALL, STOCK_OPTION_PUT
  - OPEN_INTEREST is never mislabeled as TRADING_VOLUME
  - Reported row totals validated against component column sums
  - Market balance checked (Sum(Long) vs Sum(Short))
  - Gross positions must be >= 0; negative gross positions are rejected
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass
class ParsedParticipantRow:
    """Normalized position/volume fact for a single participant and instrument bucket."""

    participant_type: str  # "CLIENT", "DII", "FII", "PRO"
    instrument_bucket: str  # "INDEX_FUTURE", "STOCK_FUTURE", etc.
    long_contracts: int
    short_contracts: int
    reported_total_long: int | None = None
    reported_total_short: int | None = None


@dataclass
class ParsedFiiStatisticRow:
    """Normalized row for FII Derivatives Statistics."""

    instrument_category: str  # "INDEX_FUTURES", "INDEX_OPTIONS", "STOCK_FUTURES", "STOCK_OPTIONS"
    buy_contracts: int | None = None
    buy_amount: Decimal | None = None
    sell_contracts: int | None = None
    sell_amount: Decimal | None = None
    open_interest_contracts: int | None = None
    open_interest_amount: Decimal | None = None


@dataclass
class ParticipantParseResult:
    """Parsed output containing records, metadata, and data quality issues."""

    dataset_type: str  # "OPEN_INTEREST" or "TRADING_VOLUME"
    report_date: date
    rows: list[ParsedParticipantRow] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    raw_row_count: int = 0
    is_market_balanced: bool = True


@dataclass
class FiiStatisticsParseResult:
    """Parsed output for FII Derivatives Statistics."""

    report_date: date
    rows: list[ParsedFiiStatisticRow] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    raw_row_count: int = 0


# Participant category mapping
PARTICIPANT_TYPE_MAP: dict[str, str] = {
    "CLIENT": "CLIENT",
    "CLIENTS": "CLIENT",
    "DII": "DII",
    "FII": "FII",
    "FPI": "FII",
    "FII/FPI": "FII",
    "PRO": "PRO",
    "PROPRIETARY": "PRO",
}


def _clean_int(val: Any) -> int | None:
    """Parse string or number into non-negative integer."""
    if val is None or val == "":
        return None
    s = str(val).strip().replace(",", "")
    try:
        n = int(float(s))
        return n
    except (ValueError, TypeError):
        return None


def _clean_decimal(val: Any) -> Decimal | None:
    """Parse string or number into Decimal."""
    if val is None or val == "":
        return None
    s = str(val).strip().replace(",", "")
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError):
        return None


def parse_report_date_from_filename(filename: str) -> date | None:
    """Extract report date from standard NSE filenames (e.g. fao_participant_oi_10082026.csv or fii_stats_2026-08-10.csv)."""
    if not filename:
        return None

    # Pattern 1: YYYY-MM-DD
    m1 = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if m1:
        try:
            return date(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)))
        except ValueError:
            pass

    # Pattern 2: DDMMYYYY
    m2 = re.search(r"(\d{2})(\d{2})(\d{4})", filename)
    if m2:
        try:
            return date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        except ValueError:
            pass

    return None


class ParticipantOpenInterestParser:
    """Parser for NSE Participant-wise Open Interest reports (dataset_type = "OPEN_INTEREST")."""

    def parse(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str | None = None,
    ) -> ParticipantParseResult:
        return self._parse_participant_report(
            content=content,
            dataset_type="OPEN_INTEREST",
            override_date=report_date,
            filename=filename,
        )

    def _parse_participant_report(
        self,
        content: str | bytes,
        dataset_type: str,
        override_date: date | None = None,
        filename: str | None = None,
    ) -> ParticipantParseResult:
        if isinstance(content, bytes):
            text_str = content.decode("utf-8", errors="replace")
        else:
            text_str = content

        quality_warnings: list[str] = []
        lines = [line.strip() for line in text_str.splitlines() if line.strip()]

        if not lines:
            raise ValueError("Empty file content")

        # Determine report date
        resolved_date = override_date
        if resolved_date is None and filename:
            resolved_date = parse_report_date_from_filename(filename)

        # Look for date in header lines if not resolved
        if resolved_date is None:
            for line in lines[:5]:
                m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", line)
                if m:
                    try:
                        resolved_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                        break
                    except ValueError:
                        pass
                m2 = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", line)
                if m2:
                    try:
                        resolved_date = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                        break
                    except ValueError:
                        pass

        if resolved_date is None:
            resolved_date = date.today()
            quality_warnings.append("DQ_PARTICIPANT_BAD_REPORT_DATE")

        # Find header row
        header_idx = -1
        for i, line in enumerate(lines[:10]):
            l_lower = line.lower()
            if "client type" in l_lower or "client" in l_lower or "future index" in l_lower:
                header_idx = i
                break

        if header_idx == -1:
            raise ValueError("Unsupported participant report layout — missing Client Type header")

        reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])))
        header_raw = next(reader, None)
        if not header_raw:
            raise ValueError("Missing CSV header row")

        header_norm = [c.strip().lower().replace("\n", " ").replace("  ", " ") for c in header_raw]

        # Map column indices
        col_map: dict[str, int] = {}
        for idx, col in enumerate(header_norm):
            if "client" in col or "participant" in col:
                col_map["client_type"] = idx
            elif "future index long" in col or "futidx long" in col:
                col_map["fut_idx_long"] = idx
            elif "future index short" in col or "futidx short" in col:
                col_map["fut_idx_short"] = idx
            elif "future stock long" in col or "futstk long" in col:
                col_map["fut_stk_long"] = idx
            elif "future stock short" in col or "futstk short" in col:
                col_map["fut_stk_short"] = idx
            elif "option index call long" in col or "optidx call long" in col:
                col_map["opt_idx_call_long"] = idx
            elif "option index call short" in col or "optidx call short" in col:
                col_map["opt_idx_call_short"] = idx
            elif "option index put long" in col or "optidx put long" in col:
                col_map["opt_idx_put_long"] = idx
            elif "option index put short" in col or "optidx put short" in col:
                col_map["opt_idx_put_short"] = idx
            elif "option stock call long" in col or "optstk call long" in col:
                col_map["opt_stk_call_long"] = idx
            elif "option stock call short" in col or "optstk call short" in col:
                col_map["opt_stk_call_short"] = idx
            elif "option stock put long" in col or "optstk put long" in col:
                col_map["opt_stk_put_long"] = idx
            elif "option stock put short" in col or "optstk put short" in col:
                col_map["opt_stk_put_short"] = idx
            elif "total long" in col:
                col_map["total_long"] = idx
            elif "total short" in col:
                col_map["total_short"] = idx

        if "client_type" not in col_map or "fut_idx_long" not in col_map:
            raise ValueError("Unsupported participant CSV header structure")

        parsed_rows: list[ParsedParticipantRow] = []
        raw_count = 0

        for row_cells in reader:
            if not row_cells or len(row_cells) <= max(col_map.values()):
                continue

            raw_part = row_cells[col_map["client_type"]].strip()
            part_key = raw_part.upper()

            if part_key == "TOTAL" or "TOTAL" in part_key:
                continue

            part_type = PARTICIPANT_TYPE_MAP.get(part_key)
            if not part_type:
                quality_warnings.append(f"DQ_PARTICIPANT_UNKNOWN_TYPE:{raw_part}")
                continue

            raw_count += 1

            # Extract contract fields
            fut_idx_l = _clean_int(row_cells[col_map["fut_idx_long"]]) or 0
            fut_idx_s = _clean_int(row_cells[col_map["fut_idx_short"]]) or 0
            fut_stk_l = _clean_int(row_cells[col_map.get("fut_stk_long", -1)]) or 0
            fut_stk_s = _clean_int(row_cells[col_map.get("fut_stk_short", -1)]) or 0

            opt_idx_c_l = _clean_int(row_cells[col_map.get("opt_idx_call_long", -1)]) or 0
            opt_idx_c_s = _clean_int(row_cells[col_map.get("opt_idx_call_short", -1)]) or 0
            opt_idx_p_l = _clean_int(row_cells[col_map.get("opt_idx_put_long", -1)]) or 0
            opt_idx_p_s = _clean_int(row_cells[col_map.get("opt_idx_put_short", -1)]) or 0

            opt_stk_c_l = _clean_int(row_cells[col_map.get("opt_stk_call_long", -1)]) or 0
            opt_stk_c_s = _clean_int(row_cells[col_map.get("opt_stk_call_short", -1)]) or 0
            opt_stk_p_l = _clean_int(row_cells[col_map.get("opt_stk_put_long", -1)]) or 0
            opt_stk_p_s = _clean_int(row_cells[col_map.get("opt_stk_put_short", -1)]) or 0

            rep_tot_l = _clean_int(row_cells[col_map.get("total_long", -1)])
            rep_tot_s = _clean_int(row_cells[col_map.get("total_short", -1)])

            # Validate gross negative values
            gross_vals = [
                fut_idx_l,
                fut_idx_s,
                fut_stk_l,
                fut_stk_s,
                opt_idx_c_l,
                opt_idx_c_s,
                opt_idx_p_l,
                opt_idx_p_s,
                opt_stk_c_l,
                opt_stk_c_s,
                opt_stk_p_l,
                opt_stk_p_s,
            ]
            if any(v < 0 for v in gross_vals):
                quality_warnings.append(f"DQ_PARTICIPANT_NEGATIVE_VALUE:{part_type}")
                continue

            # Validate reported row totals against calculated row sum
            calc_tot_l = fut_idx_l + fut_stk_l + opt_idx_c_l + opt_idx_p_l + opt_stk_c_l + opt_stk_p_l
            calc_tot_s = fut_idx_s + fut_stk_s + opt_idx_c_s + opt_idx_p_s + opt_stk_c_s + opt_stk_p_s

            if rep_tot_l is not None and calc_tot_l != rep_tot_l:
                quality_warnings.append(f"DQ_PARTICIPANT_TOTAL_MISMATCH:{part_type}_LONG")
            if rep_tot_s is not None and calc_tot_s != rep_tot_s:
                quality_warnings.append(f"DQ_PARTICIPANT_TOTAL_MISMATCH:{part_type}_SHORT")

            # Append normalized rows per instrument bucket
            buckets: list[tuple[str, int, int]] = [
                ("INDEX_FUTURE", fut_idx_l, fut_idx_s),
                ("STOCK_FUTURE", fut_stk_l, fut_stk_s),
                ("INDEX_OPTION_CALL", opt_idx_c_l, opt_idx_c_s),
                ("INDEX_OPTION_PUT", opt_idx_p_l, opt_idx_p_s),
                ("STOCK_OPTION_CALL", opt_stk_c_l, opt_stk_c_s),
                ("STOCK_OPTION_PUT", opt_stk_p_l, opt_stk_p_s),
            ]

            for bucket, l_cnt, s_cnt in buckets:
                parsed_rows.append(
                    ParsedParticipantRow(
                        participant_type=part_type,
                        instrument_bucket=bucket,
                        long_contracts=l_cnt,
                        short_contracts=s_cnt,
                        reported_total_long=rep_tot_l,
                        reported_total_short=rep_tot_s,
                    )
                )

        # Market balance check (Sum of all longs vs Sum of all shorts per instrument bucket)
        is_balanced = True
        bucket_totals: dict[str, tuple[int, int]] = {}
        for r in parsed_rows:
            l_tot, s_tot = bucket_totals.get(r.instrument_bucket, (0, 0))
            bucket_totals[r.instrument_bucket] = (l_tot + r.long_contracts, s_tot + r.short_contracts)

        for bucket, (tot_l, tot_s) in bucket_totals.items():
            if tot_l != tot_s:
                is_balanced = False
                quality_warnings.append(f"DQ_PARTICIPANT_MARKET_BALANCE_MISMATCH:{bucket}")

        return ParticipantParseResult(
            dataset_type=dataset_type,
            report_date=resolved_date,
            rows=parsed_rows,
            quality_warnings=quality_warnings,
            raw_row_count=raw_count,
            is_market_balanced=is_balanced,
        )


class ParticipantTradingVolumeParser:
    """Parser for NSE Participant-wise Trading Volume reports (dataset_type = "TRADING_VOLUME")."""

    def parse(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str | None = None,
    ) -> ParticipantParseResult:
        oi_parser = ParticipantOpenInterestParser()
        res = oi_parser._parse_participant_report(
            content=content,
            dataset_type="TRADING_VOLUME",
            override_date=report_date,
            filename=filename,
        )
        return res


class FiiDerivativesStatisticsParser:
    """Parser for official FII Derivatives Statistics reports (FO_FII_DERIVATIVES)."""

    def parse(
        self,
        content: str | bytes,
        report_date: date | None = None,
        filename: str | None = None,
    ) -> FiiStatisticsParseResult:
        if isinstance(content, bytes):
            text_str = content.decode("utf-8", errors="replace")
        else:
            text_str = content

        quality_warnings: list[str] = []
        lines = [line.strip() for line in text_str.splitlines() if line.strip()]

        if not lines:
            raise ValueError("Empty file content")

        resolved_date = report_date or parse_report_date_from_filename(filename or "")
        if resolved_date is None:
            resolved_date = date.today()
            quality_warnings.append("DQ_FII_DERIVATIVES_BAD_REPORT_DATE")

        # Header index
        header_idx = -1
        for i, line in enumerate(lines[:10]):
            l_lower = line.lower()
            if "category" in l_lower or "instrument" in l_lower or "buy contracts" in l_lower:
                header_idx = i
                break

        if header_idx == -1:
            raise ValueError("Unsupported FII derivatives statistics format")

        reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])))
        header_raw = next(reader, None)
        if not header_raw:
            raise ValueError("Missing header row")

        header_norm = [c.strip().lower() for c in header_raw]
        col_map: dict[str, int] = {}

        for idx, c in enumerate(header_norm):
            if "category" in c or "instrument" in c:
                col_map["category"] = idx
            elif "buy contract" in c or "buy (contracts)" in c:
                col_map["buy_contracts"] = idx
            elif "buy amount" in c or "buy (amount" in c or "buy (in cr" in c:
                col_map["buy_amount"] = idx
            elif "sell contract" in c or "sell (contracts)" in c:
                col_map["sell_contracts"] = idx
            elif "sell amount" in c or "sell (amount" in c or "sell (in cr" in c:
                col_map["sell_amount"] = idx
            elif "open interest contract" in c or "oi (contracts)" in c or "oi contract" in c:
                col_map["oi_contracts"] = idx
            elif "open interest amount" in c or "oi (amount" in c or "oi (in cr" in c:
                col_map["oi_amount"] = idx

        if "category" not in col_map:
            raise ValueError("Unsupported FII statistics header layout")

        category_norm_map: dict[str, str] = {
            "INDEX FUTURES": "INDEX_FUTURES",
            "INDEX OPTIONS": "INDEX_OPTIONS",
            "STOCK FUTURES": "STOCK_FUTURES",
            "STOCK OPTIONS": "STOCK_OPTIONS",
        }

        parsed_rows: list[ParsedFiiStatisticRow] = []

        for row_cells in reader:
            if not row_cells or len(row_cells) <= col_map["category"]:
                continue

            raw_cat = row_cells[col_map["category"]].strip().upper()
            norm_cat = category_norm_map.get(raw_cat)
            if not norm_cat:
                continue

            b_cnt = _clean_int(row_cells[col_map.get("buy_contracts", -1)])
            b_amt = _clean_decimal(row_cells[col_map.get("buy_amount", -1)])
            s_cnt = _clean_int(row_cells[col_map.get("sell_contracts", -1)])
            s_amt = _clean_decimal(row_cells[col_map.get("sell_amount", -1)])
            oi_cnt = _clean_int(row_cells[col_map.get("oi_contracts", -1)])
            oi_amt = _clean_decimal(row_cells[col_map.get("oi_amount", -1)])

            parsed_rows.append(
                ParsedFiiStatisticRow(
                    instrument_category=norm_cat,
                    buy_contracts=b_cnt,
                    buy_amount=b_amt,
                    sell_contracts=s_cnt,
                    sell_amount=s_amt,
                    open_interest_contracts=oi_cnt,
                    open_interest_amount=oi_amt,
                )
            )

        return FiiStatisticsParseResult(
            report_date=resolved_date,
            rows=parsed_rows,
            quality_warnings=quality_warnings,
            raw_row_count=len(parsed_rows),
        )
