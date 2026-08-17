from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.index_catalog import (
    BRENT_CRUDE_INDEX_CODE,
    INDIA_VIX_INDEX_CODE,
    MARKET_FACTOR_INDEX_CODES,
    USD_INR_INDEX_CODE,
)
from app.market_intelligence.providers.base import ProviderResult
from app.market_intelligence.providers.manual_market_factor_provider import (
    EXPECTED_IDENTITIES,
    ManualMarketFactorProvider,
)
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.external_market_factor_intelligence import (
    ExternalMarketFactorIntelligence,
    classify_factor_direction,
    classify_vix_regime,
)

IST = ZoneInfo("Asia/Kolkata")
EVALUATION = datetime(2025, 2, 3, 16, 0, tzinfo=IST)


def _price(code: str, trade_date: date, close: Decimal, freshness: str = "recent"):
    index = MarketIndex(code=code, name=code, country="TEST")
    return MarketIndexPrice(
        market_index_id=1,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        source_name="fixture_factor",
        source_reference=f"manual://{code}/{trade_date}",
        freshness_status=freshness,
        market_index=index,
    )


def _history(code: str, end: date, start: str, step: str, sessions: int = 260):
    return [
        _price(
            code,
            end - timedelta(days=sessions - 1 - offset),
            Decimal(start) + Decimal(step) * offset,
        )
        for offset in range(sessions)
    ]


class CountingPriceRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.bounds = None

    async def get_ranges_by_index_codes(self, codes, start_date, end_date):
        self.calls += 1
        self.bounds = (codes, start_date, end_date)
        return [row for row in self.rows if start_date <= row.trade_date <= end_date]


def _service(repo, providers=()):
    return ExternalMarketFactorIntelligence(
        SimpleNamespace(), providers=providers, price_repo=repo  # type: ignore[arg-type]
    )


class StaticFactorProvider:
    supports_indices = list(MARKET_FACTOR_INDEX_CODES)

    def __init__(self, name: str, result_factory) -> None:
        self.name = name
        self.result_factory = result_factory
        self.calls = []

    async def fetch(self, **kwargs):
        raise AssertionError("range-capable provider must not be called per date")

    async def fetch_range(self, index_code, start_date, end_date, *, evaluation_at=None):
        self.calls.append((index_code, start_date, end_date, evaluation_at))
        return self.result_factory(index_code, end_date)


class RaisingFactorProvider(StaticFactorProvider):
    async def fetch_range(self, index_code, start_date, end_date, *, evaluation_at=None):
        self.calls.append((index_code, start_date, end_date, evaluation_at))
        raise RuntimeError("provider exploded")


def _provider_factor_result(
    provider_name: str,
    code: str,
    trade_date: date,
    *,
    close: str = "90",
    identity_override: dict[str, str] | None = None,
):
    observed = datetime.combine(trade_date, datetime.min.time(), tzinfo=IST)
    identity = {**EXPECTED_IDENTITIES[code], **(identity_override or {})}
    return ProviderResult(
        provider_name=provider_name,
        status="success",
        observed_at=observed.isoformat(),
        fetched_at=observed.isoformat(),
        market_date=trade_date.isoformat(),
        freshness="recent",
        data=[
            {
                "index_code": code,
                "trade_date": trade_date,
                "open": Decimal(close),
                "high": Decimal(close),
                "low": Decimal(close),
                "close": Decimal(close),
                **identity,
                "source_reference": f"provider://{provider_name}/{code}",
                "observed_at": observed,
                "available_at": observed,
            }
        ],
    )


def test_catalog_keeps_market_factors_outside_equity_global_semantics():
    assert MARKET_FACTOR_INDEX_CODES == ("INDIA_VIX", "USD_INR", "BRENT_CRUDE")
    assert classify_factor_direction(60) == "strong_positive"
    assert classify_factor_direction(-60) == "strong_negative"
    assert classify_vix_regime(11.99) == "very_low"
    assert classify_vix_regime(18) == "elevated"
    assert classify_vix_regime(35) == "extreme"


@pytest.mark.asyncio
async def test_empty_histories_preserve_missing_and_use_one_bulk_query():
    repo = CountingPriceRepository([])
    result = await _service(repo).evaluate(EVALUATION)
    assert repo.calls == 1
    assert repo.bounds[0] == MARKET_FACTOR_INDEX_CODES
    assert result["confidence"] == result["completeness"] == 0.0
    assert all(item["score"] is None for item in result["factors"].values())


@pytest.mark.asyncio
async def test_vix_level_returns_percentile_regime_and_negative_risk_context():
    rows = _history(INDIA_VIX_INDEX_CODE, date(2025, 2, 3), "10", "0.1")
    result = await _service(CountingPriceRepository(rows)).evaluate(EVALUATION)
    vix = result["factors"]["india_vix"]
    assert vix["market_date"] == "2025-02-03"
    assert set(vix["returns"]) == {1, 5, 20}
    assert vix["historical_percentile"] == 100.0
    assert vix["regime"] in {"high", "extreme"}
    assert -100 <= vix["score"] < 0
    assert 0 <= vix["confidence"] <= 100
    assert vix["identity"]["unit"] == "INDEX_POINTS"


@pytest.mark.asyncio
async def test_usd_inr_orientation_direction_returns_trend_and_score():
    rows = _history(USD_INR_INDEX_CODE, date(2025, 2, 2), "70", "0.05")
    result = await _service(CountingPriceRepository(rows)).evaluate(EVALUATION)
    fx = result["factors"]["usd_inr"]
    assert fx["identity"]["quote_convention"] == "INR_PER_1_USD"
    assert fx["market_direction"] == "inr_weakening"
    assert set(fx["returns"]) == {1, 5, 20, 60}
    assert fx["trend"] in {"bullish", "strong_bullish"}
    assert -100 <= fx["score"] < 0


@pytest.mark.asyncio
async def test_brent_exact_spot_identity_rising_context_is_negative():
    rows = _history(BRENT_CRUDE_INDEX_CODE, date(2025, 2, 2), "55", "0.10")
    result = await _service(CountingPriceRepository(rows)).evaluate(EVALUATION)
    crude = result["factors"]["brent_crude"]
    assert crude["identity"]["instrument"] == "EUROPE_BRENT_SPOT"
    assert crude["identity"]["unit"] == "USD_PER_BARREL"
    assert crude["market_direction"] == "rising"
    assert -100 <= crude["score"] < 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "factor_key"),
    [
        (INDIA_VIX_INDEX_CODE, "india_vix"),
        (USD_INR_INDEX_CODE, "usd_inr"),
        (BRENT_CRUDE_INDEX_CODE, "brent_crude"),
    ],
)
async def test_mandatory_independent_future_invariance(code: str, factor_key: str):
    end = date(2025, 2, 3) if code == INDIA_VIX_INDEX_CODE else date(2025, 2, 2)
    repo = CountingPriceRepository(_history(code, end, "50", "0.1"))
    service = _service(repo)
    before = (await service.evaluate(EVALUATION))["factors"][factor_key]
    repo.rows.append(_price(code, date(2025, 2, 4), Decimal("999999")))
    after = (await service.evaluate(EVALUATION))["factors"][factor_key]
    assert after == before


@pytest.mark.asyncio
async def test_same_day_date_only_fx_and_brent_rows_are_conservatively_excluded():
    rows = [
        _price(USD_INR_INDEX_CODE, date(2025, 2, 3), Decimal("999")),
        _price(BRENT_CRUDE_INDEX_CODE, date(2025, 2, 3), Decimal("999")),
    ]
    result = await _service(CountingPriceRepository(rows)).evaluate(EVALUATION)
    assert result["factors"]["usd_inr"]["latest"] is None
    assert result["factors"]["brent_crude"]["latest"] is None


@pytest.mark.asyncio
async def test_primary_provider_range_is_used_once_per_factor_with_explicit_attempts():
    provider = StaticFactorProvider(
        "primary_factor", lambda code, end: _provider_factor_result("primary_factor", code, end)
    )
    result = await _service(CountingPriceRepository([]), (provider,)).evaluate(EVALUATION)
    assert len(provider.calls) == len(MARKET_FACTOR_INDEX_CODES)
    assert {call[0] for call in provider.calls} == set(MARKET_FACTOR_INDEX_CODES)
    for factor in result["factors"].values():
        assert factor["fallback_level"] == "primary_provider"
        assert factor["provider_attempts"] == [
            {
                "provider_name": "primary_factor",
                "status": "success",
                "freshness": "recent",
                "error_code": None,
            }
        ]


@pytest.mark.asyncio
async def test_malformed_primary_identity_falls_to_secondary_then_stale_db():
    malformed = StaticFactorProvider(
        "malformed_primary",
        lambda code, end: _provider_factor_result(
            "malformed_primary", code, end, identity_override={"instrument": "PROXY"}
        ),
    )
    secondary = StaticFactorProvider(
        "secondary_factor",
        lambda code, end: (
            _provider_factor_result("secondary_factor", code, end)
            if code != BRENT_CRUDE_INDEX_CODE
            else ProviderResult(
                provider_name="secondary_factor",
                status="unavailable",
                freshness="missing",
                error_code="not_available",
            )
        ),
    )
    stored_brent = _price(
        BRENT_CRUDE_INDEX_CODE, date(2025, 1, 30), Decimal("75"), freshness="recent"
    )
    result = await _service(
        CountingPriceRepository([stored_brent]), (malformed, secondary)
    ).evaluate(EVALUATION)
    assert result["factors"]["usd_inr"]["fallback_level"] == "secondary_provider"
    crude = result["factors"]["brent_crude"]
    assert crude["fallback_level"] == "stale_stored_observation"
    assert crude["freshness"] == "stale"
    assert [item["provider_name"] for item in crude["provider_attempts"]] == [
        "malformed_primary",
        "secondary_factor",
    ]


@pytest.mark.asyncio
async def test_exact_stored_observation_is_distinct_from_stale_and_unavailable():
    rows = [
        _price(INDIA_VIX_INDEX_CODE, date(2025, 2, 3), Decimal("15")),
        _price(USD_INR_INDEX_CODE, date(2025, 2, 1), Decimal("86")),
    ]
    result = await _service(CountingPriceRepository(rows)).evaluate(EVALUATION)
    assert result["factors"]["india_vix"]["fallback_level"] == "exact_stored_observation"
    assert result["factors"]["usd_inr"]["fallback_level"] == "stale_stored_observation"
    assert result["factors"]["brent_crude"]["fallback_level"] == "unavailable"


@pytest.mark.asyncio
async def test_provider_exception_is_recorded_before_secondary_fallback():
    raising = RaisingFactorProvider("raising_primary", lambda code, end: None)
    secondary = StaticFactorProvider(
        "secondary_factor",
        lambda code, end: _provider_factor_result("secondary_factor", code, end),
    )
    result = await _service(
        CountingPriceRepository([]), (raising, secondary)
    ).evaluate(EVALUATION)
    fx = result["factors"]["usd_inr"]
    assert fx["fallback_level"] == "secondary_provider"
    assert fx["provider_attempts"][0]["error_code"] == "provider_exception"
    assert fx["provider_attempts"][1]["status"] == "success"


def test_provider_and_stored_overlap_is_one_session_per_trade_date():
    provider_rows = [
        _price(USD_INR_INDEX_CODE, date(2025, 2, 1), Decimal("90")),
        _price(USD_INR_INDEX_CODE, date(2025, 2, 2), Decimal("91")),
    ]
    stored_rows = [
        _price(USD_INR_INDEX_CODE, date(2025, 1, 31), Decimal("88")),
        _price(USD_INR_INDEX_CODE, date(2025, 2, 1), Decimal("89")),
    ]
    merged = ExternalMarketFactorIntelligence._merge_histories(
        provider_rows, stored_rows
    )
    assert [row.trade_date for row in merged] == [
        date(2025, 2, 2),
        date(2025, 2, 1),
        date(2025, 1, 31),
    ]
    assert merged[1].close == Decimal("90")


@pytest.mark.asyncio
async def test_manual_factor_provider_unconfigured_and_exact_identity_validation(tmp_path: Path):
    unavailable = await ManualMarketFactorProvider().fetch_range(
        USD_INR_INDEX_CODE,
        date(2025, 2, 2),
        date(2025, 2, 2),
        evaluation_at=EVALUATION,
    )
    assert unavailable.status == "unavailable"
    assert unavailable.error_code == "automation_not_configured"

    path = tmp_path / "usd_inr.csv"
    path.write_text(
        "observed_at,available_at,trade_date,index_code,open,high,low,close,exchange,"
        "instrument_type,instrument,quote_convention,currency,unit,source_reference\n"
        "2025-02-02T16:00:00+05:30,2025-02-02T16:01:00+05:30,2025-02-02,USD_INR,"
        "86,87,85,86.5,FX_SPOT,FX_SPOT,USD_INR_SPOT,INR_PER_1_USD,INR,INR_PER_USD,"
        "manual://licensed-fx-export\n",
        encoding="utf-8",
    )
    provider = ManualMarketFactorProvider({USD_INR_INDEX_CODE: path})
    success = await provider.fetch_range(
        USD_INR_INDEX_CODE,
        date(2025, 2, 2),
        date(2025, 2, 2),
        evaluation_at=EVALUATION,
    )
    assert success.status == "success"
    assert success.data[0]["close"] == Decimal("86.5")
    assert success.data[0]["quote_convention"] == "INR_PER_1_USD"
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_manual_factor_provider_rejects_proxy_identity(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text(
        "observed_at,available_at,trade_date,index_code,open,high,low,close,exchange,"
        "instrument_type,instrument,quote_convention,currency,unit,source_reference\n"
        "2025-02-02T16:00:00+05:30,2025-02-02T16:01:00+05:30,2025-02-02,"
        "BRENT_CRUDE,70,71,69,70.5,MCX,FUTURES,WTI_FUTURE,USD_PER_BARREL,USD,"
        "USD_PER_BARREL,manual://proxy\n",
        encoding="utf-8",
    )
    result = await ManualMarketFactorProvider({BRENT_CRUDE_INDEX_CODE: path}).fetch_range(
        BRENT_CRUDE_INDEX_CODE,
        date(2025, 2, 2),
        date(2025, 2, 2),
        evaluation_at=EVALUATION,
    )
    assert result.status == "failed"
    assert result.error_code == "manual_file_invalid"


@pytest.mark.asyncio
async def test_future_malformed_factor_csv_row_cannot_change_historical_replay(tmp_path: Path):
    path = tmp_path / "historical_with_future_bad.csv"
    header = (
        "observed_at,available_at,trade_date,index_code,open,high,low,close,exchange,"
        "instrument_type,instrument,quote_convention,currency,unit,source_reference\n"
    )
    known = (
        "2025-02-02T16:00:00+05:30,2025-02-02T16:01:00+05:30,2025-02-02,USD_INR,"
        "86,87,85,86.5,FX_SPOT,FX_SPOT,USD_INR_SPOT,INR_PER_1_USD,INR,INR_PER_USD,"
        "manual://known\n"
    )
    path.write_text(header + known, encoding="utf-8")
    provider = ManualMarketFactorProvider({USD_INR_INDEX_CODE: path})
    before = await provider.fetch_range(
        USD_INR_INDEX_CODE,
        date(2025, 2, 1),
        date(2025, 2, 2),
        evaluation_at=EVALUATION,
    )
    future_bad = (
        "2025-02-05T16:00:00+05:30,2025-02-05T16:01:00+05:30,2025-02-05,USD_INR,"
        "BAD,BAD,BAD,BAD,PROXY,FUTURE,INRUSD,WRONG,USD,WRONG,manual://future-bad\n"
    )
    path.write_text(header + known + future_bad, encoding="utf-8")
    after = await provider.fetch_range(
        USD_INR_INDEX_CODE,
        date(2025, 2, 1),
        date(2025, 2, 2),
        evaluation_at=EVALUATION,
    )
    assert after == before


@pytest.mark.asyncio
async def test_naive_evaluation_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        await _service(CountingPriceRepository([])).evaluate(datetime(2025, 2, 3, 16, 0))