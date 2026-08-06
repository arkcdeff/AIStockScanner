from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.models.stock import Stock
from app.repositories.scan import ScanDefinitionRepository, ScanResultRepository
from app.schemas.scanner import ScanAllResponse, ScanDefinitionRead, ScanResultRead, ScanRunResponse
from app.services.scanner_engine import ScannerService

router = APIRouter(
    prefix="/scanners",
    tags=["Scanners"],
    dependencies=[Depends(require_permission("scanners.read"))],
)
RUN_DEPENDENCIES = [Depends(require_permission("scanners.run"))]


@router.get("", response_model=list[ScanDefinitionRead])
async def list_scanners(
    session: AsyncSession = Depends(get_db_session),
) -> list[ScanDefinitionRead]:
    scan_defs = await ScanDefinitionRepository(session).list_active()
    return [ScanDefinitionRead.model_validate(sd) for sd in scan_defs]


@router.post(
    "/run",
    response_model=ScanRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=RUN_DEPENDENCIES,
)
async def run_scanner(
    scan_code: str = Query(..., min_length=1, max_length=80),
    session: AsyncSession = Depends(get_db_session),
) -> ScanRunResponse:
    service = ScannerService(session)
    start = __import__("time").perf_counter()
    try:
        results = await service.run_scanner(scan_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await service.persist_results({scan_code: results})
    elapsed_ms = (__import__("time").perf_counter() - start) * 1000

    scan_def = await ScanDefinitionRepository(session).get_by_code(scan_code)
    scan_name = scan_def.name if scan_def else scan_code

    stock_ids = [r.stock_id for r in results]
    stocks = {}
    if stock_ids:
        stock_rows = (
            await session.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids)))
        ).fetchall()
        stocks = {row[0]: row[1] for row in stock_rows}

    return ScanRunResponse(
        scanner=scan_name,
        matched_stocks=len(results),
        score=sum(r.score for r in results) / len(results) if results else 0.0,
        execution_time_ms=round(elapsed_ms, 2),
        results=[
            ScanResultRead(
                id=0,
                scan_definition_id=r.scan_definition_id,
                stock_id=r.stock_id,
                symbol=stocks.get(r.stock_id, ""),
                scan_date=r.scan_date,
                score=r.score,
                metrics=r.metrics,
            )
            for r in results
        ],
    )


@router.post(
    "/run-all",
    response_model=ScanAllResponse,
    status_code=status.HTTP_200_OK,
    dependencies=RUN_DEPENDENCIES,
)
async def run_all_scanners(
    session: AsyncSession = Depends(get_db_session),
) -> ScanAllResponse:
    service = ScannerService(session)
    start = __import__("time").perf_counter()
    results = await service.run_all()
    await service.persist_results(results)
    elapsed_ms = (__import__("time").perf_counter() - start) * 1000

    total_matched = sum(len(v) for v in results.values())
    formatted: dict[str, list[ScanResultRead]] = {}
    stock_ids = set()
    for entries in results.values():
        for e in entries:
            stock_ids.add(e.stock_id)

    stocks = {}
    if stock_ids:
        stock_rows = (
            await session.execute(
                select(Stock.id, Stock.symbol).where(Stock.id.in_(list(stock_ids)))
            )
        ).fetchall()
        stocks = {row[0]: row[1] for row in stock_rows}

    for code, entries in results.items():
        formatted[code] = [
            ScanResultRead(
                id=0,
                scan_definition_id=e.scan_definition_id,
                stock_id=e.stock_id,
                symbol=stocks.get(e.stock_id, ""),
                scan_date=e.scan_date,
                score=e.score,
                metrics=e.metrics,
            )
            for e in entries
        ]

    return ScanAllResponse(
        total_scanners=len(results),
        total_matched=total_matched,
        execution_time_ms=round(elapsed_ms, 2),
        results=formatted,
    )


@router.get("/results", response_model=list[ScanResultRead])
async def list_scan_results(
    scan_code: str | None = Query(default=None, min_length=1, max_length=80),
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[ScanResultRead]:
    results = await ScanResultRepository(session).list_results(
        scan_code=scan_code,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )

    stock_ids = list({r.stock_id for r in results})
    stocks = {}
    if stock_ids:
        stock_rows = (
            await session.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids)))
        ).fetchall()
        stocks = {row[0]: row[1] for row in stock_rows}

    return [
        ScanResultRead(
            id=r.id,
            scan_definition_id=r.scan_definition_id,
            stock_id=r.stock_id,
            symbol=stocks.get(r.stock_id, ""),
            scan_date=r.scan_date,
            score=r.score,
            metrics=r.metrics,
        )
        for r in results
    ]
