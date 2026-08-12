"""Task 17.3 simulator tests: entry, sizing, positions, exits, costs, equity, run state."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.backtest import BacktestEquityPoint, BacktestStrategy, BacktestTrade
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.repositories.backtest import BacktestRunRepository
from app.services.backtest_replay import BacktestReplayService, ReplaySignal, ReplaySummary
from app.services.backtest_simulator import BacktestSimulator


def _signal(
    stock_id: int,
    symbol: str,
    trade_date: date,
    *,
    scanner_code: str = "ema_bullish_crossover",
) -> ReplaySignal:
    return ReplaySignal(
        trade_date=trade_date,
        stock_id=stock_id,
        symbol=symbol,
        scanner_code=scanner_code,
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
            name="Sim Strategy",
            code=f"sim_{abs(hash(str(config)))}",
            strategy_type="scanner",
            scanner_code="ema_bullish_crossover",
            configuration_json=config,
            is_active=True,
        )
        session.add(strategy)
        await session.flush()
        await session.commit()
        return strategy.id


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


def _patch_replay(
    monkeypatch,
    *,
    signals: list[ReplaySignal] | None = None,
    opposite_signals: list[ReplaySignal] | None = None,
) -> None:
    """Replace replay_scanner/replay_all with controlled signal sources."""

    async def _fake_scanner(self, scanner_code, start_date, end_date, symbols=None):
        if opposite_signals and scanner_code == "ema_bearish_crossover":
            return _summary_with(opposite_signals)
        return _summary_with(signals or [])

    async def _fake_all(self, start_date, end_date, symbols=None):
        return _summary_with(signals or [])

    monkeypatch.setattr(BacktestReplayService, "replay_scanner", _fake_scanner)
    monkeypatch.setattr(BacktestReplayService, "replay_all", _fake_all)


async def _trades_for(run_id: int) -> list[BacktestTrade]:
    factory = get_session_factory()
    async with factory() as session:
        trades = list(
            (
                await session.scalars(
                    select(BacktestTrade).where(BacktestTrade.backtest_run_id == run_id)
                )
            ).all()
        )
    return trades


async def _equity_for(run_id: int) -> list[BacktestEquityPoint]:
    factory = get_session_factory()
    async with factory() as session:
        points = list(
            (
                await session.scalars(
                    select(BacktestEquityPoint).where(BacktestEquityPoint.backtest_run_id == run_id)
                )
            ).all()
        )
    return points


async def _size_fixture(symbol: str) -> tuple[int, date, date]:
    """Stock with D0 modest open and D1 large open (entry sizes use D1 open)."""
    stock_id = await _create_stock(symbol)
    d0, d1 = date(2023, 1, 2), date(2023, 1, 4)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 200, "high": 210, "low": 198, "close": 205},
        ],
    )
    return stock_id, d0, d1


# --------------------------------------------------------------------------- ENTRY


@pytest.mark.asyncio
async def test_entry_default_next_open_executes_next_session(monkeypatch) -> None:
    stock_id = await _create_stock("ENTR1")
    d0, d1, d2 = date(2023, 1, 2), date(2023, 1, 4), date(2023, 1, 5)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 202, "high": 210, "low": 200, "close": 205},
            {"date": d2, "open": 206, "high": 212, "low": 203, "close": 208},
        ],
    )
    signal = _signal(stock_id, "ENTR1", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)

    assert summary.entries_created == 1
    assert len(trades) == 1
    # signal on D0 (Jan 2) -> entry on D1 (Jan 4) at open, no look-ahead
    assert trades[0].entry_date == date(2023, 1, 4)
    assert trades[0].entry_price == Decimal("202") * Decimal("1.001")


@pytest.mark.asyncio
async def test_entry_next_close(monkeypatch) -> None:
    stock_id = await _create_stock("ENTR2")
    d0, d1 = date(2023, 1, 2), date(2023, 1, 4)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 102, "high": 107, "low": 101, "close": 106},
            {"date": date(2023, 1, 5), "open": 107, "high": 110, "low": 104, "close": 109},
        ],
    )
    signal = _signal(stock_id, "ENTR2", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_close")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].entry_date == date(2023, 1, 4)
    assert trades[0].entry_price == Decimal("106") * Decimal("1.001")


@pytest.mark.asyncio
async def test_entry_signal_close(monkeypatch) -> None:
    stock_id = await _create_stock("ENTR3")
    d0 = date(2023, 1, 2)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 102, "high": 107, "low": 101, "close": 106},
        ],
    )
    signal = _signal(stock_id, "ENTR3", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="signal_close")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    # signal_close prices at signal date close but entry is recorded next session
    assert trades[0].entry_date == date(2023, 1, 4)
    assert trades[0].entry_price == Decimal("101") * Decimal("1.001")


@pytest.mark.asyncio
async def test_entry_missing_next_session_skip(monkeypatch) -> None:
    stock_id = await _create_stock("ENTR4")
    d0 = date(2023, 1, 2)
    await _add_prices(stock_id, [{"date": d0, "open": 100, "high": 105, "low": 98, "close": 101}])
    signal = _signal(stock_id, "ENTR4", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 0
    assert summary.entries_skipped == 1
    assert len(await _trades_for(run_id)) == 0


@pytest.mark.asyncio
async def test_entry_slippage_applied(monkeypatch) -> None:
    stock_id = await _create_stock("ENTR5")
    d0, d1 = date(2023, 1, 2), date(2023, 1, 4)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 200, "high": 210, "low": 198, "close": 205},
        ],
    )
    signal = _signal(stock_id, "ENTR5", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(strategy_id, slippage_rate=Decimal("0.005"))

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].entry_price == Decimal("200") * Decimal("1.005")


# --------------------------------------------------------------------------- SIZING


@pytest.mark.asyncio
async def test_sizing_fixed_amount(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("SIZE1")
    signal = _signal(stock_id, "SIZE1", d0)
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_amount", position_size_value=Decimal("1000")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].quantity == 4  # floor(1000 / (200 * 1.001))


@pytest.mark.asyncio
async def test_sizing_fixed_quantity(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("SIZE2")
    signal = _signal(stock_id, "SIZE2", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("7")
    )
    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].quantity == 7


@pytest.mark.asyncio
async def test_sizing_percent_capital(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("SIZE3")
    signal = _signal(stock_id, "SIZE3", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="percent_capital", position_size_value=Decimal("10")
    )
    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert trades[0].quantity == 49  # floor(10000 / 200.2)


@pytest.mark.asyncio
async def test_sizing_equal_weight(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("SIZE4")
    signal = _signal(stock_id, "SIZE4", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id,
        position_size_type="equal_weight",
        position_size_value=Decimal("0"),
        max_positions=4,
    )
    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert trades[0].quantity == 124  # floor(25000 / 200.2)


@pytest.mark.asyncio
async def test_sizing_insufficient_cash_skips(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("SIZE5")
    signal = _signal(stock_id, "SIZE5", d0)
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("5000"),
        initial_capital=Decimal("1000"),
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 0
    assert summary.entries_skipped == 1


@pytest.mark.asyncio
async def test_max_positions_skips_extra(monkeypatch) -> None:
    stock1 = await _create_stock("MAXP1")
    stock2 = await _create_stock("MAXP2")
    d0 = date(2023, 1, 2)
    for stock in (stock1, stock2):
        await _add_prices(
            stock,
            [
                {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
                {"date": date(2023, 1, 4), "open": 200, "high": 210, "low": 198, "close": 205},
            ],
        )
    s1 = _signal(stock1, "MAXP1", d0)
    s2 = _signal(stock2, "MAXP2", d0)
    _patch_replay(monkeypatch, signals=[s1, s2])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("1"),
        max_positions=1,
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 1
    assert summary.entries_skipped == 1


# --------------------------------------------------------------------------- POSITIONS


@pytest.mark.asyncio
async def test_duplicate_signal_ignored(monkeypatch) -> None:
    stock_id, d0, d1 = await _size_fixture("DUPE1")
    s1 = _signal(stock_id, "DUPE1", d0)
    s2 = _signal(stock_id, "DUPE1", d1)
    _patch_replay(monkeypatch, signals=[s1, s2])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 1
    assert summary.skip_reasons.get("duplicate_signal", 0) == 1
    assert len(await _trades_for(run_id)) == 1


# --------------------------------------------------------------------------- EXITS


async def _exit_fixture(symbol: str) -> int:
    """Three sessions: entry D1 open, volatile D2 candle, flat D3."""
    stock_id = await _create_stock(symbol)
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 102, "low": 99, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": date(2023, 1, 5), "open": 102, "high": 115, "low": 88, "close": 110},
        ],
    )
    return stock_id


@pytest.mark.asyncio
async def test_exit_stop_loss_and_take_profit(monkeypatch) -> None:
    stock_id = await _exit_fixture("EXIT1")
    signal = _signal(stock_id, "EXIT1", date(2023, 1, 2))
    strategy_id = await _create_strategy(
        execution_mode="next_open",
        exit={"stop_loss_percent": 10, "take_profit_percent": 20},
    )
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    t = trades[0]
    assert t.status == "closed"
    # entry 100 * 1.001 => stop 90.09, target 120.12; D2 low 88 hits stop -> stop loss
    assert t.exit_signal == "stop_loss"
    # stored exit price includes exit slippage (1 - 0.001); column rounds to 4dp
    assert abs(
        t.exit_price - Decimal("100") * Decimal("1.001") * Decimal("0.90") * Decimal("0.999")
    ) < Decimal("0.001")


@pytest.mark.asyncio
async def test_exit_take_profit(monkeypatch) -> None:
    stock_id = await _exit_fixture("EXIT2")
    signal = _signal(stock_id, "EXIT2", date(2023, 1, 2))
    strategy_id = await _create_strategy(
        execution_mode="next_open",
        exit={"stop_loss_percent": 40, "take_profit_percent": 5},
    )
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].exit_signal == "take_profit"
    assert abs(
        trades[0].exit_price
        - Decimal("100") * Decimal("1.001") * Decimal("1.05") * Decimal("0.999")
    ) < Decimal("0.001")


@pytest.mark.asyncio
async def test_exit_max_holding_sessions(monkeypatch) -> None:
    stock_id = await _create_stock("EXIT3")
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 105, "low": 99, "close": 102},
            {"date": date(2023, 1, 5), "open": 102, "high": 106, "low": 100, "close": 104},
            {"date": date(2023, 1, 6), "open": 104, "high": 108, "low": 102, "close": 106},
        ],
    )
    signal = _signal(stock_id, "EXIT3", date(2023, 1, 2))
    strategy_id = await _create_strategy(execution_mode="next_open", exit={"max_holding_days": 2})
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].exit_signal == "max_holding_days"
    assert trades[0].bars_held == 2


@pytest.mark.asyncio
async def test_exit_opposite_signal(monkeypatch) -> None:
    stock_id = await _exit_fixture("EXIT4")
    d0 = date(2023, 1, 2)
    entry_signal = _signal(stock_id, "EXIT4", d0)
    opp = _signal(stock_id, "EXIT4", date(2023, 1, 5), scanner_code="ema_bearish_crossover")
    strategy_id = await _create_strategy(
        execution_mode="next_open",
        exit={"opposite_scanner_code": "ema_bearish_crossover"},
    )
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[entry_signal], opposite_signals=[opp])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].exit_signal == "opposite_signal"


@pytest.mark.asyncio
async def test_exit_end_of_backtest_forced_close(monkeypatch) -> None:
    stock_id = await _exit_fixture("FCLOSE")
    signal = _signal(stock_id, "FCLOSE", date(2023, 1, 2))
    strategy_id = await _create_strategy(execution_mode="next_open", exit={"max_holding_days": 99})
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    assert trades[0].exit_signal == "end_of_backtest"
    assert trades[0].exit_date == date(2023, 1, 5)


# --------------------------------------------------------------------------- COSTS


@pytest.mark.asyncio
async def test_costs_make_pnl_net_return(monkeypatch) -> None:
    stock_id = await _create_stock("COST1")
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 102, "high": 105, "low": 100, "close": 103},
            {"date": date(2023, 1, 5), "open": 105, "high": 110, "low": 103, "close": 108},
        ],
    )
    signal = _signal(stock_id, "COST1", date(2023, 1, 2))
    strategy_id = await _create_strategy(execution_mode="next_open", exit={"max_holding_days": 2})
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("100"),
        commission_rate=Decimal("0.002"),
        slippage_rate=Decimal("0.001"),
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    t = trades[0]
    # entry on D1 open 102 * 1.001 slippage, qty 100
    entry_price = Decimal("102") * Decimal("1.001")
    # exit next session (D2) close 108 * 0.999 slippage
    exit_price = Decimal("108") * Decimal("0.999")
    assert t.quantity == 100
    assert t.entry_price == entry_price
    assert t.exit_price == exit_price
    gross = (exit_price - entry_price) * 100
    costs = entry_price * 100 * Decimal("0.002") + exit_price * 100 * Decimal("0.002")
    net = gross - costs
    assert abs(t.gross_pnl - gross) < Decimal("0.001")
    assert abs(t.costs - costs) < Decimal("0.001")
    assert abs(t.net_pnl - net) < Decimal("0.001")
    assert t.net_pnl == gross - costs


# --------------------------------------------------------------------------- EQUITY / STATE


@pytest.mark.asyncio
async def test_equity_one_point_per_session(monkeypatch) -> None:
    stock_id = await _create_stock("EQT1")
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 102, "high": 105, "low": 100, "close": 103},
            {"date": date(2023, 1, 5), "open": 105, "high": 110, "low": 103, "close": 108},
        ],
    )
    signal = _signal(stock_id, "TESTT", date(2023, 1, 2))
    strategy_id = await _create_strategy(execution_mode="next_open", exit={"max_holding_days": 99})
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    _patch_replay(monkeypatch, signals=[signal])

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    points = await _equity_for(run_id)
    assert len(points) == 3
    assert sorted(p.trade_date for p in points) == [
        date(2023, 1, 2),
        date(2023, 1, 4),
        date(2023, 1, 5),
    ]
    assert all(p.portfolio_value is not None for p in points)


@pytest.mark.asyncio
async def test_no_signals_completes_cleanly(monkeypatch) -> None:
    _patch_replay(monkeypatch, signals=[])
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(strategy_id)

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.signals_received == 0
    assert summary.entries_created == 0
    # build loop never ran, so equity empty; summary trading_days is 0
    assert summary.final_capital == summary.initial_capital


@pytest.mark.asyncio
async def test_final_day_signal_executes(monkeypatch) -> None:
    stock_id = await _create_stock("FINAL1")
    d0, d1 = date(2023, 1, 2), date(2023, 1, 4)
    await _add_prices(
        stock_id,
        [
            {"date": d0, "open": 100, "high": 105, "low": 98, "close": 101},
            {"date": d1, "open": 102, "high": 106, "low": 100, "close": 104},
        ],
    )
    signal = _signal(stock_id, "FINAL1", d1)  # signal on last trading day
    _patch_replay(monkeypatch, signals=[signal])
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    assert summary.entries_created == 0  # no next session -> scan skip, no look-ahead


@pytest.mark.asyncio
async def test_run_state_completed_and_failure(monkeypatch) -> None:
    # success path
    stock_id, d0, _ = await _size_fixture("STATE1")
    signal = _signal(stock_id, "STATE1", d0)
    _patch_replay(monkeypatch, signals=[signal])
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
        run = await BacktestRunRepository(session).get(run_id)
    assert run.status == "completed"
    assert abs(run.final_capital - summary.final_capital) < Decimal("0.0001")
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.duration_ms is not None

    # failure path: invalid execution mode
    bad_strategy = await _create_strategy(execution_mode="bogus_mode")
    bad_run = await _create_run(bad_strategy)
    async with get_session_factory()() as session:
        with pytest.raises(ValueError, match="execution_mode"):
            await BacktestSimulator(session).simulate_run(bad_run)
    async with get_session_factory()() as session:
        run = await BacktestRunRepository(session).get(bad_run)
    assert run.status == "failed"
    assert run.error_message and len(run.error_message) < 2000


@pytest.mark.asyncio
async def test_rerun_already_completed_rejected(monkeypatch) -> None:
    stock_id, d0, _ = await _size_fixture("RERUN1")
    signal = _signal(stock_id, "RERUN1", d0)
    _patch_replay(monkeypatch, signals=[signal])
    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id, position_size_type="fixed_quantity", position_size_value=Decimal("1")
    )

    async with get_session_factory()() as session:
        await BacktestSimulator(session).simulate_run(run_id)
    async with get_session_factory()() as session:
        with pytest.raises(ValueError, match="already completed"):
            await BacktestSimulator(session).simulate_run(run_id)


# --------------------------------------------------------------------------- SETTLEMENT


async def _flat_round_trip(symbol: str) -> int:
    """Flat round trip: signal D0, entry D1 at open, forced close at D2 close."""
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


async def _trend_round_trip(symbol: str, d2_close: int) -> int:
    """Round trip ending at ``d2_close`` used for zero-cost win/loss checks."""
    stock_id = await _create_stock(symbol)
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 102, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 100, "low": 100, "close": 100},
            {
                "date": date(2023, 1, 5),
                "open": d2_close,
                "high": d2_close,
                "low": d2_close,
                "close": d2_close,
            },
        ],
    )
    return stock_id


@pytest.mark.asyncio
async def test_settlement_entry_commission_not_double_counted(monkeypatch) -> None:
    """Entry commission is paid once (in the entry outlay), never twice.

    Regression: the entry outlay already includes the entry commission, but the
    close previously credited ``trade.net_pnl`` (which subtracts the entry
    commission again), understating final capital by exactly the entry commission.
    """
    stock_id = await _flat_round_trip("SETT1")
    signal = _signal(stock_id, "SETT1", date(2023, 1, 2))
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("10"),
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.001"),
    )

    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
        run = await BacktestRunRepository(session).get(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    t = trades[0]

    entry_value = t.entry_price * t.quantity  # 1001.0000
    exit_value = t.exit_price * t.quantity  # 999.0000
    entry_commission = entry_value * Decimal("0.001")  # 1.001
    exit_commission = exit_value * Decimal("0.001")  # 0.999
    assert abs(t.costs - (entry_commission + exit_commission)) < Decimal("0.001")

    # final_capital = initial + gross P&L - entry costs - exit costs (no double count)
    expected_final = (
        Decimal("100000") + (exit_value - entry_value) - entry_commission - exit_commission
    )
    assert expected_final == Decimal("99996.000")
    assert abs(summary.final_capital - expected_final) < Decimal("0.001")
    assert abs(run.final_capital - expected_final) < Decimal("0.001")
    assert abs(
        summary.net_pnl - (summary.final_capital - summary.initial_capital)
    ) < Decimal("0.001")


@pytest.mark.asyncio
async def test_settlement_zero_cost_win_and_loss(monkeypatch) -> None:
    """With zero commission/slippage, final_capital moves exactly by gross P&L."""
    win_stock = await _trend_round_trip("SETW1", 110)
    win_signal = _signal(win_stock, "SETW1", date(2023, 1, 2))
    _patch_replay(monkeypatch, signals=[win_signal])
    strategy_id = await _create_strategy(execution_mode="next_open")
    win_run = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("10"),
        commission_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    )
    async with get_session_factory()() as session:
        win_summary = await BacktestSimulator(session).simulate_run(win_run)
    assert win_summary.total_costs == 0
    assert abs(win_summary.final_capital - Decimal("100100")) < Decimal("0.001")
    assert win_summary.gross_pnl == Decimal("100")

    loss_stock = await _trend_round_trip("SETL1", 90)
    loss_signal = _signal(loss_stock, "SETL1", date(2023, 1, 2))
    _patch_replay(monkeypatch, signals=[loss_signal])
    loss_strategy = await _create_strategy(execution_mode="next_open", scenario="loss")
    loss_run = await _create_run(
        loss_strategy,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("10"),
        commission_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    )
    async with get_session_factory()() as session:
        loss_summary = await BacktestSimulator(session).simulate_run(loss_run)
    assert loss_summary.total_costs == 0
    assert abs(loss_summary.final_capital - Decimal("99900")) < Decimal("0.001")
    assert loss_summary.gross_pnl == Decimal("-100")


@pytest.mark.asyncio
async def test_settlement_cash_flow_charges_exit_commission_once(monkeypatch) -> None:
    """Cash change over one round trip equals -entry_outlay + net exit proceeds."""
    stock_id = await _flat_round_trip("SETC1")
    signal = _signal(stock_id, "SETC1", date(2023, 1, 2))
    _patch_replay(monkeypatch, signals=[signal])

    strategy_id = await _create_strategy(execution_mode="next_open")
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("10"),
        commission_rate=Decimal("0.002"),
        slippage_rate=Decimal("0.001"),
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 1
    t = trades[0]

    entry_value = t.entry_price * t.quantity
    exit_value = t.exit_price * t.quantity
    entry_commission = entry_value * Decimal("0.002")
    exit_commission = exit_value * Decimal("0.002")

    cash_change = summary.final_capital - summary.initial_capital
    expected_change = -(entry_value + entry_commission) + (exit_value - exit_commission)
    assert abs(cash_change - expected_change) < Decimal("0.001")
    assert abs(cash_change - (t.gross_pnl - t.costs)) < Decimal("0.001")


@pytest.mark.asyncio
async def test_settlement_multiple_sequential_trades_identity(monkeypatch) -> None:
    """Accounting identity holds across multiple sequential round trips."""
    stock_id = await _create_stock("SETM1")
    await _add_prices(
        stock_id,
        [
            {"date": date(2023, 1, 2), "open": 100, "high": 102, "low": 98, "close": 101},
            {"date": date(2023, 1, 4), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2023, 1, 5), "open": 110, "high": 110, "low": 110, "close": 110},
            {"date": date(2023, 1, 6), "open": 120, "high": 120, "low": 120, "close": 120},
        ],
    )
    # max_holding_days=1 exits the first position at D2 close; a second signal
    # on D2 re-enters at D3 open (forced closed at D3 close).
    s1 = _signal(stock_id, "SETM1", date(2023, 1, 2))
    s2 = _signal(stock_id, "SETM1", date(2023, 1, 5))
    _patch_replay(monkeypatch, signals=[s1, s2])

    strategy_id = await _create_strategy(
        execution_mode="next_open", exit={"max_holding_days": 1}
    )
    run_id = await _create_run(
        strategy_id,
        position_size_type="fixed_quantity",
        position_size_value=Decimal("10"),
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.001"),
    )
    async with get_session_factory()() as session:
        summary = await BacktestSimulator(session).simulate_run(run_id)
        run = await BacktestRunRepository(session).get(run_id)
    trades = await _trades_for(run_id)
    assert len(trades) == 2

    gross = sum((t.gross_pnl or Decimal("0")) for t in trades)
    costs = sum((t.costs or Decimal("0")) for t in trades)
    net = sum((t.net_pnl or Decimal("0")) for t in trades)
    expected_final = summary.initial_capital + gross - costs

    assert abs(summary.final_capital - expected_final) < Decimal("0.001")
    assert abs(run.final_capital - expected_final) < Decimal("0.001")
    assert abs(net - (summary.final_capital - summary.initial_capital)) < Decimal("0.001")
