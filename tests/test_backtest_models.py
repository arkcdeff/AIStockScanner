from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select, text

from app.db.session import get_session_factory
from app.models.backtest import (
    BacktestEquityPoint,
    BacktestMetric,
    BacktestRun,
    BacktestStrategy,
    BacktestTrade,
)
from app.models.stock import Stock
from app.repositories.backtest import (
    BacktestEquityPointRepository,
    BacktestMetricRepository,
    BacktestRunRepository,
    BacktestStrategyRepository,
    BacktestTradeRepository,
)


def _strategy(code: str = "ema_cross_17") -> BacktestStrategy:
    return BacktestStrategy(
        name="EMA Cross 17",
        code=code,
        description="Strategy for tests",
        strategy_type="scanner",
        scanner_code="ema_bullish_crossover",
        configuration_json={"fast": 20, "slow": 50},
        is_active=True,
    )


def _run(strategy: BacktestStrategy, **kwargs) -> BacktestRun:
    return BacktestRun(
        strategy_id=strategy.id,
        status=kwargs.pop("status", "pending"),
        start_date=kwargs.pop("start_date", date(2024, 1, 1)),
        end_date=kwargs.pop("end_date", date(2024, 12, 31)),
        initial_capital=Decimal("100000.00"),
        commission_rate=Decimal("0.0005"),
        slippage_rate=Decimal("0.001"),
        position_size_type="percent",
        position_size_value=Decimal("10.0"),
        **kwargs,
    )


async def _stock(session, symbol: str = "TEST17") -> Stock:
    stock = Stock(
        symbol=symbol,
        company_name=f"{symbol} Ltd",
        exchange="NSE",
        series="EQ",
        isin=f"INE{symbol}17",
    )
    session.add(stock)
    await session.flush()
    return stock


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_create_defaults_and_repository() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestStrategyRepository(session)
        strategy = await repo.add(_strategy())

        assert strategy.id is not None
        assert strategy.is_active is True
        assert strategy.strategy_type == "scanner"
        assert strategy.created_at is not None

        assert await repo.get(strategy.id) is strategy
        found = await repo.get_by_code("ema_cross_17")
        assert found is strategy
        assert await repo.list(limit=10) == [strategy]
        await session.rollback()


@pytest.mark.asyncio
async def test_strategy_code_is_unique() -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add_all([_strategy(), _strategy()])
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_strategy_invalid_type_rejected_by_check_constraint() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = _strategy(code="bad_type")
        strategy.strategy_type = "quantum"
        session.add(strategy)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_strategy_update() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestStrategyRepository(session)
        strategy = await repo.add(_strategy())
        strategy.name = "Renamed Strategy"
        strategy.configuration_json = {"fast": 30, "slow": 100}
        strategy.is_active = False
        await session.flush()
        await session.refresh(strategy)

        assert strategy.name == "Renamed Strategy"
        assert strategy.configuration_json == {"fast": 30, "slow": 100}
        assert strategy.is_active is False
        await session.rollback()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_create_relationships_and_status() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))

        assert run.id is not None
        assert run.status == "pending"
        assert run.initial_capital == Decimal("100000.00")
        assert run.commission_rate == Decimal("0.0005")
        assert run.strategy_id == strategy.id

        detailed = await repo.get_with_details(run.id)
        assert detailed is not None
        assert detailed.strategy.id == strategy.id
        await session.rollback()


@pytest.mark.asyncio
async def test_run_invalid_date_range_rejected() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = _run(strategy, start_date=date(2024, 12, 31), end_date=date(2024, 1, 1))
        session.add(run)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_run_initial_capital_must_be_positive() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = _run(strategy)
        run.initial_capital = Decimal("-10")
        session.add(run)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["commission_rate", "slippage_rate"])
async def test_run_cost_rates_must_be_non_negative(field: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = _run(strategy)
        setattr(run, field, Decimal("-0.01"))
        session.add(run)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_run_invalid_status_rejected() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = _run(strategy, status="exploded")
        session.add(run)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trade_create_and_stock_relationship() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            position_side="long",
            entry_signal="ema_cross",
            status="open",
        )
        session.add(trade)
        await session.flush()

        assert trade.id is not None
        assert trade.stock_id == stock.id

        detailed = await repo.get_with_details(run.id)
        assert detailed is not None
        assert len(detailed.trades) == 1
        assert detailed.trades[0].id == trade.id
        assert detailed.trades[0].stock.symbol == "TEST17"
        await session.rollback()


@pytest.mark.asyncio
async def test_trade_quantity_must_be_positive() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            quantity=Decimal("-5"),
            position_side="long",
            status="open",
        )
        session.add(trade)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_trade_entry_price_must_be_positive() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("0"),
            quantity=Decimal("10"),
            position_side="long",
            status="open",
        )
        session.add(trade)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_trade_exit_price_must_be_positive_when_present() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            exit_date=date(2024, 3, 5),
            exit_price=Decimal("0"),
            quantity=Decimal("10"),
            position_side="long",
            status="closed",
        )
        session.add(trade)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["long", "short"])
async def test_trade_position_side_allowed_values(side: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            quantity=Decimal("10"),
            position_side=side,
            status="open",
        )
        session.add(trade)
        await session.flush()
        assert trade.position_side == side
        await session.rollback()


@pytest.mark.asyncio
async def test_trade_invalid_position_side_rejected() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            quantity=Decimal("10"),
            position_side="diagonal",
            status="open",
        )
        session.add(trade)
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# Equity points
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_equity_point_create_and_run_relationship() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))

        point = BacktestEquityPoint(
            backtest_run_id=run.id,
            trade_date=date(2024, 6, 1),
            cash=Decimal("50000.00"),
            invested_value=Decimal("55000.00"),
            portfolio_value=Decimal("105000.00"),
            drawdown_percent=Decimal("-1.5"),
        )
        session.add(point)
        await session.flush()

        assert point.id is not None

        detailed = await repo.get_with_details(run.id)
        assert detailed is not None
        assert len(detailed.equity_points) == 1
        assert detailed.equity_points[0].id == point.id
        await session.rollback()


@pytest.mark.asyncio
async def test_equity_point_unique_run_date() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        for _ in range(2):
            session.add(
                BacktestEquityPoint(
                    backtest_run_id=run.id,
                    trade_date=date(2024, 6, 1),
                    cash=Decimal("50000.00"),
                    invested_value=Decimal("55000.00"),
                    portfolio_value=Decimal("105000.00"),
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_create_and_run_relationship() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))

        metric = BacktestMetric(
            backtest_run_id=run.id,
            total_return_percent=Decimal("12.5"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            win_rate=Decimal("0.6"),
            max_drawdown_percent=Decimal("-8.0"),
        )
        session.add(metric)
        await session.flush()

        assert metric.id is not None

        detailed = await repo.get_with_details(run.id)
        assert detailed is not None
        assert detailed.metric is not None
        assert detailed.metric.id == metric.id
        await session.rollback()


@pytest.mark.asyncio
async def test_metric_unique_per_run() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        for _ in range(2):
            session.add(
                BacktestMetric(
                    backtest_run_id=run.id,
                    total_return_percent=Decimal("5.0"),
                    total_trades=1,
                    winning_trades=1,
                    losing_trades=0,
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_repository_status_update() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))

        updated = await repo.update_status(run.id, "running")
        assert updated is not None
        assert updated.status == "running"

        failed = await repo.update_status(run.id, "failed", error_message="boom")
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error_message == "boom"
        await session.rollback()


@pytest.mark.asyncio
async def test_run_repository_paginated_list() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        for i in range(5):
            await repo.add(_run(strategy, start_date=date(2024, 1, i + 1)))

        first_page = await repo.list_runs(limit=2)
        assert len(first_page) == 2
        second_page = await repo.list_runs(offset=2, limit=2)
        assert len(second_page) == 2
        assert first_page[0].id != second_page[0].id

        pending = await repo.list_runs(status="pending")
        assert len(pending) == 5
        await session.rollback()


@pytest.mark.asyncio
async def test_trade_repository_bulk_insert() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestTradeRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stocks = [await _stock(session, symbol=f"BULK{i}") for i in range(3)]

        trades = [
            BacktestTrade(
                backtest_run_id=run.id,
                stock_id=stocks[i].id,
                entry_date=date(2024, 3, i + 1),
                entry_price=Decimal("100"),
                quantity=Decimal("10"),
                position_side="long",
                status="open",
            )
            for i in range(3)
        ]
        await repo.bulk_insert(trades)

        total = await session.scalar(select(func.count()).select_from(BacktestTrade))
        assert total == 3
        await session.rollback()


@pytest.mark.asyncio
async def test_equity_point_repository_bulk_insert() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestEquityPointRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))

        points = [
            BacktestEquityPoint(
                backtest_run_id=run.id,
                trade_date=date(2024, 6, i + 1),
                cash=Decimal("50000"),
                invested_value=Decimal("50000"),
                portfolio_value=Decimal("100000"),
            )
            for i in range(4)
        ]
        await repo.bulk_insert(points)

        total = await session.scalar(select(func.count()).select_from(BacktestEquityPoint))
        assert total == 4
        await session.rollback()


@pytest.mark.asyncio
async def test_metric_repository_upsert() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestMetricRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))

        metric = BacktestMetric(
            backtest_run_id=run.id,
            total_return_percent=Decimal("10.0"),
            total_trades=5,
            winning_trades=3,
            losing_trades=2,
            win_rate=Decimal("0.6"),
        )
        created = await repo.upsert(run.id, metric)
        assert created.total_return_percent == Decimal("10.0")
        assert created.id is not None

        update_metric = BacktestMetric(
            backtest_run_id=run.id,
            total_return_percent=Decimal("25.0"),
            total_trades=9,
            winning_trades=6,
            losing_trades=3,
            win_rate=Decimal("0.667"),
        )
        updated = await repo.upsert(run.id, update_metric)
        assert updated.id == created.id
        assert updated.total_return_percent == Decimal("25.0")
        assert updated.total_trades == 9

        count = await session.scalar(select(func.count()).select_from(BacktestMetric))
        assert count == 1
        await session.rollback()


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_run_cascades_to_trades_equity_and_metrics() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await BacktestRunRepository(session).add(_run(strategy))
        stock = await _stock(session)

        session.add_all(
            [
                BacktestTrade(
                    backtest_run_id=run.id,
                    stock_id=stock.id,
                    entry_date=date(2024, 3, 1),
                    entry_price=Decimal("100"),
                    quantity=Decimal("10"),
                    position_side="long",
                    status="open",
                ),
                BacktestEquityPoint(
                    backtest_run_id=run.id,
                    trade_date=date(2024, 6, 1),
                    cash=Decimal("50000"),
                    invested_value=Decimal("50000"),
                    portfolio_value=Decimal("100000"),
                ),
                BacktestMetric(
                    backtest_run_id=run.id,
                    total_return_percent=Decimal("5"),
                    total_trades=1,
                    winning_trades=1,
                    losing_trades=0,
                ),
            ]
        )
        await session.flush()

        await session.delete(run)
        await session.commit()

        assert await session.scalar(select(func.count()).select_from(BacktestTrade)) == 0
        assert await session.scalar(select(func.count()).select_from(BacktestEquityPoint)) == 0
        assert await session.scalar(select(func.count()).select_from(BacktestMetric)) == 0


@pytest.mark.asyncio
async def test_delete_strategy_cascades_to_runs() -> None:
    factory = get_session_factory()
    async with factory() as session:
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        await BacktestRunRepository(session).add(_run(strategy))

        await session.delete(strategy)
        await session.commit()

        assert await session.scalar(select(func.count()).select_from(BacktestRun)) == 0
        assert await session.scalar(select(func.count()).select_from(BacktestStrategy)) == 0


@pytest.mark.asyncio
async def test_trade_schema_reflects_stock() -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        strategy = await BacktestStrategyRepository(session).add(_strategy())
        run = await repo.add(_run(strategy))
        stock = await _stock(session)
        trade = BacktestTrade(
            backtest_run_id=run.id,
            stock_id=stock.id,
            entry_date=date(2024, 3, 1),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            position_side="long",
            status="open",
        )
        session.add(trade)
        await session.flush()

        result = await session.execute(
            text(
                "SELECT s.symbol FROM backtest_trades t "
                "JOIN stocks s ON s.id = t.stock_id WHERE t.id = :tid"
            ),
            {"tid": trade.id},
        )
        assert result.scalar_one() == "TEST17"
        await session.rollback()