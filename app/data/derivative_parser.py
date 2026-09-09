"""Derivative Contract Master File Parser — Sprint 19.1D-1.

Parses official NSE F&O contract master CSV files (standard legacy format,
UDiFF format, and generic/custom formats) into validated contract dataclasses.

Features:
  - Header & format auto-detection
  - Robust date parsing (YYYY-MM-DD, DD-MMM-YYYY)
  - Decimal strike conversion (never float)
  - Instrument & option type normalization (FUTURE / OPTION, CE / PE)
  - Detailed per-row error capture without silent corruption
  - Standard error codes: DERIVATIVE_INVALID_EXPIRY, DERIVATIVE_INVALID_STRIKE, etc.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.data.exchange_constants import (
    DQ_DERIVATIVE_INVALID_EXPIRY,
    DQ_DERIVATIVE_INVALID_LOT_SIZE,
    DQ_DERIVATIVE_INVALID_OPTION_TYPE,
    DQ_DERIVATIVE_INVALID_STRIKE,
    DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
)


class DerivativeParserError(Exception):
    """Raised when contract master file format cannot be parsed at all."""
    pass


@dataclass(frozen=True)
class ParsedDerivativeContract:
    """A validated derivative contract parsed from source data."""

    trading_symbol: str
    underlying_symbol: str
    instrument_type: str  # 'FUTURE' or 'OPTION'
    expiry_date: date
    strike_price: Decimal | None
    option_type: str | None  # 'CE', 'PE', or None
    lot_size: int
    tick_size: Decimal | None
    source_contract_id: str | None
    available_at: date
    raw_row: dict[str, Any]


@dataclass(frozen=True)
class ParsedDerivativeRowError:
    """An error recorded for a specific row during contract parsing."""

    row_index: int
    issue_code: str
    description: str
    raw_row: dict[str, Any]


@dataclass
class DerivativeParseResult:
    """Result of parsing a derivative contract master file."""

    contracts: list[ParsedDerivativeContract]
    row_errors: list[ParsedDerivativeRowError]
    total_rows: int


class DerivativeContractParser:
    """Parser for NSE F&O contract master files."""

    def parse_csv(
        self,
        content: str | bytes,
        as_of_date: date,
        source_name: str = "NSE_FO_CONTRACT_MASTER",
    ) -> DerivativeParseResult:
        """Parse CSV text or bytes into a DerivativeParseResult.

        R4: as_of_date is required — no date.today() fallback.
        """
        if as_of_date is None:
            raise DerivativeParserError("as_of_date is required; refusing to guess via date.today()")
        if isinstance(content, bytes):
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("latin-1")
        else:
            text_content = content

        reader = csv.DictReader(io.StringIO(text_content))
        if not reader.fieldnames:
            raise DerivativeParserError("UNSUPPORTED_FORMAT: Empty file or no CSV header found.")

        # Map headers
        field_map = self._detect_columns(reader.fieldnames)

        contracts: list[ParsedDerivativeContract] = []
        row_errors: list[ParsedDerivativeRowError] = []
        total_rows = 0

        default_available_at = as_of_date

        for idx, row in enumerate(reader, start=1):
            total_rows += 1
            try:
                parsed = self._parse_row(row, idx, field_map, default_available_at)
                if isinstance(parsed, ParsedDerivativeContract):
                    contracts.append(parsed)
                elif isinstance(parsed, ParsedDerivativeRowError):
                    row_errors.append(parsed)
            except Exception as exc:
                row_errors.append(
                    ParsedDerivativeRowError(
                        row_index=idx,
                        issue_code=DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
                        description=f"Unhandled error parsing row: {str(exc)}",
                        raw_row=dict(row),
                    )
                )

        return DerivativeParseResult(
            contracts=contracts,
            row_errors=row_errors,
            total_rows=total_rows,
        )

    def _detect_columns(self, fieldnames: list[str]) -> dict[str, str]:
        """Detect and map header names to internal field roles."""
        normalized = {f.strip().upper(): f for f in fieldnames}

        role_candidates = {
            "underlying": ["UNDERLYING", "SYMBOL", "TCKRSYMB", "ASSET_NAME", "SECURITY"],
            "trading_symbol": ["TRADING_SYMBOL", "CONTRACT_SYMBOL", "SYMBOL_NAME", "FININSTRMNM", "SYMBOL"],
            "instrument": ["INSTRUMENT", "INSTRUMENT_TYPE", "FININSTRMTP", "INST_TYPE"],
            "expiry": ["EXPIRY_DT", "EXPIRY", "XPRYDT", "EXPIRY_DATE"],
            "strike": ["STRIKE_PR", "STRIKE", "STRKPRIC", "STRIKE_PRICE"],
            "option_type": ["OPTION_TYP", "OPTION_TYPE", "OPTNTP"],
            "lot_size": ["MARKET_LOT", "LOT_SIZE", "LOTSZ", "LOT_SZ", "NEWBRDLOTQTY"],
            "token": ["TOKEN", "CONTRACT_ID", "FININSTRMID", "ID"],
            "tick_size": ["TICK_SIZE", "TCKSZ"],
            "available_at": ["AVAILABLE_AT", "RPTDT", "TRADDT", "DATE"],
        }

        field_map = {}
        for role, candidates in role_candidates.items():
            found = None
            for cand in candidates:
                if cand in normalized:
                    found = normalized[cand]
                    break
            if found:
                field_map[role] = found

        # Ensure required roles exist
        required_roles = ["underlying", "expiry", "lot_size"]
        missing = [r for r in required_roles if r not in field_map]
        if missing:
            raise DerivativeParserError(
                f"UNSUPPORTED_FORMAT: Missing required CSV columns for roles: {missing}. Header: {fieldnames}"
            )

        return field_map

    def _parse_row(
        self,
        row: dict[str, str],
        idx: int,
        field_map: dict[str, str],
        default_available_at: date,
    ) -> ParsedDerivativeContract | ParsedDerivativeRowError:
        """Parse a single row into a contract object or error."""
        # 1. Underlying symbol
        underlying_raw = row.get(field_map["underlying"], "").strip().upper()
        if not underlying_raw:
            return ParsedDerivativeRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
                description="Missing underlying symbol",
                raw_row=dict(row),
            )

        # 2. Expiry date
        expiry_raw = row.get(field_map["expiry"], "").strip()
        expiry_date = self._parse_date(expiry_raw)
        if not expiry_date:
            return ParsedDerivativeRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_INVALID_EXPIRY,
                description=f"Invalid expiry date: '{expiry_raw}'",
                raw_row=dict(row),
            )

        # 3. Lot size
        lot_size_raw = row.get(field_map["lot_size"], "").strip()
        try:
            lot_size = int(float(lot_size_raw))
            if lot_size <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return ParsedDerivativeRowError(
                row_index=idx,
                issue_code=DQ_DERIVATIVE_INVALID_LOT_SIZE,
                description=f"Invalid or non-positive lot size: '{lot_size_raw}'",
                raw_row=dict(row),
            )

        # 4. Instrument & Option Type
        inst_raw = row.get(field_map.get("instrument", ""), "").strip().upper()
        opt_raw = row.get(field_map.get("option_type", ""), "").strip().upper()

        opt_type = self._normalize_option_type(opt_raw)
        instrument_type = self._determine_instrument_type(inst_raw, opt_type)

        # 5. Strike Price
        strike_raw = row.get(field_map.get("strike", ""), "").strip()
        strike_price: Decimal | None = None
        if strike_raw and strike_raw not in ("0", "0.00", "0.0", "XX", "-"):
            try:
                strike_price = Decimal(strike_raw)
            except (InvalidOperation, TypeError):
                return ParsedDerivativeRowError(
                    row_index=idx,
                    issue_code=DQ_DERIVATIVE_INVALID_STRIKE,
                    description=f"Invalid strike price format: '{strike_raw}'",
                    raw_row=dict(row),
                )

        # 6. Validate Futures vs Options Invariants
        if instrument_type == "FUTURE":
            if strike_price is not None and strike_price > 0:
                # Force strike to None for Futures
                strike_price = None
            opt_type = None
        elif instrument_type == "OPTION":
            if opt_type not in ("CE", "PE"):
                return ParsedDerivativeRowError(
                    row_index=idx,
                    issue_code=DQ_DERIVATIVE_INVALID_OPTION_TYPE,
                    description=f"Option contract requires option_type 'CE' or 'PE', got '{opt_raw}'",
                    raw_row=dict(row),
                )
            if strike_price is None or strike_price <= 0:
                return ParsedDerivativeRowError(
                    row_index=idx,
                    issue_code=DQ_DERIVATIVE_INVALID_STRIKE,
                    description=f"Option contract requires positive strike_price, got '{strike_raw}'",
                    raw_row=dict(row),
                )

        # 7. Token / Source ID
        source_id = row.get(field_map.get("token", ""), "").strip() or None

        # 8. Tick size
        tick_size_raw = row.get(field_map.get("tick_size", ""), "").strip()
        tick_size = Decimal("0.05")
        if tick_size_raw:
            try:
                tick_size = Decimal(tick_size_raw)
            except (InvalidOperation, TypeError):
                pass

        # 9. Available At
        available_raw = row.get(field_map.get("available_at", ""), "").strip()
        available_at = self._parse_date(available_raw) or default_available_at

        # 10. Trading Symbol
        trading_sym = row.get(field_map.get("trading_symbol", ""), "").strip().upper()
        if not trading_sym or trading_sym == underlying_raw:
            trading_sym = self._construct_trading_symbol(
                underlying_raw, instrument_type, expiry_date, strike_price, opt_type
            )

        return ParsedDerivativeContract(
            trading_symbol=trading_sym,
            underlying_symbol=underlying_raw,
            instrument_type=instrument_type,
            expiry_date=expiry_date,
            strike_price=strike_price,
            option_type=opt_type,
            lot_size=lot_size,
            tick_size=tick_size,
            source_contract_id=source_id,
            available_at=available_at,
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

    def _normalize_option_type(self, val: str) -> str | None:
        if not val:
            return None
        val = val.upper().strip()
        if val in ("CE", "CALL", "C"):
            return "CE"
        if val in ("PE", "PUT", "P"):
            return "PE"
        return None

    def _determine_instrument_type(self, inst_raw: str, opt_type: str | None) -> str:
        if "OPT" in inst_raw or opt_type in ("CE", "PE"):
            return "OPTION"
        return "FUTURE"

    def _construct_trading_symbol(
        self,
        underlying: str,
        inst_type: str,
        expiry: date,
        strike: Decimal | None,
        opt_type: str | None,
    ) -> str:
        exp_str = expiry.strftime("%d%b%Y").upper()
        if inst_type == "FUTURE":
            return f"{underlying}{exp_str}FUT"
        strike_str = f"{strike.normalize():f}" if strike else ""
        return f"{underlying}{exp_str}{strike_str}{opt_type or ''}"
