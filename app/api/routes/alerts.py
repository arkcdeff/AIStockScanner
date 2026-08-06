from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.schemas.alert import (
    AlertEventRead,
    AlertEventUpdate,
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
    AlertRunResult,
)
from app.services.alert import AlertEngineError, AlertService

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"],
    dependencies=[Depends(require_permission("alerts.read"))],
)
WRITE_DEPENDENCIES = [Depends(require_permission("alerts.write"))]


def _rule_to_read(rule) -> AlertRuleRead:
    item = AlertRuleRead.model_validate(rule)
    return item


@router.get("/rules", response_model=list[AlertRuleRead])
async def list_rules(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[AlertRuleRead]:
    service = AlertService(session)
    rules = await service.list_rules(limit=limit, offset=offset)
    return [_rule_to_read(rule) for rule in rules]


@router.post("/rules", response_model=AlertRuleRead, dependencies=WRITE_DEPENDENCIES)
async def create_rule(
    payload: AlertRuleCreate,
    session: AsyncSession = Depends(get_db_session),
) -> AlertRuleRead:
    service = AlertService(session)
    try:
        rule = await service.create_rule(payload.model_dump())
    except AlertEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _rule_to_read(rule)


@router.get("/rules/{rule_id}", response_model=AlertRuleRead)
async def get_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> AlertRuleRead:
    service = AlertService(session)
    rule = await service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return _rule_to_read(rule)


@router.put("/rules/{rule_id}", response_model=AlertRuleRead, dependencies=WRITE_DEPENDENCIES)
async def update_rule(
    rule_id: int,
    payload: AlertRuleUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> AlertRuleRead:
    service = AlertService(session)
    try:
        rule = await service.update_rule(rule_id, payload.model_dump(exclude_unset=True))
    except AlertEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _rule_to_read(rule)


@router.delete(
    "/rules/{rule_id}", status_code=204, dependencies=WRITE_DEPENDENCIES
)
async def delete_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    service = AlertService(session)
    try:
        await service.delete_rule(rule_id)
    except AlertEngineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/run", response_model=AlertRunResult, dependencies=WRITE_DEPENDENCIES)
async def run_alert_engine(
    session: AsyncSession = Depends(get_db_session),
) -> AlertRunResult:
    service = AlertService(session)
    result = await service.run_for_trade_date()
    return AlertRunResult(**result)


@router.get("/events", response_model=list[AlertEventRead])
async def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> list[AlertEventRead]:
    service = AlertService(session)
    events = await service.list_recent_events(limit=limit, offset=offset, status=status)
    return [_event_to_read(event) for event in events]


@router.get("/events/pending", response_model=list[AlertEventRead])
async def list_pending_events(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[AlertEventRead]:
    service = AlertService(session)
    events = await service.list_recent_events(limit=limit, status="triggered")
    return [_event_to_read(event) for event in events]


@router.get("/events/symbol/{symbol}", response_model=list[AlertEventRead])
async def list_events_for_symbol(
    symbol: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> list[AlertEventRead]:
    service = AlertService(session)
    events = await service.list_events_for_symbol(symbol, limit=limit)
    return [_event_to_read(event) for event in events]


@router.patch(
    "/events/{event_id}",
    response_model=AlertEventRead,
    dependencies=WRITE_DEPENDENCIES,
)
async def update_event_status(
    event_id: int,
    payload: AlertEventUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> AlertEventRead:
    service = AlertService(session)
    event = await service.update_event_status(event_id, payload.status)
    if event is None:
        raise HTTPException(status_code=404, detail="Alert event not found")
    return _event_to_read(event)


def _event_to_read(event) -> AlertEventRead:
    item = AlertEventRead.model_validate(event)
    item.rule_name = event.alert_rule.name if event.alert_rule else None
    item.symbol = event.stock.symbol if event.stock else None
    return item
