"""Deterministic trade simulation engine for backtesting.

Converts ReplaySignal results into simulated trades with:
- next-session execution (no look-ahead)
- capital management
- position sizing
- commission/slippage costs
- stop loss / take profit / max holding / opposite signal exits
- end-of-backtest forced close
- equity curve persistence
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest import (
    BacktestEquityPoint,
    BacktestRun,
    BacktestStrategy,
    BacktestTrade,
)
from app.models.daily_price import DailyPrice
from app.repositories.backtest import (
    BacktestEquityPointRepository,
    BacktestRunRepository,
    BacktestTradeRepository,
)
from app.services.backtest_replay import BacktestReplayService, ReplaySignal

# Valid execution modes
EXECUTION_MODES = ("next_open", "next_close", "signal_close")

# Valid position size types
POSITION_SIZE_TYPES = ("fixed_amount", "fixed_quantity", "percent_capital", "equal_weight")

# Default exit configuration
DEFAULT_EXIT_CONFIG: dict[str, Any] = {
    "stop_loss_percent": None,
    "take_profit_percent": None,
    "max_holding_days": None,
    "opposite_scanner_code": None,
}


@dataclass(slots=True)
class SimulationSummary:
    """In-memory summary of a simulation run."""

    run_id: int
    start_date: date
    end_date: date
    signals_received: int = 0
    entries_created: int = 0
    entries_skipped: int = 0
    trades_closed: int = 0
    open_positions_forced_closed: int = 0
    initial_capital: Decimal = Decimal("0")
    final_capital: Decimal = Decimal("0")
    gross_pnl: Decimal = Decimal("0")
    total_costs: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    trading_days: int = 0
    duration_ms: float = 0.0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "signals_received": self.signals_received,
            "entries_created": self.entries_created,
            "entries_skipped": self.entries_skipped,
            "trades_closed": self.trades_closed,
            "open_positions_forced_closed": self.open_positions_forced_closed,
            "initial_capital": str(self.initial_capital),
            "final_capital": str(self.final_capital),
            "gross_pnl": str(self.gross_pnl),
            "total_costs": str(self.total_costs),
            "net_pnl": str(self.net_pnl),
            "trading_days": self.trading_days,
            "duration_ms": self.duration_ms,
            "skip_reasons": self.skip_reasons,
        }


@dataclass(slots=True)
class _OpenPosition:
    """Internal representation of an open position during simulation."""

    stock_id: int
    symbol: str
    entry_date: date
    entry_price: Decimal
    quantity: Decimal
    entry_signal: str
    stop_loss: Decimal | None
    target_price: Decimal | None
    entry_commission: Decimal
    bars_held: int = 0
    entry_slippage: Decimal = Decimal("0")
    commission_rate: Decimal = Decimal("0")


class BacktestSimulator:
    """Simulate trades from replay signals against historical prices."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = BacktestRunRepository(session)
        self.trades = BacktestTradeRepository(session)
        self.equity = BacktestEquityPointRepository(session)

    # ------------------------------------------------------------------ public API

    async def simulate_run(self, run_id: int) -> SimulationSummary:
        """Simulate a backtest run from its strategy and replay signals."""
        started = time.perf_counter()

        run = await self.runs.get_with_details(run_id)
        if run is None:
            raise ValueError(f"Backtest run {run_id} not found")
        if run.status == "completed":
            raise ValueError(
                f"Backtest run {run_id} is already completed; "
                "reset or create a new run to re-simulate"
            )

        strategy = run.strategy
        if strategy is None:
            raise ValueError(f"Backtest run {run_id} has no strategy")

        # Mark running
        run.status = "running"
        run.started_at = datetime.now(UTC)
        run.error_message = None
        await self.session.flush()

        try:
            summary = await self._simulate(run, strategy)
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            run.duration_ms = (time.perf_counter() - started) * 1000
            run.final_capital = summary.final_capital
            await self.session.commit()
            return summary
        except Exception as exc:
            await self.session.rollback()
            run = await self.runs.get(run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.duration_ms = (time.perf_counter() - started) * 1000
                run.error_message = str(exc)[:2000]
                await self.session.commit()
            raise

    # ------------------------------------------------------------------ core simulation

    async def _simulate(
        self, run: BacktestRun, strategy: BacktestStrategy
    ) -> SimulationSummary:
        """Core simulation loop."""
        summary = SimulationSummary(
            run_id=run.id,
            start_date=run.start_date,
            end_date=run.end_date,
            initial_capital=run.initial_capital,
            final_capital=run.initial_capital,
        )

        # Validate configuration
        config = self._validate_configuration(strategy, run)

        # Replay signals
        replay = BacktestReplayService(self.session)
        if strategy.scanner_code:
            replay_summary = await replay.replay_scanner(
                strategy.scanner_code, run.start_date, run.end_date
            )
        else:
            replay_summary = await replay.replay_all(run.start_date, run.end_date)

        signals = replay_summary.signals
        summary.signals_received = len(signals)

        # Group signals by date
        signals_by_date: dict[date, list[ReplaySignal]] = {}
        for signal in signals:
            signals_by_date.setdefault(signal.trade_date, []).append(signal)

        # Load all prices for the universe/date range (plus look-forward for next-session)
        stock_ids = list({s.stock_id for s in signals})
        prices_by_stock = await self._load_prices(stock_ids, run.start_date, run.end_date)

        # Build a sorted list of trading dates
        trading_dates = sorted(
            {p.trade_date for prices in prices_by_stock.values() for p in prices}
        )
        trading_dates = [d for d in trading_dates if run.start_date <= d <= run.end_date]
        summary.trading_days = len(trading_dates)

        # State
        cash = run.initial_capital
        open_positions: dict[int, _OpenPosition] = {}
        closed_trades: list[BacktestTrade] = []
        equity_points: list[BacktestEquityPoint] = []
        skip_reasons: dict[str, int] = {}

        # Opposite scanner signals (if configured)
        opposite_signals_by_date: dict[date, set[int]] = {}
        if config["opposite_scanner_code"]:
            opp_replay = await replay.replay_scanner(
                config["opposite_scanner_code"], run.start_date, run.end_date
            )
            for signal in opp_replay.signals:
                opposite_signals_by_date.setdefault(signal.trade_date, set()).add(
                    signal.stock_id
                )

        # Process each trading date in order
        for trade_date in trading_dates:
            # 1. Check exits for open positions using today's candle
            for stock_id in list(open_positions.keys()):
                pos = open_positions[stock_id]
                prices = prices_by_stock.get(stock_id, [])
                today_price = next((p for p in prices if p.trade_date == trade_date), None)
                if today_price is None:
                    continue

                exit_result = self._check_exit(
                    pos, today_price, trade_date, config, opposite_signals_by_date
                )
                if exit_result is not None:
                    exit_price, exit_signal, exit_date = exit_result
                    trade = self._close_position(
                        pos, exit_date, exit_price, exit_signal, run.id
                    )
                    closed_trades.append(trade)
                    cash += self._cash_returned_on_close(trade, pos)
                    del open_positions[stock_id]
                    summary.trades_closed += 1

            # 2. Process new signals for this date (entries execute next session)
            if trade_date in signals_by_date:
                for signal in signals_by_date[trade_date]:
                    if signal.stock_id in open_positions:
                        skip_reasons["duplicate_signal"] = (
                            skip_reasons.get("duplicate_signal", 0) + 1
                        )
                        continue

                    entry = await self._try_entry(
                        signal, trade_date, run, config, prices_by_stock, cash, open_positions
                    )
                    if entry is not None:
                        entry_pos, entry_cost = entry
                        open_positions[signal.stock_id] = entry_pos
                        cash -= entry_cost
                        summary.entries_created += 1
                    else:
                        summary.entries_skipped += 1

            # 3. Record equity point for this date
            invested = self._mark_to_market(open_positions, prices_by_stock, trade_date)
            portfolio_value = cash + invested
            equity_points.append(
                BacktestEquityPoint(
                    backtest_run_id=run.id,
                    trade_date=trade_date,
                    cash=cash,
                    invested_value=invested,
                    portfolio_value=portfolio_value,
                    drawdown_percent=None,
                )
            )

        # End-of-backtest: force close all remaining positions
        for stock_id in list(open_positions.keys()):
            pos = open_positions[stock_id]
            prices = prices_by_stock.get(stock_id, [])
            last_price = next(
                (p for p in reversed(prices) if p.trade_date <= run.end_date), None
            )
            if last_price is not None:
                exit_price = Decimal(str(last_price.close))
                trade = self._close_position(
                    pos, last_price.trade_date, exit_price, "end_of_backtest", run.id
                )
                closed_trades.append(trade)
                cash += self._cash_returned_on_close(trade, pos)
                summary.open_positions_forced_closed += 1
            del open_positions[stock_id]

        # Persist trades and equity in bulk
        if closed_trades:
            await self.trades.bulk_insert(closed_trades)
        if equity_points:
            await self.equity.bulk_insert(equity_points)

        # Compute summary
        summary.final_capital = cash
        summary.gross_pnl = sum(
            (t.gross_pnl or Decimal("0")) for t in closed_trades
        )
        summary.total_costs = sum((t.costs or Decimal("0")) for t in closed_trades)
        summary.net_pnl = sum((t.net_pnl or Decimal("0")) for t in closed_trades)
        summary.skip_reasons = skip_reasons
        summary.duration_ms = 0.0  # set by caller

        return summary

    # ------------------------------------------------------------------ helpers

    def _validate_configuration(
        self, strategy: BacktestStrategy, run: BacktestRun
    ) -> dict[str, Any]:
        """Validate strategy configuration and run settings."""
        config = dict(DEFAULT_EXIT_CONFIG)
        raw = strategy.configuration_json or {}

        # Execution mode
        execution_mode = raw.get("execution_mode", "next_open")
        if execution_mode not in EXECUTION_MODES:
            raise ValueError(
                f"execution_mode must be one of {', '.join(EXECUTION_MODES)}"
            )
        config["execution_mode"] = execution_mode

        # Position size type
        if run.position_size_type not in POSITION_SIZE_TYPES:
            raise ValueError(
                f"position_size_type must be one of {', '.join(POSITION_SIZE_TYPES)}"
            )
        config["position_size_type"] = run.position_size_type
        config["position_size_value"] = run.position_size_value

        # Exit configuration
        exit_cfg = raw.get("exit", {})
        stop_loss = exit_cfg.get("stop_loss_percent")
        if stop_loss is not None:
            stop_loss = Decimal(str(stop_loss))
            if stop_loss < 0:
                raise ValueError("stop_loss_percent must be >= 0")
            config["stop_loss_percent"] = stop_loss

        take_profit = exit_cfg.get("take_profit_percent")
        if take_profit is not None:
            take_profit = Decimal(str(take_profit))
            if take_profit < 0:
                raise ValueError("take_profit_percent must be >= 0")
            config["take_profit_percent"] = take_profit

        max_holding = exit_cfg.get("max_holding_days")
        if max_holding is not None:
            max_holding = int(max_holding)
            if max_holding <= 0:
                raise ValueError("max_holding_days must be > 0")
            config["max_holding_days"] = max_holding

        opposite = exit_cfg.get("opposite_scanner_code")
        if opposite is not None:
            config["opposite_scanner_code"] = str(opposite)

        # Commission/slippage bounds
        if run.commission_rate < 0 or run.commission_rate > 0.1:
            raise ValueError("commission_rate must be between 0 and 0.1")
        if run.slippage_rate < 0 or run.slippage_rate > 0.1:
            raise ValueError("slippage_rate must be between 0 and 0.1")

        # Percent capital bounds
        if run.position_size_type == "percent_capital":
            if run.position_size_value <= 0 or run.position_size_value > 100:
                raise ValueError("percent_capital position_size_value must be > 0 and <= 100")

        return config

    async def _load_prices(
        self, stock_ids: list[int], start_date: date, end_date: date
    ) -> dict[int, list[DailyPrice]]:
        """Bulk-load DailyPrice rows for the universe/date range, grouped by stock."""
        if not stock_ids:
            return {}
        rows = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id.in_(stock_ids))
                .where(DailyPrice.trade_date >= start_date)
                .where(DailyPrice.trade_date <= end_date)
                .order_by(DailyPrice.stock_id, DailyPrice.trade_date)
            )
        ).all()
        grouped: dict[int, list[DailyPrice]] = {}
        for row in rows:
            grouped.setdefault(row.stock_id, []).append(row)
        return grouped

    async def _try_entry(
        self,
        signal: ReplaySignal,
        signal_date: date,
        run: BacktestRun,
        config: dict[str, Any],
        prices_by_stock: dict[int, list[DailyPrice]],
        cash: Decimal,
        open_positions: dict[int, _OpenPosition],
    ) -> tuple[_OpenPosition, Decimal] | None:
        """Attempt to create an entry for a signal. Returns (position, cash_required) or None."""
        prices = prices_by_stock.get(signal.stock_id, [])
        if not prices:
            return None

        # Find the next trading day after the signal date
        next_prices = [p for p in prices if p.trade_date > signal_date]
        if not next_prices:
            return None

        next_day = next_prices[0]

        # Determine theoretical entry price based on execution mode
        execution_mode = config["execution_mode"]
        if execution_mode == "next_open":
            theoretical_price = Decimal(str(next_day.open))
        elif execution_mode == "next_close":
            theoretical_price = Decimal(str(next_day.close))
        elif execution_mode == "signal_close":
            # signal_close uses the signal date's close (explicitly configured)
            signal_price = next(
                (p for p in prices if p.trade_date == signal_date), None
            )
            if signal_price is None:
                return None
            theoretical_price = Decimal(str(signal_price.close))
        else:
            return None

        if theoretical_price <= 0:
            return None

        # Apply slippage for entry
        slippage = run.slippage_rate
        entry_price = theoretical_price * (Decimal("1") + slippage)

        # Position sizing
        quantity = self._compute_quantity(
            run, config, entry_price, cash, len(open_positions)
        )
        if quantity <= 0:
            return None

        # Entry costs
        gross_entry = quantity * entry_price
        commission = gross_entry * run.commission_rate
        cash_required = gross_entry + commission

        # Check capital
        if cash_required > cash:
            return None

        # Check max positions
        if run.max_positions is not None and len(open_positions) >= run.max_positions:
            return None

        # Build position
        stop_loss = None
        target_price = None
        if config["stop_loss_percent"] is not None:
            stop_loss = entry_price * (
                Decimal("1") - config["stop_loss_percent"] / Decimal("100")
            )
        if config["take_profit_percent"] is not None:
            target_price = entry_price * (
                Decimal("1") + config["take_profit_percent"] / Decimal("100")
            )

        position = _OpenPosition(
            stock_id=signal.stock_id,
            symbol=signal.symbol,
            entry_date=next_day.trade_date,
            entry_price=entry_price,
            quantity=quantity,
            entry_signal=signal.scanner_code,
            stop_loss=stop_loss,
            target_price=target_price,
            entry_commission=commission,
            entry_slippage=slippage,
            commission_rate=run.commission_rate,
        )
        return position, cash_required

    def _compute_quantity(
        self,
        run: BacktestRun,
        config: dict[str, Any],
        entry_price: Decimal,
        cash: Decimal,
        open_count: int,
    ) -> Decimal:
        """Compute position quantity based on position_size_type."""
        size_type = config["position_size_type"]
        size_value = config["position_size_value"]

        if size_type == "fixed_quantity":
            return Decimal(str(size_value))

        if size_type == "fixed_amount":
            if size_value <= 0:
                return Decimal("0")
            return (Decimal(str(size_value)) / entry_price).quantize(
                Decimal("1"), rounding=ROUND_FLOOR
            )

        if size_type == "percent_capital":
            if size_value <= 0 or size_value > 100:
                return Decimal("0")
            allocation = cash * (Decimal(str(size_value)) / Decimal("100"))
            return (allocation / entry_price).quantize(Decimal("1"), rounding=ROUND_FLOOR)

        if size_type == "equal_weight":
            if run.max_positions is None or run.max_positions <= 0:
                return Decimal("0")
            allocation = cash / Decimal(str(run.max_positions))
            return (allocation / entry_price).quantize(Decimal("1"), rounding=ROUND_FLOOR)

        return Decimal("0")

    def _check_exit(
        self,
        pos: _OpenPosition,
        today_price: DailyPrice,
        trade_date: date,
        config: dict[str, Any],
        opposite_signals_by_date: dict[date, set[int]],
    ) -> tuple[Decimal, str, date] | None:
        """Check if an open position should exit today.

        Returns (exit_price, signal, date) or None.
        """
        high = Decimal(str(today_price.high)) if today_price.high is not None else None
        low = Decimal(str(today_price.low)) if today_price.low is not None else None
        close = Decimal(str(today_price.close)) if today_price.close is not None else None

        # Increment bars held
        pos.bars_held += 1

        # Stop loss / take profit (conservative: stop executes first)
        stop_hit = False
        target_hit = False
        if pos.stop_loss is not None and low is not None and low <= pos.stop_loss:
            stop_hit = True
        if pos.target_price is not None and high is not None and high >= pos.target_price:
            target_hit = True

        if stop_hit:
            exit_price = pos.stop_loss
            return exit_price, "stop_loss", trade_date
        if target_hit:
            exit_price = pos.target_price
            return exit_price, "take_profit", trade_date

        # Max holding days (trading sessions)
        if (
            config["max_holding_days"] is not None
            and pos.bars_held >= config["max_holding_days"]
        ):
            return close, "max_holding_days", trade_date

        # Opposite signal
        if config["opposite_scanner_code"]:
            opposite_stocks = opposite_signals_by_date.get(trade_date, set())
            if pos.stock_id in opposite_stocks:
                return close, "opposite_signal", trade_date

        return None

    @staticmethod
    def _cash_returned_on_close(trade: BacktestTrade, pos: _OpenPosition) -> Decimal:
        """Cash credited to the account when a position closes.

        The entry outlay (``entry_value + entry_commission``) was already
        deducted from cash on entry, so crediting ``trade.net_pnl`` here would
        deduct the entry commission a second time. The correct credit is the
        exit proceeds net of exit costs, which equals
        ``net_pnl + entry_value + entry_commission``. This preserves the
        settlement identity ``final_capital = initial_capital + gross_pnl - costs``.
        """
        return trade.net_pnl + pos.entry_price * pos.quantity + pos.entry_commission

    def _close_position(
        self,
        pos: _OpenPosition,
        exit_date: date,
        exit_price: Decimal,
        exit_signal: str,
        run_id: int,
    ) -> BacktestTrade:
        """Close a position and create a BacktestTrade record."""
        # Apply exit slippage (longs sell at a discount to the reference price).
        executed_exit = exit_price * (Decimal("1") - pos.entry_slippage)

        entry_value = pos.entry_price * pos.quantity
        exit_value = executed_exit * pos.quantity
        gross_pnl = exit_value - entry_value
        exit_commission = exit_value * pos.commission_rate
        costs = pos.entry_commission + exit_commission
        net_pnl = gross_pnl - costs
        return_percent = (
            net_pnl / entry_value * Decimal("100") if entry_value > 0 else Decimal("0")
        )

        return BacktestTrade(
            backtest_run_id=run_id,
            stock_id=pos.stock_id,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=exit_date,
            exit_price=executed_exit,
            quantity=pos.quantity,
            position_side="long",
            entry_signal=pos.entry_signal,
            exit_signal=exit_signal,
            stop_loss=pos.stop_loss,
            target_price=pos.target_price,
            gross_pnl=gross_pnl,
            costs=costs,
            net_pnl=net_pnl,
            return_percent=return_percent,
            bars_held=pos.bars_held,
            status="closed",
        )

    def _mark_to_market(
        self,
        open_positions: dict[int, _OpenPosition],
        prices_by_stock: dict[int, list[DailyPrice]],
        trade_date: date,
    ) -> Decimal:
        """Compute the current market value of open positions."""
        total = Decimal("0")
        for stock_id, pos in open_positions.items():
            prices = prices_by_stock.get(stock_id, [])
            today_price = next((p for p in prices if p.trade_date == trade_date), None)
            if today_price is not None:
                total += Decimal(str(today_price.close)) * pos.quantity
            else:
                total += pos.entry_price * pos.quantity
        return total