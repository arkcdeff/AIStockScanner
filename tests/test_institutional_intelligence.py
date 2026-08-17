from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.providers.base import ProviderResult
from app.market_intelligence.providers.institutional_flow_provider import (
    InstitutionalFlowProvider,
)
from app.models.institutional_flow import InstitutionalFlow
from app.services.institutional_intelligence import (
    InstitutionalIntelligence,
    classify_institutional_direction,
)

IST = ZoneInfo("Asia/Kolkata")
EVALUATION = datetime(2025, 2, 3, 10, 0, tzinfo=IST)


def _flow(
    investor_type: str,
    trade_date: date,
    buy: str,
    sell: str,
    *,
    stored_net: str = "999999",
    freshness: str = "recent",
) -> InstitutionalFlow:
    return InstitutionalFlow(
        trade_date=trade_date,
        investor_type=investor_type,
        buy_value=float(Decimal(buy)),
        sell_value=float(Decimal(sell)),
        net_value=float(Decimal(stored_net)),
        source_name="fixture_institutional",
        observed_at=trade_date,
        fetched_at=trade_date,
        freshness_status=freshness,
    )


class CountingFlowRepository:
    def __init__(self, rows: list[InstitutionalFlow]) -> None:
        self.rows = rows
        self.calls = 0
        self.bounds = None

    async def get_range_for_types(self, start_date, end_date, investor_types):
        self.calls += 1
        self.bounds = (start_date, end_date, investor_types)
        return [row for row in self.rows if start_date <= row.trade_date <= end_date]


def _service(repo: CountingFlowRepository, providers=()) -> InstitutionalIntelligence:
    return InstitutionalIntelligence(
        SimpleNamespace(), providers=providers, flow_repo=repo  # type: ignore[arg-type]
    )


class StaticFlowProvider:
    supports_markets = ["INDIA_INSTITUTIONAL_FLOWS"]

    def __init__(self, name: str, result: ProviderResult) -> None:
        self.name = name
        self.result = result
        self.calls = []

    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RaisingFlowProvider(StaticFlowProvider):
    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("provider exploded")


def _provider_flow_result(
    provider_name: str,
    *,
    investor_types=("FII", "DII"),
    source_label_override: str | None = None,
):
    observed = datetime(2025, 2, 2, 18, 0, tzinfo=IST)
    payload = []
    for investor_type in investor_types:
        payload.append(
            {
                "trade_date": date(2025, 2, 2),
                "investor_type": investor_type,
                "source_investor_label": source_label_override
                or ("FII/FPI" if investor_type == "FII" else "DII"),
                "buy_value": Decimal("1200"),
                "sell_value": Decimal("200"),
                "net_value": Decimal("-999999"),
                "unit": "INR_CRORE",
                "dataset": "PROVISIONAL_CASH_MARKET_INSTITUTIONAL_ACTIVITY",
                "source_reference": f"provider://{provider_name}/{investor_type}",
                "observed_at": observed,
                "available_at": observed,
            }
        )
    return ProviderResult(
        provider_name=provider_name,
        status="success",
        observed_at=observed.isoformat(),
        fetched_at=observed.isoformat(),
        market_date="2025-02-02",
        freshness="recent",
        data=payload,
    )


def test_direction_thresholds_are_exact_and_centralized():
    assert classify_institutional_direction(60) == "strong_buying"
    assert classify_institutional_direction(25) == "buying"
    assert classify_institutional_direction(0) == "neutral"
    assert classify_institutional_direction(-25) == "selling"
    assert classify_institutional_direction(-60) == "strong_selling"
    assert classify_institutional_direction(None) is None


@pytest.mark.asyncio
async def test_empty_history_preserves_missing_values_and_one_bulk_query():
    repo = CountingFlowRepository([])
    result = await _service(repo).evaluate(EVALUATION)
    assert repo.calls == 1
    assert result["institutional_score"] is None
    assert result["direction"] is None
    assert result["freshness"] == "missing"
    assert result["confidence"] == result["completeness"] == 0.0
    assert result["combined"]["daily_net_value"] is None


@pytest.mark.asyncio
async def test_net_is_recomputed_and_rollups_include_1_3_5_20_sessions():
    rows = []
    cutoff = date(2025, 2, 2)
    for investor_type in ("FII", "DII"):
        for offset in range(20):
            rows.append(_flow(investor_type, cutoff - timedelta(days=offset), "1200", "200"))
    result = await _service(CountingFlowRepository(rows)).evaluate(EVALUATION)
    fii = result["investors"]["FII"]
    assert fii["net_value"] == 1000.0
    assert fii["cumulative"] == {1: 1000.0, 3: 3000.0, 5: 5000.0, 20: 20000.0}
    assert result["combined"]["daily_net_value"] == 2000.0
    assert result["combined"]["cumulative"][20] == 40000.0
    assert 0 < result["institutional_score"] <= 100
    assert 0 <= result["confidence"] <= 100
    assert result["completeness"] == 100.0


@pytest.mark.asyncio
async def test_distinct_fii_dii_weights_and_partial_history_quality_are_exposed():
    rows = [
        _flow("FII", date(2025, 2, 2), "2000", "0"),
        _flow("DII", date(2025, 2, 2), "0", "2000", freshness="stale"),
    ]
    result = await _service(CountingFlowRepository(rows)).evaluate(EVALUATION)
    assert result["score_breakdown"]["configured_investor_weights"] == {
        "FII": 0.65,
        "DII": 0.35,
    }
    assert result["institutional_score"] > 0
    assert result["completeness"] == 25.0
    assert result["freshness"] == "stale"


@pytest.mark.asyncio
async def test_mandatory_institutional_future_invariance():
    repo = CountingFlowRepository(
        [_flow(kind, date(2025, 2, 2), "1200", "200") for kind in ("FII", "DII")]
    )
    service = _service(repo)
    before = await service.evaluate(EVALUATION)
    repo.rows.extend(
        [_flow(kind, date(2025, 2, 4), "0", "999999") for kind in ("FII", "DII")]
    )
    after = await service.evaluate(EVALUATION)
    assert after == before


@pytest.mark.asyncio
async def test_primary_provider_is_called_once_and_provider_net_is_ignored():
    provider = StaticFlowProvider("primary_flow", _provider_flow_result("primary_flow"))
    result = await _service(CountingFlowRepository([]), (provider,)).evaluate(EVALUATION)
    assert len(provider.calls) == 1
    assert provider.calls[0]["evaluation_at"] == EVALUATION
    assert result["investors"]["FII"]["net_value"] == 1000.0
    assert result["investors"]["DII"]["net_value"] == 1000.0
    assert result["fallback_by_investor"] == {
        "FII": "primary_provider",
        "DII": "primary_provider",
    }


@pytest.mark.asyncio
async def test_partial_primary_uses_secondary_for_missing_investor_only():
    primary = StaticFlowProvider(
        "primary_flow", _provider_flow_result("primary_flow", investor_types=("FII",))
    )
    secondary = StaticFlowProvider(
        "secondary_flow", _provider_flow_result("secondary_flow", investor_types=("DII",))
    )
    result = await _service(CountingFlowRepository([]), (primary, secondary)).evaluate(EVALUATION)
    assert len(primary.calls) == len(secondary.calls) == 1
    assert result["fallback_by_investor"] == {
        "FII": "primary_provider",
        "DII": "secondary_provider",
    }
    assert result["investors"]["FII"]["source_name"] == "primary_flow"
    assert result["investors"]["DII"]["source_name"] == "secondary_flow"


@pytest.mark.asyncio
async def test_malformed_provider_payload_falls_to_exact_and_stale_stored_data():
    malformed = StaticFlowProvider(
        "malformed_flow",
        _provider_flow_result("malformed_flow", source_label_override="WRONG"),
    )
    rows = [
        _flow("FII", date(2025, 2, 2), "1000", "100"),
        _flow("DII", date(2025, 2, 1), "1000", "100", freshness="recent"),
    ]
    result = await _service(CountingFlowRepository(rows), (malformed,)).evaluate(EVALUATION)
    assert result["fallback_by_investor"] == {
        "FII": "exact_stored_observation",
        "DII": "stale_stored_observation",
    }
    assert result["investors"]["FII"]["freshness"] == "recent"
    assert result["investors"]["DII"]["freshness"] == "stale"
    assert result["provider_attempts"][0]["provider_name"] == "malformed_flow"


@pytest.mark.asyncio
async def test_provider_exception_is_recorded_before_secondary_flow_fallback():
    raising = RaisingFlowProvider(
        "raising_primary",
        ProviderResult(
            provider_name="unused",
            status="unavailable",
            freshness="missing",
        ),
    )
    secondary = StaticFlowProvider(
        "secondary_flow", _provider_flow_result("secondary_flow")
    )
    result = await _service(
        CountingFlowRepository([]), (raising, secondary)
    ).evaluate(EVALUATION)
    assert result["fallback_by_investor"] == {
        "FII": "secondary_provider",
        "DII": "secondary_provider",
    }
    assert result["provider_attempts"][0]["error_code"] == "provider_exception"
    assert result["provider_attempts"][1]["status"] == "success"


@pytest.mark.asyncio
async def test_institutional_provider_is_unavailable_by_default_and_has_no_http():
    provider = InstitutionalFlowProvider()
    result = await provider.fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "automation_not_configured"
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_institutional_provider_validates_identity_unit_and_recomputes_net(tmp_path: Path):
    path = tmp_path / "flows.csv"
    path.write_text(
        "observed_at,available_at,trade_date,investor_type,source_investor_label,"
        "buy_value,sell_value,unit,dataset,source_reference\n"
        "2025-02-02T18:00:00+05:30,2025-02-02T18:01:00+05:30,2025-02-02,FII,"
        "FII/FPI,1200.25,200.10,INR_CRORE,"
        "PROVISIONAL_CASH_MARKET_INSTITUTIONAL_ACTIVITY,manual://nse-export\n",
        encoding="utf-8",
    )
    result = await InstitutionalFlowProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "success"
    assert result.data[0]["net_value"] == Decimal("1000.15")
    assert result.data[0]["unit"] == "INR_CRORE"


@pytest.mark.asyncio
async def test_future_malformed_flow_csv_row_cannot_change_historical_replay(tmp_path: Path):
    path = tmp_path / "flows_with_future_bad.csv"
    header = (
        "observed_at,available_at,trade_date,investor_type,source_investor_label,"
        "buy_value,sell_value,unit,dataset,source_reference\n"
    )
    known = (
        "2025-02-02T18:00:00+05:30,2025-02-02T18:01:00+05:30,2025-02-02,FII,"
        "FII/FPI,1200,200,INR_CRORE,PROVISIONAL_CASH_MARKET_INSTITUTIONAL_ACTIVITY,"
        "manual://known\n"
    )
    path.write_text(header + known, encoding="utf-8")
    provider = InstitutionalFlowProvider(path)
    before = await provider.fetch(
        evaluation_at=EVALUATION,
        start_date=date(2025, 2, 1),
        end_date=date(2025, 2, 2),
    )
    future_bad = (
        "2025-02-05T18:00:00+05:30,2025-02-05T18:01:00+05:30,2025-02-05,FII,"
        "WRONG,BAD,BAD,WRONG,WRONG,manual://future-bad\n"
    )
    path.write_text(header + known + future_bad, encoding="utf-8")
    after = await provider.fetch(
        evaluation_at=EVALUATION,
        start_date=date(2025, 2, 1),
        end_date=date(2025, 2, 2),
    )
    assert after == before


@pytest.mark.asyncio
async def test_naive_evaluation_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        await _service(CountingFlowRepository([])).evaluate(datetime(2025, 2, 3, 10, 0))