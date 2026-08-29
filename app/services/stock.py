from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.sector import Sector
from app.models.stock import Stock
from app.repositories.stock import StockRepository
from app.schemas.stock import StockCreate


class StockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = StockRepository(session)

    async def create(self, payload: StockCreate) -> Stock:
        existing = await self.repository.get_by_symbol(payload.symbol)
        if existing is not None:
            raise ValueError(f"Stock symbol '{payload.symbol.upper()}' already exists")

        stock = Stock(
            symbol=payload.symbol.upper().strip(),
            company_name=payload.company_name.strip(),
            exchange=payload.exchange.upper().strip(),
            series=payload.series.upper().strip() if payload.series else None,
            isin=payload.isin.upper().strip() if payload.isin else None,
            sector_id=payload.sector_id,
            industry=payload.industry.strip() if payload.industry else None,
        )
        await self.repository.add(stock)
        await self.session.commit()
        return stock

    async def list_explorer(
        self,
        *,
        query: str = "",
        readiness: str = "all",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, object]:
        """Return one paginated, price-aware stock explorer query without N+1 reads."""
        history = (
            select(
                DailyPrice.stock_id.label("stock_id"),
                func.count(DailyPrice.id).label("history_sessions"),
                func.max(DailyPrice.trade_date).label("latest_trade_date"),
            )
            .group_by(DailyPrice.stock_id)
            .subquery()
        )
        ranked_prices = select(
            DailyPrice.stock_id.label("stock_id"),
            DailyPrice.trade_date.label("trade_date"),
            DailyPrice.close.label("latest_close"),
            func.lag(DailyPrice.close)
            .over(
                partition_by=DailyPrice.stock_id,
                order_by=DailyPrice.trade_date.asc(),
            )
            .label("previous_close"),
            func.row_number()
            .over(
                partition_by=DailyPrice.stock_id,
                order_by=DailyPrice.trade_date.desc(),
            )
            .label("price_rank"),
        ).subquery()

        conditions = [Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)]
        cleaned = query.strip()
        if cleaned:
            pattern = f"%{cleaned}%"
            conditions.append(or_(Stock.symbol.ilike(pattern), Stock.company_name.ilike(pattern)))
        if readiness == "ready":
            conditions.append(history.c.history_sessions >= 50)
        elif readiness == "limited":
            conditions.extend(
                [history.c.history_sessions.is_not(None), history.c.history_sessions < 50]
            )
        elif readiness == "missing":
            conditions.append(history.c.history_sessions.is_(None))

        statement = (
            select(
                Stock.id,
                Stock.symbol,
                Stock.company_name,
                Stock.exchange,
                Stock.industry,
                Stock.is_active,
                Sector.name.label("sector"),
                ranked_prices.c.latest_close,
                ranked_prices.c.previous_close,
                history.c.latest_trade_date,
                history.c.history_sessions,
            )
            .outerjoin(Sector, Stock.sector_id == Sector.id)
            .outerjoin(history, history.c.stock_id == Stock.id)
            .outerjoin(
                ranked_prices,
                (ranked_prices.c.stock_id == Stock.id) & (ranked_prices.c.price_rank == 1),
            )
            .where(*conditions)
            .order_by(Stock.symbol)
        )
        total_statement = (
            select(func.count(Stock.id))
            .outerjoin(history, history.c.stock_id == Stock.id)
            .where(*conditions)
        )
        total = int(await self.session.scalar(total_statement) or 0)
        rows = (
            await self.session.execute(statement.offset((page - 1) * page_size).limit(page_size))
        ).all()

        summary = (
            await self.session.execute(
                select(
                    func.count(Stock.id),
                    func.count(Stock.id).filter(history.c.history_sessions.is_not(None)),
                    func.count(Stock.id).filter(history.c.history_sessions >= 50),
                    func.max(history.c.latest_trade_date),
                    func.count(Stock.id).filter(Stock.sector_id.is_not(None)),
                )
                .outerjoin(history, history.c.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
            )
        ).one()

        results: list[dict[str, object]] = []
        for row in rows:
            latest_close = float(row.latest_close) if row.latest_close is not None else None
            previous_close = float(row.previous_close) if row.previous_close is not None else None
            change = None
            change_pct = None
            if latest_close is not None and previous_close:
                change = latest_close - previous_close
                change_pct = (change / previous_close) * 100
            sessions = int(row.history_sessions or 0)
            history_state = "ready" if sessions >= 50 else "limited" if sessions else "missing"
            results.append(
                {
                    "id": row.id,
                    "symbol": row.symbol,
                    "company_name": row.company_name,
                    "exchange": row.exchange,
                    "sector": row.sector,
                    "industry": row.industry,
                    "is_active": row.is_active,
                    "latest_close": latest_close,
                    "previous_close": previous_close,
                    "change": change,
                    "change_pct": change_pct,
                    "latest_trade_date": row.latest_trade_date,
                    "history_sessions": sessions,
                    "history_state": history_state,
                }
            )

        return {
            "rows": results,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "summary": {
                "stocks": int(summary[0] or 0),
                "with_prices": int(summary[1] or 0),
                "scanner_ready": int(summary[2] or 0),
                "latest_trade_date": summary[3],
                "sector_mapped": int(summary[4] or 0),
            },
        }
