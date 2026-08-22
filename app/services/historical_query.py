"""Historical Query Service — point-in-time historical data access.

Provides:
  - HistoricalQueryService: all point-in-time research queries
  - HistoricalCoverageService: coverage and quality summary queries

Point-in-time contract:
  At evaluation time T, do not expose information with available_at > T.
  All methods that accept as_of_date apply this gate strictly.

Survivorship bias:
  - get_universe_as_of() uses IndexMembership (if available) or lifecycle_status
    to exclude stocks that were not yet listed or had been delisted by T.
  - The current EOD scanner universe is UNCHANGED — it uses ScanRunner and is
    separate from this service.

Adjustment semantics:
  - Raw prices (get_price_history) are never modified.
  - Adjusted prices (get_adjusted_price_history) are computed on demand using
    PriceAdjustmentService with only corporate actions knowable at as_of_date.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.exchange_constants import ADJUSTABLE_ACTION_TYPES
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.data_quality_issue import DataQualityIssue
from app.models.index_membership import IndexMembership
from app.models.stock import Stock
from app.repositories.historical import (
    CorporateActionRepository,
    DataQualityIssueRepository,
    HistoricalStockRepository,
    IndexMembershipRepository,
    SecurityAliasRepository,
)
from app.services.price_adjustment import AdjustedPrice, PriceAdjustmentService


class HistoricalQueryService:
    """Point-in-time historical data access service.

    All methods that accept `as_of_date` apply strict point-in-time semantics:
    only data with `available_at <= as_of_date` is returned.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._alias_repo = SecurityAliasRepository(session)
        self._ca_repo = CorporateActionRepository(session)
        self._im_repo = IndexMembershipRepository(session)
        self._stock_repo = HistoricalStockRepository(session)

    # ------------------------------------------------------------------
    # Security identity
    # ------------------------------------------------------------------

    async def resolve_symbol_as_of(
        self, exchange: str, symbol: str, as_of_date: date
    ) -> Stock | None:
        """Resolve a historical (exchange, symbol) to a current Stock entity.

        First attempts resolution via SecurityAlias (handles symbol changes).
        Falls back to direct Stock lookup if no alias exists.

        Returns None if no stock can be found for the given identity at T.
        """
        alias = await self._alias_repo.resolve_as_of(exchange, symbol, as_of_date)
        if alias is not None:
            return await self.session.get(Stock, alias.stock_id)

        # Fallback: direct lookup (works for stocks that have always had the same symbol)
        stmt = (
            select(Stock)
            .where(Stock.exchange == exchange)
            .where(Stock.symbol == symbol)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_security_as_of(
        self, exchange: str, symbol: str, as_of_date: date
    ) -> dict[str, Any] | None:
        """Return security identity details knowable at as_of_date.

        Returns a dictionary with security details or None if not found.
        """
        stock = await self.resolve_symbol_as_of(exchange, symbol, as_of_date)
        if stock is None:
            return None

        # Also look up the active alias for this (exchange, symbol) at T
        alias = await self._alias_repo.resolve_as_of(exchange, symbol, as_of_date)

        return {
            "stock_id": stock.id,
            "current_symbol": stock.symbol,
            "company_name": stock.company_name,
            "exchange": stock.exchange,
            "isin": alias.isin if alias else stock.isin,
            "series": alias.series if alias else stock.series,
            "lifecycle_status": stock.lifecycle_status,
            "is_active": stock.is_active,
            "as_of_date": as_of_date.isoformat(),
            "resolved_via_alias": alias is not None,
        }

    # ------------------------------------------------------------------
    # Price history
    # ------------------------------------------------------------------

    async def get_price_history(
        self,
        stock_id: int,
        start_date: date,
        end_date: date,
    ) -> list[DailyPrice]:
        """Return raw (unadjusted) DailyPrice rows for a stock.

        Rows are ordered by trade_date ascending.
        Raw prices are NEVER modified by this method.
        """
        stmt = (
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date >= start_date)
            .where(DailyPrice.trade_date <= end_date)
            .order_by(DailyPrice.trade_date.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_adjusted_price_history(
        self,
        stock_id: int,
        start_date: date,
        end_date: date,
        as_of_date: date,
    ) -> list[AdjustedPrice]:
        """Return backward-adjusted DailyPrice rows for a stock.

        Only corporate actions with available_at <= as_of_date are applied
        (point-in-time: no look-ahead into future announcements).

        Adjustment only covers split and bonus (see PriceAdjustmentService).
        Dividend / rights / merger adjustments are deferred.
        Raw DailyPrice rows are NEVER modified.
        """
        price_rows = await self.get_price_history(stock_id, start_date, end_date)
        if not price_rows:
            return []

        # Load only adjustable action types knowable at as_of_date
        ca_rows = await self._ca_repo.list_for_stock_as_of(
            stock_id,
            as_of_date=as_of_date,
            action_types=list(ADJUSTABLE_ACTION_TYPES),
        )

        factors = PriceAdjustmentService.compute_cumulative_factors(
            ca_rows, as_of_date=as_of_date
        )

        return PriceAdjustmentService.adjust_price_series(price_rows, factors)

    # ------------------------------------------------------------------
    # Corporate actions
    # ------------------------------------------------------------------

    async def get_corporate_actions(
        self,
        stock_id: int,
        as_of_date: date,
        action_types: list[str] | None = None,
    ) -> list[CorporateAction]:
        """Return corporate actions for a stock knowable at as_of_date.

        Applies available_at <= as_of_date filter (no look-ahead).
        """
        return await self._ca_repo.list_for_stock_as_of(
            stock_id, as_of_date=as_of_date, action_types=action_types
        )

    # ------------------------------------------------------------------
    # Index membership
    # ------------------------------------------------------------------

    async def get_index_members(
        self, index_id: int, as_of_date: date
    ) -> list[IndexMembership]:
        """Return index members valid AND knowable at as_of_date.

        Survivorship-bias-safe: excludes stocks that were added after T
        or whose membership was not yet announced at T.
        """
        return await self._im_repo.get_members_as_of(index_id, as_of_date)

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------

    async def get_universe_as_of(
        self,
        as_of_date: date,
        exchange: str = "NSE",
        series: list[str] | None = None,
        index_id: int | None = None,
    ) -> list[Stock]:
        """Return the eligible stock universe at a point in time.

        Strategy:
          1. If index_id is provided, return members of that index at T
             (survivorship-bias-safe via IndexMembership.available_at).
          2. Otherwise, return all stocks on the exchange with
             lifecycle_status 'active' at T (coarser; no explicit survivorship
             bias correction, but better than using today's universe).

        NOTE: The existing EOD scanner universe (ScanRunner._fetch_indicator_rows)
        is UNCHANGED and does NOT use this method.  This is strictly for
        historical research queries.
        """
        if index_id is not None:
            memberships = await self._im_repo.get_members_as_of(index_id, as_of_date)
            stock_ids = [m.stock_id for m in memberships]
            if not stock_ids:
                return []
            stmt = (
                select(Stock)
                .where(Stock.id.in_(stock_ids))
                .order_by(Stock.symbol)
            )
            result = await self.session.execute(stmt)
            return list(result.scalars())

        return await self._stock_repo.get_universe_as_of(
            exchange=exchange,
            as_of_date=as_of_date,
            lifecycle_statuses=["active"],
            series=series,
        )

    # ------------------------------------------------------------------
    # Data quality
    # ------------------------------------------------------------------

    async def get_data_quality_issues(
        self,
        exchange: str | None = None,
        trade_date: date | None = None,
        resolved: bool = False,
    ) -> list[DataQualityIssue]:
        """Return data quality issues, optionally filtered by exchange and date."""
        repo = DataQualityIssueRepository(self.session)
        if resolved:
            # Return all (open + closed) — less common query
            stmt = select(DataQualityIssue)
            if exchange:
                stmt = stmt.where(DataQualityIssue.exchange == exchange)
            if trade_date:
                stmt = stmt.where(DataQualityIssue.trade_date == trade_date)
            stmt = stmt.order_by(DataQualityIssue.detected_at.desc())
            result = await self.session.execute(stmt)
            return list(result.scalars())
        return await repo.list_open(exchange=exchange, trade_date=trade_date)

    # ------------------------------------------------------------------
    # Derivatives Master
    # ------------------------------------------------------------------

    async def get_derivative_contracts(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        instrument_type: str | None = None,
        expiry_date: date | None = None,
        as_of_date: date | None = None,
        include_expired: bool = True,
    ) -> list[Any]:
        """Return derivative contracts matching point-in-time cutoff filters."""
        from app.repositories.derivative import DerivativeContractRepository

        repo = DerivativeContractRepository(self.session)
        return await repo.list_contracts(
            underlying_stock_id=underlying_stock_id,
            underlying_index_id=underlying_index_id,
            instrument_type=instrument_type,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            include_expired=include_expired,
        )

    # ------------------------------------------------------------------
    # Derivative Daily Prices (Futures) — 19.1D-2
    # ------------------------------------------------------------------

    async def get_futures_history(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        expiry_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
    ) -> list[tuple[Any, Any]]:
        """Return point-in-time safe historical futures prices with contract details."""
        from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository

        repo = DerivativeDailyPriceRepository(self.session)
        return await repo.get_futures_history(
            underlying_stock_id=underlying_stock_id,
            underlying_index_id=underlying_index_id,
            expiry_date=expiry_date,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
        )

    async def get_futures_curve(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        trade_date: date = date.today(),
        as_of_date: date | None = None,
    ) -> list[tuple[Any, Any]]:
        """Return active futures curve (all expiries and price facts) for an underlying on trade_date."""
        from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository

        repo = DerivativeDailyPriceRepository(self.session)
        return await repo.get_futures_curve(
            underlying_stock_id=underlying_stock_id,
            underlying_index_id=underlying_index_id,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )

    # ------------------------------------------------------------------
    # Derivative Daily Prices (Options) — 19.1D-3
    # ------------------------------------------------------------------

    async def get_options_for_expiry(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        expiry_date: date | None = None,
        trade_date: date = date.today(),
        as_of_date: date | None = None,
    ) -> list[tuple[Any, Any]]:
        """Return raw strike ladder (prices & contracts) for an underlying on trade_date."""
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice

        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
            .where(DerivativeDailyPrice.trade_date == trade_date)
        )

        if underlying_stock_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if underlying_index_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)
        if expiry_date is not None:
            stmt = stmt.where(DerivativeContract.expiry_date == expiry_date)

        as_of = as_of_date or trade_date
        stmt = stmt.where(DerivativeDailyPrice.available_at <= as_of)
        stmt = stmt.where(DerivativeContract.available_at <= as_of)

        stmt = stmt.order_by(
            DerivativeContract.strike_price.asc(),
            DerivativeContract.option_type.asc(),
        )

        res = await self.session.execute(stmt)
        return list(res.all())


class HistoricalCoverageService:
    """Coverage and quality summary queries for the historical data platform.

    Extends the existing MarketDataService coverage (which covers raw bhavcopy
    import completeness) with additional coverage summaries for corporate
    actions, index membership, and data quality.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_corporate_action_coverage(self, stock_id: int) -> dict[str, Any]:
        """Return summary of corporate actions available for a stock."""
        stmt = (
            select(
                func.count().label("total"),
                func.min(CorporateAction.ex_date).label("earliest_ex"),
                func.max(CorporateAction.ex_date).label("latest_ex"),
            )
            .where(CorporateAction.stock_id == stock_id)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "stock_id": stock_id,
            "total_actions": int(row.total or 0),
            "earliest_ex_date": row.earliest_ex.isoformat() if row.earliest_ex else None,
            "latest_ex_date": row.latest_ex.isoformat() if row.latest_ex else None,
        }

    async def get_index_membership_coverage(self, index_id: int) -> dict[str, Any]:
        """Return summary of index membership records for an index."""
        stmt = (
            select(
                func.count().label("total"),
                func.count(IndexMembership.valid_to.is_(None)).label("current_members"),
                func.min(IndexMembership.valid_from).label("earliest_from"),
                func.max(IndexMembership.valid_from).label("latest_from"),
            )
            .where(IndexMembership.index_id == index_id)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "index_id": index_id,
            "total_membership_records": int(row.total or 0),
            "earliest_membership": row.earliest_from.isoformat() if row.earliest_from else None,
            "latest_membership_change": row.latest_from.isoformat() if row.latest_from else None,
        }

    async def get_data_quality_summary(
        self,
        exchange: str | None = None,
        trade_date: date | None = None,
    ) -> dict[str, Any]:
        """Return data quality issue counts by severity."""
        repo = DataQualityIssueRepository(self.session)
        counts = await repo.count_by_severity(exchange=exchange, trade_date=trade_date)
        return {
            "exchange": exchange,
            "trade_date": trade_date.isoformat() if trade_date else None,
            "open_warnings": counts.get("warning", 0),
            "open_errors": counts.get("error", 0),
            "total_open": counts.get("warning", 0) + counts.get("error", 0),
        }

    async def get_historical_coverage(
        self,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Return stock-level price data coverage across a date range.

        Counts how many distinct stocks have any DailyPrice row within
        [start_date, end_date] for the given exchange.
        """
        stmt = (
            select(
                func.count(func.distinct(DailyPrice.stock_id)).label("stocks_with_data"),
                func.count(DailyPrice.id).label("total_price_rows"),
                func.min(DailyPrice.trade_date).label("earliest_date"),
                func.max(DailyPrice.trade_date).label("latest_date"),
            )
            .join(Stock, Stock.id == DailyPrice.stock_id)
            .where(Stock.exchange == exchange)
            .where(DailyPrice.trade_date >= start_date)
            .where(DailyPrice.trade_date <= end_date)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "exchange": exchange,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "stocks_with_data": int(row.stocks_with_data or 0),
            "total_price_rows": int(row.total_price_rows or 0),
            "earliest_date": row.earliest_date.isoformat() if row.earliest_date else None,
            "latest_date": row.latest_date.isoformat() if row.latest_date else None,
        }

    async def get_derivative_contract_coverage(
        self,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        """Return summary metrics for derivative contracts master coverage."""
        from app.models.derivative_contract import DerivativeContract

        stmt = select(
            func.count().label("total_contracts"),
            func.count(func.nullif(DerivativeContract.instrument_type == "FUTURE", False)).label("futures_count"),
            func.count(func.nullif(DerivativeContract.instrument_type == "OPTION", False)).label("options_count"),
            func.count(func.distinct(DerivativeContract.underlying_stock_id)).label("stock_underlyings"),
            func.count(func.distinct(DerivativeContract.underlying_index_id)).label("index_underlyings"),
            func.min(DerivativeContract.expiry_date).label("earliest_expiry"),
            func.max(DerivativeContract.expiry_date).label("latest_expiry"),
        )
        if as_of_date is not None:
            stmt = stmt.where(DerivativeContract.available_at <= as_of_date)

        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
            "total_contracts": int(row.total_contracts or 0),
            "futures_count": int(row.futures_count or 0),
            "options_count": int(row.options_count or 0),
            "stock_underlyings": int(row.stock_underlyings or 0),
            "index_underlyings": int(row.index_underlyings or 0),
            "earliest_expiry": row.earliest_expiry.isoformat() if row.earliest_expiry else None,
            "latest_expiry": row.latest_expiry.isoformat() if row.latest_expiry else None,
        }

    async def get_futures_price_coverage(
        self,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        """Return summary metrics for derivative daily prices (futures) coverage."""
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice

        stmt = (
            select(
                func.count(DerivativeDailyPrice.id).label("total_price_rows"),
                func.count(func.distinct(DerivativeDailyPrice.contract_id)).label("contracts_with_prices"),
                func.min(DerivativeDailyPrice.trade_date).label("earliest_trade_date"),
                func.max(DerivativeDailyPrice.trade_date).label("latest_trade_date"),
                func.sum(DerivativeDailyPrice.volume_contracts).label("total_contracts_traded"),
            )
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "FUTURE")
        )
        if as_of_date is not None:
            stmt = stmt.where(DerivativeDailyPrice.available_at <= as_of_date)

        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
            "total_price_rows": int(row.total_price_rows or 0),
            "contracts_with_prices": int(row.contracts_with_prices or 0),
            "earliest_trade_date": row.earliest_trade_date.isoformat() if row.earliest_trade_date else None,
            "latest_trade_date": row.latest_trade_date.isoformat() if row.latest_trade_date else None,
            "total_contracts_traded": int(row.total_contracts_traded or 0),
        }

    async def get_options_price_coverage(
        self,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        """Return summary metrics for derivative daily prices (options) coverage."""
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice

        stmt = (
            select(
                func.count(DerivativeDailyPrice.id).label("total_price_rows"),
                func.count(func.distinct(DerivativeDailyPrice.contract_id)).label("contracts_with_prices"),
                func.count(func.nullif(DerivativeContract.option_type == "CE", False)).label("ce_price_rows"),
                func.count(func.nullif(DerivativeContract.option_type == "PE", False)).label("pe_price_rows"),
                func.min(DerivativeDailyPrice.trade_date).label("earliest_trade_date"),
                func.max(DerivativeDailyPrice.trade_date).label("latest_trade_date"),
                func.sum(DerivativeDailyPrice.volume_contracts).label("total_contracts_traded"),
            )
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
        )
        if as_of_date is not None:
            stmt = stmt.where(DerivativeDailyPrice.available_at <= as_of_date)

        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
            "total_price_rows": int(row.total_price_rows or 0),
            "contracts_with_prices": int(row.contracts_with_prices or 0),
            "ce_price_rows": int(row.ce_price_rows or 0),
            "pe_price_rows": int(row.pe_price_rows or 0),
            "earliest_trade_date": row.earliest_trade_date.isoformat() if row.earliest_trade_date else None,
            "latest_trade_date": row.latest_trade_date.isoformat() if row.latest_trade_date else None,
            "total_contracts_traded": int(row.total_contracts_traded or 0),
        }


