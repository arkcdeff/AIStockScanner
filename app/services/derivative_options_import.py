"""Derivative Options EOD Import Service — Sprint 19.1D-3.

Orchestrates parsing, canonical contract resolution, data quality auditing,
and bulk upserts for NSE Index & Stock Options EOD bhavcopies.

Features:
  - Canonical option contract resolution (exchange + underlying + OPTION + expiry + strike + CE/PE)
  - Options-only path: imports OPTION rows, explicitly skips FUTURE rows, and reports futures_skipped count cleanly.
  - Tracking of CE vs PE rows, index options vs stock options.
  - Decimal precision for strike prices (never float).
  - Detailed DataQualityIssue logging for unresolved option contracts or invalid rows.
  - Idempotent bulk upsert via generic DerivativeDailyPriceRepository.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.derivative_bhavcopy_parser import (
    DerivativeBhavcopyParser,
)
from app.data.exchange_constants import (
    DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT,
    DQ_SEVERITY_ERROR,
    DQ_SEVERITY_WARNING,
    EXCHANGE_NSE,
)
from app.models.data_quality_issue import DataQualityIssue
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.repositories.derivative import DerivativeContractRepository
from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository


@dataclass
class DerivativeOptionsImportSummary:
    """Summary metrics of an options EOD bhavcopy import execution."""

    total_rows: int
    options_rows: int
    futures_skipped: int
    imported_count: int
    updated_count: int
    unresolved_contracts: int
    invalid_rows: int
    quality_issues_count: int
    ce_rows: int
    pe_rows: int
    index_options_count: int
    stock_options_count: int
    duration_ms: float
    source_format: str = "NSE_FO_BHAVCOPY"


class DerivativeOptionsImportService:
    """Service to import NSE Options EOD daily price data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.parser = DerivativeBhavcopyParser()
        self.price_repo = DerivativeDailyPriceRepository(session)
        self.contract_repo = DerivativeContractRepository(session)

    async def import_options_bhavcopy(
        self,
        content: str | bytes,
        as_of_date: date | None = None,
        source_name: str = "FO_UDIFF_BHAVCOPY",
        source_reference: str | None = None,
        dry_run: bool = False,
    ) -> DerivativeOptionsImportSummary:
        """Parse and import NSE Options EOD bhavcopy data."""
        start_time = time.perf_counter()

        # 1. Parse CSV content
        parse_result = self.parser.parse_csv(content, trade_date_override=as_of_date)

        total_rows = parse_result.total_rows
        options_records = parse_result.options_records
        futures_skipped = parse_result.futures_skipped_count
        row_errors = parse_result.row_errors

        # 2. Build underlying symbol resolution maps
        index_map, stock_map = await self._build_underlying_maps()

        # 3. Build contract resolution cache for options
        contract_cache = await self._build_options_contract_cache()

        prices_to_upsert: list[DerivativeDailyPrice] = []
        unresolved_count = 0
        invalid_rows_count = len(row_errors)
        quality_issues: list[DataQualityIssue] = []

        ce_rows = 0
        pe_rows = 0
        index_options_count = 0
        stock_options_count = 0

        # Convert parse row errors to DataQualityIssues
        for err in row_errors:
            quality_issues.append(
                DataQualityIssue(
                    issue_code=err.issue_code,
                    severity=DQ_SEVERITY_ERROR,
                    exchange=EXCHANGE_NSE,
                    symbol=err.raw_row.get("SYMBOL") or err.raw_row.get("TCKRSYMB"),
                    trade_date=as_of_date,
                    description=f"Row {err.row_index}: {err.description}",
                    detected_at=datetime.now(),
                )
            )

        # 4. Resolve contract for each options record
        for record in options_records:
            symbol = record.underlying_symbol
            expiry = record.expiry_date
            strike = record.strike_price
            opt_type = record.option_type

            if opt_type == "CE":
                ce_rows += 1
            elif opt_type == "PE":
                pe_rows += 1

            # Determine underlying IDs
            stock_id: int | None = stock_map.get(symbol)
            index_id: int | None = index_map.get(symbol)
            if index_id is not None:
                stock_id = None

            if not stock_id and not index_id:
                # Prefix / alias normalization for indices
                if symbol in ("NIFTY 50", "NIFTY50"):
                    index_id = index_map.get("NIFTY")
                elif symbol in ("BANKNIFTY", "NIFTY BANK"):
                    index_id = index_map.get("BANKNIFTY")
                elif symbol in ("FINNIFTY", "NIFTY FIN SERVICE"):
                    index_id = index_map.get("FINNIFTY")
                elif symbol in ("MIDCPNIFTY", "NIFTY MID SELECT"):
                    index_id = index_map.get("MIDCPNIFTY")

            if index_id:
                index_options_count += 1
            elif stock_id:
                stock_options_count += 1

            if not stock_id and not index_id:
                unresolved_count += 1
                quality_issues.append(
                    DataQualityIssue(
                        issue_code=DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT,
                        severity=DQ_SEVERITY_WARNING,
                        exchange=EXCHANGE_NSE,
                        symbol=symbol,
                        trade_date=record.trade_date,
                        description=f"Unresolved option underlying '{symbol}' for expiry {expiry} strike {strike} {opt_type}",
                        detected_at=datetime.now(),
                    )
                )
                continue

            if strike is None or opt_type not in ("CE", "PE"):
                unresolved_count += 1
                quality_issues.append(
                    DataQualityIssue(
                        issue_code=DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT,
                        severity=DQ_SEVERITY_WARNING,
                        exchange=EXCHANGE_NSE,
                        symbol=symbol,
                        trade_date=record.trade_date,
                        description=f"Invalid strike ({strike}) or option_type ({opt_type}) for '{symbol}'",
                        detected_at=datetime.now(),
                    )
                )
                continue

            # Lookup matching DerivativeContract
            cache_key = (EXCHANGE_NSE, stock_id, index_id, "OPTION", expiry, strike, opt_type)
            contract_id = contract_cache.get(cache_key)

            if not contract_id:
                # On-demand DB query
                contract = await self.contract_repo.get_by_economic_identity(
                    exchange=EXCHANGE_NSE,
                    underlying_stock_id=stock_id,
                    underlying_index_id=index_id,
                    instrument_type="OPTION",
                    expiry_date=expiry,
                    strike_price=strike,
                    option_type=opt_type,
                )
                if contract:
                    contract_id = contract.id
                    contract_cache[cache_key] = contract_id

            if not contract_id:
                unresolved_count += 1
                quality_issues.append(
                    DataQualityIssue(
                        issue_code=DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT,
                        severity=DQ_SEVERITY_WARNING,
                        exchange=EXCHANGE_NSE,
                        symbol=symbol,
                        trade_date=record.trade_date,
                        description=f"No matching DerivativeContract for '{symbol}' {expiry} {strike} {opt_type}",
                        detected_at=datetime.now(),
                    )
                )
                continue

            available_at = record.trade_date if record.trade_date else (as_of_date or date.today())

            daily_price = DerivativeDailyPrice(
                contract_id=contract_id,
                trade_date=record.trade_date,
                open=record.open,
                high=record.high,
                low=record.low,
                close=record.close,
                last_price=record.last_price,
                settlement_price=record.settlement_price,
                previous_close=record.previous_close,
                volume_contracts=record.volume_contracts,
                turnover=record.turnover,
                open_interest=record.open_interest,
                change_in_open_interest=record.change_in_open_interest,
                underlying_value=record.underlying_value,
                available_at=available_at,
                source_name=source_name,
                source_reference=source_reference,
            )
            prices_to_upsert.append(daily_price)

        # 5. Persist quality issues
        for issue in quality_issues:
            self.session.add(issue)

        # 6. Execute bulk upsert if not dry_run
        imported_count = 0
        if not dry_run and prices_to_upsert:
            imported_count = await self.price_repo.bulk_upsert(prices_to_upsert)
            await self.session.commit()
        elif dry_run:
            imported_count = len(prices_to_upsert)

        duration = (time.perf_counter() - start_time) * 1000.0

        return DerivativeOptionsImportSummary(
            total_rows=total_rows,
            options_rows=len(options_records),
            futures_skipped=futures_skipped,
            imported_count=imported_count,
            updated_count=0,
            unresolved_contracts=unresolved_count,
            invalid_rows=invalid_rows_count,
            quality_issues_count=len(quality_issues),
            ce_rows=ce_rows,
            pe_rows=pe_rows,
            index_options_count=index_options_count,
            stock_options_count=stock_options_count,
            duration_ms=round(duration, 2),
        )

    async def _build_underlying_maps(self) -> tuple[dict[str, int], dict[str, int]]:
        """Map symbol strings to MarketIndex.id and Stock.id."""
        indices_res = await self.session.execute(select(MarketIndex))
        indices = indices_res.scalars().all()
        index_map: dict[str, int] = {
            index.code.upper(): index.id for index in indices
        }
        for index in indices:
            if index.name:
                index_map.setdefault(index.name.upper(), index.id)
            if index.provider_symbol:
                index_map.setdefault(index.provider_symbol.upper(), index.id)
        if "NIFTY_NEXT_50" in index_map:
            index_map["NIFTYNXT50"] = index_map["NIFTY_NEXT_50"]

        stocks_res = await self.session.execute(select(Stock.symbol, Stock.id))
        stock_map = {sym.upper(): stock_id for sym, stock_id in stocks_res.all()}

        alias_res = await self.session.execute(
            select(SecurityAlias.symbol, SecurityAlias.stock_id).where(SecurityAlias.exchange == EXCHANGE_NSE)
        )
        for alias_sym, stock_id in alias_res.all():
            if alias_sym.upper() not in stock_map:
                stock_map[alias_sym.upper()] = stock_id

        return index_map, stock_map

    async def _build_options_contract_cache(
        self,
    ) -> dict[tuple[str, int | None, int | None, str, date, Decimal, str], int]:
        """Cache all existing OPTION contracts."""
        stmt = select(
            DerivativeContract.id,
            DerivativeContract.exchange,
            DerivativeContract.underlying_stock_id,
            DerivativeContract.underlying_index_id,
            DerivativeContract.instrument_type,
            DerivativeContract.expiry_date,
            DerivativeContract.strike_price,
            DerivativeContract.option_type,
        ).where(DerivativeContract.instrument_type == "OPTION")

        res = await self.session.execute(stmt)
        cache = {}
        for row in res.all():
            c_id, ex, s_id, i_id, inst, exp, strike, opt_typ = row
            if strike is not None and opt_typ is not None:
                cache[(ex, s_id, i_id, inst, exp, Decimal(str(strike)), opt_typ)] = c_id

        return cache
