from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.market_intelligence.sector_universe import (
    SECTOR_BENCHMARK_CODE,
    SECTOR_DEFINITIONS_BY_CODE,
    SECTOR_INDEX_CODES,
)
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.sector_intelligence import SectorIntelligence


def _price(code: str, trade_date: date, close: Decimal) -> MarketIndexPrice:
    previous = close - Decimal("1")
    index = MarketIndex(code=code, name=code, country="INDIA")
    return MarketIndexPrice(
        market_index_id=1,
        trade_date=trade_date,
        open=previous,
        high=close + Decimal("1"),
        low=previous - Decimal("1"),
        close=close,
        previous_close=previous,
        source_name="fixture_provider",
        source_reference=f"mock://{code}/{trade_date}",
        freshness_status="recent",
        market_index=index,
    )


def _history(code: str, end_date: date, *, rising: bool = True, sessions: int = 260):
    rows = []
    for offset in range(sessions):
        trade_date = end_date - timedelta(days=sessions - 1 - offset)
        close = (
            Decimal("8000") + Decimal(offset)
            if rising
            else Decimal("12000") - Decimal(offset)
        )
        rows.append(_price(code, trade_date, close))
    return rows


class CountingPriceRepository:
    def __init__(self, rows: list[MarketIndexPrice]) -> None:
        self.rows = rows
        self.calls = 0

    async def get_ranges_by_index_codes(
        self, index_codes: tuple[str, ...], start_date: date, end_date: date
    ) -> list[MarketIndexPrice]:
        self.calls += 1
        return [
            row
            for row in self.rows
            if row.market_index.code in index_codes
            and start_date <= row.trade_date <= end_date
        ]


def _service(repo: CountingPriceRepository, **overrides) -> SectorIntelligence:
    return SectorIntelligence(
        SimpleNamespace(),  # type: ignore[arg-type]
        price_repo=repo,  # type: ignore[arg-type]
        **overrides,
    )


def _uniform_panel(*, rising_codes: tuple[str, ...], end_date: date) -> list[MarketIndexPrice]:
    rows: list[MarketIndexPrice] = []
    all_codes = set(SECTOR_INDEX_CODES) | {SECTOR_BENCHMARK_CODE}
    for code in all_codes:
        rising = code in rising_codes
        rows.extend(_history(code, end_date, rising=rising))
    return rows


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_catalog_bundles_canonical_sector_definitions():
    codes = {
        "NIFTY_BANK",
        "NIFTY_IT",
        "NIFTY_PHARMA",
        "NIFTY_METAL",
        "NIFTY_AUTO",
        "NIFTY_REALTY",
        "NIFTY_ENERGY",
        "NIFTY_FMCG",
    }
    for code in codes:
        assert code in SECTOR_DEFINITIONS_BY_CODE
    assert SECTOR_BENCHMARK_CODE == "NIFTY_50"
    assert "NIFTY_50" not in SECTOR_INDEX_CODES


@pytest.mark.asyncio
async def test_empty_database_preserves_missing_none_values():
    repo = CountingPriceRepository([])
    result = await _service(repo).evaluate(date(2025, 1, 7))
    assert repo.calls == 1
    assert result["sector_intelligence_score"] is None
    assert result["direction"] is None
    assert result["completeness_percent"] == 0.0
    assert result["confidence_score"] == 0.0
    assert result["leaders"] == []
    assert result["laggards"] == []
    assert result["available_sector_count"] == 0


@pytest.mark.asyncio
async def test_rising_leaders_and_falling_laggards_are_ranked_deterministically():
    end_date = date(2025, 1, 6)
    advocate_codes = ("NIFTY_BANK", "NIFTY_IT", "NIFTY_PHARMA")
    repo = CountingPriceRepository(_uniform_panel(rising_codes=advocate_codes, end_date=end_date))
    result = await _service(repo).evaluate(end_date)
    assert result["available_sector_count"] == len(SECTOR_INDEX_CODES)
    leaders = [item["code"] for item in result["leaders"]]
    laggards = [item["code"] for item in result["laggards"]]
    assert result["sector_intelligence_score"] is not None
    assert leaders
    assert laggards
    # Every rising sector beats every falling sector on the score.
    rising_scores = {
        result["sectors"][c]["score"]
        for c in advocate_codes
    }
    falling_scores = {
        result["sectors"][c]["score"]
        for c in SECTOR_INDEX_CODES
        if c not in advocate_codes
    }
    assert all(r is not None for r in rising_scores)
    assert all(r is not None for r in falling_scores)
    assert min(r for r in rising_scores) > max(r for r in falling_scores)


@pytest.mark.asyncio
async def test_benchmark_relative_returns_require_benchmark_data():
    end_date = date(2025, 1, 6)
    # Sector data present but NIFTY_50 benchmark entirely absent.
    rows = [
        row
        for row in _uniform_panel(rising_codes=("NIFTY_BANK",), end_date=end_date)
        if row.market_index.code != SECTOR_BENCHMARK_CODE
    ]
    result = await _service(repo := CountingPriceRepository(rows)).evaluate(end_date)
    assert repo.calls == 1
    sample = result["sectors"][SECTOR_INDEX_CODES[0]]
    # Without a benchmark, relative returns are None, never fabricated as 0.
    for horizon in (1, 5, 20, 60):
        assert sample["relative_returns"][horizon] is None
    assert sample["relative_strength"] is None


@pytest.mark.asyncio
async def test_cyclical_context_uses_cyclical_minus_defensive_delta():
    end_date = date(2025, 1, 6)
    # Strong rally concentrated in cyclical sectors.
    repo = CountingPriceRepository(
        _uniform_panel(
            rising_codes=("NIFTY_METAL", "NIFTY_REALTY", "NIFTY_AUTO"),
            end_date=end_date,
        )
    )
    result = await _service(repo).evaluate(end_date)
    assert result["cyclical_minus_defensive"] is not None
    assert result["risk_context"] in {"risk_on", "risk_off", "balanced"}
    assert result["cyclical_score"] is not None
    assert result["defensive_score"] is not None


@pytest.mark.asyncio
async def test_rotation_census_classifies_leader_and_laggard_states():
    end_date = date(2025, 1, 6)
    row_bank = _history("NIFTY_BANK", end_date, rising=True)
    # Make the bank sector improve sharply recently so it becomes an emerging/leader state.
    repo = CountingPriceRepository(
        row_bank
        + _history("NIFTY_METAL", end_date, rising=False)
        + _history(SECTOR_BENCHMARK_CODE, end_date, rising=True)
    )
    result = await _service(repo).evaluate(end_date)
    census = result["rotation"]
    assert census["classified_count"] >= 1
    assert {"leader", "laggard", "emerging_leader", "improving", "deteriorating"} & set(
        census["states"]
    )


@pytest.mark.asyncio
async def test_rotation_state_returns_none_without_5_and_20_relative_horizons():
    end_date = date(2025, 1, 6)
    rows = [_price("NIFTY_BANK", end_date, Decimal("100"))]
    result = await _service(repo := CountingPriceRepository(rows)).evaluate(end_date)
    assert repo.calls == 1
    assert result["sectors"]["NIFTY_BANK"]["rotation"]["state"] is None


# ---------------------------------------------------------------------------
# Mandatory no-look-ahead regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_future_rows_never_affect_historical_output():
    end_date = date(2025, 1, 6)
    future_date = date(2025, 6, 1)
    base_rows = _uniform_panel(rising_codes=("NIFTY_IT",), end_date=end_date)
    baseline = await _service(CountingPriceRepository(base_rows)).evaluate(end_date)

    boosted_rows = base_rows + [
        _price("NIFTY_METAL", future_date, Decimal("20000")),
        _price("NIFTY_BANK", future_date, Decimal("99999")),
    ]
    with_future = await _service(CountingPriceRepository(boosted_rows)).evaluate(end_date)

    assert baseline == with_future


@pytest.mark.asyncio
async def test_evaluate_uses_single_bulk_query():
    end_date = date(2025, 1, 6)
    repo = CountingPriceRepository(
        _uniform_panel(rising_codes=("NIFTY_IT",), end_date=end_date)
    )
    await _service(repo).evaluate(end_date)
    assert repo.calls == 1
