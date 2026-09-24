"""Signal Trajectories API Route — G.11-D7 Multi-Session Signal Lifecycle & Trajectory Engine.

Endpoints:
    GET /api/v1/stocks/{symbol}/signal-trajectory

Security:
    Gated by `require_permission("stocks.read")` and `get_current_authenticated_user`.
    Trading firewall: Read-only evidence descriptions; zero trading instructions.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.schemas.signal_trajectory import (
    NonTradingDatePolicy,
    StockSignalTrajectoryResponse,
)
from app.services.signal_trajectory_service import SignalTrajectoryService

router = APIRouter(
    tags=["Signal Trajectories"],
    dependencies=[Depends(require_permission("stocks.read"))],
)


@router.get(
    "/stocks/{symbol}/signal-trajectory",
    response_model=StockSignalTrajectoryResponse,
    summary="Multi-session signal lifecycle & trajectory evaluation for a stock",
    description=(
        "G.11-D7: Stateless, deterministic multi-session signal trajectory evaluation. "
        "Evaluates chronological forward-chaining across K sessions with 5-level precedence ladder, "
        "C-01A corporate action evidence masking, and C-03A gap/missing data handling. "
        "Evidence descriptions only — never trading recommendations."
    ),
)
async def get_stock_signal_trajectory(
    symbol: str,
    evaluation_date: date | None = Query(
        default=None,
        description="Target evaluation session date. Defaults to the latest available trading date.",
    ),
    lookback_sessions: int = Query(
        default=5,
        ge=2,
        le=20,
        description="Trajectory lookback window in trading sessions K (2 to 20).",
    ),
    recurrence_lookback_sessions: int = Query(
        default=5,
        ge=2,
        le=20,
        description="Recurrence lookback window in trading sessions M (2 to 20).",
    ),
    non_trading_policy: NonTradingDatePolicy = Query(
        default=NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION,
        description="Policy applied when the requested evaluation date is not an exchange trading day.",
    ),
    as_of: datetime | None = Query(
        default=None,
        description="Point-in-Time cutoff timestamp.",
    ),
    include_resolved: bool = Query(
        default=True,
        description="Whether to include resolved pattern trajectories in output presentation.",
    ),
    include_ai_narrative: bool = Query(
        default=False,
        description="Whether to generate optional AI narrative (non-authoritative).",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> StockSignalTrajectoryResponse:
    """Evaluate and return multi-session signal trajectory for the requested stock symbol."""
    service = SignalTrajectoryService(session=session)
    try:
        return await service.evaluate_trajectory(
            symbol=symbol,
            evaluation_date=evaluation_date,
            lookback_sessions=lookback_sessions,
            recurrence_lookback_sessions=recurrence_lookback_sessions,
            non_trading_policy=non_trading_policy,
            as_of=as_of,
            include_resolved=include_resolved,
            include_ai_narrative=include_ai_narrative,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        ) from exc
