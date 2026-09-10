"""Repository for Cash Market Bulk and Block Deals.

Provides point-in-time safe queries, bounded range fetching, pagination,
summary aggregations, and amendment-aware ingestion persistence.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bulk_block_deal import BulkBlockDeal


class BulkBlockDealRepository:
    """Repository managing Bulk & Block Deals persistence and point-in-time queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_deals(
        self,
        trade_date_from: date | None = None,
        trade_date_to: date | None = None,
        deal_type: str | None = None,
        buy_sell: str | None = None,
        symbol: str | None = None,
        stock_id: int | None = None,
        client_name: str | None = None,
        as_of_date: date | None = None,
        is_current_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[BulkBlockDeal], int]:
        """Fetch paginated bulk/block deals with optional filters and strict PIT filtering."""
        query = select(BulkBlockDeal)

        if is_current_only:
            query = query.where(BulkBlockDeal.is_current.is_(True))

        if as_of_date is not None:
            # Strict PIT: deal occurred on or before as_of_date and became publicly available on or before as_of_date
            query = query.where(
                BulkBlockDeal.trade_date <= as_of_date,
                BulkBlockDeal.available_at <= as_of_date,
            )

        if trade_date_from is not None:
            query = query.where(BulkBlockDeal.trade_date >= trade_date_from)
        if trade_date_to is not None:
            query = query.where(BulkBlockDeal.trade_date <= trade_date_to)

        if deal_type and deal_type.strip():
            query = query.where(BulkBlockDeal.deal_type == deal_type.strip().upper())
        if buy_sell and buy_sell.strip():
            query = query.where(BulkBlockDeal.buy_sell == buy_sell.strip().upper())
        if symbol and symbol.strip():
            query = query.where(BulkBlockDeal.symbol == symbol.strip().upper())
        if stock_id is not None:
            query = query.where(BulkBlockDeal.stock_id == stock_id)
        if client_name and client_name.strip():
            query = query.where(BulkBlockDeal.client_name.ilike(f"%{client_name.strip()}%"))

        # Count total matching rows
        count_stmt = select(func.count()).select_from(query.subquery())
        count_res = await self.session.execute(count_stmt)
        total_count = count_res.scalar_one()

        # Deterministic ordering
        query = query.order_by(BulkBlockDeal.trade_date.desc(), BulkBlockDeal.id.desc())

        # Pagination
        offset = max(0, (page - 1) * page_size)
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total_count

    async def get_deals_for_stock(
        self,
        stock_id: int | None = None,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        deal_type: str | None = None,
        as_of_date: date | None = None,
        is_current_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[BulkBlockDeal], int]:
        """Fetch paginated deals for a specific stock (by stock_id or symbol)."""
        return await self.get_deals(
            trade_date_from=start_date,
            trade_date_to=end_date,
            deal_type=deal_type,
            symbol=symbol,
            stock_id=stock_id,
            as_of_date=as_of_date,
            is_current_only=is_current_only,
            page=page,
            page_size=page_size,
        )

    async def get_deal_summary(
        self,
        stock_id: int | None = None,
        symbol: str | None = None,
        trade_date_from: date | None = None,
        trade_date_to: date | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        """Compute aggregated summary metrics for deals matching criteria (BUY/SELL turnover, counts)."""
        base_filter = [BulkBlockDeal.is_current.is_(True)]

        if as_of_date is not None:
            base_filter.extend([
                BulkBlockDeal.trade_date <= as_of_date,
                BulkBlockDeal.available_at <= as_of_date,
            ])
        if trade_date_from is not None:
            base_filter.append(BulkBlockDeal.trade_date >= trade_date_from)
        if trade_date_to is not None:
            base_filter.append(BulkBlockDeal.trade_date <= trade_date_to)
        if stock_id is not None:
            base_filter.append(BulkBlockDeal.stock_id == stock_id)
        elif symbol is not None:
            base_filter.append(BulkBlockDeal.symbol == symbol.upper())

        stmt = (
            select(
                func.count(BulkBlockDeal.id).label("total_deals"),
                func.coalesce(func.sum(BulkBlockDeal.deal_value), Decimal("0.00")).label("total_value"),
                func.count(BulkBlockDeal.id).filter(BulkBlockDeal.buy_sell == "BUY").label("buy_deals"),
                func.coalesce(
                    func.sum(BulkBlockDeal.deal_value).filter(BulkBlockDeal.buy_sell == "BUY"),
                    Decimal("0.00"),
                ).label("buy_value"),
                func.count(BulkBlockDeal.id).filter(BulkBlockDeal.buy_sell == "SELL").label("sell_deals"),
                func.coalesce(
                    func.sum(BulkBlockDeal.deal_value).filter(BulkBlockDeal.buy_sell == "SELL"),
                    Decimal("0.00"),
                ).label("sell_value"),
                func.count(BulkBlockDeal.id).filter(BulkBlockDeal.deal_type == "BULK").label("bulk_deals"),
                func.count(BulkBlockDeal.id).filter(BulkBlockDeal.deal_type == "BLOCK").label("block_deals"),
            )
            .where(*base_filter)
        )

        res = await self.session.execute(stmt)
        row = res.one()

        return {
            "total_deals": row.total_deals or 0,
            "total_value": row.total_value or Decimal("0.00"),
            "buy_deals": row.buy_deals or 0,
            "buy_value": row.buy_value or Decimal("0.00"),
            "sell_deals": row.sell_deals or 0,
            "sell_value": row.sell_value or Decimal("0.00"),
            "bulk_deals": row.bulk_deals or 0,
            "block_deals": row.block_deals or 0,
        }

    async def get_active_by_source_line(
        self,
        source_reference: str,
        source_line_index: int,
    ) -> BulkBlockDeal | None:
        """Fetch active (is_current=True) row for a given source reference and line index."""
        stmt = (
            select(BulkBlockDeal)
            .where(
                BulkBlockDeal.source_reference == source_reference,
                BulkBlockDeal.source_line_index == source_line_index,
                BulkBlockDeal.is_current.is_(True),
            )
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_active_by_source_reference(
        self,
        source_reference: str,
    ) -> list[BulkBlockDeal]:
        """Fetch all currently active deals for a given source reference."""
        stmt = (
            select(BulkBlockDeal)
            .where(
                BulkBlockDeal.source_reference == source_reference,
                BulkBlockDeal.is_current.is_(True),
            )
            .order_by(BulkBlockDeal.source_line_index.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def save_deal_amendment(
        self,
        existing_deal: BulkBlockDeal,
        new_deal: BulkBlockDeal,
    ) -> BulkBlockDeal:
        """Supersede an existing deal with an amended deal."""
        existing_deal.is_current = False
        existing_deal.superseded_at = datetime.now(UTC)
        await self.session.flush()

        new_deal.is_current = True
        new_deal.supersedes_id = existing_deal.id
        self.session.add(new_deal)
        await self.session.flush()
        return new_deal
