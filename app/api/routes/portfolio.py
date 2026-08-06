from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.schemas.portfolio import (
    HoldingCreate,
    HoldingRead,
    HoldingUpdate,
    HoldingValuationRead,
    PortfolioCreate,
    PortfolioDetailRead,
    PortfolioRead,
    PortfolioSummaryRead,
    PortfolioUpdate,
)
from app.services.portfolio import PortfolioError, PortfolioService

router = APIRouter(
    prefix="/portfolios",
    tags=["Portfolio"],
    dependencies=[Depends(require_permission("portfolio.read"))],
)
WRITE_DEPENDENCIES = [Depends(require_permission("portfolio.write"))]


@router.get("/summary", response_model=PortfolioSummaryRead)
async def portfolio_overall_summary(
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioSummaryRead:
    service = PortfolioService(session)
    summary = await service.overall_summary()
    return PortfolioSummaryRead(
        id=0,
        broker=None,
        account_name="All Portfolios",
        notes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        **summary,
    )


@router.get("/", response_model=list[PortfolioSummaryRead])
async def list_portfolios(
    session: AsyncSession = Depends(get_db_session),
) -> list[PortfolioSummaryRead]:
    service = PortfolioService(session)
    items: list[PortfolioSummaryRead] = []
    for portfolio in await service.list():
        summary = await service.portfolio_valuation(portfolio.id)
        item = PortfolioSummaryRead.model_validate(portfolio)
        item.holdings_count = summary["holdings_count"]
        item.total_invested = summary["total_invested"]
        item.total_value = summary["total_value"]
        item.total_pl = summary["total_pl"]
        item.total_roi_pct = summary["total_roi_pct"]
        item.today_pl = summary["today_pl"]
        items.append(item)
    return items


@router.post("/", response_model=PortfolioRead, dependencies=WRITE_DEPENDENCIES)
async def create_portfolio(
    payload: PortfolioCreate,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioRead:
    service = PortfolioService(session)
    try:
        portfolio = await service.create(payload.model_dump())
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PortfolioRead.model_validate(portfolio)


@router.get("/{portfolio_id}", response_model=PortfolioDetailRead)
async def get_portfolio(
    portfolio_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioDetailRead:
    service = PortfolioService(session)
    portfolio = await service.get(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    holdings = await service.list_holdings(portfolio_id)
    valued = await service.value_holdings(holdings)
    summary = service._aggregate(portfolio_id, valued)

    base = PortfolioRead.model_validate(portfolio)
    item = PortfolioDetailRead(
        id=base.id,
        broker=base.broker,
        account_name=base.account_name,
        notes=base.notes,
        created_at=base.created_at,
        updated_at=base.updated_at,
        total_invested=summary["total_invested"],
        total_value=summary["total_value"],
        total_pl=summary["total_pl"],
        total_roi_pct=summary["total_roi_pct"],
        today_pl=summary["today_pl"],
        holdings_count=summary["holdings_count"],
        holdings=[
            HoldingValuationRead(
                id=row["holding"].id,
                portfolio_id=portfolio_id,
                stock_id=row["holding"].stock_id,
                quantity=row["quantity"],
                average_price=row["average_price"],
                purchase_date=row["holding"].purchase_date,
                broker_order_id=row["holding"].broker_order_id,
                notes=row["holding"].notes,
                created_at=row["holding"].created_at,
                updated_at=row["holding"].updated_at,
                symbol=row["stock"].symbol,
                company_name=row["stock"].company_name,
                latest_close=row["latest_close"],
                previous_close=row["previous_close"],
                invested=row["invested"],
                current_value=row["current_value"],
                pl=row["pl"],
                roi_pct=row["roi_pct"],
                today_pl=row["today_pl"],
                allocation_pct=row["allocation_pct"],
            )
            for row in valued
        ],
    )
    return item


@router.put("/{portfolio_id}", response_model=PortfolioRead, dependencies=WRITE_DEPENDENCIES)
async def update_portfolio(
    portfolio_id: int,
    payload: PortfolioUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioRead:
    service = PortfolioService(session)
    try:
        portfolio = await service.update(portfolio_id, payload.model_dump(exclude_unset=True))
    except PortfolioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PortfolioRead.model_validate(portfolio)


@router.delete("/{portfolio_id}", status_code=204, dependencies=WRITE_DEPENDENCIES)
async def delete_portfolio(
    portfolio_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    service = PortfolioService(session)
    try:
        await service.delete(portfolio_id)
    except PortfolioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{portfolio_id}/holdings", response_model=list[HoldingValuationRead])
async def list_holdings(
    portfolio_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> list[HoldingValuationRead]:
    service = PortfolioService(session)
    if await service.get(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    holdings = await service.list_holdings(portfolio_id)
    valued = await service.value_holdings(holdings)
    return [
        HoldingValuationRead(
            id=row["holding"].id,
            portfolio_id=portfolio_id,
            stock_id=row["holding"].stock_id,
            quantity=row["quantity"],
            average_price=row["average_price"],
            purchase_date=row["holding"].purchase_date,
            broker_order_id=row["holding"].broker_order_id,
            notes=row["holding"].notes,
            created_at=row["holding"].created_at,
            updated_at=row["holding"].updated_at,
            symbol=row["stock"].symbol,
            company_name=row["stock"].company_name,
            latest_close=row["latest_close"],
            previous_close=row["previous_close"],
            invested=row["invested"],
            current_value=row["current_value"],
            pl=row["pl"],
            roi_pct=row["roi_pct"],
            today_pl=row["today_pl"],
            allocation_pct=row["allocation_pct"],
        )
        for row in valued
    ]


@router.post(
    "/{portfolio_id}/holdings", response_model=HoldingRead, dependencies=WRITE_DEPENDENCIES
)
async def add_holding(
    portfolio_id: int,
    payload: HoldingCreate,
    session: AsyncSession = Depends(get_db_session),
) -> HoldingRead:
    service = PortfolioService(session)
    try:
        holding = await service.add_holding(portfolio_id, payload.model_dump())
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HoldingRead.model_validate(holding)


@router.put(
    "/{portfolio_id}/holdings/{holding_id}",
    response_model=HoldingRead,
    dependencies=WRITE_DEPENDENCIES,
)
async def update_holding(
    portfolio_id: int,
    holding_id: int,
    payload: HoldingUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> HoldingRead:
    service = PortfolioService(session)
    try:
        holding = await service.update_holding(
            portfolio_id, holding_id, payload.model_dump(exclude_unset=True)
        )
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HoldingRead.model_validate(holding)


@router.delete(
    "/{portfolio_id}/holdings/{holding_id}",
    status_code=204,
    dependencies=WRITE_DEPENDENCIES,
)
async def remove_holding(
    portfolio_id: int,
    holding_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    service = PortfolioService(session)
    try:
        await service.remove_holding(portfolio_id, holding_id)
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
