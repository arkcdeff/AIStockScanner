from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.explanation_service import AIExplanationService, AINotFoundError
from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.stock_detail import StockDetailRepository
from app.schemas.stock_detail import (
    ChartDataResponse,
    IndicatorCardResponse,
    ScannerMatchResponse,
    StockOverviewResponse,
)
from app.services.news_policy_service import NewsPolicyService
from app.services.notification_ui import NotificationUIService
from app.services.stock_detail import StockDetailService

router = APIRouter(dependencies=[Depends(require_browser_permission("stocks.read"))])
env_loader = None


def _get_env():
    global env_loader
    if env_loader is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env_loader = Environment(
            loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
            autoescape=select_autoescape(("html", "htm", "xml")),
        )
    return env_loader


def _render(template_name: str, context: dict) -> str:
    env = _get_env()
    return env.get_template(template_name).render(**context)


@router.get("/{symbol}", response_class=HTMLResponse)
async def stock_detail_page(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    service = StockDetailService(session)
    overview = await service.get_stock360_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    response = StockOverviewResponse(
        symbol=overview["stock"].symbol,
        company_name=overview["stock"].company_name,
        exchange=overview["stock"].exchange,
        series=overview["stock"].series,
        isin=overview["stock"].isin,
        sector=overview["sector_name"],
        industry=overview["stock"].industry,
        latest_trade_date=overview["latest_price"].trade_date if overview["latest_price"] else None,
        latest_close=float(overview["latest_price"].close) if overview["latest_price"] else None,
        previous_close=float(overview["latest_price"].previous_close)
        if overview["latest_price"] and overview["latest_price"].previous_close
        else None,
        price_change=overview["price_change"],
        price_change_pct=overview["price_change_pct"],
        open=float(overview["latest_price"].open) if overview["latest_price"] else None,
        high=float(overview["latest_price"].high) if overview["latest_price"] else None,
        low=float(overview["latest_price"].low) if overview["latest_price"] else None,
        volume=overview["latest_price"].volume if overview["latest_price"] else None,
        avg_volume_20d=overview["avg_volume_20d"],
        volume_multiple=round(overview["latest_price"].volume / overview["avg_volume_20d"], 2)
        if overview["latest_price"]
        and overview["avg_volume_20d"]
        and overview["avg_volume_20d"] > 0
        else None,
        delivery_quantity=overview["latest_price"].delivery_quantity
        if overview["latest_price"]
        else None,
        delivery_percentage=float(overview["latest_price"].delivery_percentage)
        if overview["latest_price"] and overview["latest_price"].delivery_percentage
        else None,
        avg_delivery_20d=overview["avg_delivery_20d"],
        history_sessions=overview["history_sessions"],
        history_first_date=overview["history_first_date"],
    )

    explanation = None
    if settings.ai_enabled:
        try:
            explanation = await AIExplanationService(session).explain_stock(symbol)
        except AINotFoundError:
            explanation = None

    indicator = None
    if overview["latest_indicator"]:
        ind = overview["latest_indicator"]
        b_upper = float(ind.bollinger_upper) if ind.bollinger_upper is not None else None
        b_middle = float(ind.bollinger_middle) if ind.bollinger_middle is not None else None
        b_lower = float(ind.bollinger_lower) if ind.bollinger_lower is not None else None
        if b_upper is not None and b_lower is not None and b_upper < b_lower:
            b_upper, b_lower = b_lower, b_upper

        indicator = IndicatorCardResponse(
            trade_date=ind.trade_date,
            ema_20=float(ind.ema_20) if ind.ema_20 is not None else None,
            ema_50=float(ind.ema_50) if ind.ema_50 is not None else None,
            ema_100=float(ind.ema_100) if ind.ema_100 is not None else None,
            ema_200=float(ind.ema_200) if ind.ema_200 is not None else None,
            sma=float(ind.sma) if ind.sma is not None else None,
            rsi_14=float(ind.rsi_14) if ind.rsi_14 is not None else None,
            macd=float(ind.macd) if ind.macd is not None else None,
            macd_signal=float(ind.macd_signal) if ind.macd_signal is not None else None,
            macd_hist=float(ind.macd_hist) if ind.macd_hist is not None else None,
            atr=float(ind.atr) if ind.atr is not None else None,
            adx=float(ind.adx) if ind.adx is not None else None,
            supertrend=float(ind.supertrend) if ind.supertrend is not None else None,
            supertrend_direction=ind.supertrend_direction,
            vwap=float(ind.vwap) if ind.vwap is not None else None,
            obv=float(ind.obv) if ind.obv is not None else None,
            bollinger_upper=b_upper,
            bollinger_middle=b_middle,
            bollinger_lower=b_lower,
            stochastic_k=float(ind.stochastic_k) if ind.stochastic_k is not None else None,
            stochastic_d=float(ind.stochastic_d) if ind.stochastic_d is not None else None,
        )

    matches = []
    for sr in overview["scanner_matches"]:
        reason = ""
        if sr.metrics and isinstance(sr.metrics, dict):
            reason = sr.metrics.get("reason", "")
        matches.append(
            ScannerMatchResponse(
                id=sr.id,
                scan_date=sr.scan_date,
                matched=True,
                score=float(sr.score) if sr.score is not None else None,
                metrics=sr.metrics or {},
                scanner_name=sr.scan_definition.name if sr.scan_definition else None,
                category=sr.scan_definition.category if sr.scan_definition else None,
                reason=reason,
            )
        )

    from app.services.alert import AlertService
    from app.services.portfolio import PortfolioService
    from app.services.watchlist import WatchlistService

    symbol = overview["stock"].symbol
    watchlists = await WatchlistService(session).list()
    entries_for_symbol = await WatchlistService(session).watchlists_for_symbol(symbol)
    watchlist_contains = {entry.watchlist_id for entry in entries_for_symbol}
    watchlist_data = [
        {
            "id": wl.id,
            "name": wl.name,
            "contains": wl.id in watchlist_contains,
            "color": wl.color,
        }
        for wl in watchlists
    ]

    positions = await PortfolioService(session).holdings_for_stock(overview["stock"].id)
    portfolio_positions = await PortfolioService(session).value_holdings(positions)

    alert_service = AlertService(session)
    alert_events = await alert_service.list_events_for_symbol(symbol, limit=20)
    all_rules = await alert_service.list_rules(limit=200)
    alert_rules = [
        rule
        for rule in all_rules
        if rule.stock_id == overview["stock"].id or rule.watchlist_id in watchlist_contains
    ]
    recent_notifications = await NotificationUIService(session).get_stock_history(symbol, limit=5)

    # News & Government Policy Intelligence (19.1F) — PIT-safe
    # Evaluation datetime is midnight of the stock's latest available trade date.
    # available_at <= eval_dt enforced by NewsPolicyRepository.get_company_news().
    from datetime import UTC, date, datetime

    _eval_d = overview["latest_price"].trade_date if overview.get("latest_price") else date.today()
    _eval_dt = datetime.combine(_eval_d, datetime.min.time(), tzinfo=UTC)
    news_policy = None
    try:
        news_policy = await NewsPolicyService(session).get_company_news_policy_snapshot(
            symbol=overview["stock"].symbol,
            evaluation_datetime=_eval_dt,
            as_of_date=_eval_d,
        )
    except Exception:
        news_policy = None

    # Corporate Events - PIT-safe
    from app.services.fundamental_corporate_service import FundamentalCorporateService
    corporate_events = None
    try:
        corporate_events = await FundamentalCorporateService(session).get_corporate_announcements(
            symbol=overview["stock"].symbol,
            as_of_date=_eval_d,
            limit=5,
        )
    except Exception:
        corporate_events = None

    # Stock Intelligence & Decision Context (Stage 2.6.5 Phase F) — PIT-safe
    from app.services.stock_intelligence_service import StockIntelligenceService
    stock_intelligence = None
    try:
        stock_intelligence = await StockIntelligenceService(session).get_stock_intelligence(
            symbol=overview["stock"].symbol,
            evaluation_date=_eval_d,
            as_of_date=_eval_d,
        )
    except Exception:
        stock_intelligence = None

    html = _render(
        "stock_detail.html",
        {
            "request": request,
            "symbol": symbol,
            "stock": overview["stock"],
            "sector_name": overview["sector_name"],
            "overview": response,
            "indicator": indicator,
            "analytics": overview["latest_analytics"],
            "matches": matches,
            "explanation": explanation,
            "watchlists": watchlist_data,
            "portfolio_positions": portfolio_positions,
            "alert_events": alert_events,
            "alert_rules": alert_rules,
            "recent_notifications": recent_notifications,
            "technical_snapshot": overview["technical_snapshot"],
            "scanner_snapshot": overview["scanner_snapshot"],
            "performance": overview["performance"],
            "data_health": overview["data_health"],
            "fundamentals": overview.get("fundamentals"),
            "valuation": overview.get("valuation"),
            "news_policy": news_policy,
            "corporate_events": corporate_events,
            "stock_intelligence": stock_intelligence,
            "evaluation_date": _eval_d,
        },
    )
    return HTMLResponse(content=html)


@router.get("/{symbol}/intelligence", response_class=HTMLResponse)
async def stock_intelligence_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    from datetime import date

    from app.services.stock_intelligence_service import StockIntelligenceService
    service = StockDetailService(session)
    overview = await service.get_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    _eval_d = overview["latest_price"].trade_date if overview.get("latest_price") else date.today()
    intel_svc = StockIntelligenceService(session)
    stock_intelligence = await intel_svc.get_stock_intelligence(
        symbol=symbol,
        evaluation_date=_eval_d,
        as_of_date=_eval_d,
    )
    return HTMLResponse(
        content=_render(
            "components/stock_intelligence.html",
            {
                "request": request,
                "symbol": symbol,
                "stock": overview["stock"],
                "stock_intelligence": stock_intelligence,
            },
        )
    )



@router.get("/{symbol}/notifications", response_class=HTMLResponse)
async def stock_notifications_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    stock = await StockDetailRepository(session).get_stock(symbol)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    recent_notifications = await NotificationUIService(session).get_stock_history(
        stock.symbol, limit=5
    )
    return HTMLResponse(
        content=_render(
            "components/stock_notifications_panel.html",
            {
                "request": request,
                "stock": stock,
                "recent_notifications": recent_notifications,
            },
        )
    )


@router.get("/{symbol}/overview", response_class=HTMLResponse)
async def stock_overview_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    service = StockDetailService(session)
    overview = await service.get_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    response = StockOverviewResponse(
        symbol=overview["stock"].symbol,
        company_name=overview["stock"].company_name,
        exchange=overview["stock"].exchange,
        series=overview["stock"].series,
        isin=overview["stock"].isin,
        sector=overview["sector_name"],
        industry=overview["stock"].industry,
        latest_trade_date=overview["latest_price"].trade_date if overview["latest_price"] else None,
        latest_close=float(overview["latest_price"].close) if overview["latest_price"] else None,
        previous_close=float(overview["latest_price"].previous_close)
        if overview["latest_price"] and overview["latest_price"].previous_close
        else None,
        price_change=overview["price_change"],
        price_change_pct=overview["price_change_pct"],
        open=float(overview["latest_price"].open) if overview["latest_price"] else None,
        high=float(overview["latest_price"].high) if overview["latest_price"] else None,
        low=float(overview["latest_price"].low) if overview["latest_price"] else None,
        volume=overview["latest_price"].volume if overview["latest_price"] else None,
        avg_volume_20d=overview["avg_volume_20d"],
        volume_multiple=round(overview["latest_price"].volume / overview["avg_volume_20d"], 2)
        if overview["latest_price"]
        and overview["avg_volume_20d"]
        and overview["avg_volume_20d"] > 0
        else None,
        delivery_quantity=overview["latest_price"].delivery_quantity
        if overview["latest_price"]
        else None,
        delivery_percentage=float(overview["latest_price"].delivery_percentage)
        if overview["latest_price"] and overview["latest_price"].delivery_percentage
        else None,
        avg_delivery_20d=overview["avg_delivery_20d"],
        history_sessions=overview["history_sessions"],
        history_first_date=overview["history_first_date"],
    )
    return HTMLResponse(
        content=_render(
            "components/stock_overview.html", {"request": request, "overview": response}
        )
    )


@router.get("/{symbol}/indicators", response_class=HTMLResponse)
async def stock_indicators_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    service = StockDetailService(session)
    overview = await service.get_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    indicator = None
    if overview["latest_indicator"]:
        ind = overview["latest_indicator"]
        indicator = IndicatorCardResponse(
            trade_date=ind.trade_date,
            ema_20=float(ind.ema_20) if ind.ema_20 is not None else None,
            ema_50=float(ind.ema_50) if ind.ema_50 is not None else None,
            ema_100=float(ind.ema_100) if ind.ema_100 is not None else None,
            ema_200=float(ind.ema_200) if ind.ema_200 is not None else None,
            sma=float(ind.sma) if ind.sma is not None else None,
            rsi_14=float(ind.rsi_14) if ind.rsi_14 is not None else None,
            macd=float(ind.macd) if ind.macd is not None else None,
            macd_signal=float(ind.macd_signal) if ind.macd_signal is not None else None,
            macd_hist=float(ind.macd_hist) if ind.macd_hist is not None else None,
            atr=float(ind.atr) if ind.atr is not None else None,
            adx=float(ind.adx) if ind.adx is not None else None,
            supertrend=float(ind.supertrend) if ind.supertrend is not None else None,
            supertrend_direction=ind.supertrend_direction,
            vwap=float(ind.vwap) if ind.vwap is not None else None,
            obv=float(ind.obv) if ind.obv is not None else None,
            bollinger_upper=float(ind.bollinger_upper) if ind.bollinger_upper is not None else None,
            bollinger_middle=float(ind.bollinger_middle)
            if ind.bollinger_middle is not None
            else None,
            bollinger_lower=float(ind.bollinger_lower) if ind.bollinger_lower is not None else None,
            stochastic_k=float(ind.stochastic_k) if ind.stochastic_k is not None else None,
            stochastic_d=float(ind.stochastic_d) if ind.stochastic_d is not None else None,
        )
    return HTMLResponse(
        content=_render(
            "components/indicator_cards.html", {"request": request, "indicator": indicator}
        )
    )


@router.get(
    "/{symbol}/scanner-results",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("scanners.read"))],
)
async def stock_scanner_results_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    service = StockDetailService(session)
    overview = await service.get_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    matches = []
    for sr in overview["scanner_matches"]:
        reason = ""
        if sr.metrics and isinstance(sr.metrics, dict):
            reason = sr.metrics.get("reason", "")
        matches.append(
            ScannerMatchResponse(
                id=sr.id,
                scan_date=sr.scan_date,
                matched=True,
                score=float(sr.score) if sr.score is not None else None,
                metrics=sr.metrics or {},
                scanner_name=sr.scan_definition.name if sr.scan_definition else None,
                category=sr.scan_definition.category if sr.scan_definition else None,
                reason=reason,
            )
        )
    return HTMLResponse(
        content=_render(
            "components/stock_scanner_matches.html", {"request": request, "matches": matches}
        )
    )


@router.get(
    "/{symbol}/analytics",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("analytics.read"))],
)
async def stock_analytics_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    service = StockDetailService(session)
    overview = await service.get_overview(symbol)
    if overview is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    return HTMLResponse(
        content=_render(
            "components/stock_analytics.html",
            {"request": request, "analytics": overview["latest_analytics"]},
        )
    )


@router.get("/{symbol}/prices", response_class=HTMLResponse)
async def stock_prices_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    repo = StockDetailRepository(session)
    stock = await repo.get_stock(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")

    prices = await repo.get_price_history(stock.id, period="1y", limit=200)
    return HTMLResponse(
        content=_render(
            "components/price_chart.html", {"request": request, "symbol": symbol, "prices": prices}
        )
    )


@router.get(
    "/{symbol}/ai",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("ai.read"))],
)
async def stock_ai_partial(
    request: Request, symbol: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    explanation = None
    if settings.ai_enabled:
        try:
            explanation = await AIExplanationService(session).explain_stock(symbol)
        except AINotFoundError:
            raise HTTPException(status_code=404, detail="Stock not found") from None
    overview = await StockDetailService(session).get_overview(symbol)
    eval_date = (
        overview["latest_price"].trade_date
        if overview and overview.get("latest_price")
        else date.today()
    )
    return HTMLResponse(
        content=_render(
            "components/stock_ai.html",
            {
                "request": request,
                "explanation": explanation,
                "evaluation_date": eval_date,
            },
        )
    )

@router.get(
    "/{symbol}/chart-data",
    response_model=ChartDataResponse,
    dependencies=[Depends(require_browser_permission("stocks.read"))],
)
async def stock_chart_data(
    symbol: str,
    period: str = Query(default="1y", pattern="^(1m|3m|6m|1y|3y|5y|all)$"),
    include_indicators: bool = Query(default=True),
    session: AsyncSession = Depends(get_db_session),
) -> ChartDataResponse:
    repo = StockDetailRepository(session)
    stock = await repo.get_stock(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")

    actual_period = "all" if period == "all" else period
    data = await repo.get_chart_data(
        stock.id, period=actual_period, include_indicators=include_indicators
    )
    return ChartDataResponse(symbol=symbol.upper(), **data)
