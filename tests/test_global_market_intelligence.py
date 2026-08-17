from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.index_catalog import GLOBAL_INTELLIGENCE_INDEX_CODES
from app.market_intelligence.session_dates import session_cutoff_date
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.global_market_intelligence import (
    GlobalMarketIntelligence,
    classify_direction,
)


def _price(
    code: str,
    trade_date: date,
    close: Decimal,
    *,
    freshness: str = "recent",
) -> MarketIndexPrice:
    previous = close - Decimal("1")
    market_index = MarketIndex(code=code, name=code, country="TEST")
    row = MarketIndexPrice(
        market_index_id=1,
        trade_date=trade_date,
        open=previous,
        high=close + Decimal("1"),
        low=previous - Decimal("1"),
        close=close,
        previous_close=previous,
        source_name="fixture_provider",
        source_reference=f"mock://{code}/{trade_date}",
        freshness_status=freshness,
        market_index=market_index,
    )
    return row


def _history(code: str, end_date: date, *, rising: bool = True, sessions: int = 260):
    rows = []
    for offset in range(sessions):
        trade_date = end_date - timedelta(days=sessions - 1 - offset)
        close = (
            Decimal("100") + Decimal(offset)
            if rising
            else Decimal("400") - Decimal(offset)
        )
        rows.append(_price(code, trade_date, close))
    return rows


class CountingPriceRepository:
    def __init__(self, rows: list[MarketIndexPrice]) -> None:
        self.rows = rows
        self.calls = 0
        self.bounds: tuple[tuple[str, ...], date, date] | None = None

    async def get_ranges_by_index_codes(
        self, index_codes: tuple[str, ...], start_date: date, end_date: date
    ) -> list[MarketIndexPrice]:
        self.calls += 1
        self.bounds = (index_codes, start_date, end_date)
        return [row for row in self.rows if start_date <= row.trade_date <= end_date]


def _service(repo: CountingPriceRepository) -> GlobalMarketIntelligence:
    return GlobalMarketIntelligence(SimpleNamespace(), price_repo=repo)  # type: ignore[arg-type]


def test_direction_threshold_boundaries_are_centralized_and_deterministic():
    assert classify_direction(100) == "strong_bullish"
    assert classify_direction(60) == "strong_bullish"
    assert classify_direction(59.99) == "bullish"
    assert classify_direction(25) == "bullish"
    assert classify_direction(24.99) == "neutral"
    assert classify_direction(-24.99) == "neutral"
    assert classify_direction(-25) == "bearish"
    assert classify_direction(-59.99) == "bearish"
    assert classify_direction(-60) == "strong_bearish"
    assert classify_direction(None) is None


def test_session_cutoffs_use_native_exchange_close_times():
    evaluation = datetime(2025, 1, 7, 15, 0, tzinfo=UTC)
    assert session_cutoff_date("SP500", evaluation) == date(2025, 1, 6)
    assert session_cutoff_date("NIKKEI_225", evaluation) == date(2025, 1, 7)
    assert session_cutoff_date("HANG_SENG", evaluation) == date(2025, 1, 7)
    assert session_cutoff_date("KOSPI", evaluation) == date(2025, 1, 7)


def test_us_session_cutoff_is_dst_safe_before_and_after_transition():
    before_dst = datetime(2025, 3, 7, 21, 0, tzinfo=UTC)
    after_dst = datetime(2025, 3, 10, 20, 0, tzinfo=UTC)
    assert session_cutoff_date("SP500", before_dst) == date(2025, 3, 7)
    assert session_cutoff_date("SP500", after_dst) == date(2025, 3, 10)


def test_session_cutoff_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        session_cutoff_date("SP500", datetime(2025, 1, 7, 15, 0))


@pytest.mark.asyncio
async def test_empty_database_preserves_missing_none_values():
    repo = CountingPriceRepository([])
    result = await _service(repo).evaluate(datetime(2025, 1, 7, 22, 0, tzinfo=UTC))
    assert repo.calls == 1
    assert result["global_score"] is None
    assert result["global_direction"] is None
    assert result["completeness"] == 0.0
    assert result["confidence"] == 0.0
    assert result["us_context"]["score"] is None
    assert result["asian_context"]["breadth"]["breadth_percent"] is None
    assert all(market["score"] is None for market in result["markets"].values())


@pytest.mark.asyncio
async def test_evaluation_bulk_loads_all_seven_histories_once():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    end = date(2025, 1, 7)
    rows = [row for code in GLOBAL_INTELLIGENCE_INDEX_CODES for row in _history(code, end)]
    repo = CountingPriceRepository(rows)
    result = await _service(repo).evaluate(evaluation)
    assert repo.calls == 1
    assert repo.bounds is not None
    assert repo.bounds[0] == GLOBAL_INTELLIGENCE_INDEX_CODES
    assert result["completeness"] == 100.0
    assert result["us_context"]["available_markets"] == 3
    assert result["asian_context"]["available_markets"] == 4


@pytest.mark.asyncio
async def test_score_is_bounded_and_bullish_history_produces_positive_context():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7), rising=True)
    ]
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    assert 0 < result["global_score"] <= 100
    assert result["global_direction"] in {"bullish", "strong_bullish"}
    assert result["us_context"]["breadth"]["positive"] == 3
    assert result["asian_context"]["breadth"]["positive"] == 4
    assert all(-100 <= market["score"] <= 100 for market in result["markets"].values())


@pytest.mark.asyncio
async def test_bearish_history_produces_negative_context_and_negative_breadth():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7), rising=False)
    ]
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    assert -100 <= result["global_score"] < 0
    assert result["global_direction"] in {"bearish", "strong_bearish"}
    assert result["us_context"]["breadth"]["negative"] == 3
    assert result["asian_context"]["breadth"]["negative"] == 4


@pytest.mark.asyncio
async def test_missing_market_renormalizes_weights_and_lowers_completeness_confidence():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    full_rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7))
    ]
    partial_rows = [row for row in full_rows if row.market_index.code != "KOSPI"]
    full = await _service(CountingPriceRepository(full_rows)).evaluate(evaluation)
    partial = await _service(CountingPriceRepository(partial_rows)).evaluate(evaluation)
    asian_weights = partial["asian_context"]["normalized_weights"]
    assert "KOSPI" not in asian_weights
    assert sum(asian_weights.values()) == pytest.approx(1.0, abs=0.0002)
    assert partial["markets"]["KOSPI"]["score"] is None
    assert partial["completeness"] == pytest.approx(85.71, abs=0.01)
    assert partial["confidence"] < full["confidence"]


@pytest.mark.asyncio
async def test_one_available_region_is_renormalized_to_full_global_weight():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = [
        row
        for code in ("SP500", "NASDAQ_100", "DOW_JONES")
        for row in _history(code, date(2025, 1, 7))
    ]
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    assert result["asian_context"]["score"] is None
    assert result["global_score"] == result["us_context"]["score"]
    assert result["score_breakdown"]["normalized_region_weights"] == {"us": 1.0}


@pytest.mark.asyncio
async def test_stale_freshness_lowers_confidence_without_changing_direction():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    recent_rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7))
    ]
    stale_rows = [
        _price(row.market_index.code, row.trade_date, row.close, freshness="stale")
        for row in recent_rows
    ]
    recent = await _service(CountingPriceRepository(recent_rows)).evaluate(evaluation)
    stale = await _service(CountingPriceRepository(stale_rows)).evaluate(evaluation)
    assert stale["global_score"] == recent["global_score"]
    assert stale["global_direction"] == recent["global_direction"]
    assert stale["confidence"] < recent["confidence"]


@pytest.mark.asyncio
async def test_market_disagreement_lowers_confidence():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    aligned_rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7), rising=True)
    ]
    disagreeing_rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(
            code,
            date(2025, 1, 7),
            rising=code in {"SP500", "NASDAQ_100", "DOW_JONES"},
        )
    ]
    aligned = await _service(CountingPriceRepository(aligned_rows)).evaluate(evaluation)
    disagreeing = await _service(CountingPriceRepository(disagreeing_rows)).evaluate(evaluation)
    assert disagreeing["confidence"] < aligned["confidence"]
    assert disagreeing["us_context"]["score"] > 0
    assert disagreeing["asian_context"]["score"] < 0


@pytest.mark.asyncio
async def test_short_rising_history_is_not_falsely_strong_bearish():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = _history("SP500", date(2025, 1, 7), sessions=30, rising=True)
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    market = result["markets"]["SP500"]
    assert market["trend"] == "bullish"
    assert market["score"] > 0


@pytest.mark.asyncio
async def test_result_is_deterministic_for_same_data_and_evaluation_timestamp():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = [
        row
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        for row in _history(code, date(2025, 1, 7))
    ]
    service = _service(CountingPriceRepository(rows))
    first = await service.evaluate(evaluation)
    second = await service.evaluate(evaluation)
    assert first == second


@pytest.mark.asyncio
async def test_no_look_ahead_excludes_us_row_before_native_close():
    evaluation = datetime(2025, 1, 7, 15, 0, tzinfo=UTC)
    rows = _history("SP500", date(2025, 1, 6), sessions=260)
    rows.append(_price("SP500", date(2025, 1, 7), Decimal("10000")))
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    market = result["markets"]["SP500"]
    assert market["cutoff_date"] == "2025-01-06"
    assert market["market_date"] == "2025-01-06"
    assert market["returns"][1] != pytest.approx(2700, abs=1)


@pytest.mark.asyncio
async def test_cross_timezone_same_instant_can_include_asia_but_not_us_session():
    evaluation = datetime(2025, 1, 7, 15, 0, tzinfo=UTC)
    rows = _history("SP500", date(2025, 1, 6), sessions=30)
    rows += _history("NIKKEI_225", date(2025, 1, 8), sessions=30)
    rows += [_price("SP500", date(2025, 1, 7), Decimal("9999"))]
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    assert result["markets"]["SP500"]["market_date"] == "2025-01-06"
    assert result["markets"]["NIKKEI_225"]["market_date"] == "2025-01-07"


@pytest.mark.asyncio
async def test_weekend_cutoff_naturally_selects_prior_stored_session():
    evaluation = datetime(2025, 1, 5, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    rows = _history("SP500", date(2025, 1, 3), sessions=30)
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    assert result["markets"]["SP500"]["cutoff_date"] == "2025-01-04"
    assert result["markets"]["SP500"]["market_date"] == "2025-01-03"


@pytest.mark.asyncio
async def test_market_context_exposes_provenance_freshness_and_score_breakdown():
    evaluation = datetime(2025, 1, 7, 22, 0, tzinfo=UTC)
    rows = _history("SP500", date(2025, 1, 7))
    result = await _service(CountingPriceRepository(rows)).evaluate(evaluation)
    market = result["markets"]["SP500"]
    assert market["source_name"] == "fixture_provider"
    assert market["source_reference"].startswith("mock://SP500/")
    assert market["freshness"] == "recent"
    assert set(market["score_breakdown"]) == {
        "component_signals",
        "normalized_weights",
        "price_action",
    }
    assert sum(market["score_breakdown"]["normalized_weights"].values()) == pytest.approx(
        1.0, abs=0.0002
    )


@pytest.mark.asyncio
async def test_evaluate_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        await _service(CountingPriceRepository([])).evaluate(datetime(2025, 1, 7, 22, 0))