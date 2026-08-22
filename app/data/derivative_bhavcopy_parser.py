"""Derivative Bhavcopy Parser — Sprint 19.1D-2.

Parses official NSE F&O EOD bhavcopy CSV files (legacy format, UDiFF format,
and custom layouts) into validated daily price dataclasses.

Features:
  - Header & format auto-detection
  - Normalization of turnover to INR Rupees (converts VAL_INLAKH to Rupees cleanly)
  - LTP and Settlement Price stored independently from Close
  - Non-negative OI validation (MISSING != ZERO)
  - Signed Change-in-OI (allows positive, negative, or zero)
  - Futures vs Options separation: parses all rows, classifies instruments, and exposes futures_records while tracking options_skipped_count.
  - No-trade row handling (0 contracts / zero volume) without treating missing prices as corruption.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.data.exchange_constants import (
    DQ_DERIVATIVE_PRICE_BAD_TURNOVER,
    DQ_DERIVATIVE_PRICE_INVALID_OHLC,
    DQ_DERIVATIVE_PRICE_NEGATIVE_OI,
    DQ_DERIVATIVE_PRICE_NEGATIVE_VOLUME,
    DQ_DERIVATIVE_PRICE_POST_EXPIRY,
    DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
)


class DerivativeBhavcopyParserError(Exception):
    """Raised when F&O bhavcopy file format cannot be parsed."""
    pass


@dataclass(frozen=True)
class ParsedDerivativeDailyPrice:
    """A validated EOD price observation for a derivative contract."""

    underlying_symbol: str
    instrument_type: str  # 'FUTURE' or 'OPTION'
    expiry_date: date
    strike_price: Decimal | None
    option_type: str | None  # 'CE', 'PE', or None
    trade_date: date

    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    last_price: Decimal | None
    settlement_price: Decimal | None
    previous_close: Decimal | None

    volume_contracts: int | None
    turnover: Decimal | None  # In INR Rupees

    open_interest: int | None
    change_in_open_interest: int | None  # Signed

    underlying_value: Decimal | None
    is_no_trade: bool
    raw_row: dict[str, Any]


@dataclass(frozen=True)
class ParsedDerivativePriceRowError:
    """An error recorded for a specific row during price parsing."""

    row_index: int
    issue_code: str
    description: str
    raw_row: dict[str, Any]


@dataclass
class DerivativePriceParseResult:
    """Result of parsing an F&O EOD bhavcopy file."""

    all_records: list[ParsedDerivativeDailyPrice]
    futures_records: list[ParsedDerivativeDailyPrice]
    options_records: list[ParsedDerivativeDailyPrice]
    futures_skipped_count: int
    options_skipped_count: int
    row_errors: list[ParsedDerivativePriceRowError]
    total_rows: int


class DerivativeBhavcopyParser:
    """Parser for NSE F&O EOD bhavcopy files."""

    def parse_csv(
        self,
        content: str | bytes,
        trade_date_override: date | None = None,
    ) -> DerivativePriceParseResult:
        """Parse CSV text or bytes into DerivativePriceParseResult."""
        if isinstance(content, bytes):
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("latin-1")
        else:
            text_content = content

        reader = csv.DictReader(io.StringIO(text_content))
        if not reader.fieldnames:
            raise DerivativeBhavcopyParserError("UNSUPPORTED_FORMAT: Empty file or no CSV header found.")

        field_map = self._detect_columns(reader.fieldnames)
        is_lakhs_turnover = field_map.get("turnover_in_lakhs", False)

        all_records: list[ParsedDerivativeDailyPrice] = []
        futures_records: list[ParsedDerivativeDailyPrice] = []
        options_records: list[ParsedDerivativeDailyPrice] = []
        futures_skipped_count = 0
        options_skipped_count = 0
        row_errors: list[ParsedDerivativePriceRowError] = []
        total_rows = 0

        for idx, row in enumerate(reader, start=1):
            total_rows += 1
            try:
                parsed = self._parse_row(row, idx, field_map, trade_date_override, is_lakhs_turnover)
                if isinstance(parsed, ParsedDerivativeDailyPrice):
                    all_records.append(parsed)
                    if parsed.instrument_type == "FUTURE":
                        futures_records.append(parsed)
                        futures_skipped_count += 1
                    else:
                        options_records.append(parsed)
                        options_skipped_count += 1
                elif isinstance(parsed, ParsedDerivativePriceRowError):
                    row_errors.append(parsed)
            except Exception as exc:
                row_errors.append(
                    ParsedDerivativePriceRowError(
                        row_index=idx,
                        issue_code=DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
                        description=f"Unhandled error parsing row: {str(exc)}",
                        raw_row=dict(row),
                    )
                )

        return DerivativePriceParseResult(
            all_records=all_records,
            futures_records=futures_records,
            options_records=options_records,
            futures_skipped_count=futures_skipped_count,
            options_skipped_count=options_skipped_count,
            row_errors=row_errors,
            total_rows=total_rows,
        )

    def _detect_columns(self, fieldnames: list[str]) -> dict[str, Any]:
        """Detect and map header names to internal field roles."""
        normalized = {f.strip().upper(): f for f in fieldnames}
        field_map: dict[str, Any] = {}

        role_candidates = {
            "underlying": ["SYMBOL", "UNDERLYING", "TCKRSYMB", "ASSET_NAME"],
            "instrument": ["INSTRUMENT", "INSTRUMENT_TYPE", "SECURITY_TYPE", "FININSTRMTP", "INST_TYPE"],
            "expiry": ["EXPIRY_DT", "EXPIRY", "XPRYDT", "EXPIRY_DATE"],
            "strike": ["STRIKE_PR", "STRIKE", "STRKPRIC", "STRIKE_PRICE"],
            "option_type": ["OPTION_TYP", "OPTION_TYPE", "OPTNTP"],
            "trade_date": ["TIMESTAMP", "RPTDT", "DATE", "TRADE_DATE", "TRADEDATE"],
            "open": ["OPEN", "OPNPRIC", "OPEN_PRICE"],
            "high": ["HIGH", "HGHPRIC", "HIGH_PRICE"],
            "low": ["LOW", "LWPRIC", "LOW_PRICE"],
            "close": ["CLOSE", "CLSPRIC", "CLOSE_PRICE"],
            "last_price": ["LTP", "LAST_PRICE", "LSTPRIC"],
            "settlement_price": ["SETTLE_PR", "SETTLEMENT_PRICE", "STTLMPRIC"],
            "previous_close": ["PREV_CLOSE", "PREVIOUS_CLOSE", "PRVCLSPRIC"],
            "volume_contracts": ["CONTRACTS", "TTLTRADGVOL", "VOLUME", "NO_OF_CONTRACTS"],
            "turnover_lakhs": ["VAL_INLAKH", "TURNOVER_LAKHS", "VAL_IN_LAKH"],
            "turnover_raw": ["TTLTRDVL", "TURNOVER", "NOTIONAL_VALUE"],
            "open_interest": ["OPEN_INT", "OPNINTRST", "OPEN_INTEREST", "OI"],
            "change_in_oi": ["CHG_IN_OI", "CHNGINOPNINTRST", "CHANGE_IN_OI"],
            "underlying_value": ["UNDERLYING_VALUE", "UNDRLYGPRIC", "SPOT_PRICE"],
        }

        for role, candidates in role_candidates.items():
            for cand in candidates:
                if cand in normalized:
                    field_map[role] = normalized[cand]
                    break

        if "turnover_lakhs" in field_map:
            field_map["turnover_in_lakhs"] = True
            field_map["turnover"] = field_map["turnover_lakhs"]
        elif "turnover_raw" in field_map:
            field_map["turnover_in_lakhs"] = False
            field_map["turnover"] = field_map["turnover_raw"]

        required = ["underlying", "expiry"]
        missing = [r for r in required if r not in field_map]
        if missing:
            raise DerivativeBhavcopyParserError(
                f"UNSUPPORTED_FORMAT: Missing required CSV columns for roles: {missing}. Header: {fieldnames}"
            )

        return field_map

    def _parse_row(
        self,
        row: dict[str, str],
        idx: int,
        field_map: dict[str, Any],
        trade_date_override: date | None,
        is_lakhs_turnover: bool,
    ) -> ParsedDerivativeDailyPrice | ParsedDerivativePriceRowError:
        """Parse a single row into a ParsedDerivativeDailyPrice or error."""
        underlying = row.get(field_map["underlying"], "").strip().upper()
        if not underlying:
            return ParsedDerivativePriceRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
                description="Missing underlying symbol",
                raw_row=dict(row),
            )

        expiry_raw = row.get(field_map["expiry"], "").strip()
        expiry_date = self._parse_date(expiry_raw)
        if not expiry_date:
            return ParsedDerivativePriceRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
                description=f"Invalid expiry date: '{expiry_raw}'",
                raw_row=dict(row),
            )

        # Trade date
        trade_date: date | None = trade_date_override
        if not trade_date and "trade_date" in field_map:
            trade_date = self._parse_date(row.get(field_map["trade_date"], "").strip())
        if not trade_date:
            trade_date = date.today()

        if trade_date > expiry_date:
            return ParsedDerivativePriceRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_PRICE_POST_EXPIRY,
                description=f"Trade date {trade_date} is after expiry date {expiry_date}",
                raw_row=dict(row),
            )

        # Instrument & Option Type
        inst_raw = row.get(field_map.get("instrument", ""), "").strip().upper()
        opt_raw = row.get(field_map.get("option_type", ""), "").strip().upper()
        opt_type = self._normalize_option_type(opt_raw)
        instrument_type = "OPTION" if ("OPT" in inst_raw or opt_type in ("CE", "PE")) else "FUTURE"

        # Strike price
        strike_raw = row.get(field_map.get("strike", ""), "").strip()
        strike_price: Decimal | None = None
        if strike_raw and strike_raw not in ("0", "0.00", "0.0", "XX", "-"):
            try:
                strike_price = Decimal(strike_raw)
            except (InvalidOperation, TypeError):
                pass

        if instrument_type == "FUTURE":
            strike_price = None
            opt_type = None

        # Parse numeric market fields
        open_val = self._parse_decimal(row.get(field_map.get("open", ""), ""))
        high_val = self._parse_decimal(row.get(field_map.get("high", ""), ""))
        low_val = self._parse_decimal(row.get(field_map.get("low", ""), ""))
        close_val = self._parse_decimal(row.get(field_map.get("close", ""), ""))
        last_val = self._parse_decimal(row.get(field_map.get("last_price", ""), ""))
        settle_val = self._parse_decimal(row.get(field_map.get("settlement_price", ""), ""))
        prev_val = self._parse_decimal(row.get(field_map.get("previous_close", ""), ""))
        underlying_val = self._parse_decimal(row.get(field_map.get("underlying_value", ""), ""))

        volume = self._parse_int(row.get(field_map.get("volume_contracts", ""), ""))
        if volume is not None and volume < 0:
            return ParsedDerivativePriceRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_PRICE_NEGATIVE_VOLUME,
                description=f"Negative contract volume: {volume}",
                raw_row=dict(row),
            )

        # Open Interest
        oi_val = self._parse_int(row.get(field_map.get("open_interest", ""), ""))
        if oi_val is not None and oi_val < 0:
            return ParsedDerivativePriceRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_PRICE_NEGATIVE_OI,
                description=f"Negative open interest: {oi_val}",
                raw_row=dict(row),
            )

        # Signed Change in OI
        change_oi = self._parse_int(row.get(field_map.get("change_in_oi", ""), ""))

        # Turnover in INR Rupees
        turnover_raw = self._parse_decimal(row.get(field_map.get("turnover", ""), ""))
        turnover: Decimal | None = None
        if turnover_raw is not None:
            if turnover_raw < 0:
                return ParsedDerivativePriceRowError(
                    row_index=idx,
                    issue_code=DQ_DERIVATIVE_PRICE_BAD_TURNOVER,
                    description=f"Negative turnover: {turnover_raw}",
                    raw_row=dict(row),
                )
            if is_lakhs_turnover:
                turnover = (turnover_raw * Decimal("100000")).quantize(Decimal("0.0001"))
            else:
                turnover = turnover_raw

        # No-trade determination
        is_no_trade = (volume == 0 or volume is None) and (
            open_val in (None, Decimal(0))
            and high_val in (None, Decimal(0))
            and low_val in (None, Decimal(0))
        )

        # OHLC validation for traded contracts
        if not is_no_trade and all(v is not None and v > 0 for v in (open_val, high_val, low_val, close_val)):
            assert open_val is not None and high_val is not None and low_val is not None and close_val is not None
            if not (low_val <= open_val <= high_val and low_val <= close_val <= high_val):
                return ParsedDerivativePriceRowError(
                    row_index=idx,
                    issue_code=DQ_DERIVATIVE_PRICE_INVALID_OHLC,
                    description=f"Invalid OHLC bounds: O={open_val}, H={high_val}, L={low_val}, C={close_val}",
                    raw_row=dict(row),
                )

        return ParsedDerivativeDailyPrice(
            underlying_symbol=underlying,
            instrument_type=instrument_type,
            expiry_date=expiry_date,
            strike_price=strike_price,
            option_type=opt_type,
            trade_date=trade_date,
            open=open_val,
            high=high_val,
            low=low_val,
            close=close_val,
            last_price=last_val,
            settlement_price=settle_val,
            previous_close=prev_val,
            volume_contracts=volume,
            turnover=turnover,
            open_interest=oi_val,
            change_in_open_interest=change_oi,
            underlying_value=underlying_val,
            is_no_trade=is_no_trade,
            raw_row=dict(row),
        )

    def _parse_date(self, val: str) -> date | None:
        if not val:
            return None
        val = val.strip()
        for fmt in (
            "%Y-%m-%d",
            "%d-%b-%Y",
            "%d-%B-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_decimal(self, val: str) -> Decimal | None:
        if not val:
            return None
        val = val.strip()
        if val in ("-", "XX", "N/A", "NA", ""):
            return None
        try:
            return Decimal(val)
        except (InvalidOperation, TypeError):
            return None

    def _parse_int(self, val: str) -> int | None:
        if not val:
            return None
        val = val.strip()
        if val in ("-", "XX", "N/A", "NA", ""):
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

    def _normalize_option_type(self, val: str) -> str | None:
        if not val:
            return None
        val = val.upper().strip()
        if val in ("CE", "CALL", "C"):
            return "CE"
        if val in ("PE", "PUT", "P"):
            return "PE"
        return None
