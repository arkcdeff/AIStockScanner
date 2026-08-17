from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.sql.elements import ColumnElement

from app.models.stock import Stock


@dataclass(frozen=True, slots=True)
class BreadthUniverse:
    """Centralized definition of the canonical market-breadth universe.

    Membership methodology:
      * The repository does not store point-in-time NSE index constituent
        membership. The canonical initial universe is therefore defined as the
        active NSE equity (``EQ`` series) stocks present in the ``stocks``
        table.
      * Because constituency is *current* membership, historical breadth uses
        today's members. This limitation is surfaced in the output as
        ``membership_limitation`` and lowers confidence appropriately.
    """

    code: str
    name: str
    exchange: str
    series: tuple[str, ...]
    category: str = "equity"
    active_only: bool = True
    methodology: str = (
        "Active NSE EQ-series securities in the stock master, excluding "
        "identifiable ETF/fund records; current membership is reused for "
        "historical dates because point-in-time constituent membership and a "
        "canonical instrument-type field are not stored."
    )

    def excludes_etfs_and_non_equity(self) -> bool:
        return self.category == "equity" and "EQ" in self.series


DEFAULT_BREADTH_UNIVERSE = BreadthUniverse(
    code="NSE_ACTIVE_EQ_STOCK_MASTER",
    name="Active NSE EQ Stock-Master Equities",
    exchange="NSE",
    series=("EQ",),
    category="equity",
    active_only=True,
)

#: Canonical registry; future universes (e.g. exact NIFTY_500 baskets) can be
#: added here once point-in-time membership is available.
BREADTH_UNIVERSES: tuple[BreadthUniverse, ...] = (DEFAULT_BREADTH_UNIVERSE,)

_FUND_NAME_MARKERS = (
    " ETF",
    "ETF ",
    "EXCHANGE TRADED FUND",
    "MUTUAL FUND",
)
_FUND_SYMBOL_SUFFIXES = ("ETF", "BEES")


def universe_eligible_stock(stock: Stock, universe: BreadthUniverse) -> bool:
    """Return whether a stock belongs to the breadth universe.

    Active-only filtering is applied, and the exchange/series must match the
    universe definition so ETFs, indices, bonds and inactive securities are
    excluded from the breadth denominator.
    """
    if universe.active_only and not stock.is_active:
        return False
    if stock.exchange != universe.exchange:
        return False
    if universe.series and stock.series not in universe.series:
        return False
    symbol = stock.symbol.upper().strip()
    company_name = stock.company_name.upper().strip()
    if symbol.endswith(_FUND_SYMBOL_SUFFIXES):
        return False
    if any(marker in company_name for marker in _FUND_NAME_MARKERS):
        return False
    return True


def universe_sql_filters(universe: BreadthUniverse) -> tuple[ColumnElement[bool], ...]:
    """Return coarse SQL filters; exact fund exclusions remain centralized above."""
    filters: list[ColumnElement[bool]] = [Stock.exchange == universe.exchange]
    if universe.active_only:
        filters.append(Stock.is_active.is_(True))
    if universe.series:
        filters.append(Stock.series.in_(universe.series))
    return tuple(filters)