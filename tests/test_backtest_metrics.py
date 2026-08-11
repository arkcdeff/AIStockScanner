"""Task 17.4 metrics engine tests.

Covers total/annualized/CAGR returns, trade statistics, drawdown, Sharpe,
Sortino, holding days, benchmark return, alpha, persistence idempotency, CLI
integration, determinism, and the sanitized failure policy.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models.backtest import (
    BacktestEquityPoint,
    BacktestMetric,
    BacktestStrategy,
    BacktestTrade,
)
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.repositories.backtest import BacktestMetricRepository, BacktestRunRepository
from app.services.backtest_metrics import (
    BacktestMetricCalculator,
    BacktestMetricError,
    MetricSummary,
    sanitized_metric_failure,
)
from app.services.backtest_replay import BacktestReplayService, ReplaySignal, ReplaySummary
from app.services.backtest_simulator import BacktestSimulator

# --------------------------------------------------------------------------- helpers


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


async def _create_strategy(**config) -> int:
    factory = get_session_factory()
    async with factory() as session:
        strategy = BacktestStrategy(
            name="Metric Strategy",
            code=f"metric_{abs(hash(str(config)))}",
            strategy_type="scanner",
            scanner_code="ema_bullish_crossover",
            configuration_json=config,
            is_active=True,
        )
        session.add(strategy)
        await session.flush()
        await session.commit()
        return strategy.id


async def _add_prices(stock_id: int, rows: list[dict]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for row in rows:
            session.add(
                DailyPrice(
                    stock_id=stock_id,
                    trade_date=row["date"],
                    open=row.get("open", row["close"]),
                    high=row.get("high", row["close"]),
                    low=row.get("low", row["close"]),
                    close=row["close"],
                    previous_close=row.get("previous_close", row["close"]),
                    volume=row.get("volume", 1000),
                    delivery_percentage=row.get("delivery_percentage", 50.0),
                )
            )
        await session.commit()


async def _create_completed_run(
    strategy_id: int,
    stock_id: int,
    *,
    start_date: str = "2023-01-02",
    end_date: str = "2023-12-29",
    initial_capital: Decimal = Decimal("100000"),
    final_capital: Decimal | None = None,
    benchmark_symbol: str | None = None,
    trades: list[dict] | None = None,
    equity: list[tuple[date, Decimal]] | None = None,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        run = await repo.create_run(
            strategy_id=strategy_id,
            requested_by_user_id=None,
            status="completed",
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.001"),
            position_size_type="percent_capital",
            position_size_value=Decimal("10"),
            benchmark_symbol=benchmark_symbol,
        )
        if final_capital is not None:
            run.final_capital = final_capital
        for spec in trades or []:
            entry = spec.get("entry_date", date(2023, 1, 2))
            session.add(
                BacktestTrade(
                    backtest_run_id=run.id,
                    stock_id=spec.get("stock_id", stock_id),
                    entry_date=entry,
                    entry_price=spec.get("entry_price", Decimal("100")),
                    exit_date=spec.get("exit_date"),
                    exit_price=spec.get("exit_price"),
                    quantity=spec.get("quantity", Decimal("1")),
                    position_side="long",
                    entry_signal=spec.get("entry_signal", "test"),
                    exit_signal=spec.get("exit_signal", "test"),
                    gross_pnl=spec.get("gross_pnl"),
                    costs=spec.get("costs", Decimal("0")),
                    net_pnl=spec.get("net_pnl"),
                    return_percent=spec.get("return_percent"),
                    bars_held=spec.get("bars_held"),
                    status=spec.get("status", "closed"),
                )
            )
        for trade_date, value in equity or []:
            session.add(
                BacktestEquityPoint(
                    backtest_run_id=run.id,
                    trade_date=trade_date,
                    cash=value,
                    invested_value=Decimal("0"),
                    portfolio_value=value,
                )
            )
        await session.commit()
        return run.id


async def _run_metrics(run_id: int) -> MetricSummary:
    factory = get_session_factory()
    async with factory() as session:
        return await BacktestMetricCalculator(session).compute_metrics(run_id)


async def _metric_row(run_id: int) -> BacktestMetric | None:
    factory = get_session_factory()
    async with factory() as session:
        return await BacktestMetricRepository(session).get_by_run(run_id)


async def _metric_count(run_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(BacktestMetric)
                    .where(BacktestMetric.backtest_run_id == run_id)
                )
            )
            or 0
        )


# --------------------------------------------------------------------------- RETURNS


@pytest.mark.asyncio
async def test_total_return_positive() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("RET1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
        trades=[{"net_pnl": Decimal("10000")}],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_return_percent == Decimal("10")
    assert metrics.total_trades == 1


@pytest.mark.asyncio
async def test_total_return_negative() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("RET2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("90000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("90000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_return_percent == Decimal("-10")


@pytest.mark.asyncio
async def test_total_return_flat() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("RET3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("100000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("100000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_return_percent == Decimal("0")
    assert metrics.max_drawdown_percent == Decimal("0")


# --------------------------------------------------------------------------- CAGR / ANNUALIZED


@pytest.mark.asyncio
async def test_cagr_multiyear() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("CAGR1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        start_date="2021-01-01",
        end_date="2024-01-01",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("200000"),
        equity=[(date(2021, 1, 1), Decimal("100000")), (date(2024, 1, 1), Decimal("200000"))],
    )
    metrics = await _run_metrics(run_id)
    days = (date(2024, 1, 1) - date(2021, 1, 1)).days
    expected = (
        (Decimal("200000") / Decimal("100000"))
        ** (Decimal("365.25") / Decimal(days))
        - 1
    ) * 100
    assert metrics.cagr is not None
    assert abs(metrics.cagr - expected) < Decimal("0.0001")
    assert metrics.annualized_return_percent == metrics.cagr


@pytest.mark.asyncio
async def test_cagr_subyear() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("CAGR2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        start_date="2023-01-02",
        end_date="2023-04-02",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 4, 2), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    days = (date(2023, 4, 2) - date(2023, 1, 2)).days
    expected = (
        (Decimal("110000") / Decimal("100000"))
        ** (Decimal("365.25") / Decimal(days))
        - 1
    ) * 100
    assert metrics.cagr is not None
    assert abs(metrics.cagr - expected) < Decimal("0.0001")


@pytest.mark.asyncio
async def test_cagr_zero_days_is_none() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("CAGR3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        start_date="2023-01-02",
        end_date="2023-01-02",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.cagr is None
    assert metrics.annualized_return_percent is None
    assert metrics.total_return_percent == Decimal("10")


@pytest.mark.asyncio
async def test_cagr_nonpositive_final_capital_is_none() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("CAGR4")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("0"),
        equity=[(date(2023, 1, 2), Decimal("0"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.cagr is None
    assert metrics.total_return_percent == Decimal("-100")


# --------------------------------------------------------------------------- TRADE STATS


@pytest.mark.asyncio
async def test_trade_stats_winners_and_losers() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("100000"),
        trades=[
            {"net_pnl": Decimal("50"), "gross_pnl": Decimal("50")},
            {"net_pnl": Decimal("50"), "gross_pnl": Decimal("50")},
            {"net_pnl": Decimal("-20"), "gross_pnl": Decimal("-20")},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert abs(metrics.win_rate - Decimal("66.66666666666666666666666667")) < Decimal("0.0001")
    assert metrics.gross_profit == Decimal("100")
    assert metrics.gross_loss == Decimal("20")
    assert metrics.profit_factor == Decimal("5")
    assert metrics.average_win == Decimal("50")
    assert metrics.average_loss == Decimal("20")
    assert abs(metrics.expectancy - Decimal("26.66666666666666666666666667")) < Decimal("0.0001")


@pytest.mark.asyncio
async def test_trade_stats_no_trades_are_null() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("100000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("100000"))],
        trades=[],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 0
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 0
    assert metrics.win_rate is None
    assert metrics.gross_profit == Decimal("0")
    assert metrics.gross_loss == Decimal("0")
    assert metrics.profit_factor is None
    assert metrics.average_win is None
    assert metrics.average_loss is None
    assert metrics.expectancy is None
    assert metrics.average_holding_days is None


@pytest.mark.asyncio
async def test_trade_stats_no_winners() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {"net_pnl": Decimal("-10"), "gross_pnl": Decimal("-10")},
            {"net_pnl": Decimal("-30"), "gross_pnl": Decimal("-30")},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.winning_trades == 0
    assert metrics.average_win is None
    assert metrics.win_rate == Decimal("0")
    assert metrics.gross_profit == Decimal("0")
    assert metrics.gross_loss == Decimal("40")
    assert metrics.profit_factor == Decimal("0")
    assert metrics.average_loss == Decimal("20")
    assert metrics.expectancy == Decimal("-20")


@pytest.mark.asyncio
async def test_trade_stats_no_losers() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT4")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {"net_pnl": Decimal("10"), "gross_pnl": Decimal("10")},
            {"net_pnl": Decimal("30"), "gross_pnl": Decimal("30")},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.losing_trades == 0
    assert metrics.average_loss is None
    assert metrics.win_rate == Decimal("100")
    assert metrics.gross_loss == Decimal("0")
    assert metrics.profit_factor is None
    assert metrics.average_win == Decimal("20")
    assert metrics.expectancy == Decimal("20")


@pytest.mark.asyncio
async def test_trade_stats_breakeven_neither_winner_nor_loser() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT5")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {"net_pnl": Decimal("0"), "gross_pnl": Decimal("5")},
            {"net_pnl": Decimal("10"), "gross_pnl": Decimal("10")},
            {"net_pnl": Decimal("-10"), "gross_pnl": Decimal("-10")},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 3
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert abs(metrics.win_rate - Decimal("33.33333333333333333333333333")) < Decimal("0.0001")


@pytest.mark.asyncio
async def test_trade_stats_open_trades_excluded() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("STAT6")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {"net_pnl": Decimal("100"), "gross_pnl": Decimal("100")},
            {"net_pnl": Decimal("50"), "gross_pnl": Decimal("50"), "status": "open"},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 1
    assert metrics.winning_trades == 1


# --------------------------------------------------------------------------- DRAWDOWN


@pytest.mark.asyncio
async def test_drawdown_flat_series() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DD1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("100000")),
            (date(2023, 1, 4), Decimal("100000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.max_drawdown_percent == Decimal("0")


@pytest.mark.asyncio
async def test_drawdown_single_drawdown() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DD2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("80000")),
            (date(2023, 1, 4), Decimal("90000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.max_drawdown_percent == Decimal("20")


@pytest.mark.asyncio
async def test_drawdown_multiple_peaks_deepest_wins() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DD3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("120000")),
            (date(2023, 1, 4), Decimal("110000")),
            (date(2023, 1, 5), Decimal("90000")),
            (date(2023, 1, 6), Decimal("130000")),
            (date(2023, 1, 7), Decimal("125000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    # deepest peak-to-trough: 120000 -> 90000 = 25%
    assert metrics.max_drawdown_percent == Decimal("25")


@pytest.mark.asyncio
async def test_drawdown_recovery_then_deeper() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DD4")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("90000")),
            (date(2023, 1, 4), Decimal("100000")),
            (date(2023, 1, 5), Decimal("80000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.max_drawdown_percent == Decimal("20")


@pytest.mark.asyncio
async def test_drawdown_persisted_on_equity_points() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DD5")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("120000")),
            (date(2023, 1, 4), Decimal("90000")),
        ],
    )
    await _run_metrics(run_id)
    factory = get_session_factory()
    async with factory() as session:
        points = list(
            (
                await session.execute(
                    select(BacktestEquityPoint).where(
                        BacktestEquityPoint.backtest_run_id == run_id
                    )
                )
            ).scalars()
        )
    by_date = {p.trade_date: p for p in points}
    assert by_date[date(2023, 1, 2)].drawdown_percent == Decimal("0")
    assert by_date[date(2023, 1, 3)].drawdown_percent == Decimal("0")
    assert by_date[date(2023, 1, 4)].drawdown_percent == Decimal("25")


# --------------------------------------------------------------------------- SHARPE / SORTINO


@pytest.mark.asyncio
async def test_sharpe_valid() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("SH1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("110000")),
            (date(2023, 1, 4), Decimal("99000")),
            (date(2023, 1, 5), Decimal("108900")),
        ],
    )
    metrics = await _run_metrics(run_id)
    returns = [Decimal("0.1"), Decimal("-0.1"), Decimal("0.1")]
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((r - mean) ** 2 for r in returns) / Decimal(len(returns))
    expected = (mean / variance.sqrt()) * Decimal(252).sqrt()
    assert metrics.sharpe_ratio is not None
    assert abs(metrics.sharpe_ratio - expected) < Decimal("0.0001")


@pytest.mark.asyncio
async def test_sharpe_zero_variance_is_none() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("SH2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("100000")),
            (date(2023, 1, 4), Decimal("100000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.sharpe_ratio is None


@pytest.mark.asyncio
async def test_sharpe_insufficient_observations_is_none() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("SH3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 1, 3), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


@pytest.mark.asyncio
async def test_sortino_downside_only() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("SO1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("110000")),
            (date(2023, 1, 4), Decimal("99000")),
            (date(2023, 1, 5), Decimal("108900")),
        ],
    )
    metrics = await _run_metrics(run_id)
    returns = [Decimal("0.1"), Decimal("-0.1"), Decimal("0.1")]
    downside = [r for r in returns if r < 0]
    mean = sum(returns) / Decimal(len(returns))
    downside_dev = (sum(r ** 2 for r in downside) / Decimal(len(downside))).sqrt()
    expected = (mean / downside_dev) * Decimal(252).sqrt()
    assert metrics.sortino_ratio is not None
    assert abs(metrics.sortino_ratio - expected) < Decimal("0.0001")


@pytest.mark.asyncio
async def test_sortino_no_downside_is_none() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("SO2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("110000")),
            (date(2023, 1, 4), Decimal("121000")),
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.sortino_ratio is None


# --------------------------------------------------------------------------- HOLDING DAYS


@pytest.mark.asyncio
async def test_average_holding_days_uses_bars_held() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("HOLD1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {"net_pnl": Decimal("10"), "bars_held": 5},
            {"net_pnl": Decimal("10"), "bars_held": 7},
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.average_holding_days == Decimal("6")


@pytest.mark.asyncio
async def test_average_holding_days_fallback_to_calendar_days() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("HOLD2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {
                "net_pnl": Decimal("10"),
                "bars_held": None,
                "entry_date": date(2023, 1, 2),
                "exit_date": date(2023, 1, 5),
            },
            {"net_pnl": Decimal("10"), "bars_held": 4},
        ],
    )
    metrics = await _run_metrics(run_id)
    # fallback trade = 3 calendar days, other = 4 bars -> average 3.5
    assert metrics.average_holding_days == Decimal("3.5")


@pytest.mark.asyncio
async def test_average_holding_days_none_without_data() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("HOLD3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        trades=[
            {
                "net_pnl": Decimal("10"),
                "bars_held": None,
                "entry_date": date(2023, 1, 2),
                "exit_date": None,
            }
        ],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.average_holding_days is None


# --------------------------------------------------------------------------- BENCHMARK / ALPHA


@pytest.mark.asyncio
async def test_benchmark_return_valid() -> None:
    benchmark_stock_id = await _create_stock("NIFTY50")
    await _add_prices(
        benchmark_stock_id,
        [
            {"date": date(2023, 1, 2), "close": Decimal("100")},
            {"date": date(2023, 6, 1), "close": Decimal("110")},
            {"date": date(2023, 12, 29), "close": Decimal("115")},
        ],
    )
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        benchmark_symbol="NIFTY50",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.benchmark_return_percent == Decimal("15")
    assert metrics.alpha == Decimal("-5")


@pytest.mark.asyncio
async def test_benchmark_return_none_without_symbol() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.benchmark_return_percent is None
    assert metrics.alpha is None


@pytest.mark.asyncio
async def test_benchmark_return_none_when_stock_missing() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK3")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        benchmark_symbol="DOES_NOT_EXIST",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.benchmark_return_percent is None
    assert metrics.alpha is None


@pytest.mark.asyncio
async def test_benchmark_return_none_when_no_prices() -> None:
    await _create_stock("NIFTY50")
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK4")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        benchmark_symbol="NIFTY50",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.benchmark_return_percent is None


@pytest.mark.asyncio
async def test_benchmark_uses_first_after_start_and_last_before_end() -> None:
    benchmark_stock_id = await _create_stock("NIFTY50")
    await _add_prices(
        benchmark_stock_id,
        [
            {"date": date(2022, 12, 30), "close": Decimal("50")},
            {"date": date(2023, 1, 2), "close": Decimal("100")},
            {"date": date(2023, 12, 29), "close": Decimal("115")},
            {"date": date(2024, 1, 2), "close": Decimal("200")},
        ],
    )
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK5")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        benchmark_symbol="NIFTY50",
        start_date="2023-01-02",
        end_date="2023-12-29",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    # outside-range prices (50, 200) must not be used; 100 -> 115 = 15%
    assert metrics.benchmark_return_percent == Decimal("15")


@pytest.mark.asyncio
async def test_benchmark_none_when_zero_first_close() -> None:
    benchmark_stock_id = await _create_stock("NIFTY50")
    await _add_prices(
        benchmark_stock_id,
        [
            {"date": date(2023, 1, 2), "close": Decimal("0")},
            {"date": date(2023, 12, 29), "close": Decimal("115")},
        ],
    )
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("BMK6")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        benchmark_symbol="NIFTY50",
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    metrics = await _run_metrics(run_id)
    assert metrics.benchmark_return_percent is None
    assert metrics.alpha is None


# --------------------------------------------------------------------------- PERSISTENCE


@pytest.mark.asyncio
async def test_persistence_creates_row_and_recompute_updates_same_row() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("PERS1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
        trades=[{"net_pnl": Decimal("10000")}],
    )

    first = await _run_metrics(run_id)
    assert first.total_return_percent == Decimal("10")
    assert await _metric_count(run_id) == 1
    row = await _metric_row(run_id)
    assert row is not None
    assert row.total_return_percent == Decimal("10")
    # DB Numeric(12,4) rounds to 4dp
    assert abs(row.cagr - first.cagr) < Decimal("0.0001")
    first_sharpe = first.sharpe_ratio or Decimal("0")
    row_sharpe = row.sharpe_ratio or Decimal("0")
    assert abs(row_sharpe - first_sharpe) < Decimal("0.0001")

    # recompute: same row updated, still one row
    second = await _run_metrics(run_id)
    assert await _metric_count(run_id) == 1
    assert second.total_return_percent == Decimal("10")
    row2 = await _metric_row(run_id)
    assert row2 is not None
    assert row2.id == row.id
    assert row2.total_return_percent == Decimal("10")


@pytest.mark.asyncio
async def test_persistence_no_duplicates_across_multiple_recomputes() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("PERS2")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[(date(2023, 1, 2), Decimal("100000")), (date(2023, 12, 29), Decimal("110000"))],
    )
    for _ in range(3):
        await _run_metrics(run_id)
    assert await _metric_count(run_id) == 1


@pytest.mark.asyncio
async def test_determinism() -> None:
    strategy_id = await _create_strategy()
    stock_id = await _create_stock("DET1")
    run_id = await _create_completed_run(
        strategy_id,
        stock_id,
        initial_capital=Decimal("100000"),
        final_capital=Decimal("110000"),
        equity=[
            (date(2023, 1, 2), Decimal("100000")),
            (date(2023, 1, 3), Decimal("110000")),
            (date(2023, 1, 4), Decimal("99000")),
            (date(2023, 12, 29), Decimal("110000")),
        ],
        trades=[
            {"net_pnl": Decimal("50"), "gross_pnl": Decimal("50")},
            {"net_pnl": Decimal("-20"), "gross_pnl": Decimal("-20")},
        ],
    )
    first = await _run_metrics(run_id)
    second = await _run_metrics(run_id)
    assert first.as_dict() == second.as_dict()


# --------------------------------------------------------------------------- INTEGRATION


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


async def _create_run(strategy_id: int, **kwargs) -> int:
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        defaults = dict(
            strategy_id=strategy_id,
            requested_by_user_id=None,
            status="pending",
            start_date="2023-01-02",
            end_date="2023-01-06",
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.001"),
            position_size_type="percent_capital",
            position_size_value=Decimal("10"),
        )
        defaults.update(kwargs)
        run = await repo.create_run(**defaults)
        await session.commit()
        return run.id


def _patch_replay(
    monkeypatch,
    *,
    signals: list[ReplaySignal] | None = None,
) -> None:
    async def _fake_scanner(self, scanner_code, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    async def _fake_all(self, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    monkeypatch.setattr(BacktestReplayService, "replay_scanner", _fake_scanner)
    monkeypatch.setattr(BacktestReplayService, "replay_all", _fake_all)


@pytest.mark.asyncio
async def test_integration_simulator_then_metrics(monkeypatch) -> None:
    stock_id = await _create_stock("INTG1")
    d0, d1, d2 = date(2023, 1, 2), date(2023, 1, 4), date(2023, 1, 5)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 102, "high": 105, "low": 100, "close": 103},
            {"date": d2, "open": 105, "high": 110, "low": 103, "close": 108},
        ],
    )
    signal = _signal(stock_id, "INTG1", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open", exit={"max_holding_days": 2})
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)

    assert summary.entries_created == 1

    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 1

    async with get_session_factory()() as session:
        run = await BacktestRunRepository(session).get(run_id)
    expected_return = (
        (run.final_capital - run.initial_capital) / run.initial_capital * Decimal("100")
    )
    assert metrics.total_return_percent == expected_return
    assert metrics.cagr is not None
    assert metrics.win_rate in (Decimal("0"), Decimal("100"))

    row = await _metric_row(run_id)
    assert row is not None
    assert row.backtest_run_id == run_id
    assert row.total_trades == 1
    assert row.max_drawdown_percent is not None

    # equity point drawdown persisted
    factory = get_session_factory()
    async with factory() as session:
        points = list(
            (
                await session.execute(
                    select(BacktestEquityPoint).where(
                        BacktestEquityPoint.backtest_run_id == run_id
                    )
                )
            ).scalars()
        )
    assert len(points) == 3
    assert all(p.drawdown_percent is not None for p in points)


@pytest.mark.asyncio
async def test_integration_no_signals_metrics_still_compute(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 0

    metrics = await _run_metrics(run_id)
    assert metrics.total_trades == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.total_return_percent is not None


# --------------------------------------------------------------------------- FAILURE POLICY


@pytest.mark.asyncio
async def test_failure_policy_non_completed_run_raises() -> None:
    strategy_id = await _create_strategy()
    factory = get_session_factory()
    async with factory() as session:
        repo = BacktestRunRepository(session)
        run = await repo.create_run(
            strategy_id=strategy_id,
            requested_by_user_id=None,
            status="pending",
            start_date="2023-01-02",
            end_date="2023-01-06",
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.001"),
            position_size_type="percent_capital",
            position_size_value=Decimal("10"),
        )
        await session.commit()
        run_id = run.id
    async with factory() as session:
        with pytest.raises(BacktestMetricError, match="not completed"):
            await BacktestMetricCalculator(session).compute_metrics(run_id)


@pytest.mark.asyncio
async def test_failure_policy_missing_run_raises() -> None:
    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(BacktestMetricError, match="not found"):
            await BacktestMetricCalculator(session).compute_metrics(999999)


def test_sanitized_failure_never_leaks_internals() -> None:
    exc = ValueError(
        "DB down at postgresql://user:secret@host/db "
        "traceback: app/services/backtest_metrics.py:123"
    )
    failure = sanitized_metric_failure(42, exc)
    message = str(failure)
    assert "secret" not in message
    assert "postgresql://" not in message
    assert "traceback" not in message
    assert "backtest_metrics.py" not in message
    assert "Metrics computation failed for run 42" in message
    assert "ValueError" in message
