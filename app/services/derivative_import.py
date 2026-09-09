"""Derivative Contract Import Service — Sprint 19.1D-1.

Orchestrates derivative contract master parsing, underlying identity resolution,
idempotent upsertion, and data quality issue logging.

Key Policies:
  - Strict Underlying Resolution: Resolves Index vs Stock without fabricating missing entries.
  - Non-fabrication: Unresolved underlying symbols generate DataQualityIssue (DQ_DERIVATIVE_UNKNOWN_UNDERLYING) and skip insertion.
  - Expired Contracts Retained: Contracts with expiry_date < available_at are imported with lifecycle_status='EXPIRED'.
  - Idempotency: Duplicate imports hit SHA-256 fingerprint deduplication without crashing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.derivative_parser import (
    DerivativeContractParser,
    DerivativeParseResult,
)
from app.data.exchange_constants import (
    DQ_DERIVATIVE_UNKNOWN_UNDERLYING,
    DQ_SEVERITY_WARNING,
    EXCHANGE_NSE,
)
from app.models.data_quality_issue import DataQualityIssue
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.repositories.derivative import DerivativeContractRepository


@dataclass
class DerivativeImportSummary:
    """Summary metrics of a derivative contract import execution."""

    total_rows: int
    imported_count: int
    skipped_count: int
    error_count: int
    unresolved_underlying_count: int
    quality_issues_count: int


class DerivativeContractImportService:
    """Service orchestrating derivative contract master file ingestion."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DerivativeContractRepository(session)
        self.parser = DerivativeContractParser()

    async def import_contract_master_file(
        self,
        content: str | bytes,
        as_of_date: date | None = None,
        source_name: str = "FO_CONTRACT_MASTER",
        source_reference: str | None = None,
        dry_run: bool = False,
    ) -> DerivativeImportSummary:
        """Parse and import derivative contracts from file content."""
        parse_result: DerivativeParseResult = self.parser.parse_csv(
            content=content,
            as_of_date=as_of_date,
            source_name=source_name,
        )

        imported_count = 0
        skipped_count = 0
        unresolved_underlying_count = 0
        quality_issues_count = 0

        # Pre-load known indices map (e.g. NIFTY, BANKNIFTY)
        index_map = await self._load_index_map()
        stock_cache: dict[str, int | None] = {}

        for parsed in parse_result.contracts:
            underlying_sym = parsed.underlying_symbol
            eff_available_at = parsed.available_at or as_of_date

            # Resolve Underlying Index vs Stock
            stock_id: int | None = None
            index_id: int | None = None

            if underlying_sym in index_map:
                index_id = index_map[underlying_sym]
            else:
                stock_id = await self._resolve_stock_underlying(
                    underlying_sym, eff_available_at, stock_cache
                )

            if stock_id is None and index_id is None:
                unresolved_underlying_count += 1
                skipped_count += 1
                if not dry_run:
                    dq_issue = DataQualityIssue(
                        issue_code=DQ_DERIVATIVE_UNKNOWN_UNDERLYING,
                        severity=DQ_SEVERITY_WARNING,
                        exchange=EXCHANGE_NSE,
                        symbol=underlying_sym,
                        trade_date=eff_available_at,
                        description=(
                            f"Derivative contract '{parsed.trading_symbol}' could not be resolved "
                            f"to any known Stock or MarketIndex for underlying '{underlying_sym}'."
                        ),
                        detected_at=datetime.now(),
                    )
                    self.session.add(dq_issue)
                    quality_issues_count += 1
                continue

            # Lifecycle status
            lifecycle = (
                "EXPIRED" if parsed.expiry_date < eff_available_at else "ACTIVE"
            )

            contract = DerivativeContract(
                exchange=EXCHANGE_NSE,
                segment="NFO",
                instrument_type=parsed.instrument_type,
                underlying_stock_id=stock_id,
                underlying_index_id=index_id,
                trading_symbol=parsed.trading_symbol,
                source_contract_id=parsed.source_contract_id,
                expiry_date=parsed.expiry_date,
                strike_price=parsed.strike_price,
                option_type=parsed.option_type,
                lot_size=parsed.lot_size,
                tick_size=parsed.tick_size,
                lifecycle_status=lifecycle,
                available_at=eff_available_at,
                source_name=source_name,
                source_reference=source_reference,
                fetched_at=datetime.now(),
            )

            if not dry_run:
                saved = await self.repo.add(contract)
                if saved.id:
                    imported_count += 1
                else:
                    skipped_count += 1
            else:
                imported_count += 1

        if not dry_run:
            await self.session.flush()

        return DerivativeImportSummary(
            total_rows=parse_result.total_rows,
            imported_count=imported_count,
            skipped_count=skipped_count + len(parse_result.row_errors),
            error_count=len(parse_result.row_errors),
            unresolved_underlying_count=unresolved_underlying_count,
            quality_issues_count=quality_issues_count,
        )

    async def _load_index_map(self) -> dict[str, int]:
        """Load mapping of MarketIndex symbols/codes to primary key IDs."""
        stmt = select(MarketIndex)
        result = await self.session.execute(stmt)
        indices = result.scalars().all()
        mapping = {idx.code.upper(): idx.id for idx in indices}
        for idx in indices:
            if idx.name:
                mapping.setdefault(idx.name.upper(), idx.id)
            if idx.provider_symbol:
                mapping.setdefault(idx.provider_symbol.upper(), idx.id)
        if "NIFTY_NEXT_50" in mapping:
            mapping["NIFTYNXT50"] = mapping["NIFTY_NEXT_50"]
        return mapping

    async def _resolve_stock_underlying(
        self, symbol: str, as_of_date: date, cache: dict[str, int | None]
    ) -> int | None:
        """Resolve a stock symbol to Stock.id via direct lookup or SecurityAlias."""
        if symbol in cache:
            return cache[symbol]

        # 1. Direct Stock lookup
        stmt_stock = select(Stock).where(
            Stock.exchange == EXCHANGE_NSE, Stock.symbol == symbol
        )
        res_stock = (await self.session.execute(stmt_stock)).scalar_one_or_none()
        if res_stock:
            cache[symbol] = res_stock.id
            return res_stock.id

        # 2. Point-in-time SecurityAlias lookup
        stmt_alias = select(SecurityAlias).where(
            SecurityAlias.exchange == EXCHANGE_NSE,
            SecurityAlias.symbol == symbol,
            SecurityAlias.available_at <= as_of_date,
            SecurityAlias.valid_from <= as_of_date,
            (SecurityAlias.valid_to.is_(None)) | (SecurityAlias.valid_to >= as_of_date),
        )
        res_alias = (await self.session.execute(stmt_alias)).scalars().first()
        if res_alias:
            cache[symbol] = res_alias.stock_id
            return res_alias.stock_id

        cache[symbol] = None
        return None
