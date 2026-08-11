"""Backtest performance metric calculator.

Computes standard performance metrics from closed trades and the equity curve:
- returns (total, annualized, CAGR)
- win rate, profit factor, expectancy
- drawdown, Sharpe, Sortino
- benchmark comparison, alpha

Conventions:
- Only CLOSED BacktestTrade rows are used; open trades are ignored.
- Undefined metrics are persisted as NULL (never NaN, Infinity, or -Infinity).
- CAGR and annualized_return_percent use the actual calendar duration between
  run.start_date and run.end_date, annualized with 365.25 days/year. Both
  columns share the same formula so results never diverge.
- The risk-free rate is 0 (config.py defines no backtest risk-free setting).
- Sharpe and Sortino derive periodic returns from ordered BacktestEquityPoint
  values and annualize with sqrt(252) sessions/year.
- average_holding_days prefers BacktestTrade.bars_held and falls back to
  calendar days ((exit_date - entry_date).days) when bars_held is NULL.
- Benchmark return is computed only from existing DailyPrice rows inside the
  run's date range; no external data is ever fetched.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest import BacktestEquityPoint, BacktestMetric, BacktestRun, BacktestTrade
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.repositories.backtest import BacktestMetricRepository, BacktestRunRepository

# Risk-free rate (annualized) for Sharpe/Sortino. config.py defines no
# backtest risk-free setting, so the default is 0.
RISK_FREE_RATE = Decimal("0")

# Calendar days per year for CAGR / annualized return.
CALENDAR_DAYS_PER_YEAR = Decimal("365.25")

# Trading sessions per year for Sharpe/Sortino annualization.
SESSIONS_PER_YEAR = Decimal("252")


class BacktestMetricError(ValueError):
    """Raised when backtest metrics cannot be computed for a run."""


def sanitized_metric_failure(run_id: int, exc: Exception) -> BacktestMetricError:
    """Build a sanitized metric failure error that never leaks internals.

    Only the exception type name is surfaced; exception payload, SQL, paths,
    and credentials are never included.
    """
    return BacktestMetricError(
        f"Metrics computation failed for run {run_id}: {type(exc).__name__}"
    )


@dataclass(frozen=True, slots=True)
class MetricSummary:
    """In-memory summary of computed metrics."""

    run_id: int
    total_return_percent: Decimal | None
    annualized_return_percent: Decimal | None
    cagr: Decimal | None
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None
    max_drawdown_percent: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    average_holding_days: Decimal | None
    benchmark_return_percent: Decimal | None
    alpha: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_return_percent": _fmt(self.total_return_percent),
            "annualized_return_percent": _fmt(self.annualized_return_percent),
            "cagr": _fmt(self.cagr),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": _fmt(self.win_rate),
            "gross_profit": str(self.gross_profit),
            "gross_loss": str(self.gross_loss),
            "profit_factor": _fmt(self.profit_factor),
            "average_win": _fmt(self.average_win),
            "average_loss": _fmt(self.average_loss),
            "expectancy": _fmt(self.expectancy),
            "max_drawdown_percent": str(self.max_drawdown_percent),
            "sharpe_ratio": _fmt(self.sharpe_ratio),
            "sortino_ratio": _fmt(self.sortino_ratio),
            "average_holding_days": _fmt(self.average_holding_days),
            "benchmark_return_percent": _fmt(self.benchmark_return_percent),
            "alpha": _fmt(self.alpha),
        }


def _fmt(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class BacktestMetricCalculator:
    """Compute performance metrics from backtest trades and equity curve."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = BacktestRunRepository(session)
        self.metrics = BacktestMetricRepository(session)

    # ------------------------------------------------------------------ public API

    async def compute_metrics(self, run_id: int) -> MetricSummary:
        """Compute and persist metrics for a completed backtest run."""
        run = await self.runs.get_with_details(run_id)
        if run is None:
            raise BacktestMetricError(f"Backtest run {run_id} not found")
        if run.status != "completed":
            raise BacktestMetricError(f"Backtest run {run_id} is not completed")

        closed_trades = [t for t in run.trades if t.status == "closed"]
        ordered_points = sorted(run.equity_points, key=lambda p: p.trade_date)
        drawdowns = self._compute_drawdowns(ordered_points)

        benchmark_return = await self._compute_benchmark_return(run)
        summary = self._compute_metrics(
            run, closed_trades, ordered_points, drawdowns, benchmark_return
        )

        self._persist_drawdowns(ordered_points, drawdowns)

        metric = BacktestMetric(
            backtest_run_id=run_id,
            total_return_percent=summary.total_return_percent,
            annualized_return_percent=summary.annualized_return_percent,
            cagr=summary.cagr,
            total_trades=summary.total_trades,
            winning_trades=summary.winning_trades,
            losing_trades=summary.losing_trades,
            win_rate=summary.win_rate,
            gross_profit=summary.gross_profit,
            gross_loss=summary.gross_loss,
            profit_factor=summary.profit_factor,
            average_win=summary.average_win,
            average_loss=summary.average_loss,
            expectancy=summary.expectancy,
            max_drawdown_percent=summary.max_drawdown_percent,
            sharpe_ratio=summary.sharpe_ratio,
            sortino_ratio=summary.sortino_ratio,
            average_holding_days=summary.average_holding_days,
            benchmark_return_percent=summary.benchmark_return_percent,
            alpha=summary.alpha,
        )
        await self.metrics.upsert(run_id, metric)
        await self.session.commit()
        return summary

    # ------------------------------------------------------------------ core computation

    def _compute_metrics(
        self,
        run: BacktestRun,
        trades: list[BacktestTrade],
        equity_points: list[BacktestEquityPoint],
        drawdowns: list[Decimal],
        benchmark_return: Decimal | None,
    ) -> MetricSummary:
        """Core metric computation."""
        start_value = run.initial_capital
        end_value = run.final_capital
        if end_value is None and equity_points:
            end_value = equity_points[-1].portfolio_value

        days = (run.end_date - run.start_date).days

        # Total return
        total_return_percent = None
        if start_value > 0 and end_value is not None:
            total_return_percent = (end_value - start_value) / start_value * Decimal("100")

        # CAGR / annualized return (actual calendar duration, 365.25 days/year)
        annualized_return_percent = None
        cagr = None
        if start_value > 0 and end_value is not None and end_value > 0 and days > 0:
            annualized = (end_value / start_value) ** (
                CALENDAR_DAYS_PER_YEAR / Decimal(str(days))
            ) - Decimal("1")
            annualized_return_percent = annualized * Decimal("100")
            cagr = annualized_return_percent

        # Trade statistics (closed trades only)
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if (t.net_pnl or Decimal("0")) > 0)
        losing_trades = sum(1 for t in trades if (t.net_pnl or Decimal("0")) < 0)

        win_rate = (
            Decimal(str(winning_trades)) / Decimal(str(total_trades)) * Decimal("100")
            if total_trades > 0
            else None
        )

        gross_profit = sum(
            (t.gross_pnl or Decimal("0")) for t in trades if (t.gross_pnl or Decimal("0")) > 0
        )
        gross_loss = abs(
            sum(
                (t.gross_pnl or Decimal("0"))
                for t in trades
                if (t.gross_pnl or Decimal("0")) < 0
            )
        )

        profit_factor = (
            gross_profit / gross_loss if total_trades > 0 and gross_loss > 0 else None
        )

        average_win = (
            gross_profit / Decimal(str(winning_trades)) if winning_trades > 0 else None
        )
        average_loss = (
            gross_loss / Decimal(str(losing_trades)) if losing_trades > 0 else None
        )

        expectancy = None
        if total_trades > 0:
            win_probability = Decimal(str(winning_trades)) / Decimal(str(total_trades))
            loss_probability = Decimal(str(losing_trades)) / Decimal(str(total_trades))
            win_contribution = average_win if average_win is not None else Decimal("0")
            loss_contribution = average_loss if average_loss is not None else Decimal("0")
            expectancy = win_probability * win_contribution - loss_probability * loss_contribution

        # Drawdown
        max_drawdown_percent = max(drawdowns) if drawdowns else Decimal("0")

        # Risk-adjusted returns
        sharpe, sortino = self._compute_risk_adjusted(equity_points)

        # Holding period
        holding_days: list[Decimal] = []
        for trade in trades:
            if trade.bars_held is not None:
                holding_days.append(Decimal(str(trade.bars_held)))
            elif trade.exit_date is not None and trade.entry_date is not None:
                holding_days.append(Decimal(str((trade.exit_date - trade.entry_date).days)))
        average_holding_days = (
            sum(holding_days) / Decimal(str(len(holding_days))) if holding_days else None
        )

        # Benchmark comparison and alpha
        alpha = None
        if benchmark_return is not None and total_return_percent is not None:
            alpha = total_return_percent - benchmark_return

        return MetricSummary(
            run_id=run.id,
            total_return_percent=total_return_percent,
            annualized_return_percent=annualized_return_percent,
            cagr=cagr,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            profit_factor=profit_factor,
            average_win=average_win,
            average_loss=average_loss,
            expectancy=expectancy,
            max_drawdown_percent=max_drawdown_percent,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            average_holding_days=average_holding_days,
            benchmark_return_percent=benchmark_return,
            alpha=alpha,
        )

    # ------------------------------------------------------------------ helpers

    def _compute_drawdowns(
        self, equity_points: list[BacktestEquityPoint]
    ) -> list[Decimal]:
        """Compute running-peak drawdown percent for every equity point."""
        drawdowns: list[Decimal] = []
        peak: Decimal | None = None
        for point in equity_points:
            value = point.portfolio_value
            if peak is None or value > peak:
                peak = value
            drawdowns.append(
                (peak - value) / peak * Decimal("100") if peak > 0 else Decimal("0")
            )
        return drawdowns

    def _persist_drawdowns(
        self,
        equity_points: list[BacktestEquityPoint],
        drawdowns: list[Decimal],
    ) -> None:
        """Persist the running-peak drawdown percent on each equity point."""
        for point, drawdown in zip(equity_points, drawdowns, strict=True):
            point.drawdown_percent = drawdown

    def _compute_risk_adjusted(
        self, equity_points: list[BacktestEquityPoint]
    ) -> tuple[Decimal | None, Decimal | None]:
        """Compute Sharpe and Sortino ratios from ordered equity points.

        Periodic returns come from consecutive portfolio values. The risk-free
        rate is 0, and both ratios are annualized with sqrt(252). Either ratio
        is None when there is insufficient data, zero volatility, or (for
        Sortino) no downside deviation.
        """
        if len(equity_points) < 2:
            return None, None

        returns: list[Decimal] = []
        for i in range(1, len(equity_points)):
            prev = equity_points[i - 1].portfolio_value
            curr = equity_points[i].portfolio_value
            if prev > 0:
                returns.append((curr - prev) / prev)

        if len(returns) < 2:
            return None, None

        mean_return = sum(returns) / Decimal(str(len(returns)))
        variance = sum((r - mean_return) ** 2 for r in returns) / Decimal(
            str(len(returns))
        )
        volatility = variance.sqrt()

        sharpe = None
        if volatility > 0:
            sharpe = (mean_return / volatility) * Decimal(str(SESSIONS_PER_YEAR)).sqrt()

        downside_returns = [r for r in returns if r < 0]
        sortino = None
        if downside_returns:
            downside_variance = sum(r ** 2 for r in downside_returns) / Decimal(
                str(len(downside_returns))
            )
            downside_deviation = downside_variance.sqrt()
            if downside_deviation > 0:
                sortino = (mean_return / downside_deviation) * Decimal(
                    str(SESSIONS_PER_YEAR)
                ).sqrt()

        return sharpe, sortino

    async def _compute_benchmark_return(self, run: BacktestRun) -> Decimal | None:
        """Compute benchmark return for the same period.

        Uses the first available close on/after run.start_date and the last
        available close on/before run.end_date from existing DailyPrice rows.
        No external data is fetched and no dates outside the run range are used.
        Returns None when the stock or prices are missing, or the first close
        is invalid (None or <= 0).
        """
        if not run.benchmark_symbol:
            return None

        stock = await self.session.scalar(
            select(Stock).where(Stock.symbol == run.benchmark_symbol)
        )
        if stock is None:
            return None

        rows = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id == stock.id)
                .where(DailyPrice.trade_date >= run.start_date)
                .where(DailyPrice.trade_date <= run.end_date)
                .order_by(DailyPrice.trade_date)
            )
        ).all()
        if not rows:
            return None

        first_close = rows[0].close
        last_close = rows[-1].close
        if first_close is None or last_close is None or first_close <= 0:
            return None

        return (last_close - first_close) / first_close * Decimal("100")
