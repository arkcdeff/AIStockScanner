from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.market_intelligence.breadth_universe import (
    BREADTH_UNIVERSES,
    DEFAULT_BREADTH_UNIVERSE,
    universe_eligible_stock,
)
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.services.market_breadth import (
    DIVERGENCE_SIGNALS,
    MarketBreadth,
    breadth_state,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _stock(stock_id: int, symbol: str, **overrides) -> Stock:
    fields = {
        "id": stock_id,
        "symbol": symbol,
        "company_name": symbol,
        "exchange": "NSE",
        "series": "EQ",
        "is_active": True,
    }
    fields.update(overrides)
    return Stock(**fields)


def _price(stock_id: int, trade_date: date, close: Decimal) -> DailyPrice:
    return DailyPrice(
        stock_id=stock_id,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100000,
    )


def _closes_for(symbol: str, sessions: int) -> list[Decimal]:
    if symbol == "UP":
        return [Decimal("100") + Decimal(offset) for offset in range(sessions)]
    if symbol == "DOWN":
        return [Decimal("400") - Decimal(offset) for offset in range(sessions)]
    if symbol == "FLAT":
        return [Decimal("100")] * sessions
    if symbol == "TURN":
        return [
            Decimal(300 - offset) if offset < 20 else Decimal(150 + (offset - 19) * 35)
            for offset in range(sessions)
        ]
    if symbol == "CRASH":
        clauses = [Decimal("100") + Decimal(i) for i in range(21)]
        clauses.extend(Decimal("120") - Decimal((i - 21) * 20) for i in range(21, sessions))
        return clauses
    raise ValueError(symbol)


def _panel(
    symbols: tuple[str, ...],
    *,
    sessions: int = 26,
    start: date = date(2025, 1, 6),
) -> tuple[list[Stock], list[DailyPrice], date]:
    """Build a uniform multi-stock panel ending on the last session date."""
    dates = [start + timedelta(days=offset) for offset in range(sessions)]
    market_date = dates[-1]
    stocks = [
        _stock(index + 1, f"S{index + 1}{symbol[0]}") for index, symbol in enumerate(symbols)
    ]
    rows = []
    for index, symbol in enumerate(symbols):
        closes = _closes_for(symbol, sessions)
        for trade_date, close in zip(dates, closes, strict=True):
            rows.append(_price(index + 1, trade_date, close))
    return stocks, rows, market_date


def _index_rows(market_date: date, *, sessions: int = 26, rising: bool = True):
    start = market_date - timedelta(days=sessions - 1)
    base = Decimal("3000")
    return [
        SimpleNamespace(
            code="NIFTY_50",
            trade_date=start + timedelta(days=offset),
            close=base + Decimal(offset) if rising else base - Decimal(offset),
        )
        for offset in range(sessions)
    ]


class MemoryBreadthRepository:
    def __init__(self, stocks: list[Stock], rows: list[DailyPrice]) -> None:
        self.stocks = stocks
        self.rows = rows
        self.price_bounds: tuple[date, date] | None = None

    async def list_universe_stocks(self, universe):
        return [s for s in self.stocks if universe_eligible_stock(s, universe)]

    async def load_daily_prices(self, stock_ids, start_date, end_date):
        self.price_bounds = (start_date, end_date)
        id_set = set(stock_ids)
        return [
            r
            for r in self.rows
            if r.stock_id in id_set and start_date <= r.trade_date <= end_date
        ]


class MemoryIndexRepository:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def get_date_range_by_index_code(self, index_code, start_date, end_date):
        return [
            r
            for r in self.rows
            if r.code == index_code and start_date <= r.trade_date <= end_date
        ]


def _service(stocks, rows, index_rows) -> MarketBreadth:
    return MarketBreadth(
        SimpleNamespace(),  # type: ignore[arg-type]
        breadth_repo=MemoryBreadthRepository(stocks, rows),  # type: ignore[arg-type]
        index_price_repo=MemoryIndexRepository(index_rows),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_breadth_state_enumeration_boundaries_are_centralized_and_deterministic():
    assert breadth_state(Decimal("70")) == "STRONG"
    assert breadth_state(Decimal("60")) == "STRONG"
    assert breadth_state(Decimal("59.99")) == "POSITIVE"
    assert breadth_state(Decimal("25")) == "POSITIVE"
    assert breadth_state(Decimal("24.99")) == "NEUTRAL"
    assert breadth_state(Decimal("0")) == "NEUTRAL"
    assert breadth_state(Decimal("-24.99")) == "NEUTRAL"
    assert breadth_state(Decimal("-25")) == "WEAK"
    assert breadth_state(Decimal("-59.99")) == "WEAK"
    assert breadth_state(Decimal("-60")) == "VERY_WEAK"
    assert breadth_state(Decimal("-70")) == "VERY_WEAK"
    assert breadth_state(None) is None


def test_supported_universes_contains_the_canonical_initial_universe():
    codes = {universe.code for universe in BREADTH_UNIVERSES}
    assert codes == {"NSE_ACTIVE_EQ_STOCK_MASTER"}
    assert DEFAULT_BREADTH_UNIVERSE.code in codes


def test_universe_eligibility_excludes_inactive_wrong_exchange_and_non_equity():
    active = _stock(1, "A")
    inactive = _stock(2, "B", is_active=False)
    bse = _stock(3, "C", exchange="BSE")
    sme = _stock(4, "D", series="SME")
    bond = _stock(5, "E", series="GS")
    assert universe_eligible_stock(active, DEFAULT_BREADTH_UNIVERSE)
    assert not universe_eligible_stock(inactive, DEFAULT_BREADTH_UNIVERSE)
    assert not universe_eligible_stock(bse, DEFAULT_BREADTH_UNIVERSE)
    assert not universe_eligible_stock(sme, DEFAULT_BREADTH_UNIVERSE)
    assert not universe_eligible_stock(bond, DEFAULT_BREADTH_UNIVERSE)


# ---------------------------------------------------------------------------
# Integration against the MarketBreadth service
# ---------------------------------------------------------------------------


def test_all_advancing_produces_full_bullish_breadth():
    stocks, rows, market_date = _panel(("UP", "UP", "UP"), sessions=260)
    index_rows = _index_rows(market_date, rising=True)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["advancing"] == 3
    assert result["declining"] == 0
    assert result["unchanged"] == 0
    assert result["advancing_percent"] == 100.0
    assert result["advance_decline_ratio"] is None
    assert result["advance_decline_ratio_state"] == "infinite_positive"
    assert result["net_advances"] == 3
    assert result["advance_decline_line"] == 777
    assert result["new_highs"]["new_20d_highs"] == 3
    assert result["new_highs"]["new_52w_highs"] == 3
    assert result["new_highs"]["high_low_percent"] == 100.0
    assert result["moving_average_participation"] == {
        "sma20_participation": 100.0,
        "sma50_participation": 100.0,
        "sma200_participation": 100.0,
    }
    assert result["participation"] == 100.0
    assert result["breadth_state"] == "STRONG"
    assert result["score"] == 88.75
    assert result["direction"] == "strong_bullish"
    assert result["completeness"] == 100.0


def test_all_declining_produces_full_bearish_breadth():
    stocks, rows, market_date = _panel(("DOWN", "DOWN", "DOWN"), sessions=260)
    index_rows = _index_rows(market_date, rising=False)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["advancing"] == 0
    assert result["declining"] == 3
    assert result["advancing_percent"] == 0.0
    assert result["advance_decline_ratio"] == 0.0
    assert result["advance_decline_ratio_state"] == "finite"
    assert result["advance_decline_line"] == -777
    assert result["new_lows"]["new_20d_lows"] == 3
    assert result["new_lows"]["new_52w_lows"] == 3
    assert result["new_highs"]["high_low_percent"] == 0.0
    assert result["moving_average_participation"] == {
        "sma20_participation": 0.0,
        "sma50_participation": 0.0,
        "sma200_participation": 0.0,
    }
    assert result["participation"] == 0.0
    assert result["breadth_state"] == "VERY_WEAK"
    assert result["score"] == -88.75
    assert result["direction"] == "strong_bearish"


def test_mixed_market_counts_advancing_declining_and_unchanged_separately():
    stocks, rows, market_date = _panel(("UP", "DOWN", "FLAT"))
    index_rows = _index_rows(market_date, rising=False)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["advancing"] == 1
    assert result["declining"] == 1
    assert result["unchanged"] == 1
    assert result["advancing_percent"] == 33.33
    assert result["advance_decline_ratio"] == 1.0
    assert result["advance_decline_ratio_state"] == "finite"


def test_recently_listed_stock_is_unclassified_and_lowers_completeness():
    stocks, rows, market_date = _panel(("UP", "UP"))
    late_listing = _stock(3, "S3NEW")
    rows.append(_price(3, market_date, Decimal("50")))
    stocks.append(late_listing)
    index_rows = _index_rows(market_date, rising=True)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["universe"]["expected_size"] == 3
    assert result["unclassified"] == 1
    assert result["eligible"] == 3
    assert result["advancing"] == 2
    assert result["advancing_percent"] == 100.0
    assert result["completeness"] < 100.0


def test_empty_database_preserves_missing_none_values_and_zero_confidence():
    service = _service([], [], [])
    result = await_run(service, date(2025, 1, 31))

    assert result["score"] is None
    assert result["direction"] is None
    assert result["advancing"] is None
    assert result["advance_decline_ratio_state"] == "missing"
    assert result["divergence"]["signal"] is None
    assert result["completeness"] == 0.0
    assert result["confidence"] == 0.0
    assert result["data_as_of"] is None
    assert result["freshness"] == "missing"


def test_positive_divergence_when_index_falls_but_breadth_improves():
    stocks, rows, market_date = _panel(("TURN", "TURN", "TURN"))
    index_rows = _index_rows(market_date, rising=False)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["momentum"] is not None
    assert result["momentum"] > 50
    assert result["divergence"]["benchmark_return_percent"] < 0
    assert result["divergence"]["signal"] == "positive_divergence"
    assert result["divergence"]["signal"] in DIVERGENCE_SIGNALS


def test_negative_divergence_when_index_rises_but_breadth_deteriorates():
    stocks, rows, market_date = _panel(("CRASH", "CRASH", "CRASH"))
    index_rows = _index_rows(market_date, rising=True)
    result = await_run(_service(stocks, rows, index_rows), market_date)

    assert result["momentum"] is not None
    assert result["momentum"] < -50
    assert result["divergence"]["benchmark_return_percent"] > 0
    assert result["divergence"]["signal"] == "negative_divergence"


def test_no_look_ahead_future_invariance():
    stocks, rows, market_date = _panel(("UP", "DOWN", "UP"))
    index_rows = _index_rows(market_date, rising=False)

    repo_a = MemoryBreadthRepository(stocks, list(rows))
    result_a = await_run(
        MarketBreadth(
            SimpleNamespace(),  # type: ignore[arg-type]
            breadth_repo=repo_a,  # type: ignore[arg-type]
            index_price_repo=MemoryIndexRepository(index_rows),  # type: ignore[arg-type]
        ),
        market_date,
    )
    assert repo_a.price_bounds is not None
    assert repo_a.price_bounds[1] == market_date

    future_start = market_date + timedelta(days=1)
    rows_with_future = list(rows)
    extra_end = future_start + timedelta(days=4)
    for stock_id in (1, 2, 3):
        trade_date = future_start
        close = Decimal("999")
        while trade_date <= extra_end:
            rows_with_future.append(_price(stock_id, trade_date, close))
            close += Decimal("1")
            trade_date += timedelta(days=1)

    repo_b = MemoryBreadthRepository(stocks, rows_with_future)
    result_b = await_run(
        MarketBreadth(
            SimpleNamespace(),  # type: ignore[arg-type]
            breadth_repo=repo_b,  # type: ignore[arg-type]
            index_price_repo=MemoryIndexRepository(index_rows),  # type: ignore[arg-type]
        ),
        market_date,
    )
    assert result_a == result_b


def await_run(service, market_date):
    import asyncio

    return asyncio.run(service.evaluate(market_date))