from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.market_intelligence.session_dates import (
    gift_nifty_completed_daily_cutoff_date,
    gift_nifty_session_context,
    indian_cash_context,
    indian_cash_reference_cutoff_date,
)
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.gift_nifty_intelligence import (
    GiftNiftyIntelligence,
    classify_gift_direction,
)

IST = ZoneInfo("Asia/Kolkata")


def _price(
    code: str,
    trade_date: date,
    close: str,
    *,
    fetched_at: date | None = None,
    freshness: str = "recent",
) -> MarketIndexPrice:
    value = Decimal(close)
    index = MarketIndex(code=code, name=code, country="INDIA")
    return MarketIndexPrice(
        market_index_id=1,
        trade_date=trade_date,
        open=value,
        high=value,
        low=value,
        close=value,
        fetched_at=fetched_at,
        source_name="fixture_database",
        source_reference=f"db://{code}/{trade_date}",
        freshness_status=freshness,
        market_index=index,
    )


class CountingRepository:
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


class FakeGiftProvider(MarketProvider):
    supports_indices = ["GIFT_NIFTY"]

    def __init__(self, name: str, result: ProviderResult) -> None:
        self.name = name
        self.result = result
        self.calls = 0
        self.kwargs: dict[str, Any] | None = None

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        self.calls += 1
        self.kwargs = kwargs
        return self.result


def _result(
    price: str = "24120",
    *,
    observed_at: str = "2025-01-08T08:20:00+05:30",
    fetched_at: str = "2025-01-08T08:21:00+05:30",
    status: str = "success",
    provider_name: str = "fixture_primary",
) -> ProviderResult:
    data = None
    if status in {"success", "cached", "stale"}:
        data = {
            "index_code": "GIFT_NIFTY",
            "price": Decimal(price),
            "contract_symbol": "NIFTY25JANFUT",
            "expiry_date": date(2025, 1, 30),
            "exchange": "NSE_IX",
            "instrument_type": "FUTIDX",
            "underlying": "NIFTY_50",
            "currency": "USD",
            "source_reference": "manual://licensed/gift.csv",
        }
    return ProviderResult(
        provider_name=provider_name,
        status=status,
        freshness="recent" if data else "missing",
        observed_at=observed_at if data else None,
        fetched_at=fetched_at if data else None,
        market_date="2025-01-08" if data else None,
        data=data,
    )


def _service(
    repo: CountingRepository,
    providers: tuple[MarketProvider, ...] = (),
    *,
    holidays: tuple[date, ...] = (),
) -> GiftNiftyIntelligence:
    return GiftNiftyIntelligence(
        SimpleNamespace(),
        providers,
        price_repo=repo,  # type: ignore[arg-type]
        cash_holidays=holidays,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.75", "strong_gap_up"),
        ("0.7499", "gap_up"),
        ("0.20", "gap_up"),
        ("0.1999", "flat"),
        ("-0.1999", "flat"),
        ("-0.20", "gap_down"),
        ("-0.7499", "gap_down"),
        ("-0.75", "strong_gap_down"),
        (None, None),
    ],
)
def test_direction_threshold_boundaries_are_exact(value: str | None, expected: str | None):
    assert classify_gift_direction(Decimal(value) if value is not None else None) == expected


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (6, 29, "closed"),
        (6, 30, "session_one"),
        (15, 40, "session_one"),
        (15, 41, "inter_session_break"),
        (16, 34, "inter_session_break"),
        (16, 35, "session_two"),
        (23, 59, "session_two"),
        (2, 45, "session_two"),
    ],
)
def test_gift_session_boundaries(hour: int, minute: int, expected: str):
    day = 9 if hour <= 2 else 8
    assert gift_nifty_session_context(datetime(2025, 1, day, hour, minute, tzinfo=IST)) == expected


def test_weekend_and_monday_after_midnight_session_context():
    assert gift_nifty_session_context(datetime(2025, 1, 11, 2, 0, tzinfo=IST)) == "session_two"
    assert gift_nifty_session_context(datetime(2025, 1, 11, 8, 0, tzinfo=IST)) == "closed_weekend"
    assert (
        gift_nifty_session_context(datetime(2025, 1, 13, 2, 0, tzinfo=IST))
        == "closed_weekend"
    )


def test_cash_context_and_reference_cutoff_before_during_after_close():
    pre = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    during = datetime(2025, 1, 8, 12, 0, tzinfo=IST)
    after = datetime(2025, 1, 8, 15, 30, tzinfo=IST)
    assert indian_cash_context(pre) == "pre_open"
    assert indian_cash_context(during) == "cash_market_open"
    assert indian_cash_context(after) == "post_close"
    assert indian_cash_reference_cutoff_date(pre) == date(2025, 1, 7)
    assert indian_cash_reference_cutoff_date(during) == date(2025, 1, 7)
    assert indian_cash_reference_cutoff_date(after) == date(2025, 1, 8)


def test_cash_context_weekend_holiday_and_daily_gift_cutoff():
    weekend = datetime(2025, 1, 11, 8, 0, tzinfo=IST)
    holiday = datetime(2025, 1, 8, 8, 0, tzinfo=IST)
    assert indian_cash_context(weekend) == "weekend"
    assert indian_cash_context(holiday, holiday_dates=frozenset({holiday.date()})) == "holiday"
    assert gift_nifty_completed_daily_cutoff_date(holiday) == date(2025, 1, 7)


@pytest.mark.asyncio
async def test_provider_observation_and_latest_completed_nifty_close_drive_gap():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository(
        [
            _price("NIFTY_50", date(2025, 1, 6), "23900"),
            _price("NIFTY_50", date(2025, 1, 7), "24000"),
            _price("NIFTY_50", date(2025, 1, 8), "99999"),
        ]
    )
    provider = FakeGiftProvider("primary", _result("24120"))
    output = await _service(repo, (provider,)).evaluate(evaluation)
    assert repo.calls == 1
    assert repo.bounds is not None
    assert repo.bounds[0] == ("GIFT_NIFTY", "NIFTY_50")
    assert output["nifty_reference"]["market_date"] == "2025-01-07"
    assert output["nifty_reference"]["close"] == 24000.0
    assert output["indicated_gap_points"] == 120.0
    assert output["indicated_gap_percent"] == 0.5
    assert output["direction"] == "gap_up"
    assert output["gift_nifty_score"] == 50.0
    assert output["context_label"] == "pre_open"


@pytest.mark.asyncio
async def test_gap_down_flat_and_strong_boundaries_are_calculated_not_fabricated():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "20000")])
    cases = [("20150", "strong_gap_up"), ("20000", "flat"), ("19850", "strong_gap_down")]
    for price, expected in cases:
        output = await _service(
            repo, (FakeGiftProvider("primary", _result(price)),)
        ).evaluate(evaluation)
        assert output["direction"] == expected


@pytest.mark.asyncio
async def test_zero_or_missing_reference_yields_none_not_zero():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    provider = FakeGiftProvider("primary", _result())
    missing = await _service(CountingRepository([]), (provider,)).evaluate(evaluation)
    zero = await _service(
        CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "0")]),
        (provider,),
    ).evaluate(evaluation)
    for output in (missing, zero):
        assert output["indicated_gap_points"] is None
        assert output["indicated_gap_percent"] is None
        assert output["direction"] is None
        assert output["gift_nifty_score"] is None
        assert output["confidence_score"] == 0.0


@pytest.mark.asyncio
async def test_score_is_clamped_to_both_bounds():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "100")])
    positive = await _service(
        repo, (FakeGiftProvider("positive", _result("1000")),)
    ).evaluate(evaluation)
    negative = await _service(
        repo, (FakeGiftProvider("negative", _result("1")),)
    ).evaluate(evaluation)
    assert positive["gift_nifty_score"] == 100.0
    assert negative["gift_nifty_score"] == -100.0


@pytest.mark.asyncio
async def test_freshness_reduces_confidence_without_reversing_direction():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")])
    live = FakeGiftProvider("live", _result(observed_at="2025-01-08T08:20:00+05:30"))
    stale = FakeGiftProvider("stale", _result(observed_at="2025-01-07T20:00:00+05:30"))
    live_output = await _service(repo, (live,)).evaluate(evaluation)
    stale_output = await _service(repo, (stale,)).evaluate(evaluation)
    assert stale_output["direction"] == live_output["direction"] == "gap_up"
    assert stale_output["gift_nifty_score"] == live_output["gift_nifty_score"]
    assert stale_output["confidence_score"] < live_output["confidence_score"]
    assert stale_output["gift_observation"]["freshness"] == "stale"


@pytest.mark.asyncio
async def test_completeness_and_momentum_truthfully_reflect_available_inputs():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    provider = FakeGiftProvider("primary", _result())
    full = await _service(
        CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")]),
        (provider,),
    ).evaluate(evaluation)
    gift_only = await _service(CountingRepository([]), (provider,)).evaluate(evaluation)
    unavailable = await _service(CountingRepository([])).evaluate(evaluation)
    assert full["data_completeness_percent"] == 90.0
    assert gift_only["data_completeness_percent"] == 45.0
    assert unavailable["data_completeness_percent"] == 0.0
    assert full["momentum_percent"] is None
    assert "same-contract history" in full["limitations"]["momentum"]


@pytest.mark.asyncio
async def test_provider_fallback_primary_then_secondary_then_database():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    rows = [
        _price("NIFTY_50", date(2025, 1, 7), "24000"),
        _price("GIFT_NIFTY", date(2025, 1, 7), "24050"),
    ]
    failed = FakeGiftProvider("primary", _result(status="unavailable"))
    secondary = FakeGiftProvider("secondary", _result("24120", provider_name="secondary"))
    output = await _service(CountingRepository(rows), (failed, secondary)).evaluate(evaluation)
    assert failed.calls == secondary.calls == 1
    assert output["fallback_level"] == "secondary_provider"
    assert output["gift_observation"]["price"] == 24120.0
    assert [item["status"] for item in output["provider_attempts"]] == [
        "unavailable",
        "success",
    ]


@pytest.mark.asyncio
async def test_exact_and_stale_stored_fallback_are_distinguished():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    nifty = _price("NIFTY_50", date(2025, 1, 7), "24000")
    exact = await _service(
        CountingRepository([nifty, _price("GIFT_NIFTY", date(2025, 1, 7), "24050")])
    ).evaluate(evaluation)
    stale = await _service(
        CountingRepository([nifty, _price("GIFT_NIFTY", date(2025, 1, 6), "24025")])
    ).evaluate(evaluation)
    assert exact["fallback_level"] == "exact_stored_observation"
    assert stale["fallback_level"] == "stale_stored_observation"
    assert exact["gift_observation"]["timestamp_precision"] == "date"
    assert exact["gift_observation"]["freshness"] == "stale"


@pytest.mark.asyncio
async def test_same_day_date_only_gift_row_is_never_claimed_as_intraday():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    rows = [
        _price("NIFTY_50", date(2025, 1, 7), "24000"),
        _price("GIFT_NIFTY", date(2025, 1, 8), "99999"),
    ]
    output = await _service(CountingRepository(rows)).evaluate(evaluation)
    assert output["gift_observation"] is None
    assert output["context_label"] == "unavailable"
    assert output["gift_nifty_score"] is None


@pytest.mark.asyncio
async def test_future_fetched_date_only_rows_are_excluded():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    rows = [
        _price("NIFTY_50", date(2025, 1, 7), "24000", fetched_at=date(2025, 1, 9)),
        _price("GIFT_NIFTY", date(2025, 1, 7), "24100", fetched_at=date(2025, 1, 9)),
    ]
    output = await _service(CountingRepository(rows)).evaluate(evaluation)
    assert output["nifty_reference"] is None
    assert output["gift_observation"] is None


@pytest.mark.asyncio
async def test_malformed_or_future_provider_observation_falls_back_to_database():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    malformed_result = _result()
    assert malformed_result.data is not None
    malformed_result.data["price"] = "not-a-number"
    malformed = FakeGiftProvider("malformed", malformed_result)
    proxy_result = _result()
    assert proxy_result.data is not None
    proxy_result.data["currency"] = "INR"
    proxy = FakeGiftProvider("proxy", proxy_result)
    wrong_contract_result = _result()
    assert wrong_contract_result.data is not None
    wrong_contract_result.data["contract_symbol"] = "SGXNIFTY"
    wrong_contract = FakeGiftProvider("wrong_contract", wrong_contract_result)
    future = FakeGiftProvider(
        "future",
        _result(
            observed_at="2025-01-08T08:31:00+05:30",
            fetched_at="2025-01-08T08:31:01+05:30",
        ),
    )
    rows = [
        _price("NIFTY_50", date(2025, 1, 7), "24000"),
        _price("GIFT_NIFTY", date(2025, 1, 7), "24050"),
    ]
    output = await _service(
        CountingRepository(rows), (malformed, proxy, wrong_contract, future)
    ).evaluate(evaluation)
    assert malformed.calls == proxy.calls == wrong_contract.calls == future.calls == 1
    assert output["fallback_level"] == "exact_stored_observation"
    assert output["gift_observation"]["price"] == 24050.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evaluation", "expected"),
    [
        (datetime(2025, 1, 8, 8, 30, tzinfo=IST), "pre_open"),
        (datetime(2025, 1, 8, 12, 0, tzinfo=IST), "cash_market_open"),
        (datetime(2025, 1, 8, 16, 0, tzinfo=IST), "post_close"),
        (datetime(2025, 1, 11, 8, 30, tzinfo=IST), "weekend"),
    ],
)
async def test_output_context_labels(evaluation: datetime, expected: str):
    reference_date = indian_cash_reference_cutoff_date(evaluation)
    provider = FakeGiftProvider(
        "primary",
        _result(
            observed_at=(evaluation - timedelta(minutes=5)).isoformat(),
            fetched_at=(evaluation - timedelta(minutes=4)).isoformat(),
        ),
    )
    output = await _service(
        CountingRepository([_price("NIFTY_50", reference_date, "24000")]),
        (provider,),
    ).evaluate(evaluation)
    assert output["context_label"] == expected


@pytest.mark.asyncio
async def test_holiday_context_is_explicit_when_calendar_is_supplied():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    output = await _service(
        CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")]),
        (FakeGiftProvider("primary", _result()),),
        holidays=(date(2025, 1, 8),),
    ).evaluate(evaluation)
    assert output["context_label"] == "holiday"


@pytest.mark.asyncio
async def test_unavailable_context_preserves_provider_failure_provenance():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    provider = FakeGiftProvider("manual", _result(status="unavailable"))
    output = await _service(CountingRepository([]), (provider,)).evaluate(evaluation)
    assert output["context_label"] == "unavailable"
    assert output["fallback_level"] == "unavailable"
    assert output["confidence_score"] == 0.0
    assert output["provider_attempts"][0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_provenance_formulas_thresholds_and_limitations_are_exposed():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    output = await _service(
        CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")]),
        (FakeGiftProvider("primary", _result()),),
    ).evaluate(evaluation)
    assert output["provenance"]["instrument"] == {
        "index_code": "GIFT_NIFTY",
        "exchange": "NSE_IX",
        "instrument_type": "FUTIDX",
        "underlying": "NIFTY_50",
        "currency": "USD",
    }
    assert output["calculation"]["gap_points_formula"].startswith("gift_nifty_price")
    assert output["calculation"]["direction_thresholds_percent"]["gap_up"] == 0.2
    assert "date-only" in output["limitations"]["timestamp_replay"]
    assert "not a guaranteed" in output["limitations"]["prediction"]


@pytest.mark.asyncio
async def test_same_data_and_timestamp_are_deterministic():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")])
    service = _service(repo, (FakeGiftProvider("primary", _result()),))
    first = await service.evaluate(evaluation)
    second = await service.evaluate(evaluation)
    assert first == second


@pytest.mark.asyncio
async def test_mandatory_no_look_ahead_future_rows_leave_historical_output_unchanged():
    evaluation = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
    repo = CountingRepository(
        [
            _price("NIFTY_50", date(2025, 1, 7), "24000"),
            _price("GIFT_NIFTY", date(2025, 1, 7), "24120"),
        ]
    )
    service = _service(repo)
    before = await service.evaluate(evaluation)
    repo.rows.extend(
        [
            _price("NIFTY_50", date(2025, 1, 9), "999999"),
            _price("GIFT_NIFTY", date(2025, 1, 9), "1"),
        ]
    )
    after = await service.evaluate(evaluation)
    for key in (
        "nifty_reference",
        "indicated_gap_points",
        "indicated_gap_percent",
        "direction",
        "gift_nifty_score",
        "confidence_score",
    ):
        assert after[key] == before[key]


@pytest.mark.asyncio
async def test_aware_same_instant_in_utc_produces_india_context():
    evaluation = datetime(2025, 1, 8, 3, 0, tzinfo=UTC)  # 08:30 IST
    output = await _service(
        CountingRepository([_price("NIFTY_50", date(2025, 1, 7), "24000")]),
        (
            FakeGiftProvider(
                "primary",
                _result(
                    observed_at="2025-01-08T02:50:00+00:00",
                    fetched_at="2025-01-08T02:51:00+00:00",
                ),
            ),
        ),
    ).evaluate(evaluation)
    assert output["context_label"] == "pre_open"
    assert output["evaluation_timezone"] == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_naive_evaluation_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        await _service(CountingRepository([])).evaluate(datetime(2025, 1, 8, 8, 30))