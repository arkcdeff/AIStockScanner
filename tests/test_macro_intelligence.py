from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.macro_event_catalog import (
    CORE_EVENT_CODES,
    MACRO_EVENT_CATALOG,
    SECTOR_EVENT_CODES,
)
from app.market_intelligence.providers.base import ProviderResult
from app.market_intelligence.providers.macro_event_provider import MacroEventProvider
from app.models.macro_event import EconEvent
from app.services.macro_intelligence import (
    MacroIntelligence,
    classify_macro_direction,
)

IST = ZoneInfo("Asia/Kolkata")
US = ZoneInfo("America/New_York")
EVALUATION = datetime(2025, 2, 3, 10, 0, tzinfo=IST)


def _store_event(
    code: str,
    reference_time: datetime,
    *,
    actual: str | None = None,
    forecast: str | None = None,
    previous: str | None = None,
    fetched_at: datetime | None = None,
    freshness: str = "recent",
    known_since: datetime | None = None,
) -> EconEvent:
    known = known_since or reference_time
    return EconEvent(
        event_code=code,
        reference_time=reference_time,
        actual=float(Decimal(actual)) if actual is not None else None,
        forecast=float(Decimal(forecast)) if forecast is not None else None,
        previous=float(Decimal(previous)) if previous is not None else None,
        unit="PERCENT",
        source_name="fixture_macro",
        source_reference=f"manual://{code}",
        observed_at=known,
        available_at=known,
        fetched_at=fetched_at or known,
        freshness_status=freshness,
    )


class CountingEventRepository:
    def __init__(self, rows: list[EconEvent]) -> None:
        self.rows = rows
        self.calls = 0
        self.bounds = None

    async def get_range_for_codes(self, start_time, end_time, codes=()):
        self.calls += 1
        self.bounds = (start_time, end_time, codes)
        return [
            row
            for row in self.rows
            if start_time <= row.reference_time <= end_time
            and (not codes or row.event_code in codes)
        ]


def _service(repo: CountingEventRepository, providers=()) -> MacroIntelligence:
    return MacroIntelligence(
        SimpleNamespace(), providers=providers, event_repo=repo  # type: ignore[arg-type]
    )


class StaticEventProvider:
    supports_markets = ["INDIA_MACRO_EVENTS"]

    def __init__(self, name: str, result: ProviderResult) -> None:
        self.name = name
        self.result = result
        self.calls = []

    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _provider_result(code: str, reference: datetime, actual: str) -> ProviderResult:
    payload = {
        "event_code": code,
        "reference_time": reference,
        "actual": Decimal(actual),
        "forecast": Decimal(actual),
        "previous": Decimal(actual),
        "unit": "PERCENT",
        "source_reference": f"provider://{code}",
        "observed_at": reference,
        "available_at": reference,
    }
    return ProviderResult(
        provider_name="macro_provider",
        status="success",
        observed_at=reference.isoformat(),
        fetched_at=reference.isoformat(),
        market_date=reference.date().isoformat(),
        freshness="recent",
        data=[payload],
    )


def test_catalog_is_34_derived_not_hardcoded():
    assert len(MACRO_EVENT_CATALOG) == 34
    assert len(CORE_EVENT_CODES) == 18
    assert len(SECTOR_EVENT_CODES) == 16
    assert len(MACRO_EVENT_CATALOG) == len(CORE_EVENT_CODES | SECTOR_EVENT_CODES)


@pytest.mark.asyncio
async def test_direction_thresholds_are_exact_and_centralized():
    assert classify_macro_direction(60) == "strong_supportive"
    assert classify_macro_direction(25) == "supportive"
    assert classify_macro_direction(0) == "neutral"
    assert classify_macro_direction(-25) == "restrictive"
    assert classify_macro_direction(-60) == "strongly_restrictive"
    assert classify_macro_direction(None) is None


@pytest.mark.asyncio
async def test_empty_history_preserves_missing_and_one_bulk_query():
    repo = CountingEventRepository([])
    result = await _service(repo).evaluate(EVALUATION)
    assert repo.calls == 1
    assert result["macro_score"] is None
    assert result["direction"] is None
    assert result["freshness"] == "missing"
    assert result["confidence"] == 0.0
    assert result["completeness"] == 0.0
    all_lifecycles = {c["lifecycle"] for c in result["events"].values()}
    assert all_lifecycles == {"missing"}


@pytest.mark.asyncio
async def test_inverted_inflation_surprise_restricts_score():
    rows = [
        _store_event(
            "INDIA_CPI",
            datetime(2025, 1, 12, 17, 30, tzinfo=IST),
            actual="5.2",
            forecast="5.0",
            previous="5.0",
        )
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    inflation = result["domains"]["primary"]["inflation"]
    assert inflation["score"] < 0
    assert result["macro_score"] is not None and result["macro_score"] < 0
    assert result["events"]["INDIA_CPI"]["lifecycle"] == "known"


@pytest.mark.asyncio
async def test_positive_growth_surprise_is_supportive():
    rows = [
        _store_event(
            "INDIA_GDP",
            datetime(2025, 1, 20, 17, 30, tzinfo=IST),
            actual="6.8",
            forecast="6.4",
            previous="6.2",
        )
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    growth = result["domains"]["primary"]["growth"]
    assert growth["score"] > 0
    assert result["macro_score"] > 0


@pytest.mark.asyncio
async def test_contradictory_inflation_and_growth_still_yields_one_direction():
    rows = [
        _store_event(
            "INDIA_CPI",
            datetime(2025, 1, 12, 17, 30, tzinfo=IST),
            actual="5.2",
            forecast="5.0",
        ),
        _store_event(
            "INDIA_GDP",
            datetime(2025, 1, 20, 17, 30, tzinfo=IST),
            actual="6.8",
            forecast="6.4",
        ),
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    inflation = result["domains"]["primary"]["inflation"]["score"]
    growth = result["domains"]["primary"]["growth"]["score"]
    assert (inflation < 0) and (growth > 0)
    assert result["score_breakdown"]["normalized_weights"]["inflation"] > 0
    assert result["score_breakdown"]["normalized_weights"]["growth"] > 0
    assert 0 <= result["confidence"] <= 100


@pytest.mark.asyncio
async def test_upcoming_event_adds_no_direction_but_raises_risk():
    rows = [
        _store_event(
            "RBI_POLICY_DECISION",
            datetime(2025, 2, 7, 9, 0, tzinfo=IST),
            actual=None,
            known_since=EVALUATION - timedelta(days=3),
        ),
        _store_event(
            "INDIA_CPI",
            datetime(2025, 1, 12, 17, 30, tzinfo=IST),
            actual="4.0",
            forecast="4.2",
            previous="4.5",
        ),
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    rbi = result["events"]["RBI_POLICY_DECISION"]
    assert rbi["lifecycle"] == "upcoming"
    assert rbi["signal"] is None
    assert rbi["actual"] is None
    assert rbi["reference_time"] > EVALUATION.isoformat()
    assert result["upcoming_risk"] > 0
    assert "RBI_POLICY_DECISION" in [e["event_code"] for e in result["upcoming_events"]]
    # upcoming alone must never move the score positive from a restrictive base
    assert result["macro_score"] is not None


@pytest.mark.asyncio
async def test_scheduled_not_knowable_contributes_no_direction():
    rows = [
        _store_event(
            "US_CPI",
            datetime(2025, 1, 28, 8, 30, tzinfo=US),
            actual=None,
            forecast="3.0",
            previous="3.1",
            fetched_at=datetime(2025, 2, 5, 8, 30, tzinfo=US),
        )
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    us_cpi = result["events"]["US_CPI"]
    assert us_cpi["lifecycle"] == "scheduled_not_knowable"
    assert us_cpi["signal"] is None
    assert result["macro_score"] is None


@pytest.mark.asyncio
async def test_surprise_falls_back_to_previous_when_no_forecast():
    rows = [
        _store_event(
            "INDIA_BANK_CREDIT_GROWTH",
            datetime(2025, 1, 24, 17, 0, tzinfo=IST),
            actual="13.5",
            forecast=None,
            previous="12.0",
        )
    ]
    result = await _service(CountingEventRepository(rows)).evaluate(EVALUATION)
    bank = result["events"]["INDIA_BANK_CREDIT_GROWTH"]
    assert bank["lifecycle"] == "known"
    assert bank["surprise"] > 0
    assert bank["signal"] > 0


@pytest.mark.asyncio
async def test_future_stored_row_cannot_change_historical_replay():
    repo = CountingEventRepository(
        [
            _store_event(
                "INDIA_CPI",
                datetime(2025, 1, 12, 17, 30, tzinfo=IST),
                actual="5.0",
                forecast="5.2",
            )
        ]
    )
    service = _service(repo)
    before = await service.evaluate(EVALUATION)
    repo.rows.append(
        _store_event(
            "INDIA_GDP",
            datetime(2025, 6, 20, 17, 30, tzinfo=IST),
            actual="9.9",
            forecast="5.0",
        )
    )
    after = await service.evaluate(EVALUATION)
    assert after == before


@pytest.mark.asyncio
async def test_future_available_at_is_excluded_from_direction():
    repo = CountingEventRepository(
        [
            _store_event(
                "INDIA_CPI",
                datetime(2025, 1, 12, 17, 30, tzinfo=IST),
                actual="5.0",
                forecast="5.2",
            ),
            _store_event(
                "US_CPI",
                datetime(2025, 1, 12, 8, 30, tzinfo=US),
                actual="9.9",
                forecast="5.0",
                fetched_at=datetime(2025, 2, 20, 8, 30, tzinfo=US),
            ),
        ]
    )
    result = await _service(repo).evaluate(EVALUATION)
    assert result["events"]["US_CPI"]["lifecycle"] == "scheduled_not_knowable"
    assert result["events"]["US_CPI"]["signal"] is None


@pytest.mark.asyncio
async def test_macro_provider_unavailable_by_default_and_has_no_http():
    provider = MacroEventProvider()
    result = await provider.fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "automation_not_configured"
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_provider_rows_merge_with_stored_and_are_called_once():
    provider = StaticEventProvider(
        "macro_provider",
        _provider_result(
            "INDIA_CPI",
            datetime(2025, 1, 12, 17, 30, tzinfo=IST),
            "5.0",
        ),
    )
    stored = [
        _store_event(
            "INDIA_GDP",
            datetime(2025, 1, 20, 17, 30, tzinfo=IST),
            actual="6.8",
            forecast="6.4",
        )
    ]
    result = await _service(
        CountingEventRepository(stored), (provider,)
    ).evaluate(EVALUATION)
    assert len(provider.calls) == 1
    assert provider.calls[0]["evaluation_at"] == EVALUATION
    assert result["events"]["INDIA_CPI"]["lifecycle"] == "known"
    assert result["events"]["INDIA_GDP"]["lifecycle"] == "known"
    assert result["provider_attempts"][0]["provider_name"] == "macro_provider"


@pytest.mark.asyncio
async def test_naive_evaluation_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        await _service(CountingEventRepository([])).evaluate(datetime(2025, 2, 3, 10, 0))


@pytest.mark.asyncio
async def test_macro_score_bounds_and_direction_never_exceed_range():
    # A large positive GDP surprise should still clamp at/under +100.
    big = CountingEventRepository(
        [
            _store_event(
                "INDIA_GDP",
                datetime(2025, 1, 20, 17, 30, tzinfo=IST),
                actual="20.0",
                forecast="6.0",
            )
        ]
    )
    result = await _service(big).evaluate(EVALUATION)
    assert -100 <= result["macro_score"] <= 100
    assert result["macro_score"] > 0


@pytest.mark.asyncio
async def test_upcoming_event_does_not_dampen_empty_score_into_direction():
    repo = CountingEventRepository(
        [
            _store_event(
                "FED_POLICY_DECISION",
                datetime(2025, 2, 5, 18, 0, tzinfo=US),
                actual=None,
                known_since=EVALUATION - timedelta(days=1),
            )
        ]
    )
    result = await _service(repo).evaluate(EVALUATION)
    assert result["macro_score"] is None
    assert result["direction"] is None
