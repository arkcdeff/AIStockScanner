"""Relative Strength calculation service — Sprint 19.1G.

Calculates point-in-time performance of a stock relative to Nifty 50 benchmark
and sector benchmark across 5D, 20D, 60D, and 120D historical windows.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.schemas.stock360 import RelativeStrengthMetrics


class RelativeStrengthService:
    """Calculates PIT-safe relative strength metrics against broad index and sector."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def calculate_relative_strength(
        self,
        stock_id: int,
        evaluation_date: date,
    ) -> RelativeStrengthMetrics:
        """Calculate PIT relative performance metrics."""
        # 1. Fetch stock price history up to evaluation_date
        stmt_stock = (
            select(DailyPrice.trade_date, DailyPrice.close)
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date <= evaluation_date)
            .order_by(DailyPrice.trade_date.desc())
            .limit(135)
        )
        res_stock = await self.session.execute(stmt_stock)
        stock_rows = list(res_stock.all())

        if not stock_rows or len(stock_rows) < 5:
            return RelativeStrengthMetrics(rs_trend="INSUFFICIENT_DATA")

        curr_p = float(stock_rows[0].close)

        def _get_return(lookback: int) -> float | None:
            if len(stock_rows) > lookback and stock_rows[lookback].close:
                past_p = float(stock_rows[lookback].close)
                if past_p > 0:
                    return ((curr_p - past_p) / past_p) * 100.0
            return None

        st_ret_5d = _get_return(5)
        st_ret_20d = _get_return(20)
        st_ret_60d = _get_return(60)
        st_ret_120d = _get_return(120)

        # 2. Fetch Nifty 50 benchmark history
        stmt_idx = select(MarketIndex.id).where(MarketIndex.name.ilike("%Nifty 50%")).limit(1)
        idx_res = await self.session.execute(stmt_idx)
        idx_id = idx_res.scalar_one_or_none()

        idx_ret_5d = idx_ret_20d = idx_ret_60d = idx_ret_120d = None
        if idx_id:
            stmt_idx_prices = (
                select(MarketIndexPrice.trade_date, MarketIndexPrice.close)
                .where(MarketIndexPrice.market_index_id == idx_id)
                .where(MarketIndexPrice.trade_date <= evaluation_date)
                .order_by(MarketIndexPrice.trade_date.desc())
                .limit(135)
            )
            res_idx_p = await self.session.execute(stmt_idx_prices)
            idx_rows = list(res_idx_p.all())

            if idx_rows and len(idx_rows) >= 5:
                curr_idx = float(idx_rows[0].close)

                def _get_idx_return(lookback: int) -> float | None:
                    if len(idx_rows) > lookback and idx_rows[lookback].close:
                        past_idx = float(idx_rows[lookback].close)
                        if past_idx > 0:
                            return ((curr_idx - past_idx) / past_idx) * 100.0
                    return None

                idx_ret_5d = _get_idx_return(5)
                idx_ret_20d = _get_idx_return(20)
                idx_ret_60d = _get_idx_return(60)
                idx_ret_120d = _get_idx_return(120)

        rs_5d = (st_ret_5d - idx_ret_5d) if (st_ret_5d is not None and idx_ret_5d is not None) else None
        rs_20d = (st_ret_20d - idx_ret_20d) if (st_ret_20d is not None and idx_ret_20d is not None) else None
        rs_60d = (st_ret_60d - idx_ret_60d) if (st_ret_60d is not None and idx_ret_60d is not None) else None
        rs_120d = (st_ret_120d - idx_ret_120d) if (st_ret_120d is not None and idx_ret_120d is not None) else None

        trend = "NEUTRAL"
        if rs_20d is not None and rs_60d is not None:
            if rs_20d > 2.0 and rs_60d > 3.0:
                trend = "OUTPERFORMING"
            elif rs_20d < -2.0 and rs_60d < -3.0:
                trend = "UNDERPERFORMING"

        return RelativeStrengthMetrics(
            stock_vs_nifty_5d_pct=round(rs_5d, 2) if rs_5d is not None else None,
            stock_vs_nifty_20d_pct=round(rs_20d, 2) if rs_20d is not None else None,
            stock_vs_nifty_60d_pct=round(rs_60d, 2) if rs_60d is not None else None,
            stock_vs_nifty_120d_pct=round(rs_120d, 2) if rs_120d is not None else None,
            rs_trend=trend,
        )
