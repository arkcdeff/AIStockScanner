from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.market_data import MarketDataRepository
from app.schemas.market_data import (
    DailyPriceRead,
    DownloadRequest,
    DownloadResponse,
    ImportJobRead,
    MarketDataSummary,
)
from app.services.nse_downloader import NSEDownloadError, NSEDownloader
from app.services.nse_import import NSEImportService

router = APIRouter(prefix="/market-data", tags=["Market Data"])


@router.post("/download", response_model=DownloadResponse, status_code=status.HTTP_200_OK)
async def download_bhavcopy(
    payload: DownloadRequest,
) -> DownloadResponse:
    try:
        with NSEDownloader() as downloader:
            path, meta = downloader.download(payload.trade_date)
    except NSEDownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DownloadResponse(
        trade_date=payload.trade_date,
        path=meta["path"],
        size=meta["size"],
        checksum=meta["checksum"],
        downloaded=meta["downloaded"],
    )


@router.post("/download/latest", response_model=DownloadResponse, status_code=status.HTTP_200_OK)
async def download_latest_bhavcopy() -> DownloadResponse:
    try:
        with NSEDownloader() as downloader:
            trade_date, path, meta = downloader.download_latest()
    except NSEDownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DownloadResponse(
        trade_date=trade_date,
        path=meta["path"],
        size=meta["size"],
        checksum=meta["checksum"],
        downloaded=meta["downloaded"],
    )


@router.post("/import-file", response_model=ImportJobRead, status_code=status.HTTP_201_CREATED)
async def import_file(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> ImportJobRead:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(file_bytes) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail="File too large")

    job = await NSEImportService(session).import_bytes(
        bhavcopy=file_bytes,
        bhavcopy_name=file.filename,
    )
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error_message)
    return ImportJobRead.model_validate(job)


@router.post("/import/nse", response_model=ImportJobRead, status_code=status.HTTP_201_CREATED)
async def import_nse_files(
    bhavcopy: UploadFile = File(...),
    delivery: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ImportJobRead:
    bhavcopy_bytes = await bhavcopy.read()
    if not bhavcopy_bytes:
        raise HTTPException(status_code=400, detail="Bhavcopy file is empty")
    delivery_bytes = await delivery.read() if delivery else None
    job = await NSEImportService(session).import_bytes(
        bhavcopy=bhavcopy_bytes,
        bhavcopy_name=bhavcopy.filename or "bhavcopy.csv",
        delivery=delivery_bytes,
        delivery_name=delivery.filename if delivery else None,
    )
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error_message)
    return ImportJobRead.model_validate(job)


@router.get("/prices", response_model=list[DailyPriceRead])
async def list_prices(
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    trade_date: date | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),
) -> list[DailyPriceRead]:
    rows = await MarketDataRepository(session).list_prices(
        symbol=symbol, trade_date=trade_date, offset=offset, limit=limit
    )
    return [
        DailyPriceRead(
            id=price.id,
            symbol=stock.symbol,
            series=stock.series,
            trade_date=price.trade_date,
            open=price.open,
            high=price.high,
            low=price.low,
            close=price.close,
            previous_close=price.previous_close,
            last_price=price.last_price,
            volume=price.volume,
            turnover=price.turnover,
            trade_count=price.trade_count,
            delivery_quantity=price.delivery_quantity,
            delivery_percentage=price.delivery_percentage,
        )
        for price, stock in rows
    ]


@router.get("/imports", response_model=list[ImportJobRead])
async def list_imports(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[ImportJobRead]:
    jobs = await MarketDataRepository(session).list_imports(limit)
    return [ImportJobRead.model_validate(job) for job in jobs]


@router.get("/summary", response_model=MarketDataSummary)
async def market_data_summary(
    session: AsyncSession = Depends(get_db_session),
) -> MarketDataSummary:
    return MarketDataSummary.model_validate(await MarketDataRepository(session).summary())
