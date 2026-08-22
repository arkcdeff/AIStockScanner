"""Price Adjustment Service — deterministic, Decimal-safe split/bonus adjustment.

Design principles:
  - NEVER modify DailyPrice rows.  Raw prices are immutable source data.
  - Adjustments are computed on demand (on-the-fly), returned as AdjustedPrice dataclasses.
  - Only split and bonus are implemented in 19.1C; other types are documented as deferred.
  - Adjustment factors use Decimal arithmetic throughout to avoid float imprecision.
  - Point-in-time: only corporate actions with available_at <= as_of_date are applied.
  - No look-ahead: do not apply future corporate actions to historical prices.

Formulas (standard backward-adjustment convention):
  For prices BEFORE ex_date, divide by the cumulative adjustment factor:

  Split (ratio_numerator : ratio_denominator, e.g. 1:2 → split into 2):
      factor = denominator / numerator   (e.g. 2/1 = 2.0 — price halved)
      adjusted_price = raw_price / factor

  Bonus (ratio_numerator : ratio_denominator, e.g. 1:2 → 1 free per 2 held):
      new_total = denominator + numerator  (e.g. 2+1 = 3 shares per original 2)
      factor = new_total / denominator     (e.g. 3/2 = 1.5 — price reduced by 1.5×)
      adjusted_price = raw_price / factor

  Cumulative: when multiple actions exist, factors are compounded in chronological
  order from oldest to most recent ex_date.

Deferred (see exchange_constants.DEFERRED_ADJUSTMENT_TYPES):
  - Dividend: requires total-return vs. price-return convention decision.
  - Rights: requires subscription price.
  - Merger/demerger: requires exchange ratio of both entities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.data.exchange_constants import ADJUSTABLE_ACTION_TYPES, CA_BONUS, CA_SPLIT
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice

if TYPE_CHECKING:
    pass

# Decimal precision for adjustment factor computation
_FACTOR_PRECISION = Decimal("0.000001")
_PRICE_PRECISION = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class AdjustedPrice:
    """A single price row with backward-adjusted values."""

    stock_id: int
    trade_date: date
    # Raw (unadjusted) prices from DailyPrice
    raw_open: Decimal
    raw_high: Decimal
    raw_low: Decimal
    raw_close: Decimal
    # Adjusted prices (raw / cumulative_factor)
    adj_open: Decimal
    adj_high: Decimal
    adj_low: Decimal
    adj_close: Decimal
    # Cumulative factor applied — 1 means no adjustment
    cumulative_factor: Decimal
    # Number of corporate actions applied
    actions_applied: int


@dataclass(frozen=True, slots=True)
class AdjustmentFactor:
    """A single corporate action's price adjustment factor."""

    action_id: int
    action_type: str
    ex_date: date
    factor: Decimal  # prices before ex_date are divided by this


class PriceAdjustmentService:
    """Compute backward-adjusted price series from raw DailyPrice + CorporateAction data.

    This service is PURE — it does not interact with the database directly.
    Callers load the relevant DailyPrice and CorporateAction rows and pass them in.
    This makes the service fully testable without any database dependency.
    """

    @staticmethod
    def compute_factor(action: CorporateAction) -> AdjustmentFactor | None:
        """Compute the backward-adjustment factor for a single corporate action.

        Returns None for deferred/unsupported action types.

        Parameters
        ----------
        action:
            A CorporateAction with action_type in ADJUSTABLE_ACTION_TYPES.

        Returns
        -------
        AdjustmentFactor or None
        """
        if action.action_type not in ADJUSTABLE_ACTION_TYPES:
            return None

        num = action.ratio_numerator
        den = action.ratio_denominator
        if num is None or den is None:
            return None
        if den == Decimal(0) or num == Decimal(0):
            return None

        if action.action_type == CA_SPLIT:
            # Split n:m (n shares become m shares)
            # Factor = m/n (price is divided by m/n → historical prices go down)
            # e.g. 1:2 split: 1 share → 2 shares, factor = 2/1 = 2
            factor = (den / num).quantize(_FACTOR_PRECISION, rounding=ROUND_HALF_UP)

        elif action.action_type == CA_BONUS:
            # Bonus n:m (receive n free shares per m held → total m+n per m)
            # Factor = (m + n) / m
            # e.g. bonus 1:2 (1 free per 2 held): (2+1)/2 = 1.5
            factor = ((den + num) / den).quantize(_FACTOR_PRECISION, rounding=ROUND_HALF_UP)

        else:
            return None

        if factor <= Decimal(0):
            return None

        return AdjustmentFactor(
            action_id=action.id,
            action_type=action.action_type,
            ex_date=action.ex_date,
            factor=factor,
        )

    @staticmethod
    def compute_cumulative_factors(
        actions: list[CorporateAction],
        as_of_date: date | None = None,
    ) -> list[AdjustmentFactor]:
        """Compute the list of valid adjustment factors in ex_date order.

        Parameters
        ----------
        actions:
            CorporateAction rows for a stock.  Must already be pre-filtered by
            available_at <= as_of_date by the caller (repository).
        as_of_date:
            If provided, further confirms the point-in-time gate (belt+suspenders).
            Actions with available_at > as_of_date are skipped.

        Returns
        -------
        Factors sorted by ex_date ascending (oldest first).
        """
        service = PriceAdjustmentService()
        factors: list[AdjustmentFactor] = []
        for action in sorted(actions, key=lambda a: a.ex_date):
            if as_of_date is not None and action.available_at > as_of_date:
                continue  # point-in-time guard (belt+suspenders)
            factor = service.compute_factor(action)
            if factor is not None:
                factors.append(factor)
        return factors

    @staticmethod
    def adjust_price_series(
        price_rows: list[DailyPrice],
        factors: list[AdjustmentFactor],
    ) -> list[AdjustedPrice]:
        """Apply backward-adjustment factors to a list of DailyPrice rows.

        The cumulative factor for a price on date D is the product of all
        factors whose ex_date > D (i.e. actions that happened after D and
        therefore require us to adjust D's prices downward to be comparable
        to today's prices).

        Parameters
        ----------
        price_rows:
            Raw DailyPrice rows (unmodified).
        factors:
            AdjustmentFactor list from compute_cumulative_factors(), sorted
            by ex_date ascending.

        Returns
        -------
        List of AdjustedPrice, same order as price_rows.
        """
        result: list[AdjustedPrice] = []
        for row in price_rows:
            # Cumulative factor = product of all factor.factor where factor.ex_date > row.trade_date
            cum_factor = Decimal("1")
            n_applied = 0
            for af in factors:
                if af.ex_date > row.trade_date:
                    cum_factor = (cum_factor * af.factor).quantize(
                        _FACTOR_PRECISION, rounding=ROUND_HALF_UP
                    )
                    n_applied += 1

            def _adj(raw: Decimal, cf: Decimal = cum_factor) -> Decimal:
                if cf == Decimal("1"):
                    return raw
                return (raw / cf).quantize(_PRICE_PRECISION, rounding=ROUND_HALF_UP)

            result.append(
                AdjustedPrice(
                    stock_id=row.stock_id,
                    trade_date=row.trade_date,
                    raw_open=row.open,
                    raw_high=row.high,
                    raw_low=row.low,
                    raw_close=row.close,
                    adj_open=_adj(row.open),
                    adj_high=_adj(row.high),
                    adj_low=_adj(row.low),
                    adj_close=_adj(row.close),
                    cumulative_factor=cum_factor,
                    actions_applied=n_applied,
                )
            )
        return result
