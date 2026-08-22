"""Repositories for Sprint 19.1C historical data models.

Contains:
  - SecurityAliasRepository
  - CorporateActionRepository
  - IndexMembershipRepository
  - DataQualityIssueRepository
  - HistoricalStockRepository  (lifecycle-status queries on Stock)

All repositories follow the project pattern: receive an AsyncSession,
return model instances or None, raise nothing except DB errors.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.corporate_action import CorporateAction
from app.models.data_quality_issue import DataQualityIssue
from app.models.index_membership import IndexMembership
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock

# ---------------------------------------------------------------------------
# Security Alias Repository
# ---------------------------------------------------------------------------


class SecurityAliasRepository:
    """CRUD and point-in-time query for SecurityAlias records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, alias: SecurityAlias) -> SecurityAlias:
        self.session.add(alias)
        await self.session.flush()
        return alias

    async def get_by_id(self, alias_id: int) -> SecurityAlias | None:
        return await self.session.get(SecurityAlias, alias_id)

    async def resolve_as_of(
        self, exchange: str, symbol: str, as_of_date: date
    ) -> SecurityAlias | None:
        """Return the alias for (exchange, symbol) that was valid AND knowable at as_of_date.

        Applies point-in-time semantics:
          - valid_from <= as_of_date
          - valid_to IS NULL OR valid_to >= as_of_date
          - available_at <= as_of_date
        """
        stmt = (
            select(SecurityAlias)
            .where(SecurityAlias.exchange == exchange)
            .where(SecurityAlias.symbol == symbol)
            .where(SecurityAlias.valid_from <= as_of_date)
            .where(SecurityAlias.available_at <= as_of_date)
            .where(
                or_(
                    SecurityAlias.valid_to.is_(None),
                    SecurityAlias.valid_to >= as_of_date,
                )
            )
            .order_by(SecurityAlias.valid_from.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_stock(self, stock_id: int) -> list[SecurityAlias]:
        stmt = (
            select(SecurityAlias)
            .where(SecurityAlias.stock_id == stock_id)
            .order_by(SecurityAlias.valid_from.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_exact(
        self, exchange: str, symbol: str, valid_from: date
    ) -> SecurityAlias | None:
        """Fetch by unique key (exchange, symbol, valid_from)."""
        stmt = (
            select(SecurityAlias)
            .where(SecurityAlias.exchange == exchange)
            .where(SecurityAlias.symbol == symbol)
            .where(SecurityAlias.valid_from == valid_from)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_overlapping(
        self,
        exchange: str,
        symbol: str,
        valid_from: date,
        valid_to: date | None,
        exclude_id: int | None = None,
    ) -> list[SecurityAlias]:
        """Find any existing aliases for (exchange, symbol) that overlap the given interval."""
        stmt = (
            select(SecurityAlias)
            .where(SecurityAlias.exchange == exchange)
            .where(SecurityAlias.symbol == symbol)
            .where(SecurityAlias.valid_from <= (valid_to or date(9999, 12, 31)))
            .where(
                or_(
                    SecurityAlias.valid_to.is_(None),
                    SecurityAlias.valid_to >= valid_from,
                )
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(SecurityAlias.id != exclude_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())


# ---------------------------------------------------------------------------
# Corporate Action Repository
# ---------------------------------------------------------------------------


class CorporateActionRepository:
    """CRUD and point-in-time query for CorporateAction records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, action: CorporateAction) -> CorporateAction:
        if not getattr(action, "idempotency_hash", None):
            action.idempotency_hash = action.compute_fingerprint()

        # Application-level idempotency
        stmt = select(CorporateAction).where(CorporateAction.idempotency_hash == action.idempotency_hash)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        self.session.add(action)
        await self.session.flush()
        return action

    async def get_by_id(self, action_id: int) -> CorporateAction | None:
        return await self.session.get(CorporateAction, action_id)

    async def get_by_fingerprint(self, fingerprint: str) -> CorporateAction | None:
        """Fetch by deterministic fingerprint."""
        stmt = select(CorporateAction).where(CorporateAction.idempotency_hash == fingerprint)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_stock_as_of(
        self,
        stock_id: int,
        as_of_date: date,
        action_types: list[str] | None = None,
    ) -> list[CorporateAction]:
        """Return actions for a stock that were knowable at as_of_date.

        Ordered chronologically by ex_date ascending.
        """
        stmt = (
            select(CorporateAction)
            .where(CorporateAction.stock_id == stock_id)
            .where(CorporateAction.available_at <= as_of_date)
            .order_by(CorporateAction.ex_date.asc())
        )
        if action_types:
            stmt = stmt.where(CorporateAction.action_type.in_(action_types))
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_for_stock(self, stock_id: int) -> list[CorporateAction]:
        """Return all actions for a stock regardless of available_at."""
        stmt = (
            select(CorporateAction)
            .where(CorporateAction.stock_id == stock_id)
            .order_by(CorporateAction.ex_date.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())


# ---------------------------------------------------------------------------
# Index Membership Repository
# ---------------------------------------------------------------------------


class IndexMembershipRepository:
    """CRUD and point-in-time query for IndexMembership records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, membership: IndexMembership) -> IndexMembership:
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def get_by_id(self, membership_id: int) -> IndexMembership | None:
        return await self.session.get(IndexMembership, membership_id)

    async def get_exact(
        self, index_id: int, stock_id: int, valid_from: date
    ) -> IndexMembership | None:
        """Fetch by unique key (index_id, stock_id, valid_from)."""
        stmt = (
            select(IndexMembership)
            .where(IndexMembership.index_id == index_id)
            .where(IndexMembership.stock_id == stock_id)
            .where(IndexMembership.valid_from == valid_from)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_members_as_of(
        self, index_id: int, as_of_date: date
    ) -> list[IndexMembership]:
        """Return members of an index that are valid AND knowable at as_of_date.

        Survivorship-bias-safe: only returns members present in the index at T
        and whose membership was publicly announced/knowable at T.
        """
        stmt = (
            select(IndexMembership)
            .where(IndexMembership.index_id == index_id)
            .where(IndexMembership.valid_from <= as_of_date)
            .where(IndexMembership.available_at <= as_of_date)
            .where(
                or_(
                    IndexMembership.valid_to.is_(None),
                    IndexMembership.valid_to >= as_of_date,
                )
            )
            .order_by(IndexMembership.stock_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def find_overlapping(
        self,
        index_id: int,
        stock_id: int,
        valid_from: date,
        valid_to: date | None,
        exclude_id: int | None = None,
    ) -> list[IndexMembership]:
        """Find existing memberships that overlap the given validity interval."""
        stmt = (
            select(IndexMembership)
            .where(IndexMembership.index_id == index_id)
            .where(IndexMembership.stock_id == stock_id)
            .where(IndexMembership.valid_from <= (valid_to or date(9999, 12, 31)))
            .where(
                or_(
                    IndexMembership.valid_to.is_(None),
                    IndexMembership.valid_to >= valid_from,
                )
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(IndexMembership.id != exclude_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_for_stock(
        self, stock_id: int, index_id: int | None = None
    ) -> list[IndexMembership]:
        """Return all membership records for a stock, optionally filtered by index."""
        stmt = (
            select(IndexMembership)
            .where(IndexMembership.stock_id == stock_id)
            .order_by(IndexMembership.index_id, IndexMembership.valid_from)
        )
        if index_id is not None:
            stmt = stmt.where(IndexMembership.index_id == index_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())


# ---------------------------------------------------------------------------
# Data Quality Issue Repository
# ---------------------------------------------------------------------------


class DataQualityIssueRepository:
    """CRUD and query for DataQualityIssue records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, issue: DataQualityIssue) -> DataQualityIssue:
        self.session.add(issue)
        await self.session.flush()
        return issue

    async def get_by_id(self, issue_id: int) -> DataQualityIssue | None:
        return await self.session.get(DataQualityIssue, issue_id)

    async def list_open(
        self,
        exchange: str | None = None,
        trade_date: date | None = None,
        issue_code: str | None = None,
        severity: str | None = None,
    ) -> list[DataQualityIssue]:
        """Return unresolved data quality issues matching the given filters."""
        stmt = select(DataQualityIssue).where(DataQualityIssue.resolved.is_(False))
        if exchange is not None:
            stmt = stmt.where(DataQualityIssue.exchange == exchange)
        if trade_date is not None:
            stmt = stmt.where(DataQualityIssue.trade_date == trade_date)
        if issue_code is not None:
            stmt = stmt.where(DataQualityIssue.issue_code == issue_code)
        if severity is not None:
            stmt = stmt.where(DataQualityIssue.severity == severity)
        stmt = stmt.order_by(
            DataQualityIssue.detected_at.desc()
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_by_severity(
        self,
        exchange: str | None = None,
        trade_date: date | None = None,
    ) -> dict[str, int]:
        """Return {'warning': N, 'error': N} counts of open issues."""
        from sqlalchemy import func
        stmt = (
            select(DataQualityIssue.severity, func.count().label("cnt"))
            .where(DataQualityIssue.resolved.is_(False))
        )
        if exchange is not None:
            stmt = stmt.where(DataQualityIssue.exchange == exchange)
        if trade_date is not None:
            stmt = stmt.where(DataQualityIssue.trade_date == trade_date)
        stmt = stmt.group_by(DataQualityIssue.severity)
        result = await self.session.execute(stmt)
        counts: dict[str, int] = {"warning": 0, "error": 0}
        for severity, cnt in result:
            counts[severity] = int(cnt)
        return counts

    async def resolve(
        self,
        issue_id: int,
        resolution_notes: str | None = None,
    ) -> DataQualityIssue | None:
        """Mark an issue as resolved."""
        issue = await self.get_by_id(issue_id)
        if issue is None:
            return None
        issue.resolved = True
        issue.resolved_at = datetime.now(UTC)
        issue.resolution_notes = resolution_notes
        await self.session.flush()
        return issue


# ---------------------------------------------------------------------------
# Historical Stock Repository
# ---------------------------------------------------------------------------


class HistoricalStockRepository:
    """Lifecycle-status queries on the Stock table for historical research."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_universe_as_of(
        self,
        exchange: str,
        as_of_date: date,
        lifecycle_statuses: list[str] | None = None,
        series: list[str] | None = None,
    ) -> list[Stock]:
        """Return the equity universe for an exchange at a point in time.

        By default returns stocks with lifecycle_status 'active'.
        Caller may pass lifecycle_statuses=['active','suspended'] to
        include suspended stocks.

        NOTE: This is the historical-research universe. The existing EOD
        scanner universe (ScanRunner._fetch_indicator_rows) is UNCHANGED
        and does not use this method.
        """
        if lifecycle_statuses is None:
            lifecycle_statuses = ["active"]

        stmt = (
            select(Stock)
            .where(Stock.exchange == exchange)
            .where(Stock.lifecycle_status.in_(lifecycle_statuses))
        )
        if series:
            stmt = stmt.where(Stock.series.in_(series))
        stmt = stmt.order_by(Stock.symbol)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_by_isin_as_of(
        self, isin: str, as_of_date: date
    ) -> Stock | None:
        """Look up a stock by ISIN for historical identification."""
        stmt = select(Stock).where(Stock.isin == isin)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
