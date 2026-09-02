from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import finalize_page_response, stage_csrf_token
from app.auth import csrf
from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.market_data import MarketDataRepository
from app.services.market_data_service import MarketDataService


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


def _flash_url(**params: str | int) -> str:
    parts = [f"{k}={v}" for k, v in params.items()]
    return "/market-data?" + "&".join(parts) if parts else "/market-data"


router = APIRouter(dependencies=[Depends(require_browser_permission("stocks.read"))])
WRITE_DEPENDENCIES = [Depends(require_browser_permission("market_data.write"))]


def _require_csrf(request: Request, form: dict | None = None) -> None:
    csrf.verify_request_csrf(request, form)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def market_data_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    service = MarketDataService(session)
    summary = await repo.product_summary()
    imports = await repo.list_imports(limit=15)
    prices = await repo.list_prices(limit=10, product_only=True)
    status = await service.data_status()
    readiness = await service.scanner_readiness()

    today = datetime.now(UTC).date()
    coverage_start = today - timedelta(days=30)
    coverage = await service.coverage(coverage_start, today)
    coverage_summary = {
        "complete": sum(1 for row in coverage if row["status"] == "complete"),
        "partial": sum(1 for row in coverage if row["status"] == "partial"),
        "missing": sum(1 for row in coverage if row["status"] == "missing"),
    }

    price_rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "close": price.close,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    csrf_token = stage_csrf_token(request)
    html = _render(
        "market_data.html",
        {
            "request": request,
            "summary": summary,
            "status": status,
            "readiness": readiness,
            "imports": imports,
            "prices": price_rows,
            "coverage": coverage,
            "coverage_summary": coverage_summary,
            "settings_eod_catchup_days": settings.eod_scheduler_catchup_max_days,
            "csrf_token": csrf_token,
        },
    )
    return finalize_page_response(request, HTMLResponse(content=html))


@router.get("/derivatives", response_class=HTMLResponse)
async def derivatives_workspace_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Full-page wrapper for the existing deterministic derivatives research services."""
    from app.services.derivative_coverage_service import DerivativeCoverageService

    coverage = await DerivativeCoverageService(session).get_derivatives_coverage()
    html = _render(
        "derivatives.html",
        {
            "request": request,
            "coverage": coverage,
        },
    )
    return HTMLResponse(content=html)


@router.post(
    "/import",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def import_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    bhavcopy = form.get("bhavcopy")
    if bhavcopy is None or not getattr(bhavcopy, "filename", None):
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    payload = await bhavcopy.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(payload) > settings.max_upload_size:
        raise HTTPException(status_code=413, detail="File too large")

    file_name = bhavcopy.filename or "bhavcopy.csv"
    job = await MarketDataService(session).import_upload(
        file_name=file_name, payload=payload, mode="UPLOAD"
    )
    details = job.details or {}
    if job.status == "completed":
        return RedirectResponse(
            url=_flash_url(
                level="success",
                message="Import completed",
                imported=job.status,
                rows=job.rows_inserted + job.rows_updated,
                inserted=job.rows_inserted,
                updated=job.rows_updated,
                rejected=job.rows_rejected,
                format=details.get("format") or "unknown",
                trade_date=job.trade_date.isoformat() if job.trade_date else "",
            ),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="danger",
            message=str(job.error_message or "Import failed"),
            imported=job.status,
        ),
        status_code=303,
    )


@router.post(
    "/download-latest",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def download_latest_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    try:
        result = await MarketDataService(session).download_and_import_latest()
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Download failed: {exc}"),
            status_code=303,
        )
    if result.get("status") == "failed":
        return RedirectResponse(
            url=_flash_url(level="danger", message=str(result.get("message"))),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Downloaded {result.get('trade_date')}",
            imported=result.get("status"),
            rows=result.get("rows_inserted", 0) + result.get("rows_updated", 0),
            inserted=result.get("rows_inserted", 0),
            updated=result.get("rows_updated", 0),
            rejected=result.get("rows_rejected", 0),
            format=result.get("format") or "unknown",
        ),
        status_code=303,
    )


@router.post(
    "/download-date",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def download_date_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    try:
        trade_date = date.fromisoformat(str(form.get("trade_date", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid trade date") from exc
    try:
        result = await MarketDataService(session).download_and_import_date(trade_date)
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Download failed: {exc}"),
            status_code=303,
        )
    if result.status in ("blocked_403", "failed"):
        return RedirectResponse(
            url=_flash_url(level="danger", message=result.message),
            status_code=303,
        )
    if result.status == "skipped_weekend":
        return RedirectResponse(
            url=_flash_url(level="warning", message=f"{trade_date} is a weekend"),
            status_code=303,
        )
    if result.status == "skipped_holiday":
        return RedirectResponse(
            url=_flash_url(level="warning", message=f"No bhavcopy for {trade_date}"),
            status_code=303,
        )
    if result.status == "already_present":
        return RedirectResponse(
            url=_flash_url(level="info", message=f"{trade_date} already imported"),
            status_code=303,
        )
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Downloaded {trade_date}",
            imported="completed",
            inserted=result.rows_inserted,
            updated=result.rows_updated,
            format="nse",
        ),
        status_code=303,
    )


@router.post(
    "/backfill",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def backfill_market_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    _require_csrf(request, form)
    try:
        start_date = date.fromisoformat(str(form.get("start_date", "")))
        end_date = date.fromisoformat(str(form.get("end_date", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid backfill date range") from exc
    from app.services.nse_historical import MAX_RANGE_DAYS

    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be <= end_date")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail="Backfill range too large")
    try:
        summary = await MarketDataService(session).backfill(start_date, end_date)
    except Exception as exc:  # noqa: BLE001 - friendly page result
        return RedirectResponse(
            url=_flash_url(level="danger", message=f"Backfill failed: {exc}"),
            status_code=303,
        )
    downloaded = summary.get("downloaded", 0)
    already_present = summary.get("already_present", 0)
    skipped = summary.get("skipped", 0)
    blocked = summary.get("blocked", 0)
    failed = summary.get("failed", 0)

    if downloaded == 0 and already_present == 0 and (blocked > 0 or failed > 0):
        level = "danger"
        message = f"Backfill failed: {blocked} blocked, {failed} failed (0 downloaded)"
    elif blocked > 0 or failed > 0:
        level = "warning"
        message = (
            f"Backfill partially completed: {downloaded} downloaded, {already_present} present, "
            f"{blocked} blocked, {failed} failed"
        )
    else:
        level = "success"
        message = f"Backfill completed: {downloaded} downloaded, {already_present} already present"

    return RedirectResponse(
        url=_flash_url(
            level=level,
            message=message,
            backfill="completed",
            downloaded=downloaded,
            already_present=already_present,
            skipped=skipped,
            blocked=blocked,
            failed=failed,
        ),
        status_code=303,
    )


@router.post(
    "/run-indicators",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def run_indicators(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    from app.services.indicator_engine import IndicatorEngine

    summary = await IndicatorEngine(session).calculate_all()
    await session.commit()
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Indicators calculated: {summary.get('calculated', 0)}",
        ),
        status_code=303,
    )


@router.post(
    "/run-scanners",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def run_scanners(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_csrf(request, await request.form())
    from app.services.scanner_engine import ScannerService

    service = ScannerService(session)
    results = await service.run_all()
    await service.persist_results(results)
    await session.commit()
    return RedirectResponse(
        url=_flash_url(
            level="success",
            message=f"Scanners run: {len(results)}",
        ),
        status_code=303,
    )


@router.get("/prices", response_class=HTMLResponse)
async def market_prices_partial(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    prices = await repo.list_prices(symbol=symbol, limit=limit, product_only=True)
    rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "close": price.close,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    html = _render(
        "components/market_prices_table.html",
        {"request": request, "prices": rows},
    )
    return HTMLResponse(content=html)


@router.get("/imports", response_class=HTMLResponse)
async def market_imports_partial(
    request: Request,
    limit: int = Query(default=15, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    imports = await MarketDataRepository(session).list_imports(limit=limit)
    html = _render(
        "components/market_imports_table.html",
        {"request": request, "imports": imports},
    )
    return HTMLResponse(content=html)


@router.get("/coverage", response_class=HTMLResponse)
async def market_coverage_partial(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    today = datetime.now(UTC).date()
    rows = await MarketDataService(session).coverage(today - timedelta(days=days), today)
    html = _render(
        "components/market_coverage_table.html",
        {"request": request, "coverage": rows},
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options/chain/table", response_class=HTMLResponse)
async def option_chain_table_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    expiry_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_option_chain import HistoricalOptionChainService

    service = HistoricalOptionChainService(session)
    chain = None
    try:
        chain = await service.get_historical_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
    except Exception:
        chain = None

    html = _render(
        "components/option_chain_table.html",
        {"request": request, "chain": chain},
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/futures/analytics/table", response_class=HTMLResponse)
async def futures_analytics_table_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService

    service = HistoricalFuturesAnalyticsService(session)
    snapshot = None
    rollover = None
    contract_analytics_map = {}

    try:
        snapshot = await service.get_underlying_futures_snapshot(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )
        rollover = await service.get_rollover_analytics(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )

        for item in snapshot.items:
            try:
                analytics = await service.get_contract_analytics(
                    contract_id=item.contract_id,
                    trade_date=trade_date,
                    as_of_date=as_of_date,
                )
                contract_analytics_map[item.contract_id] = analytics
            except Exception:
                pass
    except Exception:
        snapshot = None

    html = _render(
        "components/futures_analytics_table.html",
        {
            "request": request,
            "snapshot": snapshot,
            "rollover": rollover,
            "contract_analytics_map": contract_analytics_map,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options/analytics/cards", response_class=HTMLResponse)
async def options_analytics_cards_partial(
    request: Request,
    symbol: str = Query(...),
    trade_date: date = Query(...),
    expiry_date: date = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

    service = HistoricalOptionsAnalyticsService(session)
    analytics = None
    expiry_analytics = None
    try:
        analytics = await service.get_option_chain_analytics(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of_date,
        )
        expiry_analytics = await service.get_expiry_options_analytics(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of_date,
        )
    except Exception:
        analytics = None
        expiry_analytics = None

    html = _render(
        "components/options_analytics_cards.html",
        {
            "request": request,
            "analytics": analytics,
            "expiry_analytics": expiry_analytics,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/participants/cards", response_class=HTMLResponse)
async def participant_positioning_cards_partial(
    request: Request,
    report_date: date = Query(...),
    dataset_type: str = Query(default="OPEN_INTEREST"),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.historical_participant_analytics import HistoricalParticipantAnalyticsService

    service = HistoricalParticipantAnalyticsService(session)
    snapshot = None
    try:
        snapshot = await service.get_participant_snapshot(
            report_date=report_date,
            dataset_type=dataset_type,
            as_of_date=as_of_date,
        )
    except Exception:
        snapshot = None

    html = _render(
        "components/participant_positioning_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/replay/cards", response_class=HTMLResponse)
async def derivatives_replay_cards_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    trade_date: date | None = Query(default=None),
    expiry_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.derivative_coverage_service import DerivativeCoverageService
    from app.services.historical_derivatives_replay import HistoricalDerivativesReplayService

    if trade_date is None:
        trade_date = as_of_date or date(2026, 8, 5)

    replay_service = HistoricalDerivativesReplayService(session)
    cov_service = DerivativeCoverageService(session)

    replay = await replay_service.get_historical_replay(
        symbol=symbol,
        trade_date=trade_date,
        expiry_date=expiry_date,
        as_of_date=as_of_date,
    )
    coverage = await cov_service.get_derivatives_coverage(as_of_date=as_of_date)

    html = _render(
        "components/derivatives_replay_cards.html",
        {
            "request": request,
            "replay": replay,
            "coverage": coverage,
        },
    )
    return HTMLResponse(content=html)


@router.get("/fundamentals/cards", response_class=HTMLResponse)
async def fundamentals_cards_partial(
    request: Request,
    symbol: str = Query(...),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    statement_basis: str = Query(default="CONSOLIDATED"),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
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
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/fundamentals_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/news/cards", response_class=HTMLResponse)
async def news_cards_partial(
    request: Request,
    symbol: str = Query(...),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from datetime import datetime

    from app.services.news_policy_service import NewsPolicyService

    eval_dt = datetime.combine(as_of_date or date.today(), datetime.min.time(), tzinfo=UTC)
    service = NewsPolicyService(session)
    try:
        snapshot = await service.get_company_news_policy_snapshot(
            symbol=symbol, evaluation_datetime=eval_dt
        )
    except ValueError as exc:
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/news_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/stock360/cards", response_class=HTMLResponse)
async def stock360_cards_partial(
    request: Request,
    symbol: str = Query(...),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.stock360_service import Stock360Service

    service = Stock360Service(session)
    try:
        snapshot = await service.get_stock360_snapshot(
            symbol=symbol,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=f"<div class='p-4 text-rose-400'>Error: {exc}</div>", status_code=404
        )

    html = _render(
        "components/stock360_cards.html",
        {
            "request": request,
            "snapshot": snapshot,
        },
    )
    return HTMLResponse(content=html)


@router.get("/opportunities/board", response_class=HTMLResponse)
async def opportunity_board_partial(
    request: Request,
    profile: str = Query(default="BREAKOUT"),
    evaluation_date: date | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
    sector: str | None = Query(default=None),
    fno_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
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

    html = _render(
        "components/opportunity_board.html",
        {
            "request": request,
            "summary": summary,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/advanced-options/tab")
async def advanced_options_tab_partial(
    request: Request,
    symbol: str = Query(default="NIFTY"),
    trade_date: date | None = None,
    expiry_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render HTMX partial for Advanced Options Analytics (IV, Greeks, Volatility Structure, Liquidity)."""
    from app.services.advanced_options_service import AdvancedOptionsService
    from app.services.historical_option_chain import HistoricalOptionChainService

    t_date = trade_date or date.today()
    as_of = as_of_date or t_date
    chain_service = HistoricalOptionChainService(session)
    expiries = await chain_service.get_available_expiries(symbol, t_date, as_of)

    target_exp = expiry_date or (expiries[0] if expiries else t_date)

    adv_service = AdvancedOptionsService(session)
    enriched_rows = []
    expected_move = None
    skew = None
    term_structure = []

    try:
        enriched_rows = await adv_service.get_enriched_option_chain(
            symbol, t_date, target_exp, as_of
        )
        expected_move = await adv_service.get_expected_move(symbol, t_date, target_exp, as_of)
        skew = await adv_service.get_iv_skew(symbol, t_date, target_exp, as_of)
        term_structure = await adv_service.get_iv_term_structure(symbol, t_date, as_of)
    except Exception:
        pass

    html = _render(
        "components/advanced_options_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "expiry_date": target_exp,
            "available_expiries": expiries,
            "rows": enriched_rows,
            "expected_move": expected_move,
            "skew": skew,
            "term_structure": term_structure,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/advanced-exposure/tab", response_class=HTMLResponse)
async def advanced_exposure_tab_partial(
    request: Request,
    symbol: str = "NIFTY",
    trade_date: date | None = None,
    expiry_date: date | None = None,
    as_of_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Advanced Exposure tab component."""
    from app.services.advanced_exposure_service import AdvancedExposureService
    from app.services.historical_option_chain import HistoricalOptionChainService

    t_date = trade_date or date.today()
    as_of = as_of_date or t_date

    chain_service = HistoricalOptionChainService(session)
    exp_service = AdvancedExposureService(session)

    expiries = await chain_service.get_available_expiries(symbol, t_date, as_of)
    target_exp = expiry_date or (expiries[0] if expiries else t_date)

    snapshot = None
    strikes: list = []
    gamma_profile: list = []

    try:
        snapshot = await exp_service.get_exposure_snapshot(symbol, t_date, target_exp, as_of)
        strikes = await exp_service.get_strike_exposure_table(symbol, t_date, target_exp, as_of)
        gamma_profile = await exp_service.get_gamma_profile(symbol, t_date, target_exp, as_of)
    except Exception:
        pass

    html = _render(
        "components/advanced_exposure_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "expiry_date": target_exp,
            "available_expiries": expiries,
            "snapshot": snapshot,
            "strikes": strikes,
            "gamma_profile": gamma_profile,
        },
    )
    return HTMLResponse(content=html)


@router.get("/derivatives/options-opportunity/tab", response_class=HTMLResponse)
async def options_opportunity_tab_partial(
    request: Request,
    symbol: str = "NIFTY",
    trade_date: date | None = None,
    as_of_date: date | None = None,
    setup_profile: str = "SWING",
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Options Opportunity Research tab component."""
    from app.services.options_opportunity_service import OptionOpportunityService

    t_date = trade_date or date.today()
    as_of = as_of_date or t_date

    opp_service = OptionOpportunityService(session)
    assessment = None

    try:
        assessment = await opp_service.assess_opportunity(
            symbol=symbol,
            trade_date=t_date,
            as_of_date=as_of,
            setup_profile=setup_profile,
        )
    except Exception:
        pass

    html = _render(
        "components/options_opportunity_tab.html",
        {
            "request": request,
            "symbol": symbol.upper(),
            "trade_date": t_date,
            "assessment": assessment,
        },
    )
    return HTMLResponse(content=html)


@router.get("/validation/lab", response_class=HTMLResponse)
async def validation_lab_tab_partial(
    request: Request,
    profile: str = "SWING",
    symbol: str = "NIFTY",
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial endpoint for rendering the Historical Validation & Calibration Lab component."""
    from app.services.historical_coverage_certification_service import (
        HistoricalCoverageCertificationService,
    )
    from app.services.historical_opportunity_validation_service import (
        HistoricalOpportunityValidationService,
    )
    from app.services.historical_options_validation_service import (
        HistoricalOptionsValidationService,
    )

    val_service = HistoricalOpportunityValidationService(session)
    opt_val_service = HistoricalOptionsValidationService(session)
    cert_service = HistoricalCoverageCertificationService(session)

    stock_summary = None
    options_summary = None
    coverage_cert = None

    try:
        stock_summary = await val_service.validate_opportunities(profile=profile)
        options_summary = await opt_val_service.validate_options(symbol=symbol)
        coverage_cert = await cert_service.certify_coverage()
    except Exception:
        pass

    html = _render(
        "components/validation_lab_tab.html",
        {
            "request": request,
            "profile": profile,
            "symbol": symbol.upper(),
            "stock_summary": stock_summary,
            "options_summary": options_summary,
            "coverage_cert": coverage_cert,
        },
    )
    return HTMLResponse(content=html)
