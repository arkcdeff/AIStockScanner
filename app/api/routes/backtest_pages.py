"""Task 17.6: Browser pages and HTMX partials for the Backtesting workspace.

Pages render persisted strategy/run/metric/equity/trade data through the same
repositories the REST API uses and trigger execution through the shared
``BacktestSimulator`` / ``BacktestMetricCalculator`` services. No business
logic is duplicated and no results are recomputed client-side.

Permission model mirrors the REST API:
- ``backtests.read`` for every page and partial.
- ``backtests.write`` for strategy mutation and run creation.
- ``backtests.run`` for run execution.
- Runs remain private: a non-admin may only view runs they requested.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import (
    ForbiddenError,
    finalize_page_response,
    get_browser_user,
    stage_csrf_token,
)
from app.auth import csrf
from app.auth.authorization import is_admin_user, require_browser_permission, user_has_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.backtest import (
    BACKTEST_POSITION_SIZE_TYPES,
    BACKTEST_RUN_STATUSES,
    BACKTEST_STRATEGY_TYPES,
    BacktestMetric,
    BacktestRun,
    BacktestStrategy,
)
from app.models.scan_definition import ScanDefinition
from app.models.stock import Stock
from app.repositories.backtest import (
    BacktestEquityPointRepository,
    BacktestMetricRepository,
    BacktestRunRepository,
    BacktestStrategyRepository,
    BacktestTradeRepository,
)
from app.repositories.scan import ScanDefinitionRepository
from app.services.backtest_metrics import BacktestMetricCalculator
from app.services.backtest_simulator import BacktestSimulator

router = APIRouter(
    dependencies=[Depends(require_browser_permission("backtests.read"))]
)

env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)

_PAGE_SIZE = 20


# --------------------------------------------------------------------------- formatting


def _finite_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    if number.is_nan() or number.is_infinite():
        return None
    return number


def _finite_float(value: Any) -> float | None:
    number = _finite_decimal(value)
    return float(number) if number is not None else None


def _indian_group_int(int_str: str) -> str:
    if len(int_str) <= 3:
        return int_str
    head, tail = int_str[:-3], int_str[-3:]
    parts = [tail]
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    parts.insert(0, head)
    return ",".join(parts)


def _fmt_inr(value: Any, digits: int = 2) -> str:
    number = _finite_decimal(value)
    if number is None:
        return "—"
    rounded = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    int_str, _, dec_str = format(abs(rounded), f".{digits}f").partition(".")
    grouped = _indian_group_int(int_str)
    prefix = "-₹" if rounded < 0 else "₹"
    return f"{prefix}{grouped}.{dec_str}"


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _finite_decimal(value)
    if number is None:
        return "—"
    rounded = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    int_str, _, dec_str = format(abs(rounded), f".{digits}f").partition(".")
    grouped = _indian_group_int(int_str)
    prefix = "-" if rounded < 0 else ""
    return f"{prefix}{grouped}.{dec_str}"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _finite_decimal(value)
    if number is None:
        return "—"
    rounded = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{rounded:+.{digits}f}%"


def _fmt_int(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value)}"
    except (TypeError, ValueError):
        return "—"


def _sign_class(value: Any) -> str:
    number = _finite_decimal(value)
    if number is None or number == 0:
        return "text-muted"
    return "text-success" if number > 0 else "text-danger"


_STATUS_BADGES = {
    "pending": "badge bg-secondary",
    "running": "badge bg-info text-dark",
    "completed": "badge bg-success",
    "failed": "badge bg-danger",
    "cancelled": "badge bg-warning text-dark",
}


def _status_badge(status: Any) -> str:
    return _STATUS_BADGES.get(status or "", "badge bg-secondary")


def _label(value: Any) -> str:
    return str(value or "").replace("_", " ").title()


for _name, _func in {
    "inr": _fmt_inr,
    "num": _fmt_num,
    "pct": _fmt_pct,
    "int": _fmt_int,
    "sign_class": _sign_class,
    "status_badge": _status_badge,
    "label": _label,
}.items():
    env.filters[_name] = _func


# --------------------------------------------------------------------------- rendering


def _render(template_name: str, context: dict[str, Any]) -> str:
    return env.get_template(template_name).render(**context)


def _page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    html = _render(template_name, context)
    return finalize_page_response(request, HTMLResponse(content=html, status_code=status_code))


def _existing_csrf_token(request: Request) -> str:
    """Reuse the browser's current CSRF cookie for partials that carry forms.

    Staging a fresh token on an HTMX partial would rotate the cookie while the
    page's ``meta`` tag (and every later HTMX request header) still carries the
    old value, breaking validation. For partials we keep the existing cookie.
    """
    token = request.cookies.get(settings.csrf_cookie_name, "")
    return token or stage_csrf_token(request)


def _not_found(request: Request, user, message: str) -> HTMLResponse:
    return _page(
        request,
        "errors/403.html",
        {
            "request": request,
            "browser_user": user,
            "csrf_token": stage_csrf_token(request),
            "message": message,
        },
        status_code=404,
    )


def _handle_csrf(request: Request, form) -> None:
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, "CSRF validation failed") from exc


def _checked(form, name: str) -> bool:
    return str(form.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


# --------------------------------------------------------------------------- access / data


async def _browser_user(request: Request, session: AsyncSession):
    user = await get_browser_user(request, session)
    if user is None:
        raise ForbiddenError()
    return user


def _can_access_run(user, run: BacktestRun) -> bool:
    if is_admin_user(user):
        return True
    owner = run.requested_by_user_id
    return owner is not None and owner == user.user.id


async def _run_for_access(
    request: Request, session: AsyncSession, run_id: int
) -> BacktestRun | None:
    run = await BacktestRunRepository(session).get(run_id)
    if run is None:
        return None
    user = await _browser_user(request, session)
    if not _can_access_run(user, run):
        raise ForbiddenError()
    return run


async def _runs_visible_to(
    session: AsyncSession,
    user,
    *,
    offset: int = 0,
    limit: int = 100,
    status: str | None = None,
    strategy_id: int | None = None,
) -> tuple[list[BacktestRun], int]:
    owner = None if is_admin_user(user) else user.user.id
    repo = BacktestRunRepository(session)
    items = await repo.list_runs(
        offset=offset,
        limit=limit,
        status=status,
        strategy_id=strategy_id,
        requested_by_user_id=owner,
    )
    total = await repo.count_runs(
        status=status,
        strategy_id=strategy_id,
        requested_by_user_id=owner,
    )
    return items, total


async def _run_view(session: AsyncSession, runs: list[BacktestRun]) -> list[dict[str, Any]]:
    strategy_ids = {run.strategy_id for run in runs}
    run_ids = [run.id for run in runs]
    strategies: dict[int, BacktestStrategy] = {}
    if strategy_ids:
        rows = await session.scalars(
            select(BacktestStrategy).where(BacktestStrategy.id.in_(strategy_ids))
        )
        strategies = {strategy.id: strategy for strategy in rows}
    metrics: dict[int, BacktestMetric] = {}
    if run_ids:
        rows = await session.scalars(
            select(BacktestMetric).where(BacktestMetric.backtest_run_id.in_(run_ids))
        )
        metrics = {metric.backtest_run_id: metric for metric in rows}
    views: list[dict[str, Any]] = []
    for run in runs:
        strategy = strategies.get(run.strategy_id)
        metric = metrics.get(run.id)
        views.append(
            {
                "id": run.id,
                "status": run.status,
                "strategy_name": strategy.name if strategy else None,
                "strategy_code": strategy.code if strategy else None,
                "start_date": run.start_date,
                "end_date": run.end_date,
                "initial_capital": run.initial_capital,
                "final_capital": run.final_capital,
                "total_return_percent": metric.total_return_percent if metric else None,
                "created_at": run.created_at,
                "requested_by_user_id": run.requested_by_user_id,
            }
        )
    return views


async def _active_symbols(session: AsyncSession, *, limit: int = 300) -> list[str]:
    rows = await session.scalars(
        select(Stock.symbol)
        .where(Stock.is_active.is_(True))
        .order_by(Stock.symbol)
        .limit(limit)
    )
    return list(rows)


async def _symbol_map(session: AsyncSession, trades) -> dict[int, str]:
    stock_ids = {trade.stock_id for trade in trades}
    if not stock_ids:
        return {}
    rows = await session.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids)))
    return {row[0]: row[1] for row in rows}


def _trade_view(trades, symbols: dict[int, str]) -> list[dict[str, Any]]:
    return [
        {
            "id": trade.id,
            "stock_id": trade.stock_id,
            "symbol": symbols.get(trade.stock_id),
            "entry_date": trade.entry_date,
            "entry_price": trade.entry_price,
            "exit_date": trade.exit_date,
            "exit_price": trade.exit_price,
            "quantity": trade.quantity,
            "position_side": trade.position_side,
            "entry_signal": trade.entry_signal,
            "exit_signal": trade.exit_signal,
            "gross_pnl": trade.gross_pnl,
            "costs": trade.costs,
            "net_pnl": trade.net_pnl,
            "return_percent": trade.return_percent,
            "bars_held": trade.bars_held,
            "status": trade.status,
        }
        for trade in trades
    ]


def _equity_payload(points, initial_capital: Decimal) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "initial_capital": _finite_float(initial_capital),
        "points": [],
    }
    for point in points:
        value = _finite_float(point.portfolio_value)
        if value is None:
            continue
        payload["points"].append(
            {
                "time": point.trade_date.isoformat(),
                "value": value,
                "drawdown": _finite_float(point.drawdown_percent),
            }
        )
    return payload


def _benchmark_cards(metric) -> dict[str, Any] | None:
    if (
        metric is None
        or metric.total_return_percent is None
        or metric.benchmark_return_percent is None
    ):
        return None
    strategy = float(metric.total_return_percent)
    benchmark = float(metric.benchmark_return_percent)

    def clamp(value: float) -> float:
        return max(0.0, min(100.0, 50.0 + value))

    return {
        "strategy_value": metric.total_return_percent,
        "benchmark_value": metric.benchmark_return_percent,
        "alpha_value": metric.alpha,
        "strategy_width": clamp(strategy),
        "benchmark_width": clamp(benchmark),
    }


# --------------------------------------------------------------------------- run workspace


async def _run_workspace_context(
    request: Request,
    session: AsyncSession,
    run_id: int,
    user,
    *,
    can_run: bool | None = None,
    can_write: bool | None = None,
) -> dict[str, Any]:
    run = await BacktestRunRepository(session).get_with_details(run_id)
    equity_points = await BacktestEquityPointRepository(session).list_for_run(run_id)
    trades, trades_total = await BacktestTradeRepository(session).list_for_run(
        run_id, offset=0, limit=_PAGE_SIZE
    )
    symbols = await _symbol_map(session, trades)
    return {
        "request": request,
        "run": run,
        "metric": run.metric,
        "equity_points": equity_points,
        "equity_payload": _equity_payload(equity_points, run.initial_capital),
        "benchmark": _benchmark_cards(run.metric),
        "trades": _trade_view(trades, symbols),
        "trades_total": trades_total,
        "trades_page": 1,
        "trades_page_size": _PAGE_SIZE,
        "trades_filters": {"status": "", "position_side": "", "profitable": "", "symbol": ""},
        "can_run": user_has_permission(user, "backtests.run") if can_run is None else can_run,
        "can_write": can_write if can_write is not None else
            user_has_permission(user, "backtests.write"),
        "flash": None,
    }


# --------------------------------------------------------------------------- workspace page


async def _workspace_response(
    request: Request,
    session: AsyncSession,
    user,
    *,
    flash: dict[str, str] | None = None,
    run_values: Any = None,
) -> HTMLResponse:
    strategies = await BacktestStrategyRepository(session).list(is_active=True, limit=500)
    scan_definitions = await ScanDefinitionRepository(session).list_active()
    recent_runs, total = await _runs_visible_to(session, user, limit=10)
    active_symbols = await _active_symbols(session, limit=300)
    defaults = {
        "start_date": (date.today() - timedelta(days=180)).isoformat(),
        "end_date": date.today().isoformat(),
        "initial_capital": "100000",
        "commission_rate": "0.001",
        "slippage_rate": "0.001",
        "position_size_type": "percent_capital",
        "position_size_value": "10",
        "max_positions": "5",
    }
    return _page(
        request,
        "backtests/workspace.html",
        {
            "request": request,
            "browser_user": user,
            "csrf_token": stage_csrf_token(request),
            "body_theme": "light",
            "strategies": strategies,
            "scan_definitions": scan_definitions,
            "recent_runs": await _run_view(session, recent_runs),
            "recent_runs_total": total,
            "recent_runs_page": 1,
            "recent_runs_page_size": 10,
            "recent_runs_total_pages": 1,
            "active_symbols": active_symbols,
            "run_defaults": defaults,
            "run_values": run_values,
            "position_size_types": BACKTEST_POSITION_SIZE_TYPES,
            "can_write": user_has_permission(user, "backtests.write"),
            "can_run": user_has_permission(user, "backtests.run"),
            "flash": flash,
        },
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def backtests_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    return await _workspace_response(request, session, user)


# --------------------------------------------------------------------------- run creation


async def _parse_run_form(form) -> tuple[dict[str, Any], str | None]:
    try:
        strategy_id = int(str(form.get("strategy_id", "")).strip())
    except (TypeError, ValueError):
        return {}, "A valid strategy is required."
    if strategy_id <= 0:
        return {}, "A valid strategy is required."
    try:
        start_date = date.fromisoformat(str(form.get("start_date", "")).strip())
        end_date = date.fromisoformat(str(form.get("end_date", "")).strip())
    except ValueError:
        return {}, "Dates must use YYYY-MM-DD format."
    if start_date > end_date:
        return {}, "Start date must be on or before end date."
    if end_date > date.today():
        return {}, "End date cannot be in the future."
    try:
        initial_capital = Decimal(str(form.get("initial_capital", "")).strip())
        commission_rate = Decimal(str(form.get("commission_rate", "0") or "0").strip())
        slippage_rate = Decimal(str(form.get("slippage_rate", "0") or "0").strip())
        position_size_value = Decimal(str(form.get("position_size_value", "0") or "0").strip())
    except (TypeError, ValueError, ArithmeticError):
        return {}, "Numeric fields must be valid numbers."
    if initial_capital <= 0:
        return {}, "Initial capital must be positive."
    if commission_rate < 0 or slippage_rate < 0 or position_size_value < 0:
        return {}, "Rates and sizing values cannot be negative."
    position_size_type = str(form.get("position_size_type", "percent_capital")).strip()
    if position_size_type not in BACKTEST_POSITION_SIZE_TYPES:
        return {}, "Invalid position sizing type."
    benchmark_symbol = _optional(form.get("benchmark_symbol"))
    if benchmark_symbol:
        benchmark_symbol = benchmark_symbol.upper()
    max_positions: int | None = None
    raw_max = _optional(form.get("max_positions"))
    if raw_max:
        try:
            max_positions = int(raw_max)
        except ValueError:
            return {}, "Max positions must be a whole number."
        if max_positions <= 0:
            return {}, "Max positions must be greater than zero."
    return (
        {
            "strategy_id": strategy_id,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "slippage_rate": slippage_rate,
            "position_size_type": position_size_type,
            "position_size_value": position_size_value,
            "benchmark_symbol": benchmark_symbol,
            "max_positions": max_positions,
        },
        None,
    )


@router.post(
    "/runs/create",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("backtests.write"))],
)
async def create_run(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    values, error = await _parse_run_form(form)
    if error is not None:
        return await _workspace_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": error},
            run_values=form,
        )
    strategy = await BacktestStrategyRepository(session).get(values["strategy_id"])
    if strategy is None or not strategy.is_active:
        return await _workspace_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": "Selected strategy is inactive or missing."},
            run_values=form,
        )
    if values["benchmark_symbol"]:
        benchmark = await session.scalar(
            select(Stock).where(Stock.symbol == values["benchmark_symbol"])
        )
        if benchmark is None or not benchmark.is_active:
            return await _workspace_response(
                request,
                session,
                user,
                flash={
                    "level": "danger",
                    "message": "Benchmark symbol must reference an active stock.",
                },
                run_values=form,
            )
    run = await BacktestRunRepository(session).create_run(
        strategy_id=values["strategy_id"],
        requested_by_user_id=user.user.id,
        status="pending",
        start_date=values["start_date"].isoformat(),
        end_date=values["end_date"].isoformat(),
        initial_capital=values["initial_capital"],
        commission_rate=values["commission_rate"],
        slippage_rate=values["slippage_rate"],
        position_size_type=values["position_size_type"],
        position_size_value=values["position_size_value"],
        benchmark_symbol=values["benchmark_symbol"],
        max_positions=values["max_positions"],
    )
    await session.commit()
    await session.refresh(run)
    return RedirectResponse(f"/backtests/runs/{run.id}", status_code=303)


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
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("backtests.run"))],
)
async def execute_run(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _page(
            request,
            "backtests/components/run_workspace.html",
            {
                "request": request,
                "run": None,
                "missing": True,
                "csrf_token": _existing_csrf_token(request),
            },
            status_code=404,
        )
    if run.status == "completed":
        context = await _run_workspace_context(request, session, run_id, user)
        context["csrf_token"] = _existing_csrf_token(request)
        context["flash"] = {"level": "warning", "message": "Run is already completed."}
        return _page(request, "backtests/components/run_workspace.html", context)
    if run.status == "running":
        context = await _run_workspace_context(request, session, run_id, user)
        context["csrf_token"] = _existing_csrf_token(request)
        context["flash"] = {"level": "warning", "message": "Run is already running."}
        return _page(request, "backtests/components/run_workspace.html", context)

    pre_can_run = user_has_permission(user, "backtests.run")
    pre_can_write = user_has_permission(user, "backtests.write")
    async with _run_lock(run_id):
        try:
            await BacktestSimulator(session).simulate_run(run_id)
        except Exception:
            await session.rollback()
            run = await BacktestRunRepository(session).get(run_id)
            context = await _run_workspace_context(
                request, session, run_id, user, can_run=pre_can_run, can_write=pre_can_write
            )
            context["csrf_token"] = _existing_csrf_token(request)
            context["flash"] = {"level": "danger", "message": "Backtest execution failed."}
            if run is not None:
                context["run"] = run
            return _page(request, "backtests/components/run_workspace.html", context)
        try:
            await BacktestMetricCalculator(session).compute_metrics(run_id)
        except Exception:
            pass

    context = await _run_workspace_context(
        request, session, run_id, user, can_run=pre_can_run, can_write=pre_can_write
    )
    context["csrf_token"] = _existing_csrf_token(request)
    context["flash"] = {"level": "success", "message": "Backtest completed successfully."}
    return _page(request, "backtests/components/run_workspace.html", context)


# --------------------------------------------------------------------------- run pages


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    strategy_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    items, total = await _runs_visible_to(
        session,
        user,
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status_filter,
        strategy_id=strategy_id,
    )
    strategies = await BacktestStrategyRepository(session).list(limit=500)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return _page(
        request,
        "backtests/runs.html",
        {
            "request": request,
            "browser_user": user,
            "csrf_token": stage_csrf_token(request),
            "body_theme": "light",
            "runs": await _run_view(session, items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_filter": status_filter or "",
            "strategy_id_filter": strategy_id or "",
            "strategies": strategies,
            "statuses": BACKTEST_RUN_STATUSES,
            "can_run": user_has_permission(user, "backtests.run"),
            "flash": None,
        },
    )


@router.get("/runs/history", response_class=HTMLResponse)
async def runs_history_partial(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    strategy_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    items, total = await _runs_visible_to(
        session,
        user,
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status_filter,
        strategy_id=strategy_id,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return _page(
        request,
        "backtests/components/runs_table.html",
        {
            "request": request,
            "runs": await _run_view(session, items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_filter": status_filter or "",
            "strategy_id_filter": strategy_id or "",
            "can_run": user_has_permission(user, "backtests.run"),
            "flash": None,
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail_page(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _not_found(request, user, "Backtest run not found.")
    context = await _run_workspace_context(request, session, run_id, user)
    context.update(
        {
            "browser_user": user,
            "csrf_token": stage_csrf_token(request),
            "body_theme": "light",
        }
    )
    return _page(request, "backtests/run_detail.html", context)


@router.get("/runs/{run_id}/workspace", response_class=HTMLResponse)
async def run_workspace_partial(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _page(
            request,
            "backtests/components/run_workspace.html",
            {
                "request": request,
                "run": None,
                "missing": True,
                "csrf_token": _existing_csrf_token(request),
            },
            status_code=404,
        )
    context = await _run_workspace_context(request, session, run_id, user)
    context["csrf_token"] = _existing_csrf_token(request)
    return _page(request, "backtests/components/run_workspace.html", context)


@router.get("/runs/{run_id}/status", response_class=HTMLResponse)
async def run_status_partial(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _page(
            request,
            "backtests/components/run_status.html",
            {
                "request": request,
                "run": None,
                "missing": True,
                "csrf_token": _existing_csrf_token(request),
            },
            status_code=404,
        )
    return _page(
        request,
        "backtests/components/run_status.html",
        {
            "request": request,
            "run": run,
            "can_run": user_has_permission(user, "backtests.run"),
            "csrf_token": _existing_csrf_token(request),
        },
    )


@router.get("/runs/{run_id}/metrics", response_class=HTMLResponse)
async def run_metrics_partial(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _page(
            request,
            "backtests/components/metrics_cards.html",
            {"request": request, "run": None, "metric": None},
            status_code=404,
        )
    metric = await BacktestMetricRepository(session).get_by_run(run_id)
    return _page(
        request,
        "backtests/components/metrics_cards.html",
        {"request": request, "run": run, "metric": metric},
    )


@router.get("/runs/{run_id}/trades", response_class=HTMLResponse)
async def run_trades_partial(
    run_id: int,
    request: Request,
    status: str | None = Query(default=None),
    position_side: str | None = Query(default=None),
    profitable: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    run = await _run_for_access(request, session, run_id)
    if run is None:
        return _page(
            request,
            "backtests/components/trades_table.html",
            {
                "request": request,
                "run": None,
                "missing": True,
                "trades": [],
                "trades_total": 0,
                "trades_page": 1,
                "trades_page_size": page_size,
                "trades_filters": {
                    "status": status or "",
                    "position_side": position_side or "",
                    "profitable": profitable or "",
                    "symbol": symbol or "",
                },
            },
            status_code=404,
        )
    profitable_flag: bool | None = None
    if profitable == "true":
        profitable_flag = True
    elif profitable == "false":
        profitable_flag = False
    trades, total = await BacktestTradeRepository(session).list_for_run(
        run_id,
        offset=(page - 1) * page_size,
        limit=page_size,
        status=status,
        position_side=position_side,
        profitable=profitable_flag,
        symbol=symbol,
    )
    symbols = await _symbol_map(session, trades)
    return _page(
        request,
        "backtests/components/trades_table.html",
        {
            "request": request,
            "run": run,
            "trades": _trade_view(trades, symbols),
            "trades_total": total,
            "trades_page": page,
            "trades_page_size": page_size,
            "trades_filters": {
                "status": status or "",
                "position_side": position_side or "",
                "profitable": profitable or "",
                "symbol": symbol or "",
            },
        },
    )


# --------------------------------------------------------------------------- strategies


async def _scanner_is_active(session: AsyncSession, scanner_code: str | None) -> bool:
    if not scanner_code:
        return True
    scan_def = await session.scalar(
        select(ScanDefinition).where(ScanDefinition.code == scanner_code)
    )
    return scan_def is not None and scan_def.is_active


async def _strategies_table_response(
    request: Request,
    session: AsyncSession,
    user,
    *,
    flash: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    strategies = await BacktestStrategyRepository(session).list(limit=500)
    return _page(
        request,
        "backtests/components/strategies_table.html",
        {
            "request": request,
            "strategies": strategies,
            "can_write": user_has_permission(user, "backtests.write"),
            "flash": flash,
        },
        status_code=status_code,
    )


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    strategies = await BacktestStrategyRepository(session).list(limit=500)
    scan_definitions = await ScanDefinitionRepository(session).list_active()
    return _page(
        request,
        "backtests/strategies.html",
        {
            "request": request,
            "browser_user": user,
            "csrf_token": stage_csrf_token(request),
            "body_theme": "light",
            "strategies": strategies,
            "scan_definitions": scan_definitions,
            "strategy_types": BACKTEST_STRATEGY_TYPES,
            "can_write": user_has_permission(user, "backtests.write"),
        },
    )


@router.get("/strategies/table", response_class=HTMLResponse)
async def strategies_table_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    return await _strategies_table_response(request, session, user)


@router.get("/strategies/{strategy_id}/form", response_class=HTMLResponse)
async def strategy_edit_form(
    strategy_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    if not user_has_permission(user, "backtests.write"):
        return _page(
            request,
            "backtests/components/strategy_form.html",
            {
                "request": request,
                "strategy": None,
                "edit": True,
                "error": "You need the backtests.write permission to edit strategies.",
                "scan_definitions": [],
                "csrf_token": _existing_csrf_token(request),
            },
            status_code=403,
        )
    strategy = await BacktestStrategyRepository(session).get(strategy_id)
    if strategy is None:
        return _page(
            request,
            "backtests/components/strategy_form.html",
            {
                "request": request,
                "strategy": None,
                "edit": True,
                "error": "Strategy not found.",
                "scan_definitions": [],
                "csrf_token": _existing_csrf_token(request),
            },
            status_code=404,
        )
    scan_definitions = await ScanDefinitionRepository(session).list_active()
    return _page(
        request,
        "backtests/components/strategy_form.html",
        {
            "request": request,
            "strategy": strategy,
            "edit": True,
            "scan_definitions": scan_definitions,
            "csrf_token": _existing_csrf_token(request),
        },
    )


@router.post(
    "/strategies/create",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("backtests.write"))],
)
async def create_strategy(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    name = str(form.get("name", "")).strip()
    code = str(form.get("code", "")).strip()
    strategy_type = str(form.get("strategy_type", "scanner")).strip()
    description = _optional(form.get("description"))
    scanner_code = _optional(form.get("scanner_code"))
    is_active = _checked(form, "is_active")

    error: str | None = None
    if not name:
        error = "Strategy name is required."
    elif not code:
        error = "Strategy code is required."
    elif strategy_type not in BACKTEST_STRATEGY_TYPES:
        error = "Invalid strategy type."
    elif await _scanner_is_active(session, scanner_code) is False:
        error = "scanner_code must reference an active scanner."
    if error is None:
        existing = await BacktestStrategyRepository(session).get_by_code(code)
        if existing is not None:
            error = "Strategy code already exists."
    if error is not None:
        return await _strategies_table_response(
            request, session, user, flash={"level": "danger", "message": error}
        )
    strategy = BacktestStrategy(
        name=name,
        code=code,
        strategy_type=strategy_type,
        description=description,
        scanner_code=scanner_code,
        configuration_json={},
        is_active=is_active,
        created_by_user_id=user.user.id,
    )
    await BacktestStrategyRepository(session).add(strategy)
    await session.commit()
    return await _strategies_table_response(
        request,
        session,
        user,
        flash={"level": "success", "message": f"Strategy '{name}' created."},
    )


@router.post(
    "/strategies/{strategy_id}/update",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("backtests.write"))],
)
async def update_strategy(
    strategy_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    strategy = await BacktestStrategyRepository(session).get(strategy_id)
    if strategy is None:
        return await _strategies_table_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": "Strategy not found."},
            status_code=404,
        )
    name = str(form.get("name", "")).strip()
    description = _optional(form.get("description"))
    scanner_code = _optional(form.get("scanner_code"))
    is_active = _checked(form, "is_active")
    if not name:
        return await _strategies_table_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": "Strategy name is required."},
        )
    if await _scanner_is_active(session, scanner_code) is False:
        return await _strategies_table_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": "scanner_code must reference an active scanner."},
        )
    strategy.name = name
    strategy.description = description
    strategy.scanner_code = scanner_code
    strategy.is_active = is_active
    await session.commit()
    return await _strategies_table_response(
        request,
        session,
        user,
        flash={"level": "success", "message": f"Strategy '{name}' updated."},
    )


@router.post(
    "/strategies/{strategy_id}/deactivate",
    response_class=HTMLResponse,
    dependencies=[Depends(require_browser_permission("backtests.write"))],
)
async def deactivate_strategy(
    strategy_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await _browser_user(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    strategy = await BacktestStrategyRepository(session).get(strategy_id)
    if strategy is None:
        return await _strategies_table_response(
            request,
            session,
            user,
            flash={"level": "danger", "message": "Strategy not found."},
            status_code=404,
        )
    if strategy.is_active:
        strategy.is_active = False
        await session.commit()
    return await _strategies_table_response(
        request,
        session,
        user,
        flash={"level": "success", "message": f"Strategy '{strategy.code}' deactivated."},
    )
