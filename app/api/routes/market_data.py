from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.market_data import MarketDataRepository
from app.schemas.bulk_block_deal import (
    BulkBlockDealPage,
    BulkBlockDealRead,
    BulkBlockDealSummary,
)
from app.schemas.market_data import (
    BackfillRequest,
    CoverageRow,
    DailyPriceRead,
    DownloadRequest,
    DownloadResponse,
    ImportJobRead,
    MarketDataSummary,
    ScannerReadiness,
)
from app.services.market_data_service import MarketDataService
from app.services.nse_downloader import NSEDownloader, NSEDownloadError
from app.services.nse_import import NSEImportService

router = APIRouter(
    prefix="/market-data",
    tags=["Market Data"],
    dependencies=[Depends(require_permission("stocks.read"))],
)
WRITE_DEPENDENCIES = [Depends(require_permission("market_data.write"))]


@router.post(
    "/download",
    response_model=DownloadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=WRITE_DEPENDENCIES,
)
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


@router.post(
    "/download/latest",
    response_model=DownloadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=WRITE_DEPENDENCIES,
)
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


@router.post(
    "/import-file",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=WRITE_DEPENDENCIES,
)
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


@router.post(
    "/import/nse",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=WRITE_DEPENDENCIES,
)
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


@router.post(
    "/backfill",
    status_code=status.HTTP_200_OK,
    dependencies=WRITE_DEPENDENCIES,
)
async def backfill_missing_dates(
    payload: BackfillRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.services.nse_historical import MAX_RANGE_DAYS

    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=422, detail="start_date must be <= end_date")
    if (payload.end_date - payload.start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Date range exceeds maximum of {MAX_RANGE_DAYS} days",
        )
    service = MarketDataService(session)
    return await service.backfill(payload.start_date, payload.end_date)


@router.get("/coverage", response_model=list[CoverageRow])
async def market_data_coverage(
    start_date: date = Query(...),
    end_date: date = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> list[CoverageRow]:
    from app.services.nse_historical import MAX_RANGE_DAYS

    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be <= end_date")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Date range exceeds maximum of {MAX_RANGE_DAYS} days",
        )
    rows = await MarketDataService(session).coverage(start_date, end_date)
    return [CoverageRow(**row) for row in rows]


@router.get("/readiness", response_model=ScannerReadiness)
async def scanner_readiness(
    session: AsyncSession = Depends(get_db_session),
) -> ScannerReadiness:
    return ScannerReadiness.model_validate(
        await MarketDataService(session).scanner_readiness()
    )


@router.get("/status")
async def market_data_status(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await MarketDataService(session).data_status()


@router.get("/derivatives/summary")
async def derivatives_contract_summary(
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.services.historical_query import HistoricalCoverageService
    return await HistoricalCoverageService(session).get_derivative_contract_coverage(as_of_date)


@router.get("/derivatives/futures/coverage")
async def derivatives_futures_coverage(
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.services.historical_query import HistoricalCoverageService
    return await HistoricalCoverageService(session).get_futures_price_coverage(as_of_date)


@router.get("/derivatives/options/coverage")
async def derivatives_options_coverage(
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.services.historical_query import HistoricalCoverageService
    return await HistoricalCoverageService(session).get_options_price_coverage(as_of_date)


@router.get("/derivatives/options/chain")
async def derivatives_option_chain(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return historical EOD option chain for underlying, trade date, and expiry."""
    from dataclasses import asdict

    from app.services.historical_option_chain import HistoricalOptionChainService

    service = HistoricalOptionChainService(session)
    try:
        chain = await service.get_historical_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return asdict(chain)


@router.get("/derivatives/options/expiries")
async def derivatives_available_expiries(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return list of ISO expiry dates available for underlying on trade_date."""
    from app.services.historical_option_chain import HistoricalOptionChainService

    service = HistoricalOptionChainService(session)
    expiries = await service.get_available_expiries(
        symbol=symbol, trade_date=trade_date, as_of_date=as_of_date
    )
    items = [
        {
            "expiry_date": d.isoformat(),
            "dte": (d - trade_date).days,
            "is_nearest": idx == 0,
        }
        for idx, d in enumerate(expiries)
    ]
    return {
        "symbol": symbol.upper(),
        "trade_date": trade_date.isoformat(),
        "expiries": [d.isoformat() for d in expiries],
        "items": items,
    }


@router.get("/derivatives/options/oi-intelligence")
async def derivatives_oi_intelligence(
    symbol: str,
    trade_date: date,
    expiry_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return deterministic Open Interest Intelligence snapshot."""
    from app.services.historical_oi_intelligence_service import HistoricalOIIntelligenceService

    service = HistoricalOIIntelligenceService(session)
    try:
        snapshot = await service.get_oi_intelligence(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return snapshot.model_dump(mode="json")


@router.get("/derivatives/futures/analytics")
async def derivatives_futures_analytics(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return futures curve analytics for symbol and trade_date."""
    from dataclasses import asdict

    from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService

    service = HistoricalFuturesAnalyticsService(session)
    res = await service.get_underlying_futures_snapshot(
        symbol=symbol, trade_date=trade_date, as_of_date=as_of_date
    )
    return asdict(res)


@router.get("/derivatives/futures/rollover")
async def derivatives_futures_rollover(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return futures rollover analytics for symbol and trade_date."""
    from dataclasses import asdict

    from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService

    service = HistoricalFuturesAnalyticsService(session)
    res = await service.get_rollover_analytics(
        symbol=symbol, trade_date=trade_date, as_of_date=as_of_date
    )
    return asdict(res)


@router.get("/derivatives/options/analytics")
async def derivatives_options_analytics(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return options analytics (PCR, Max Pain, OI Walls) for expiry."""
    from dataclasses import asdict

    from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

    service = HistoricalOptionsAnalyticsService(session)
    try:
        res = await service.get_option_chain_analytics(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return asdict(res)


@router.get("/derivatives/options/expiry-analytics")
async def derivatives_options_expiry_analytics(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return cross-expiry options term structure analytics."""
    from dataclasses import asdict

    from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

    service = HistoricalOptionsAnalyticsService(session)
    res = await service.get_expiry_options_analytics(
        symbol=symbol, trade_date=trade_date, as_of_date=as_of_date
    )
    return asdict(res)


@router.get("/derivatives/participants")
async def derivatives_participant_snapshot(
    report_date: date,
    dataset_type: str = "OPEN_INTEREST",
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return point-in-time snapshot of participant positioning or volume."""
    from dataclasses import asdict

    from app.services.historical_participant_analytics import (
        HistoricalParticipantAnalyticsService,
    )

    if dataset_type not in ("OPEN_INTEREST", "TRADING_VOLUME"):
        msg = "dataset_type must be OPEN_INTEREST or TRADING_VOLUME"
        raise HTTPException(status_code=400, detail=msg)

    service = HistoricalParticipantAnalyticsService(session)
    snapshot = await service.get_participant_snapshot(
        report_date=report_date,
        dataset_type=dataset_type,
        as_of_date=as_of_date,
    )
    return asdict(snapshot)


@router.get("/derivatives/participants/history")
async def derivatives_participant_history(
    start_date: date,
    end_date: date,
    dataset_type: str = "OPEN_INTEREST",
    participant_type: str = "FII",
    instrument_bucket: str | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return participant category time-series positioning history."""
    from dataclasses import asdict

    from app.services.historical_participant_analytics import (
        HistoricalParticipantAnalyticsService,
    )

    service = HistoricalParticipantAnalyticsService(session)
    rows = await service.get_participant_time_series(
        start_date=start_date,
        end_date=end_date,
        dataset_type=dataset_type,
        participant_type=participant_type,
        instrument_bucket=instrument_bucket,
        as_of_date=as_of_date,
    )
    return [asdict(r) for r in rows]


@router.get("/derivatives/fii")
async def fii_derivatives_statistics(
    report_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return FII derivatives summary statistics for report_date."""
    from app.repositories.derivative_participant import DerivativeParticipantRepository

    repo = DerivativeParticipantRepository(session)
    as_of = as_of_date or report_date
    stats = await repo.get_fii_statistics_by_date(
        report_date=report_date, as_of_date=as_of
    )
    return {
        "fii_statistics": [
            {
                "report_date": s.report_date.isoformat(),
                "instrument_category": s.instrument_category,
                "buy_contracts": s.buy_contracts,
                "buy_amount": s.buy_amount,
                "sell_contracts": s.sell_contracts,
                "sell_amount": s.sell_amount,
                "open_interest_contracts": s.open_interest_contracts,
                "open_interest_amount": s.open_interest_amount,
            }
            for s in stats
        ]
    }


@router.get("/derivatives/coverage")
async def derivatives_platform_coverage(
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return 7-dimension derivatives platform coverage summary."""
    from dataclasses import asdict

    from app.services.derivative_coverage_service import DerivativeCoverageService

    service = DerivativeCoverageService(session)
    summary = await service.get_derivatives_coverage(as_of_date=as_of_date)
    return asdict(summary)


@router.get("/derivatives/quality")
async def derivatives_data_quality_issues(
    start_date: date | None = None,
    end_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return platform data quality issue records."""
    from app.services.derivative_coverage_service import DerivativeCoverageService

    service = DerivativeCoverageService(session)
    return await service.get_quality_summary(limit=100)


@router.get("/derivatives/replay")
async def historical_derivatives_replay(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return point-in-time safe historical derivatives knowledge state."""
    from dataclasses import asdict

    from app.services.historical_derivatives_replay import (
        HistoricalDerivativesReplayService,
    )

    service = HistoricalDerivativesReplayService(session)
    replay = await service.get_historical_replay(
        symbol=symbol,
        trade_date=trade_date,
        as_of_date=as_of_date,
    )
    return asdict(replay)


# ---------------------------------------------------------------------------
# Fundamental & Corporate Intelligence Endpoints (Sprint 19.1E)
# ---------------------------------------------------------------------------


@router.get("/fundamentals/coverage")
async def fundamentals_platform_coverage(
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return platform fundamentals and corporate intelligence coverage summary."""
    from app.services.fundamental_corporate_service import FundamentalCorporateService

    service = FundamentalCorporateService(session)
    summary = await service.get_fundamentals_coverage(as_of_date=as_of_date)
    return summary.model_dump()


@router.get("/fundamentals/{symbol}")
async def company_fundamental_snapshot(
    symbol: str,
    evaluation_date: date | None = None,
    as_of_date: date | None = None,
    statement_basis: str = "CONSOLIDATED",
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return point-in-time safe company fundamental snapshot."""
    from dataclasses import asdict

    from app.services.fundamental_corporate_service import FundamentalCorporateService

    eval_d = evaluation_date or date.today()
    service = FundamentalCorporateService(session)
    try:
        snapshot = await service.get_company_fundamental_snapshot(
            symbol=symbol,
            evaluation_date=eval_d,
            as_of_date=as_of_date,
            statement_basis=statement_basis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return asdict(snapshot)


@router.get("/fundamentals/{symbol}/history")
async def company_financial_history(
    symbol: str,
    period_type: str | None = None,
    statement_basis: str = "CONSOLIDATED",
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return PIT historical financial statements for symbol."""
    from app.repositories.fundamental_corporate import FundamentalCorporateRepository
    from app.schemas.fundamental_corporate import FinancialStatementRead

    repo = FundamentalCorporateRepository(session)
    stock = await repo.get_stock_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Unknown stock symbol '{symbol}'")

    stmts = await repo.get_financial_history(
        stock_id=stock.id,
        period_type=period_type,
        statement_basis=statement_basis,
        as_of_date=as_of_date,
        limit=20,
    )
    return [FinancialStatementRead.model_validate(s).model_dump() for s in stmts]


@router.get("/corporate/{symbol}/announcements")
async def company_corporate_announcements(
    symbol: str,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return corporate announcements for symbol."""
    from app.repositories.fundamental_corporate import FundamentalCorporateRepository
    from app.schemas.fundamental_corporate import CorporateAnnouncementRead

    repo = FundamentalCorporateRepository(session)
    stock = await repo.get_stock_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Unknown stock symbol '{symbol}'")

    anns = await repo.get_corporate_announcements(stock_id=stock.id, as_of_date=as_of_date, limit=20)
    return [CorporateAnnouncementRead.model_validate(a).model_dump() for a in anns]


@router.get("/corporate/{symbol}/shareholding")
async def company_shareholding_pattern(
    symbol: str,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return shareholding pattern history for symbol."""
    from app.repositories.fundamental_corporate import FundamentalCorporateRepository
    from app.schemas.fundamental_corporate import ShareholdingPatternRead

    repo = FundamentalCorporateRepository(session)
    stock = await repo.get_stock_by_symbol(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Unknown stock symbol '{symbol}'")

    sh_list = await repo.get_shareholding_history(stock_id=stock.id, as_of_date=as_of_date, limit=12)
    return [ShareholdingPatternRead.model_validate(p).model_dump() for p in sh_list]


# ---------------------------------------------------------------------------
# 19.1F — News + Government Policy Intelligence Endpoints
# ---------------------------------------------------------------------------


@router.get("/news/coverage")
async def news_policy_coverage(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return summary coverage metrics for news & government policy intelligence."""
    from app.services.news_policy_service import NewsPolicyService

    service = NewsPolicyService(session)
    cov = await service.get_news_coverage()
    return cov.model_dump()


@router.get("/news/{symbol}/snapshot")
async def company_news_policy_snapshot(
    symbol: str,
    evaluation_datetime: datetime | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return point-in-time safe company news & policy intelligence snapshot."""
    from dataclasses import asdict

    from app.services.news_policy_service import NewsPolicyService

    service = NewsPolicyService(session)
    try:
        snapshot = await service.get_company_news_policy_snapshot(
            symbol=symbol,
            evaluation_datetime=evaluation_datetime,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return asdict(snapshot)


@router.get("/news/{symbol}")
async def company_news_events(
    symbol: str,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return PIT news events for symbol."""

    from app.services.news_policy_service import NewsPolicyService

    eval_dt = datetime.combine(as_of_date or date.today(), datetime.min.time(), tzinfo=UTC)
    service = NewsPolicyService(session)
    try:
        snap = await service.get_company_news_policy_snapshot(symbol=symbol, evaluation_datetime=eval_dt)
        return [e.model_dump() for e in snap.recent_events]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/policy/market-snapshot")
async def market_news_policy_snapshot(
    evaluation_datetime: datetime | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return PIT news & policy snapshot for the market."""
    from dataclasses import asdict

    from app.services.news_policy_service import NewsPolicyService

    service = NewsPolicyService(session)
    snap = await service.get_market_news_policy_snapshot(
        evaluation_datetime=evaluation_datetime,
        as_of_date=as_of_date,
    )
    return asdict(snap)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 19.1G — Stock 360° + Opportunity Intelligence Endpoints
# ---------------------------------------------------------------------------


@router.get("/stocks/{symbol}/360")
async def stock360_snapshot(
    symbol: str,
    evaluation_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return point-in-time safe unified Stock 360° research snapshot."""
    from dataclasses import asdict

    from app.services.stock360_service import Stock360Service

    service = Stock360Service(session)
    try:
        snap = await service.get_stock360_snapshot(
            symbol=symbol,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return asdict(snap)


@router.get("/stocks/{symbol}/what-changed")
async def stock_what_changed(
    symbol: str,
    evaluation_date: date | None = Query(default=None, description="Evaluation date (defaults to latest available)"),
    comparison_window: int = Query(default=1, ge=1, le=20, description="Comparison window in trading sessions (default 1)"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return deterministic What Changed analysis for a stock."""
    from app.services.what_changed_service import WhatChangedService

    service = WhatChangedService(session)
    try:
        result = await service.detect_changes(
            symbol=symbol,
            evaluation_date=evaluation_date,
            comparison_window=comparison_window,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result.model_dump(mode="json")


@router.get("/opportunities")
async def rank_opportunities(
    profile: str = Query(default="BREAKOUT"),
    evaluation_date: date | None = None,
    as_of_date: date | None = None,
    sector: str | None = None,
    fno_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Rank research candidates for an opportunity profile."""
    from app.services.opportunity_ranking_service import OpportunityRankingService

    service = OpportunityRankingService(session)
    summary = await service.rank_opportunities(
        profile=profile,
        evaluation_date=evaluation_date,
        as_of_date=as_of_date,
        sector_name=sector,
        fno_only=fno_only,
        limit=limit,
    )
    return summary.model_dump()


@router.get("/opportunities/{symbol}")
async def evaluate_stock_opportunity(
    symbol: str,
    profile: str = Query(default="BREAKOUT"),
    evaluation_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Evaluate specific stock for an opportunity profile."""
    from dataclasses import asdict

    from app.services.opportunity_engine import OpportunityEngine
    from app.services.stock360_service import Stock360Service

    service = Stock360Service(session)
    try:
        snap = await service.get_stock360_snapshot(
            symbol=symbol,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
        )
        assessment = OpportunityEngine.evaluate_profile(snap, profile)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return asdict(assessment)


# ---------------------------------------------------------------------------
# 19.1G-A1 — Advanced Options Foundation Endpoints
# ---------------------------------------------------------------------------


@router.get("/derivatives/options/advanced-chain")
async def advanced_option_chain(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return point-in-time EOD option chain enriched with IV, Greeks, Moneyness, Breakeven, & Liquidity."""
    from app.services.advanced_options_service import AdvancedOptionsService

    service = AdvancedOptionsService(session)
    try:
        rows = await service.get_enriched_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        return [r.model_dump() for r in rows]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/iv")
async def option_iv_smile(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return IV Smile & Skew data points across strikes for an expiry."""
    from app.services.advanced_options_service import AdvancedOptionsService

    service = AdvancedOptionsService(session)
    try:
        items = await service.get_iv_smile(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        return [i.model_dump() for i in items]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/term-structure")
async def option_iv_term_structure(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return IV Term Structure across actual available expiries."""
    from app.services.advanced_options_service import AdvancedOptionsService

    service = AdvancedOptionsService(session)
    try:
        items = await service.get_iv_term_structure(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )
        return [i.model_dump() for i in items]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/expected-move")
async def option_expected_move(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return Expected Move analytics (Primary IV S*sqrt(T) and Straddle-derived)."""
    from app.services.advanced_options_service import AdvancedOptionsService

    service = AdvancedOptionsService(session)
    try:
        em = await service.get_expected_move(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        return em.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/liquidity")
async def option_chain_liquidity(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return Option Liquidity & Spread assessment per contract."""
    from app.services.advanced_options_service import AdvancedOptionsService

    service = AdvancedOptionsService(session)
    try:
        rows = await service.get_enriched_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        res: list[dict] = []
        for r in rows:
            if r.call:
                res.append(r.call.liquidity.model_dump())
            if r.put:
                res.append(r.put.liquidity.model_dump())
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/exposure")
async def options_exposure_snapshot(
    symbol: str,
    trade_date: date,
    expiry_date: date | None = None,
    as_of_date: date | None = None,
    assumption_profile: str = "GROSS_UNSIGNED",
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return comprehensive Advanced Exposure Snapshot."""
    from app.services.advanced_exposure_service import AdvancedExposureService

    service = AdvancedExposureService(session)
    try:
        snapshot = await service.get_exposure_snapshot(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            assumption_profile=assumption_profile,
        )
        return snapshot.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/gex")
async def options_strike_gex(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    assumption_profile: str = "GROSS_UNSIGNED",
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return strike-level GEX table for an underlying and expiry."""
    from app.services.advanced_exposure_service import AdvancedExposureService

    service = AdvancedExposureService(session)
    try:
        rows = await service.get_strike_exposure_table(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            assumption_profile=assumption_profile,
        )
        return [r.model_dump() for r in rows]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/gamma-profile")
async def options_gamma_profile(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    assumption_profile: str = "DEALER_SHORT_CALL_LONG_PUT_HEURISTIC",
    spot_range_pct: float = 10.0,
    spot_step_pct: float = 0.5,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return spot scenario grid Gamma Profile (S +/- range_pct, STATIC_IV)."""
    from app.services.advanced_exposure_service import AdvancedExposureService

    service = AdvancedExposureService(session)
    try:
        pts = await service.get_gamma_profile(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            assumption_profile=assumption_profile,
            spot_range_pct=spot_range_pct,
            spot_step_pct=spot_step_pct,
        )
        return [p.model_dump() for p in pts]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/gamma-flip")
async def options_gamma_flip(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    assumption_profile: str = "DEALER_SHORT_CALL_LONG_PUT_HEURISTIC",
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return Scenario Gamma Flip (Zero Gamma) level detection result."""
    from app.services.advanced_exposure_service import AdvancedExposureService

    service = AdvancedExposureService(session)
    try:
        res = await service.get_gamma_flip(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            assumption_profile=assumption_profile,
        )
        return res.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/dex")
async def options_strike_dex(
    symbol: str,
    trade_date: date,
    expiry_date: date,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return strike-level DEX table for an underlying and expiry."""
    from app.services.advanced_exposure_service import AdvancedExposureService

    service = AdvancedExposureService(session)
    try:
        rows = await service.get_strike_exposure_table(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
            assumption_profile="GROSS_UNSIGNED",
        )
        return [
            {
                "strike_price": str(r.strike_price),
                "is_atm": r.is_atm,
                "gross_dex": str(r.gross_dex),
                "net_scenario_dex": str(r.net_scenario_dex),
            }
            for r in rows
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/opportunity")
async def options_opportunity_assessment(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    setup_profile: str = "SWING",
    expiry_scope: str = "FRONT_EXPIRY",
    max_alternatives: int = 3,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return aggregate Options Research & Opportunity Assessment."""
    from app.services.options_opportunity_service import OptionOpportunityService

    service = OptionOpportunityService(session)
    try:
        res = await service.assess_opportunity(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
            setup_profile=setup_profile,
            expiry_scope=expiry_scope,
            max_alternatives=max_alternatives,
        )
        return res.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/derivatives/options/strike-ranking")
async def options_strike_ranking(
    symbol: str,
    trade_date: date,
    as_of_date: date | None = None,
    setup_profile: str = "SWING",
    max_alternatives: int = 5,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return ranked options strike candidates with explanations and veto reasons."""
    from app.services.options_opportunity_service import OptionOpportunityService

    service = OptionOpportunityService(session)
    try:
        res = await service.assess_opportunity(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
            setup_profile=setup_profile,
            max_alternatives=max_alternatives,
        )
        return {
            "symbol": res.symbol,
            "trade_date": str(res.trade_date),
            "selected_side": res.selected_side,
            "overall_classification": res.overall_classification,
            "preferred_contract": res.preferred_contract.model_dump() if res.preferred_contract else None,
            "alternative_contracts": [c.model_dump() for c in res.alternative_contracts],
            "rejected_contracts": [c.model_dump() for c in res.rejected_contracts],
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/validation/opportunities")
async def validate_stock_opportunities(
    profile: str = "SWING",
    start_date: date | None = None,
    end_date: date | None = None,
    horizon_days: int = 10,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return out-of-sample historical validation summary for stock opportunities."""
    from app.services.historical_opportunity_validation_service import (
        HistoricalOpportunityValidationService,
    )

    service = HistoricalOpportunityValidationService(session)
    res = await service.validate_opportunities(
        profile=profile,
        start_date=start_date,
        end_date=end_date,
        horizon_days=horizon_days,
    )
    return res.model_dump()


@router.get("/validation/options")
async def validate_options_opportunities(
    symbol: str = "NIFTY",
    start_date: date | None = None,
    end_date: date | None = None,
    horizon_days: int = 10,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return out-of-sample historical validation summary for options research candidates."""
    from app.services.historical_options_validation_service import (
        HistoricalOptionsValidationService,
    )

    service = HistoricalOptionsValidationService(session)
    res = await service.validate_options(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        horizon_days=horizon_days,
    )
    return res.model_dump()


@router.get("/validation/cases")
async def get_validation_cases(
    profile: str = "SWING",
    limit: int = 20,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return case-by-case paired signal snapshots and outcomes for auditing."""
    from app.services.historical_opportunity_validation_service import (
        HistoricalOpportunityValidationService,
    )

    service = HistoricalOpportunityValidationService(session)
    res = await service.validate_opportunities(profile=profile, horizon_days=10)
    return [
        {
            "profile": res.profile,
            "methodology_version": res.methodology_version,
            "sample_count": res.sample_count,
            "overall_mean_return_pct": str(res.overall_mean_return_pct),
            "overall_positive_rate": str(res.overall_positive_rate),
        }
    ]


@router.get("/validation/calibration")
async def get_validation_calibration(
    profile: str = "SWING",
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return calibration score bucket statistics and methodology performance."""
    from app.services.historical_opportunity_validation_service import (
        HistoricalOpportunityValidationService,
    )

    service = HistoricalOpportunityValidationService(session)
    res = await service.validate_opportunities(profile=profile)
    return {
        "profile": res.profile,
        "methodology_version": res.methodology_version,
        "score_buckets": [b.model_dump() for b in res.score_buckets],
        "calibration_recommendations": res.calibration_recommendations,
    }


@router.get("/coverage/certification")
async def get_coverage_certification(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return full historical data coverage certification and domain readiness audit."""
    from app.services.historical_coverage_certification_service import (
        HistoricalCoverageCertificationService,
    )

    service = HistoricalCoverageCertificationService(session)
    res = await service.certify_coverage()
    return res.model_dump()


# ============================================================
# Stage G.10: Cash Market Bulk Deals & Block Deals Endpoints
# ============================================================

@router.get(
    "/deals",
    response_model=BulkBlockDealPage,
    status_code=status.HTTP_200_OK,
    summary="Get paginated Cash Market Bulk & Block Deals",
)
async def get_bulk_block_deals(
    trade_date_from: date | None = Query(None, description="Start date of trade date range"),
    trade_date_to: date | None = Query(None, description="End date of trade date range"),
    deal_type: str | None = Query(None, description="Filter by deal type: BULK or BLOCK"),
    buy_sell: str | None = Query(None, description="Filter by transaction side: BUY or SELL"),
    symbol: str | None = Query(None, description="Filter by stock symbol"),
    stock_id: int | None = Query(None, description="Filter by stock ID"),
    client: str | None = Query(None, description="Filter by client name (case-insensitive partial match)"),
    as_of_date: date | None = Query(None, description="Point-in-time evaluation date (available_at <= as_of_date)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (max 200)"),
    session: AsyncSession = Depends(get_db_session),
) -> BulkBlockDealPage:
    """Return paginated Bulk and Block deal disclosures with strict PIT safety and summary metrics."""
    if trade_date_from and trade_date_to:
        if trade_date_from > trade_date_to:
            raise HTTPException(
                status_code=422,
                detail="trade_date_from cannot be after trade_date_to.",
            )
        if (trade_date_to - trade_date_from).days > 92:
            raise HTTPException(
                status_code=422,
                detail="Requested date window cannot exceed 92 days.",
            )

    from app.repositories.bulk_block_deal import BulkBlockDealRepository

    repo = BulkBlockDealRepository(session)
    deals, total_count = await repo.get_deals(
        trade_date_from=trade_date_from,
        trade_date_to=trade_date_to,
        deal_type=deal_type,
        buy_sell=buy_sell,
        symbol=symbol,
        stock_id=stock_id,
        client_name=client,
        as_of_date=as_of_date,
        is_current_only=True,
        page=page,
        page_size=page_size,
    )

    summary_data = await repo.get_deal_summary(
        stock_id=stock_id,
        symbol=symbol,
        trade_date_from=trade_date_from,
        trade_date_to=trade_date_to,
        as_of_date=as_of_date,
    )

    pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    return BulkBlockDealPage(
        items=[BulkBlockDealRead.model_validate(d) for d in deals],
        total=total_count,
        page=page,
        page_size=page_size,
        pages=pages,
        summary=BulkBlockDealSummary(
            total_deals=summary_data["total_deals"],
            total_value=summary_data["total_value"],
            buy_deals=summary_data["buy_deals"],
            buy_value=summary_data["buy_value"],
            sell_deals=summary_data["sell_deals"],
            sell_value=summary_data["sell_value"],
            bulk_deals=summary_data["bulk_deals"],
            block_deals=summary_data["block_deals"],
        ),
    )


@router.get(
    "/stocks/{symbol}/deals",
    response_model=BulkBlockDealPage,
    status_code=status.HTTP_200_OK,
    summary="Get Bulk & Block Deals for a specific stock",
)
async def get_stock_bulk_block_deals(
    symbol: str,
    start_date: date | None = Query(None, description="Start date of trade date range"),
    end_date: date | None = Query(None, description="End date of trade date range"),
    deal_type: str | None = Query(None, description="Filter by deal type: BULK or BLOCK"),
    as_of_date: date | None = Query(None, description="Point-in-time evaluation date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    session: AsyncSession = Depends(get_db_session),
) -> BulkBlockDealPage:
    """Return paginated Bulk and Block deal disclosures for a specific stock."""
    from sqlalchemy import select

    from app.models.stock import Stock
    from app.repositories.bulk_block_deal import BulkBlockDealRepository

    sym_upper = symbol.strip().upper()
    stock_res = await session.execute(
        select(Stock).where(Stock.symbol == sym_upper, Stock.exchange == "NSE")
    )
    stock = stock_res.scalar_one_or_none()
    if not stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock symbol '{sym_upper}' not found.",
        )

    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date cannot be after end_date.",
        )

    repo = BulkBlockDealRepository(session)
    deals, total_count = await repo.get_deals_for_stock(
        stock_id=stock.id,
        symbol=stock.symbol,
        start_date=start_date,
        end_date=end_date,
        deal_type=deal_type,
        as_of_date=as_of_date,
        is_current_only=True,
        page=page,
        page_size=page_size,
    )

    summary_data = await repo.get_deal_summary(
        stock_id=stock.id,
        symbol=stock.symbol,
        trade_date_from=start_date,
        trade_date_to=end_date,
        as_of_date=as_of_date,
    )

    pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    return BulkBlockDealPage(
        items=[BulkBlockDealRead.model_validate(d) for d in deals],
        total=total_count,
        page=page,
        page_size=page_size,
        pages=pages,
        summary=BulkBlockDealSummary(
            total_deals=summary_data["total_deals"],
            total_value=summary_data["total_value"],
            buy_deals=summary_data["buy_deals"],
            buy_value=summary_data["buy_value"],
            sell_deals=summary_data["sell_deals"],
            sell_value=summary_data["sell_value"],
            bulk_deals=summary_data["bulk_deals"],
            block_deals=summary_data["block_deals"],
        ),
    )
