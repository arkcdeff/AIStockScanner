"""Task 17.6 UI tests: browser pages, HTMX partials, charts, tables, and security.

These tests reuse the auth helpers from ``tests/auth_helpers`` and the data
helpers from ``tests/test_backtest_api``. No external NSE or network calls are
made. Execution is exercised end-to-end against the patched simulator so that
metrics/equity/trades/charts are exercised on a real run.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.main import app
from app.models.backtest import BacktestMetric, BacktestStrategy
from app.services.backtest_replay import (
    BacktestReplayService,
    ReplaySignal,
    ReplaySummary,
)
from tests.auth_helpers import (
    _create_user,
    _login_browser,
    admin_client,
    analyst_client,
    seed_builtins,
    viewer_client,
)

# -- data helpers (reused/adapted from tests/test_backtest_api.py) --


def _signal(stock_id: int, symbol: str, trade_date: date) -> ReplaySignal:
    return ReplaySignal(
        trade_date=trade_date,
        stock_id=stock_id,
        symbol=symbol,
        scanner_code="ema_bullish_crossover",
        matched=True,
        score=1.0,
        close=101.0,
        volume=1000,
    )


def _summary_with(signals: list[ReplaySignal]) -> ReplaySummary:
    summary = ReplaySummary()
    summary.signals = signals
    summary.signals_generated = len(signals)
    return summary


def _patch_replay(monkeypatch, *, signals=None) -> None:
    async def _fake_scanner(self, scanner_code, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    async def _fake_all(self, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    monkeypatch.setattr(BacktestReplayService, "replay_scanner", _fake_scanner)
    monkeypatch.setattr(BacktestReplayService, "replay_all", _fake_all)


async def _create_stock(symbol: str) -> int:
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(
            symbol=symbol,
            company_name=f"{symbol} Ltd",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stock)
        await session.flush()
        await session.commit()
        return stock.id


async def _add_prices(stock_id: int, rows: list[dict]) -> None:
    from app.models.daily_price import DailyPrice

    factory = get_session_factory()
    async with factory() as session:
        for row in rows:
            session.add(
                DailyPrice(
                    stock_id=stock_id,
                    trade_date=row["date"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    previous_close=row.get("previous_close", row["open"]),
                    volume=row.get("volume", 1000),
                    delivery_percentage=row.get("delivery_percentage", 50.0),
                )
            )
        await session.commit()


async def _rising_round_trip(symbol: str) -> int:
    stock_id = await _create_stock(symbol)
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 102, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2023, 1, 5), "open": 110, "high": 110, "low": 110, "close": 110},
        ],
    )
    return stock_id


async def _create_strategy_row(code: str, **config) -> int:
    factory = get_session_factory()
    async with factory() as session:
        strategy = BacktestStrategy(
            name=f"Strategy {code}",
            code=code,
            strategy_type="scanner",
            scanner_code="ema_bullish_crossover",
            configuration_json=config or {"execution_mode": "next_open"},
            is_active=True,
        )
        session.add(strategy)
        await session.flush()
        await session.commit()
        return strategy.id


async def _create_run_row(strategy_id: int, requested_by_user_id=None, **kwargs) -> int:
    from app.repositories.backtest import BacktestRunRepository

    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        defaults = dict(
            strategy_id=strategy_id,
            requested_by_user_id=requested_by_user_id,
            status="pending",
            start_date="2023-01-02",
            end_date="2023-01-06",
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.001"),
            position_size_type="fixed_quantity",
            position_size_value=Decimal("10"),
        )
        error_message = kwargs.pop("error_message", None)
        defaults.update(kwargs)
        run = await repo.create_run(**defaults)
        if error_message is not None:
            run.error_message = error_message
        await session.commit()
        return run.id


def _csrf_token(response_text: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    if match:
        return match.group(1)
    match = re.search(r'name="_csrf_token" value="([^"]+)"', response_text)
    assert match, "CSRF token not found in page"
    return match.group(1)


async def _execute_run(client, run_id: int) -> None:
    page = await client.get(f"/backtests/runs/{run_id}")
    token = _csrf_token(page.text)
    await client.post(
        f"/backtests/runs/{run_id}/execute",
        data={"_csrf_token": token},
        headers={"x-csrf-token": token},
    )


def _unique() -> str:
    return uuid.uuid4().hex[:8]


async def _browser_user_id(client) -> int:
    """Return the user id for the browser-authenticated client."""
    auth = client.headers.get("authorization", "")
    token = auth.replace("Bearer ", "", 1) if auth.startswith("Bearer ") else ""
    async with get_session_factory()() as session:
        from app.services.auth_service import AuthService

        current = await AuthService(session).get_current_user(token)
        return current.user.id


# --------------------------------------------------------------------------- navigation / auth


@pytest.mark.asyncio
async def test_backtests_page_requires_browser_auth() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/backtests")
        assert response.status_code == 302
        assert "/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_backtests_page_allows_reader() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/backtests")
        assert response.status_code == 200
        assert "New backtest run" in response.text


@pytest.mark.asyncio
async def test_backtests_page_forbidden_without_read() -> None:
    await seed_builtins()
    username = f"noaccess_{_unique()}"
    await _create_user(
        username, f"{username}@example.com", is_superuser=False, roles=()
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await _login_browser(client, username)
    try:
        assert (await client.get("/backtests")).status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sidebar_contains_backtesting_link_for_reader() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert 'href="/backtests"' in response.text


# --------------------------------------------------------------------------- workspace


@pytest.mark.asyncio
async def test_workspace_renders_form_and_recent_runs() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/backtests")
        assert response.status_code == 200
        assert 'name="strategy_id"' in response.text
        assert "Recent runs" in response.text


@pytest.mark.asyncio
async def test_workspace_shows_readonly_hint_for_viewer() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/backtests")
        assert response.status_code == 200
        assert "read-only" in response.text.lower()


# --------------------------------------------------------------------------- strategies


@pytest.mark.asyncio
async def test_strategy_create_read_only_denied() -> None:
    async with viewer_client(browser=True) as client:
        page = await client.get("/backtests/strategies")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "X",
                "code": "x_code",
                "strategy_type": "scanner",
                "is_active": "1",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert created.status_code == 403


@pytest.mark.asyncio
async def test_strategy_create_and_duplicate_safe() -> None:
    async with admin_client(browser=True) as client:
        page = await client.get("/backtests/strategies")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "Ema Cross",
                "code": "ema_cross_ui",
                "strategy_type": "scanner",
                "scanner_code": "ema_bullish_crossover",
                "is_active": "1",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert created.status_code == 200
        assert "created" in created.text
        assert "ema_cross_ui" in created.text

        duplicate = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "Dup",
                "code": "ema_cross_ui",
                "strategy_type": "scanner",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert duplicate.status_code == 200
        assert "already exists" in duplicate.text


@pytest.mark.asyncio
async def test_strategy_invalid_scanner_rejected() -> None:
    async with admin_client(browser=True) as client:
        page = await client.get("/backtests/strategies")
        token = _csrf_token(page.text)
        bad = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "Bad Scan",
                "code": "bad_scan_ui",
                "strategy_type": "scanner",
                "scanner_code": "no_such_scanner",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert bad.status_code == 200
        assert "active scanner" in bad.text


@pytest.mark.asyncio
async def test_strategy_edit_deactivate_xss_escaped() -> None:
    async with admin_client(browser=True) as client:
        page = await client.get("/backtests/strategies")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "XSS",
                "code": "xss_test",
                "strategy_type": "scanner",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert "created" in created.text

        async with get_session_factory()() as session:
            strategy = await session.scalar(
                select(BacktestStrategy).where(BacktestStrategy.code == "xss_test")
            )
            strategy_id = strategy.id

        form_page = await client.get(f"/backtests/strategies/{strategy_id}/form")
        assert form_page.status_code == 200
        form_token = _csrf_token(form_page.text)

        updated = await client.post(
            f"/backtests/strategies/{strategy_id}/update",
            data={
                "name": "XSS <script>alert(1)</script>",
                "description": "<img src=x onerror=alert(1)>",
                "_csrf_token": form_token,
            },
            headers={"x-csrf-token": form_token},
        )
        assert updated.status_code == 200
        assert "<script>alert(1)</script>" not in updated.text
        assert "&lt;script&gt;" in updated.text

        form_again = await client.get(f"/backtests/strategies/{strategy_id}/form")
        deact_token = _csrf_token(form_again.text)
        deactivated = await client.post(
            f"/backtests/strategies/{strategy_id}/deactivate",
            data={"_csrf_token": deact_token},
            headers={"x-csrf-token": deact_token},
        )
        assert deactivated.status_code == 200
        assert "deactivated" in deactivated.text
        assert "inactive" in deactivated.text


# --------------------------------------------------------------------------- runs / execution


@pytest.mark.asyncio
async def test_create_run_admin_redirects_to_detail() -> None:
    async with admin_client(browser=True) as client:
        strategy_id = await _create_strategy_row("admin_run")
        page = await client.get("/backtests")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/runs/create",
            data={
                "strategy_id": str(strategy_id),
                "start_date": "2023-01-02",
                "end_date": "2023-01-06",
                "initial_capital": "100000",
                "position_size_type": "fixed_quantity",
                "position_size_value": "10",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert "/backtests/runs/" in created.headers["location"]


@pytest.mark.asyncio
async def test_create_run_viewer_denied() -> None:
    async with viewer_client(browser=True) as client:
        strategy_id = await _create_strategy_row("viewer_run")
        page = await client.get("/backtests")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/runs/create",
            data={
                "strategy_id": str(strategy_id),
                "start_date": "2023-01-02",
                "end_date": "2023-01-06",
                "initial_capital": "100000",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert created.status_code == 403


@pytest.mark.asyncio
async def test_run_detail_ownership_restriction(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with admin_client(browser=True) as client:
        user_id = await _browser_user_id(client)
        strategy_id = await _create_strategy_row("owned_run")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        assert (await client.get(f"/backtests/runs/{run_id}")).status_code == 200

    async with admin_client(browser=True) as client:
        denied = await client.get(f"/backtests/runs/{run_id}")
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_run_detail_missing_returns_404() -> None:
    async with analyst_client(browser=True) as client:
        assert (await client.get("/backtests/runs/999999")).status_code == 404


@pytest.mark.asyncio
async def test_execute_run_completed_render(monkeypatch) -> None:
    stock_id = await _rising_round_trip("EQLC1")
    _patch_replay(
        monkeypatch, signals=[_signal(stock_id, "EQLC1", date(2023, 1, 2))]
    )
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("exec_success")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "completed" in detail.text
        assert "Total return" in detail.text
        assert "Equity curve" in detail.text
        assert "Trades" in detail.text


@pytest.mark.asyncio
async def test_execute_run_sanitized_failure(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with admin_client(browser=True) as client:
        page = await client.get("/backtests/strategies")
        token = _csrf_token(page.text)
        created = await client.post(
            "/backtests/strategies/create",
            data={
                "name": "Bad Config",
                "code": "bad_config_ui",
                "strategy_type": "scanner",
                "is_active": "1",
                "_csrf_token": token,
            },
            headers={"x-csrf-token": token},
        )
        assert "created" in created.text

        async with get_session_factory()() as session:
            strategy = await session.scalar(
                select(BacktestStrategy).where(
                    BacktestStrategy.code == "bad_config_ui"
                )
            )
            strategy.configuration_json = {"execution_mode": "bogus_mode"}
            await session.commit()

        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy.id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "failed" in detail.text
        assert "Execution failed" in detail.text
        text = detail.text.lower()
        assert "traceback" not in text
        assert "integrityerror" not in text
        assert "psycopg" not in text


@pytest.mark.asyncio
async def test_execute_double_execution_rejected(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("double_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)
        first = await client.get(f"/backtests/runs/{run_id}")
        assert "completed" in first.text
        page = await client.get(f"/backtests/runs/{run_id}")
        token = _csrf_token(page.text)
        second = await client.post(
            f"/backtests/runs/{run_id}/execute",
            data={"_csrf_token": token},
            headers={"x-csrf-token": token},
        )
        assert "already completed" in second.text


# -- metrics / null rendering --


@pytest.mark.asyncio
async def test_metric_cards_render_and_nulls_are_na(monkeypatch) -> None:
    stock_id = await _rising_round_trip("MTRC1")
    _patch_replay(
        monkeypatch, signals=[_signal(stock_id, "MTRC1", date(2023, 1, 2))]
    )
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("mtrc_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)

        metrics_page = await client.get(f"/backtests/runs/{run_id}/metrics")
        assert metrics_page.status_code == 200
        assert "Performance metrics" in metrics_page.text
        assert "Total return" in metrics_page.text
        assert "CAGR" in metrics_page.text
        assert "Max drawdown" in metrics_page.text
        assert "nan" not in metrics_page.text.lower()
        assert "inf" not in metrics_page.text.lower()

        async with get_session_factory()() as session:
            metric = await session.scalar(
                select(BacktestMetric).where(
                    BacktestMetric.backtest_run_id == run_id
                )
            )
            assert metric.benchmark_return_percent is None
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert "—" in detail.text


# --------------------------------------------------------------------------- charts


@pytest.mark.asyncio
async def test_charts_containers_and_chronological_data(monkeypatch) -> None:
    stock_id = await _rising_round_trip("CHRT1")
    _patch_replay(
        monkeypatch, signals=[_signal(stock_id, "CHRT1", date(2023, 1, 2))]
    )
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("chart_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert 'id="equity-chart"' in detail.text
        assert 'id="drawdown-chart"' in detail.text
        assert "backtest-equity-data" in detail.text
        assert "Equity curve" in detail.text

        match = re.search(
            r'id="backtest-equity-data">(.*?)</script>', detail.text, re.DOTALL
        )
        assert match, "equity payload not embedded"
        data = json.loads(match.group(1))
        times = [p["time"] for p in data["points"]]
        assert times == sorted(times)
        assert data["initial_capital"] == 100000.0
        assert all(
            "value" in p and isinstance(p["value"], (int, float))
            for p in data["points"]
        )


@pytest.mark.asyncio
async def test_charts_empty_state_before_execution(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("chart_empty")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "chart-empty" in detail.text


# --------------------------------------------------------------------------- trades


@pytest.mark.asyncio
async def test_trades_table_pagination_and_style(monkeypatch) -> None:
    stock_id = await _rising_round_trip("TRAD1")
    _patch_replay(
        monkeypatch, signals=[_signal(stock_id, "TRAD1", date(2023, 1, 2))]
    )
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("trade_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)
        trades_page = await client.get(f"/backtests/runs/{run_id}/trades")
        assert trades_page.status_code == 200
        assert "Trades" in trades_page.text
        assert "TRAD1" in trades_page.text
        assert "Net P" in trades_page.text

        closed = await client.get(f"/backtests/runs/{run_id}/trades?status=closed")
        assert closed.status_code == 200
        profitable = await client.get(
            f"/backtests/runs/{run_id}/trades?profitable=true"
        )
        assert profitable.status_code == 200


@pytest.mark.asyncio
async def test_trades_empty_state() -> None:
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("empty_trades")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        trades_page = await client.get(f"/backtests/runs/{run_id}/trades")
        assert trades_page.status_code == 200
        assert "No trades match" in trades_page.text


# --------------------------------------------------------------------------- history


@pytest.mark.asyncio
async def test_run_history_status_badges_and_detail_links(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("hist_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        history = await client.get("/backtests/runs")
        assert history.status_code == 200
        assert "Run history" in history.text
        assert f"/backtests/runs/{run_id}" in history.text

        partial = await client.get("/backtests/runs/history")
        assert partial.status_code == 200
        assert "Runs" in partial.text
        assert str(run_id) in partial.text


# --------------------------------------------------------------------------- run detail states


@pytest.mark.asyncio
async def test_run_detail_pending_and_completed(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("states_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "pending" in detail.text
        assert "Run backtest" in detail.text
        await _execute_run(client, run_id)
        done = await client.get(f"/backtests/runs/{run_id}")
        assert done.status_code == 200
        assert "completed" in done.text


@pytest.mark.asyncio
async def test_run_detail_running_shows_poll() -> None:
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("running_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(
            strategy_id, requested_by_user_id=user_id, status="running"
        )
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "every 3s" in detail.text


@pytest.mark.asyncio
async def test_run_detail_failed_shows_error() -> None:
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("failed_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(
            strategy_id,
            requested_by_user_id=user_id,
            status="failed",
            error_message="Simulated failure message",
        )
        detail = await client.get(f"/backtests/runs/{run_id}")
        assert detail.status_code == 200
        assert "failed" in detail.text
        assert "Execution failed" in detail.text
        assert "Simulated failure message" in detail.text


# --------------------------------------------------------------------------- HTMX partials


@pytest.mark.asyncio
async def test_partial_status_codes_and_fragments(monkeypatch) -> None:
    stock_id = await _rising_round_trip("PART1")
    _patch_replay(
        monkeypatch, signals=[_signal(stock_id, "PART1", date(2023, 1, 2))]
    )
    async with analyst_client(browser=True) as client:
        strategy_id = await _create_strategy_row("part_run")
        user_id = await _browser_user_id(client)
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        await _execute_run(client, run_id)

        metrics_partial = await client.get(f"/backtests/runs/{run_id}/metrics")
        assert metrics_partial.status_code == 200
        assert "Performance metrics" in metrics_partial.text

        trades_partial = await client.get(f"/backtests/runs/{run_id}/trades")
        assert trades_partial.status_code == 200
        assert "Trades" in trades_partial.text
        assert "table" in trades_partial.text.lower()

        workspace_partial = await client.get(
            f"/backtests/runs/{run_id}/workspace"
        )
        assert workspace_partial.status_code == 200
        assert "Run #" + str(run_id) in workspace_partial.text

        missing = await client.get("/backtests/runs/999999/metrics")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_strategy_form_partial_unauthorized() -> None:
    async with viewer_client(browser=True) as client:
        assert (await client.get("/backtests/strategies/1/form")).status_code == 403


@pytest.mark.asyncio
async def test_strategy_form_partial_missing_strategy() -> None:
    async with admin_client(browser=True) as client:
        assert (await client.get("/backtests/strategies/999999/form")).status_code == 404




