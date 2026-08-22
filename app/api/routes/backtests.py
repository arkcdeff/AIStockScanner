"""Backtesting REST API.

Endpoints manage backtest strategies, runs, execution and results.

Ownership policy:
- Strategies are shared definitions. Anyone with ``backtests.read`` can list and
  view them; ``backtests.write`` is required to create/update/deactivate.
- Runs are private to their ``requested_by_user_id``. A non-admin caller may
  only access runs they requested; administrators and superusers may access any
  run (an ``admin_override`` audit event is recorded).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import (
    DB_SESSION,
    AuthenticatedUser,
    _deny_access,
    is_admin_user,
    record_authorization_event,
    require_authenticated,
    require_permission,
)
from app.models.backtest import BacktestRun, BacktestStrategy
from app.models.scan_definition import ScanDefinition
from app.models.stock import Stock
from app.repositories.backtest import (
    BacktestEquityPointRepository,
    BacktestMetricRepository,
    BacktestRunRepository,
    BacktestStrategyRepository,
    BacktestTradeRepository,
)
from app.schemas.backtest import (
    BacktestDeleteResponse,
    BacktestEquityPointList,
    BacktestEquityPointRead,
    BacktestExecutionResponse,
    BacktestMetricRead,
    BacktestRunCreateRequest,
    BacktestRunDetailRead,
    BacktestRunList,
    BacktestRunRead,
    BacktestRunSummary,
    BacktestStrategyCreateRequest,
    BacktestStrategyList,
    BacktestStrategyRead,
    BacktestStrategyUpdate,
    BacktestTradeList,
    BacktestTradeRead,
)
from app.services.backtest_metrics import BacktestMetricCalculator
from app.services.backtest_simulator import BacktestSimulator

router = APIRouter(prefix="/backtests", tags=["Backtests"])


# --------------------------------------------------------------------------- helpers


async def _get_strategy_or_404(session: AsyncSession, strategy_id: int) -> BacktestStrategy:
    strategy = await BacktestStrategyRepository(session).get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Backtest strategy not found")
    return strategy


async def _validate_scanner_code(session: AsyncSession, scanner_code: str | None) -> None:
    """Reject a scanner_code that does not reference an active scanner definition."""
    if not scanner_code:
        return
    scan_def = await session.scalar(
        select(ScanDefinition).where(ScanDefinition.code == scanner_code)
    )
    if scan_def is None or not scan_def.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="scanner_code must reference an active scanner",
        )


def require_run_access() -> Callable[..., Awaitable[AuthenticatedUser]]:
    """Dependency: the run exists and the caller owns it or is an administrator."""

    async def dependency(
        run_id: Annotated[int, Path(ge=1)],
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        run = await session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        owner_id = run.requested_by_user_id
        if owner_id is None:
            if not is_admin_user(current):
                await _deny_access(
                    session,
                    current,
                    entity_type="backtest_run",
                    entity_id=run_id,
                    metadata={"owner_id": None},
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
                )
            return current
        if current.user.id != owner_id and not is_admin_user(current):
            await _deny_access(
                session,
                current,
                entity_type="backtest_run",
                entity_id=run_id,
                metadata={"owner_id": owner_id},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        if current.user.id != owner_id:
            await record_authorization_event(
                session,
                action="admin_override",
                user_id=current.user.id,
                entity_type="backtest_run",
                entity_id=run_id,
                metadata={"owner_id": owner_id},
            )
        return current

    return dependency


# --------------------------------------------------------------------------- strategies


@router.post(
    "/strategies",
    response_model=BacktestStrategyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("backtests.write"))],
)
async def create_strategy(
    payload: BacktestStrategyCreateRequest,
    current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
    session: DB_SESSION,
) -> BacktestStrategyRead:
    await _validate_scanner_code(session, payload.scanner_code)
    repo = BacktestStrategyRepository(session)
    existing = await repo.get_by_code(payload.code)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Strategy code already exists"
        )
    strategy = BacktestStrategy(
        name=payload.name,
        code=payload.code,
        strategy_type=payload.strategy_type,
        description=payload.description,
        scanner_code=payload.scanner_code,
        configuration_json=payload.configuration_json,
        is_active=payload.is_active,
        created_by_user_id=current.user.id,
    )
    created = await repo.add(strategy)
    await session.commit()
    await session.refresh(created)
    return BacktestStrategyRead.model_validate(created)


@router.get(
    "/strategies",
    response_model=BacktestStrategyList,
    dependencies=[Depends(require_permission("backtests.read"))],
)
async def list_strategies(
    session: DB_SESSION,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    strategy_type: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
) -> BacktestStrategyList:
    repo = BacktestStrategyRepository(session)
    items = await repo.list(
        offset=offset,
        limit=limit,
        strategy_type=strategy_type,
        is_active=is_active,
        search=search,
    )
    total = await repo.count(
        strategy_type=strategy_type,
        is_active=is_active,
        search=search,
    )
    return BacktestStrategyList(
        total=total,
        offset=offset,
        limit=limit,
        items=[BacktestStrategyRead.model_validate(s) for s in items],
    )


@router.get(
    "/strategies/{strategy_id}",
    response_model=BacktestStrategyRead,
    dependencies=[Depends(require_permission("backtests.read"))],
)
async def get_strategy(
    strategy_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestStrategyRead:
    strategy = await _get_strategy_or_404(session, strategy_id)
    return BacktestStrategyRead.model_validate(strategy)


@router.put(
    "/strategies/{strategy_id}",
    response_model=BacktestStrategyRead,
    dependencies=[Depends(require_permission("backtests.write"))],
)
async def update_strategy(
    strategy_id: Annotated[int, Path(ge=1)],
    payload: BacktestStrategyUpdate,
    session: DB_SESSION,
) -> BacktestStrategyRead:
    strategy = await _get_strategy_or_404(session, strategy_id)
    values = payload.model_dump(exclude_unset=True)
    await _validate_scanner_code(session, values.get("scanner_code", strategy.scanner_code))
    updated = await BacktestStrategyRepository(session).update(strategy_id, values)
    await session.commit()
    await session.refresh(updated)
    return BacktestStrategyRead.model_validate(updated)


@router.delete(
    "/strategies/{strategy_id}",
    response_model=BacktestDeleteResponse,
    dependencies=[Depends(require_permission("backtests.write"))],
)
async def deactivate_strategy(
    strategy_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestDeleteResponse:
    strategy = await _get_strategy_or_404(session, strategy_id)
    if strategy.is_active:
        strategy.is_active = False
        await session.flush()
        await session.commit()
        await session.refresh(strategy)
    return BacktestDeleteResponse(
        id=strategy.id,
        code=strategy.code,
        is_active=strategy.is_active,
        message="Strategy deactivated; historical runs remain available",
    )


# --------------------------------------------------------------------------- runs


@router.post(
    "/runs",
    response_model=BacktestRunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("backtests.write"))],
)
async def create_run(
    payload: BacktestRunCreateRequest,
    current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
    session: DB_SESSION,
) -> BacktestRunRead:
    strategy = await _get_strategy_or_404(session, payload.strategy_id)
    if not strategy.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Strategy is inactive"
        )
    await _validate_scanner_code(session, strategy.scanner_code)
    if payload.benchmark_symbol:
        benchmark = await session.scalar(
            select(Stock).where(Stock.symbol == payload.benchmark_symbol.upper())
        )
        if benchmark is None or not benchmark.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="benchmark_symbol must reference an active stock",
            )
    if payload.end_date > date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="end_date cannot be in the future",
        )

    repo = BacktestRunRepository(session)
    run = await repo.create_run(
        strategy_id=strategy.id,
        requested_by_user_id=current.user.id,
        status="pending",
        start_date=payload.start_date.isoformat(),
        end_date=payload.end_date.isoformat(),
        initial_capital=payload.initial_capital,
        commission_rate=payload.commission_rate,
        slippage_rate=payload.slippage_rate,
        position_size_type=payload.position_size_type,
        position_size_value=payload.position_size_value,
        benchmark_symbol=payload.benchmark_symbol,
        max_positions=payload.max_positions,
    )
    await session.commit()
    await session.refresh(run)
    return BacktestRunRead.model_validate(run)


@router.get(
    "/runs",
    response_model=BacktestRunList,
    dependencies=[Depends(require_permission("backtests.read"))],
)
async def list_runs(
    session: DB_SESSION,
    current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    strategy_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
) -> BacktestRunList:
    owner_filter = None if is_admin_user(current) else current.user.id
    repo = BacktestRunRepository(session)
    items = await repo.list_runs(
        offset=offset,
        limit=limit,
        strategy_id=strategy_id,
        status=status_filter,
        requested_by_user_id=owner_filter,
    )
    total = await repo.count_runs(
        strategy_id=strategy_id,
        status=status_filter,
        requested_by_user_id=owner_filter,
    )
    return BacktestRunList(
        total=total,
        offset=offset,
        limit=limit,
        items=[BacktestRunRead.model_validate(r) for r in items],
    )


@router.get(
    "/runs/{run_id}",
    response_model=BacktestRunDetailRead,
    dependencies=[
        Depends(require_permission("backtests.read")),
        Depends(require_run_access()),
    ],
)
async def get_run(
    run_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestRunDetailRead:
    run = await BacktestRunRepository(session).get_with_details(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return BacktestRunDetailRead.model_validate(run)


# --------------------------------------------------------------------------- execution

_EXECUTION_LOCKS: dict[int, asyncio.Lock] = {}


def _run_lock(run_id: int) -> asyncio.Lock:
    lock = _EXECUTION_LOCKS.get(run_id)
    if lock is None:
        lock = asyncio.Lock()
        _EXECUTION_LOCKS[run_id] = lock
    return lock


@router.post(
    "/runs/{run_id}/execute",
    response_model=BacktestExecutionResponse,
    dependencies=[
        Depends(require_permission("backtests.run")),
        Depends(require_run_access()),
    ],
)
async def execute_run(
    run_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestExecutionResponse:
    async with _run_lock(run_id):
        repo = BacktestRunRepository(session)
        run = await repo.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        if run.status == "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Run is already completed"
            )
        if run.status == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Run is already running"
            )

        try:
            summary = await BacktestSimulator(session).simulate_run(run_id)
        except Exception as exc:
            await session.rollback()
            code = (
                status.HTTP_400_BAD_REQUEST
                if isinstance(exc, ValueError)
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            raise HTTPException(
                status_code=code,
                detail=f"Backtest execution failed: {type(exc).__name__}",
            ) from exc

        metrics = None
        try:
            metric_summary = await BacktestMetricCalculator(session).compute_metrics(run_id)
            metrics = metric_summary.as_dict()
        except Exception:
            metrics = None

        final_run = await repo.get(run_id)
        return BacktestExecutionResponse(
            run_id=run_id,
            status=final_run.status,
            final_capital=final_run.final_capital,
            summary=summary.as_dict(),
            metrics=metrics,
        )


# --------------------------------------------------------------------------- results


@router.get(
    "/runs/{run_id}/summary",
    response_model=BacktestRunSummary,
    dependencies=[
        Depends(require_permission("backtests.read")),
        Depends(require_run_access()),
    ],
)
async def get_run_summary(
    run_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestRunSummary:
    run = await BacktestRunRepository(session).get_with_details(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    closed = [t for t in run.trades if t.status == "closed"]
    return BacktestRunSummary(
        run=BacktestRunRead.model_validate(run),
        strategy=BacktestStrategyRead.model_validate(run.strategy) if run.strategy else None,
        trade_count=len(closed),
        metric=BacktestMetricRead.model_validate(run.metric) if run.metric else None,
    )


@router.get(
    "/runs/{run_id}/trades",
    response_model=BacktestTradeList,
    dependencies=[
        Depends(require_permission("backtests.read")),
        Depends(require_run_access()),
    ],
)
async def list_run_trades(
    session: DB_SESSION,
    run_id: Annotated[int, Path(ge=1)],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    position_side: str | None = Query(default=None),
    profitable: bool | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=32),
) -> BacktestTradeList:
    trades, total = await BacktestTradeRepository(session).list_for_run(
        run_id,
        offset=offset,
        limit=limit,
        status=status_filter,
        position_side=position_side,
        profitable=profitable,
        symbol=symbol,
    )
    stock_ids = {t.stock_id for t in trades}
    symbols: dict[int, str] = {}
    if stock_ids:
        rows = await session.execute(
            select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids))
        )
        symbols = {row[0]: row[1] for row in rows}
    items: list[BacktestTradeRead] = []
    for trade in trades:
        read = BacktestTradeRead.model_validate(trade)
        read.symbol = symbols.get(trade.stock_id)
        items.append(read)
    return BacktestTradeList(total=total, offset=offset, limit=limit, items=items)


@router.get(
    "/runs/{run_id}/equity",
    response_model=BacktestEquityPointList,
    dependencies=[
        Depends(require_permission("backtests.read")),
        Depends(require_run_access()),
    ],
)
async def list_run_equity(
    run_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestEquityPointList:
    points = await BacktestEquityPointRepository(session).list_for_run(run_id)
    return BacktestEquityPointList(
        items=[BacktestEquityPointRead.model_validate(p) for p in points]
    )


@router.get(
    "/runs/{run_id}/metrics",
    response_model=BacktestMetricRead,
    dependencies=[
        Depends(require_permission("backtests.read")),
        Depends(require_run_access()),
    ],
)
async def get_run_metrics(
    run_id: Annotated[int, Path(ge=1)],
    session: DB_SESSION,
) -> BacktestMetricRead:
    run = await BacktestRunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Metrics are only available after the run completes",
        )
    metric = await BacktestMetricRepository(session).get_by_run(run_id)
    if metric is None:
        raise HTTPException(status_code=404, detail="Metrics not found for this run")
    return BacktestMetricRead.model_validate(metric)
