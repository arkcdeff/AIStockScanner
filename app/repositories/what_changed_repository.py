"""What Changed Repository — Point-in-Time Data Access Layer."""
from __future__ import annotations

from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bulk_block_deal import BulkBlockDeal
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.indicator_result import IndicatorResult
from app.models.institutional_flow import InstitutionalFlow
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock


class WhatChangedRepository:
    """Encapsulates deterministic, PIT-safe queries across all change-detection domains."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_stock_by_symbol(self, symbol: str) -> Stock | None:
        """Fetch stock entity by uppercase ticker symbol."""
        stmt = select(Stock).where(Stock.symbol == symbol.upper().strip())
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_latest_price_date(self, stock_id: int, max_date: date | None = None) -> date | None:
        """Get the most recent trading date with price data for stock, optionally capped at max_date."""
        stmt = select(DailyPrice.trade_date).where(DailyPrice.stock_id == stock_id)
        if max_date is not None:
            stmt = stmt.where(DailyPrice.trade_date <= max_date)
        stmt = stmt.order_by(desc(DailyPrice.trade_date)).limit(1)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_previous_trading_dates(
        self, stock_id: int, strictly_before: date, limit: int = 25
    ) -> list[date]:
        """Fetch chronological historical trading dates strictly prior to a given date."""
        stmt = (
            select(DailyPrice.trade_date)
            .where(DailyPrice.stock_id == stock_id, DailyPrice.trade_date < strictly_before)
            .order_by(desc(DailyPrice.trade_date))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_daily_price(self, stock_id: int, trade_date: date) -> DailyPrice | None:
        """Fetch single session DailyPrice record."""
        stmt = select(DailyPrice).where(
            DailyPrice.stock_id == stock_id, DailyPrice.trade_date == trade_date
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_indicator_result(self, stock_id: int, trade_date: date) -> IndicatorResult | None:
        """Fetch single session IndicatorResult record."""
        stmt = select(IndicatorResult).where(
            IndicatorResult.stock_id == stock_id, IndicatorResult.trade_date == trade_date
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_scan_results(
        self, stock_id: int, scan_date: date
    ) -> list[tuple[ScanResult, ScanDefinition]]:
        """Fetch matched scan results and definitions for a stock on a specific date."""
        stmt = (
            select(ScanResult, ScanDefinition)
            .join(ScanDefinition, ScanResult.scan_definition_id == ScanDefinition.id)
            .where(ScanResult.stock_id == stock_id, ScanResult.scan_date == scan_date)
            .order_by(ScanDefinition.code)
        )
        res = await self.session.execute(stmt)
        return list(res.all())

    async def get_bulk_block_deals(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        pit_cutoff: date,
    ) -> list[BulkBlockDeal]:
        """Fetch bulk/block deals whose availability falls in (start_date, end_date] and <= pit_cutoff."""
        stmt = (
            select(BulkBlockDeal)
            .where(
                BulkBlockDeal.symbol == symbol.upper().strip(),
                BulkBlockDeal.is_current.is_(True),
                BulkBlockDeal.available_at > start_date,
                BulkBlockDeal.available_at <= end_date,
                BulkBlockDeal.available_at <= pit_cutoff,
            )
            .order_by(BulkBlockDeal.trade_date.desc(), BulkBlockDeal.id.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_corporate_announcements(
        self,
        stock_id: int,
        start_date: date,
        end_date: date,
        pit_cutoff: date,
    ) -> list[CorporateAnnouncement]:
        """Fetch corporate announcements becoming available in (start_date, end_date] and <= pit_cutoff."""
        stmt = (
            select(CorporateAnnouncement)
            .where(
                CorporateAnnouncement.stock_id == stock_id,
                CorporateAnnouncement.available_at > start_date,
                CorporateAnnouncement.available_at <= end_date,
                CorporateAnnouncement.available_at <= pit_cutoff,
            )
            .order_by(CorporateAnnouncement.announcement_date.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_corporate_actions(
        self,
        stock_id: int,
        start_date: date,
        end_date: date,
        pit_cutoff: date,
    ) -> list[CorporateAction]:
        """Fetch corporate actions becoming available in (start_date, end_date] and <= pit_cutoff."""
        stmt = (
            select(CorporateAction)
            .where(
                CorporateAction.stock_id == stock_id,
                CorporateAction.available_at > start_date,
                CorporateAction.available_at <= end_date,
                CorporateAction.available_at <= pit_cutoff,
            )
            .order_by(CorporateAction.ex_date.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_institutional_flows(self, trade_date: date) -> list[InstitutionalFlow]:
        """Fetch institutional cash flow records for trade_date."""
        stmt = (
            select(InstitutionalFlow)
            .where(InstitutionalFlow.trade_date == trade_date)
            .order_by(InstitutionalFlow.investor_type)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
