from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.providers.macro_event_provider import MacroEventProvider

IST = ZoneInfo("Asia/Kolkata")
EVALUATION = datetime(2025, 2, 3, 10, 0, tzinfo=IST)

HEADER = (
    "event_code,reference_time,actual,forecast,previous,unit,"
    "observed_at,available_at,source_reference\n"
)


def _row(
    code: str = "INDIA_CPI",
    reference: str = "2025-01-12T17:30:00+05:30",
    actual: str = "5.0",
    forecast: str = "5.2",
    previous: str = "5.1",
    unit: str = "PERCENT",
    observed: str = "2025-01-12T17:31:00+05:30",
    available: str = "2025-01-12T17:32:00+05:30",
    ref: str = "manual://mospl-cpi",
) -> str:
    return (
        f"{code},{reference},{actual},{forecast},{previous},{unit},"
        f"{observed},{available},{ref}\n"
    )


@pytest.mark.asyncio
async def test_provider_is_unavailable_by_default_and_has_no_http():
    provider = MacroEventProvider()
    result = await provider.fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "automation_not_configured"
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_provider_rejects_naive_evaluation():
    provider = MacroEventProvider()
    result = await provider.fetch(evaluation_at=datetime(2025, 2, 3, 10, 0))
    assert result.status == "failed"
    assert result.error_code == "naive_evaluation_at"


@pytest.mark.asyncio
async def test_provider_rejects_unsupported_event_code():
    provider = MacroEventProvider()
    result = await provider.fetch(
        evaluation_at=EVALUATION, event_codes=("NOT_A_CODE",)
    )
    assert result.status == "failed"
    assert result.error_code == "unsupported_event_code"


@pytest.mark.asyncio
async def test_provider_normalizes_and_knows_row(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text(HEADER + _row(), encoding="utf-8")
    result = await MacroEventProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "success"
    assert result.data[0]["event_code"] == "INDIA_CPI"
    assert result.data[0]["actual"] == Decimal("5.0")
    assert result.data[0]["unit"] == "PERCENT"
    assert result.data[0]["source_reference"] == "manual://mospl-cpi"


@pytest.mark.asyncio
async def test_provider_validates_unit_and_rejects_proxy(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text(HEADER + _row(unit="WRONG"), encoding="utf-8")
    result = await MacroEventProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "failed"
    assert result.error_code == "manual_file_invalid"


@pytest.mark.asyncio
async def test_provider_ignores_future_rows_for_historical_replay(tmp_path: Path):
    path = tmp_path / "events.csv"
    known = _row(
        reference="2025-01-12T17:30:00+05:30",
        actual="5.0",
    )
    path.write_text(HEADER + known, encoding="utf-8")
    provider = MacroEventProvider(path)
    before = await provider.fetch(
        evaluation_at=EVALUATION,
        start_time=datetime(2025, 1, 1, tzinfo=IST),
        end_time=datetime(2025, 2, 3, tzinfo=IST),
    )
    future_row = _row(
        reference="2025-04-12T17:30:00+05:30",
        actual="9.9",
        ref="manual://future",
    )
    path.write_text(HEADER + known + future_row, encoding="utf-8")
    after = await provider.fetch(
        evaluation_at=EVALUATION,
        start_time=datetime(2025, 1, 1, tzinfo=IST),
        end_time=datetime(2025, 2, 3, tzinfo=IST),
    )
    assert after == before
    assert all(item["actual"] != Decimal("9.9") for item in after.data)


@pytest.mark.asyncio
async def test_provider_requires_aware_timestamps_in_csv(tmp_path: Path):
    path = tmp_path / "events_bad.csv"
    path.write_text(HEADER + _row(observed="2025-01-12T17:31:00"), encoding="utf-8")
    result = await MacroEventProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "failed"
    assert result.error_code == "manual_file_invalid"


@pytest.mark.asyncio
async def test_provider_unavailable_when_file_missing(tmp_path: Path):
    provider = MacroEventProvider(tmp_path / "does_not_exist.csv")
    result = await provider.fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "manual_file_not_found"
