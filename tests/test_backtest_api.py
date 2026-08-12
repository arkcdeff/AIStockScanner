"""Task 17.5 API tests: auth, strategies, runs, execution, results, ownership, security."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.main import app
from app.models.backtest import BacktestMetric, BacktestRun, BacktestStrategy
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.repositories.backtest import BacktestRunRepository
from app.services.backtest_replay import BacktestReplayService, ReplaySignal, ReplaySummary
from tests.auth_helpers import create_test_user, get_bearer_token, seed_builtins

API = "/api/v1/backtests"

STRATEGY_PAYLOAD = {
    "name": "API Trend Strategy",
    "code": "api_trend",
    "strategy_type": "scanner",
    "scanner_code": "ema_bullish_crossover",
    "configuration_json": {"execution_mode": "next_open"},
    "is_active": True,
}


def _run_payload(strategy_id: int) -> dict:
    return {
        "strategy_id": strategy_id,
        "start_date": "2023-01-02",
        "end_date": "2023-01-06",
        "initial_capital": 100000,
        "commission_rate": 0.001,
        "slippage_rate": 0.001,
        "position_size_type": "fixed_quantity",
        "position_size_value": 10,
        "max_positions": 5,
    }


@asynccontextmanager
async def _client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def _user_client(role: str, *, is_superuser: bool = False) -> tuple[AsyncClient, int, str]:
    """Return (client with bearer header, user_id, token) for a built-in role."""
    await seed_builtins()
    user_id, username = await create_test_user(
        roles=(role,) if role else (), is_superuser=is_superuser
    )
    token = await get_bearer_token(username)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.headers["Authorization"] = f"Bearer {token}"
    return client, user_id, token


# --------------------------------------------------------------------------- data fixtures


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


def _patch_replay(monkeypatch, *, signals: list[ReplaySignal] | None = None) -> None:
    async def _fake_scanner(self, scanner_code, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    async def _fake_all(self, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    monkeypatch.setattr(BacktestReplayService, "replay_scanner", _fake_scanner)
    monkeypatch.setattr(BacktestReplayService, "replay_all", _fake_all)


async def _create_stock(symbol: str) -> int:
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


async def _flat_round_trip(symbol: str) -> int:
    stock_id = await _create_stock(symbol)
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 102, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2023, 1, 5), "open": 100, "high": 100, "low": 100, "close": 100},
        ],
    )
    return stock_id


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


async def _create_run_row(
    strategy_id: int, requested_by_user_id: int | None = None, **kwargs
) -> int:
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
        defaults.update(kwargs)
        run = await repo.create_run(**defaults)
        await session.commit()
        return run.id


# --------------------------------------------------------------------------- auth / roles


@pytest.mark.asyncio
async def test_backtests_deny_unauthenticated() -> None:
    async with _client() as client:
        assert (await client.get(f"{API}/strategies")).status_code == 401
        assert (await client.get(f"{API}/strategies/1")).status_code == 401
        assert (await client.get(f"{API}/runs")).status_code == 401
        assert (await client.get(f"{API}/runs/1")).status_code == 401
        assert (
            await client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        ).status_code == 401
        assert (
            await client.post(f"{API}/runs", json=_run_payload(1))
        ).status_code == 401
        assert (await client.post(f"{API}/runs/1/execute")).status_code == 401
        assert (await client.get(f"{API}/runs/1/summary")).status_code == 401
        assert (await client.get(f"{API}/runs/1/trades")).status_code == 401
        assert (await client.get(f"{API}/runs/1/equity")).status_code == 401
        assert (await client.get(f"{API}/runs/1/metrics")).status_code == 401


@pytest.mark.asyncio
async def test_viewer_read_allowed_but_execute_and_write_denied() -> None:
    client, viewer_id, _ = await _user_client("viewer")
    try:
        assert (await client.get(f"{API}/strategies")).status_code == 200
        strategy_id = await _create_strategy_row("vw_strat")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=viewer_id)
        assert (await client.get(f"{API}/runs/{run_id}")).status_code == 200
        assert (await client.post(f"{API}/runs/{run_id}/execute")).status_code == 403
        assert (
            await client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        ).status_code == 403
        assert (
            await client.post(f"{API}/runs", json=_run_payload(strategy_id))
        ).status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_analyst_read_and_execute_allowed_write_denied(monkeypatch) -> None:
    stock_id = await _flat_round_trip("ANLX1")
    _patch_replay(monkeypatch, signals=[_signal(stock_id, "ANLX1", date(2023, 1, 2))])
    client, user_id, _ = await _user_client("analyst")
    try:
        assert (await client.get(f"{API}/strategies")).status_code == 200
        strategy_id = await _create_strategy_row("anl_strat")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        executed = await client.post(f"{API}/runs/{run_id}/execute")
        assert executed.status_code == 200
        assert executed.json()["status"] == "completed"
        # analyst has no write permission
        assert (
            await client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        ).status_code == 403
        assert (
            await client.post(f"{API}/runs", json=_run_payload(strategy_id))
        ).status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_admin_and_superuser_write_allowed() -> None:
    admin_client, _, _ = await _user_client("admin")
    try:
        created = await admin_client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        assert created.status_code == 201
    finally:
        await admin_client.aclose()

    super_client, _, _ = await _user_client("viewer", is_superuser=True)
    try:
        created = await super_client.post(
            f"{API}/strategies", json={**STRATEGY_PAYLOAD, "code": "super_strat"}
        )
        assert created.status_code == 201
        assert (await super_client.get(f"{API}/strategies")).status_code == 200
    finally:
        await super_client.aclose()


# --------------------------------------------------------------------------- strategies


@pytest.mark.asyncio
async def test_strategy_create_duplicate_detail_update_delete() -> None:
    client, user_id, _ = await _user_client("admin")
    try:
        created = await client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        assert created.status_code == 201
        body = created.json()
        strategy_id = body["id"]
        assert body["code"] == "api_trend"
        assert body["created_by_user_id"] == user_id

        duplicate = await client.post(f"{API}/strategies", json=STRATEGY_PAYLOAD)
        assert duplicate.status_code == 409

        detail = await client.get(f"{API}/strategies/{strategy_id}")
        assert detail.status_code == 200
        assert detail.json()["code"] == "api_trend"

        updated = await client.put(
            f"{API}/strategies/{strategy_id}",
            json={"name": "Renamed Strategy", "is_active": False},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed Strategy"
        assert updated.json()["is_active"] is False

        # soft delete (deactivate) preserves the record
        deactivated = await client.delete(f"{API}/strategies/{strategy_id}")
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        detail2 = await client.get(f"{API}/strategies/{strategy_id}")
        assert detail2.status_code == 200
        assert detail2.json()["is_active"] is False

        missing = await client.get(f"{API}/strategies/99999")
        assert missing.status_code == 404
        assert (await client.put(f"{API}/strategies/99999", json={})).status_code == 404
        assert (await client.delete(f"{API}/strategies/99999")).status_code == 404
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_strategy_invalid_scanner_code_rejected() -> None:
    client, _, _ = await _user_client("admin")
    try:
        bad = await client.post(
            f"{API}/strategies",
            json={**STRATEGY_PAYLOAD, "code": "api_bad_scan", "scanner_code": "no_such_scanner"},
        )
        assert bad.status_code == 422
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_strategy_list_pagination_and_filters() -> None:
    client, _, _ = await _user_client("admin")
    try:
        for index in range(4):
            await client.post(
                f"{API}/strategies",
                json={
                    **STRATEGY_PAYLOAD,
                    "code": f"api_trend_{index}",
                    "name": f"Strategy {index}",
                    "strategy_type": "rule_based" if index % 2 else "scanner",
                },
            )
        page = await client.get(f"{API}/strategies?limit=2&offset=0")
        assert page.status_code == 200
        body = page.json()
        assert body["total"] == 4
        assert len(body["items"]) == 2
        assert body["offset"] == 0 and body["limit"] == 2

        filtered = await client.get(f"{API}/strategies?strategy_type=rule_based")
        assert filtered.json()["total"] == 2

        search = await client.get(f"{API}/strategies?search=Strategy%201")
        assert search.json()["total"] == 1
        assert search.json()["items"][0]["code"] == "api_trend_1"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- runs


@pytest.mark.asyncio
async def test_run_create_and_validation_errors() -> None:
    client, user_id, _ = await _user_client("admin")
    try:
        strategy_id = await _create_strategy_row("api_run_strat")
        created = await client.post(f"{API}/runs", json=_run_payload(strategy_id))
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert created.json()["status"] == "pending"
        assert created.json()["requested_by_user_id"] == user_id
        assert created.json()["strategy_id"] == strategy_id

        detail = await client.get(f"{API}/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["strategy"]["code"] == "api_run_strat"

        assert (await client.get(f"{API}/runs/99999")).status_code == 404
        assert (
            await client.post(f"{API}/runs", json=_run_payload(99999))
        ).status_code == 404

        bad_dates = await client.post(
            f"{API}/runs",
            json={
                **_run_payload(strategy_id),
                "start_date": "2023-01-10",
                "end_date": "2023-01-02",
            },
        )
        assert bad_dates.status_code == 422

        bad_capital = await client.post(
            f"{API}/runs", json={**_run_payload(strategy_id), "initial_capital": 0}
        )
        assert bad_capital.status_code == 422

        bad_commission = await client.post(
            f"{API}/runs", json={**_run_payload(strategy_id), "commission_rate": -0.1}
        )
        assert bad_commission.status_code == 422

        bad_sizing = await client.post(
            f"{API}/runs", json={**_run_payload(strategy_id), "position_size_type": "bogus"}
        )
        assert bad_sizing.status_code == 422

        future = await client.post(
            f"{API}/runs", json={**_run_payload(strategy_id), "end_date": "2099-01-01"}
        )
        assert future.status_code == 422

        bad_benchmark = await client.post(
            f"{API}/runs", json={**_run_payload(strategy_id), "benchmark_symbol": "NOPE"}
        )
        assert bad_benchmark.status_code == 422

        # deactivated strategy cannot be used for a new run
        await client.delete(f"{API}/strategies/{strategy_id}")
        inactive = await client.post(f"{API}/runs", json=_run_payload(strategy_id))
        assert inactive.status_code == 422
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_run_list_pagination_and_filters() -> None:
    client, user_id, _ = await _user_client("admin")
    try:
        strategy_id = await _create_strategy_row("api_list_strat")
        for _ in range(3):
            created = await client.post(f"{API}/runs", json=_run_payload(strategy_id))
            assert created.status_code == 201

        listing = await client.get(f"{API}/runs?limit=2")
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert all(item["requested_by_user_id"] == user_id for item in body["items"])

        filtered = await client.get(f"{API}/runs?status=pending&strategy_id={strategy_id}")
        assert filtered.json()["total"] == 3

        empty = await client.get(f"{API}/runs?status=completed")
        assert empty.json()["total"] == 0
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- execution


@pytest.mark.asyncio
async def test_execute_run_end_to_end_and_settlement(monkeypatch) -> None:
    stock_id = await _flat_round_trip("APIT1")
    _patch_replay(monkeypatch, signals=[_signal(stock_id, "APIT1", date(2023, 1, 2))])

    client, user_id, _ = await _user_client("analyst")
    try:
        strategy_id = await _create_strategy_row("api_exec")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)

        executed = await client.post(f"{API}/runs/{run_id}/execute")
        assert executed.status_code == 200
        body = executed.json()
        assert body["status"] == "completed"
        assert body["summary"]["entries_created"] == 1
        assert body["summary"]["signals_received"] == 1
        # settlement regression through the API: no entry-commission double count
        assert abs(Decimal(str(body["final_capital"])) - Decimal("99996.000")) < Decimal("0.001")
        assert body["metrics"] is not None
        assert body["metrics"]["total_trades"] == 1

        # duplicate execution rejected
        again = await client.post(f"{API}/runs/{run_id}/execute")
        assert again.status_code == 409

        # persisted completed run and metric row
        async with get_session_factory()() as session:
            run = await session.get(BacktestRun, run_id)
            assert run is not None and run.status == "completed"
            metric = await session.scalar(
                select(BacktestMetric).where(BacktestMetric.backtest_run_id == run_id)
            )
            assert metric is not None
            assert metric.total_trades == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_execute_rejects_running_run(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    client, user_id, _ = await _user_client("analyst")
    try:
        strategy_id = await _create_strategy_row("api_run_lock")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        async with get_session_factory()() as session:
            run = await session.get(BacktestRun, run_id)
            run.status = "running"
            await session.commit()
        response = await client.post(f"{API}/runs/{run_id}/execute")
        assert response.status_code == 409
        assert "running" in response.json()["detail"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_execute_failure_is_sanitized(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    client, _, _ = await _user_client("admin")
    try:
        created = await client.post(
            f"{API}/strategies",
            json={
                **STRATEGY_PAYLOAD,
                "code": "api_bad_config",
                "configuration_json": {"execution_mode": "bogus_mode"},
            },
        )
        assert created.status_code == 201
        strategy_id = created.json()["id"]
        run_resp = await client.post(f"{API}/runs", json=_run_payload(strategy_id))
        assert run_resp.status_code == 201
        run_id = run_resp.json()["id"]

        executed = await client.post(f"{API}/runs/{run_id}/execute")
        assert executed.status_code == 400
        detail = executed.json()["detail"]
        assert detail == "Backtest execution failed: ValueError"
        text = executed.text
        assert "traceback" not in text.lower()
        assert "IntegrityError" not in text
        assert "psycopg" not in text.lower()

        # run persisted as failed
        async with get_session_factory()() as session:
            run = await session.get(BacktestRun, run_id)
            assert run is not None and run.status == "failed"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- results


@pytest.mark.asyncio
async def test_results_before_completion() -> None:
    client, user_id, _ = await _user_client("analyst")
    try:
        strategy_id = await _create_strategy_row("api_pending")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)

        summary = await client.get(f"{API}/runs/{run_id}/summary")
        assert summary.status_code == 200
        sbody = summary.json()
        assert sbody["run"]["status"] == "pending"
        assert sbody["metric"] is None
        assert sbody["trade_count"] == 0
        assert sbody["strategy"]["code"] == "api_pending"

        metrics = await client.get(f"{API}/runs/{run_id}/metrics")
        assert metrics.status_code == 409

        trades = await client.get(f"{API}/runs/{run_id}/trades")
        assert trades.status_code == 200
        assert trades.json()["total"] == 0

        equity = await client.get(f"{API}/runs/{run_id}/equity")
        assert equity.status_code == 200
        assert equity.json()["items"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_results_after_execution(monkeypatch) -> None:
    stock1 = await _rising_round_trip("RESL1")
    stock2 = await _rising_round_trip("RESL2")
    _patch_replay(
        monkeypatch,
        signals=[
            _signal(stock1, "RESL1", date(2023, 1, 2)),
            _signal(stock2, "RESL2", date(2023, 1, 2)),
        ],
    )
    client, user_id, _ = await _user_client("analyst")
    try:
        strategy_id = await _create_strategy_row("api_results")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=user_id)
        executed = await client.post(f"{API}/runs/{run_id}/execute")
        assert executed.status_code == 200

        # summary: status, capital, period, trade count, metrics
        summary = await client.get(f"{API}/runs/{run_id}/summary")
        assert summary.status_code == 200
        sbody = summary.json()
        assert sbody["run"]["status"] == "completed"
        assert sbody["trade_count"] == 2
        assert sbody["metric"] is not None
        assert sbody["metric"]["total_trades"] == 2

        # trades pagination + filters + symbol
        page = await client.get(f"{API}/runs/{run_id}/trades?limit=1&offset=0")
        assert page.status_code == 200
        pbody = page.json()
        assert pbody["total"] == 2
        assert len(pbody["items"]) == 1
        assert pbody["items"][0]["symbol"] in {"RESL1", "RESL2"}
        assert pbody["items"][0]["position_side"] == "long"
        assert pbody["items"][0]["status"] == "closed"

        closed = await client.get(f"{API}/runs/{run_id}/trades?status=closed")
        assert closed.json()["total"] == 2
        profitable = await client.get(f"{API}/runs/{run_id}/trades?profitable=true")
        assert profitable.json()["total"] == 2
        not_profitable = await client.get(f"{API}/runs/{run_id}/trades?profitable=false")
        assert not_profitable.json()["total"] == 0
        by_symbol = await client.get(f"{API}/runs/{run_id}/trades?symbol=RESL1")
        assert by_symbol.json()["total"] == 1

        # equity chronological
        equity = await client.get(f"{API}/runs/{run_id}/equity")
        assert equity.status_code == 200
        dates = [item["trade_date"] for item in equity.json()["items"]]
        assert dates == sorted(dates)
        assert all(item["portfolio_value"] is not None for item in equity.json()["items"])

        # metrics: persisted values with nulls preserved (no benchmark configured)
        metrics = await client.get(f"{API}/runs/{run_id}/metrics")
        assert metrics.status_code == 200
        mbody = metrics.json()
        assert mbody["total_trades"] == 2
        assert mbody["winning_trades"] == 2
        assert mbody["benchmark_return_percent"] is None
        assert mbody["alpha"] is None
        assert mbody["total_return_percent"] is not None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_results_with_benchmark_symbol(monkeypatch) -> None:
    stock_id = await _rising_round_trip("BENCH1")
    _patch_replay(monkeypatch, signals=[_signal(stock_id, "BENCH1", date(2023, 1, 2))])
    client, user_id, _ = await _user_client("analyst")
    try:
        strategy_id = await _create_strategy_row("api_bench")
        run_id = await _create_run_row(
            strategy_id,
            requested_by_user_id=user_id,
            benchmark_symbol="BENCH1",
        )
        executed = await client.post(f"{API}/runs/{run_id}/execute")
        assert executed.status_code == 200
        metrics = await client.get(f"{API}/runs/{run_id}/metrics")
        assert metrics.status_code == 200
        mbody = metrics.json()
        assert mbody["benchmark_return_percent"] is not None
        assert mbody["alpha"] is not None
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- ownership


@pytest.mark.asyncio
async def test_run_ownership_restrictions_and_admin_override(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    owner_client, owner_id, _ = await _user_client("admin")
    try:
        strategy_id = await _create_strategy_row("api_owner")
        run_id = await _create_run_row(strategy_id, requested_by_user_id=owner_id)

        # owner can read and execute
        assert (await owner_client.get(f"{API}/runs/{run_id}")).status_code == 200
        assert (await owner_client.post(f"{API}/runs/{run_id}/execute")).status_code == 200

        # another admin-role user (not superuser) is denied
        other_client, _, _ = await _user_client("admin")
        try:
            denied = await other_client.get(f"{API}/runs/{run_id}")
            assert denied.status_code == 403
            denied_exec = await other_client.post(f"{API}/runs/{run_id}/execute")
            assert denied_exec.status_code == 403
            denied_results = await other_client.get(f"{API}/runs/{run_id}/summary")
            assert denied_results.status_code == 403
            # other user's listing does not include the private run
            listing = await other_client.get(f"{API}/runs")
            assert listing.json()["total"] == 0
        finally:
            await other_client.aclose()

        # superuser override is allowed
        super_client, _, _ = await _user_client("viewer", is_superuser=True)
        try:
            assert (await super_client.get(f"{API}/runs/{run_id}")).status_code == 200
            assert (await super_client.get(f"{API}/runs/{run_id}/metrics")).status_code == 200
            listing = await super_client.get(f"{API}/runs")
            assert listing.json()["total"] == 1
        finally:
            await super_client.aclose()
    finally:
        await owner_client.aclose()
