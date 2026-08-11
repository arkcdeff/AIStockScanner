from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.backtest import (
    BacktestEquityPoint,
    BacktestMetric,
    BacktestRun,
    BacktestStrategy,
    BacktestTrade,
)
from app.repositories.base import BaseRepository


class BacktestStrategyRepository(BaseRepository[BacktestStrategy]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BacktestStrategy, session)

    async def get_by_code(self, code: str) -> BacktestStrategy | None:
        statement = select(BacktestStrategy).where(BacktestStrategy.code == code)
        return await self.session.scalar(statement)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        strategy_type: str | None = None,
        is_active: bool | None = None,
    ) -> list[BacktestStrategy]:
        statement = select(BacktestStrategy).order_by(BacktestStrategy.id)
        if strategy_type:
            statement = statement.where(BacktestStrategy.strategy_type == strategy_type)
        if is_active is not None:
            statement = statement.where(BacktestStrategy.is_active.is_(is_active))
        statement = statement.offset(offset).limit(limit)
        return list(await self.session.scalars(statement))


class BacktestRunRepository(BaseRepository[BacktestRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BacktestRun, session)

    async def create_run(
        self,
        *,
        strategy_id: int,
        requested_by_user_id: int | None,
        status: str,
        start_date: str,
        end_date: str,
        initial_capital: Decimal,
        commission_rate: Decimal,
        slippage_rate: Decimal,
        position_size_type: str,
        position_size_value: Decimal,
        benchmark_symbol: str | None = None,
        max_positions: int | None = None,
    ) -> BacktestRun:
        from datetime import date

        run = BacktestRun(
            strategy_id=strategy_id,
            requested_by_user_id=requested_by_user_id,
            status=status,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            position_size_type=position_size_type,
            position_size_value=position_size_value,
            benchmark_symbol=benchmark_symbol,
            max_positions=max_positions,
        )
        return await self.add(run)

    async def update_status(
        self, run_id: int, status: str, *, error_message: str | None = None
    ) -> BacktestRun | None:
        values: dict[str, object] = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        await self.session.execute(
            update(BacktestRun).where(BacktestRun.id == run_id).values(**values)
        )
        await self.session.flush()
        return await self.get(run_id)

    async def list_runs(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        strategy_id: int | None = None,
        status: str | None = None,
    ) -> list[BacktestRun]:
        statement = select(BacktestRun).order_by(BacktestRun.id.desc())
        if strategy_id is not None:
            statement = statement.where(BacktestRun.strategy_id == strategy_id)
        if status:
            statement = statement.where(BacktestRun.status == status)
        statement = statement.offset(offset).limit(limit)
        return list(await self.session.scalars(statement))

    async def get_with_details(self, run_id: int) -> BacktestRun | None:
        statement = (
            select(BacktestRun)
            .options(
                selectinload(BacktestRun.strategy),
                selectinload(BacktestRun.trades),
                selectinload(BacktestRun.equity_points),
                selectinload(BacktestRun.metric),
            )
            .where(BacktestRun.id == run_id)
        )
        return await self.session.scalar(statement)


class BacktestTradeRepository(BaseRepository[BacktestTrade]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BacktestTrade, session)

    async def bulk_insert(self, trades: list[BacktestTrade]) -> None:
        self.session.add_all(trades)
        await self.session.flush()


class BacktestEquityPointRepository(BaseRepository[BacktestEquityPoint]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BacktestEquityPoint, session)

    async def bulk_insert(self, points: list[BacktestEquityPoint]) -> None:
        self.session.add_all(points)
        await self.session.flush()


class BacktestMetricRepository(BaseRepository[BacktestMetric]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(BacktestMetric, session)

    async def get_by_run(self, run_id: int) -> BacktestMetric | None:
        statement = select(BacktestMetric).where(BacktestMetric.backtest_run_id == run_id)
        return await self.session.scalar(statement)

    async def upsert(self, run_id: int, metric: BacktestMetric) -> BacktestMetric:
        existing = await self.get_by_run(run_id)
        if existing is None:
            metric.backtest_run_id = run_id
            return await self.add(metric)
        for column in BacktestMetric.__table__.columns.keys():
            if column == "id" or column == "backtest_run_id":
                continue
            value = getattr(metric, column, None)
            if value is not None:
                setattr(existing, column, value)
        await self.session.flush()
        return existing