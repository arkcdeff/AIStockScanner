from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.market_intelligence.index_catalog import (
    GIFT_NIFTY_INDEX_CODE,
    GLOBAL_INTELLIGENCE_INDEX_CODES,
    INDEX_CATALOG_BY_CODE,
)
from app.market_intelligence.providers.gift_nifty_provider import GiftNiftyProvider

IST = ZoneInfo("Asia/Kolkata")
EVALUATION = datetime(2025, 1, 8, 8, 30, tzinfo=IST)
HEADERS = (
    "observed_at,available_at,market_date,price,contract_symbol,expiry_date,"
    "exchange,instrument_type,underlying,currency,source_reference\n"
)


def _row(
    *,
    observed_at: str = "2025-01-08T08:15:00+05:30",
    available_at: str = "2025-01-08T08:16:00+05:30",
    market_date: str = "2025-01-08",
    price: str = "24125.50",
    contract_symbol: str = "NIFTY25JANFUT",
    expiry_date: str = "2025-01-30",
    exchange: str = "NSE_IX",
    instrument_type: str = "FUTIDX",
    underlying: str = "NIFTY_50",
    currency: str = "USD",
    source_reference: str = "manual://licensed-export/gift-nifty.csv",
) -> str:
    return (
        f"{observed_at},{available_at},{market_date},{price},{contract_symbol},"
        f"{expiry_date},{exchange},{instrument_type},{underlying},{currency},"
        f"{source_reference}\n"
    )


def _csv(tmp_path: Path, body: str, *, headers: str = HEADERS) -> Path:
    path = tmp_path / "gift_nifty.csv"
    path.write_text(headers + body, encoding="utf-8")
    return path


def test_catalog_identifies_real_nse_ix_futures_without_changing_global_set():
    entry = INDEX_CATALOG_BY_CODE[GIFT_NIFTY_INDEX_CODE]
    assert entry.name == "GIFT Nifty Futures"
    assert entry.exchange == "NSE_IX"
    assert entry.category == "index_futures"
    assert entry.currency == "USD"
    assert entry.timezone == "Asia/Kolkata"
    assert entry.provider_symbol == "NIFTY"
    assert GIFT_NIFTY_INDEX_CODE not in GLOBAL_INTELLIGENCE_INDEX_CODES


def test_provider_supports_only_gift_nifty_and_has_no_http_client():
    provider = GiftNiftyProvider()
    assert provider.supports_indices == [GIFT_NIFTY_INDEX_CODE]
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_unconfigured_provider_truthfully_returns_unavailable():
    result = await GiftNiftyProvider().fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.freshness == "missing"
    assert result.error_code == "automation_not_configured"
    assert "licensed" in (result.message or "")


@pytest.mark.asyncio
async def test_unsupported_index_is_rejected_before_file_access():
    result = await GiftNiftyProvider("missing.csv").fetch(
        index_code="NIFTY_50", evaluation_at=EVALUATION
    )
    assert result.status == "unavailable"
    assert result.error_code == "unsupported_index"


@pytest.mark.asyncio
async def test_evaluation_timestamp_is_required_and_must_be_aware():
    missing = await GiftNiftyProvider().fetch()
    naive = await GiftNiftyProvider().fetch(evaluation_at=datetime(2025, 1, 8, 8, 30))
    assert missing.error_code == "invalid_evaluation_at"
    assert naive.error_code == "naive_evaluation_at"
    assert missing.status == naive.status == "failed"


@pytest.mark.asyncio
async def test_missing_manual_file_is_unavailable_not_success(tmp_path: Path):
    result = await GiftNiftyProvider(tmp_path / "absent.csv").fetch(
        evaluation_at=EVALUATION
    )
    assert result.status == "unavailable"
    assert result.error_code == "manual_file_not_found"


@pytest.mark.asyncio
async def test_valid_manual_csv_normalizes_exact_instrument_and_provenance(tmp_path: Path):
    result = await GiftNiftyProvider(_csv(tmp_path, _row())).fetch(
        evaluation_at=EVALUATION
    )
    assert result.status == "success"
    assert result.observed_at == "2025-01-08T08:15:00+05:30"
    assert result.fetched_at == "2025-01-08T08:16:00+05:30"
    assert result.market_date == "2025-01-08"
    assert str(result.data["price"]) == "24125.50"
    assert result.data["exchange"] == "NSE_IX"
    assert result.data["instrument_type"] == "FUTIDX"
    assert result.data["underlying"] == "NIFTY_50"
    assert result.data["currency"] == "USD"
    assert result.data["contract_symbol"] == "NIFTY25JANFUT"
    assert result.data["source_reference"].startswith("manual://")


@pytest.mark.asyncio
async def test_latest_observation_knowable_at_evaluation_is_selected(tmp_path: Path):
    body = _row(
        observed_at="2025-01-08T07:00:00+05:30",
        available_at="2025-01-08T07:01:00+05:30",
        price="24050",
    ) + _row(price="24125")
    result = await GiftNiftyProvider(_csv(tmp_path, body)).fetch(
        evaluation_at=EVALUATION
    )
    assert result.data["price"] == 24125


@pytest.mark.asyncio
async def test_future_observation_is_not_visible(tmp_path: Path):
    path = _csv(
        tmp_path,
        _row(
            observed_at="2025-01-08T08:31:00+05:30",
            available_at="2025-01-08T08:31:01+05:30",
        ),
    )
    result = await GiftNiftyProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "no_knowable_observation"


@pytest.mark.asyncio
async def test_observed_quote_with_future_availability_is_not_visible(tmp_path: Path):
    path = _csv(
        tmp_path,
        _row(available_at="2025-01-08T08:31:00+05:30"),
    )
    result = await GiftNiftyProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "unavailable"
    assert result.error_code == "no_knowable_observation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exchange", "NSE"),
        ("instrument_type", "INDEX"),
        ("underlying", "NASDAQ_100"),
        ("currency", "INR"),
        ("contract_symbol", "SGXNIFTY"),
    ],
)
async def test_proxy_or_wrong_instrument_identity_is_rejected(
    tmp_path: Path, field: str, value: str
):
    path = _csv(tmp_path, _row(**{field: value}))
    result = await GiftNiftyProvider(path).fetch(evaluation_at=EVALUATION)
    assert result.status == "failed"
    assert result.error_code == "manual_file_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
async def test_invalid_prices_are_rejected(tmp_path: Path, price: str):
    result = await GiftNiftyProvider(_csv(tmp_path, _row(price=price))).fetch(
        evaluation_at=EVALUATION
    )
    assert result.status == "failed"
    assert result.error_code == "manual_file_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_at", "2025-01-08T08:15:00"),
        ("available_at", "2025-01-08T08:16:00"),
    ],
)
async def test_manual_timestamps_must_be_timezone_aware(
    tmp_path: Path, field: str, value: str
):
    result = await GiftNiftyProvider(_csv(tmp_path, _row(**{field: value}))).fetch(
        evaluation_at=EVALUATION
    )
    assert result.status == "failed"
    assert "timezone-aware" in (result.message or "")


@pytest.mark.asyncio
async def test_availability_cannot_precede_observation(tmp_path: Path):
    result = await GiftNiftyProvider(
        _csv(tmp_path, _row(available_at="2025-01-08T08:14:00+05:30"))
    ).fetch(evaluation_at=EVALUATION)
    assert result.status == "failed"
    assert "cannot precede" in (result.message or "")


@pytest.mark.asyncio
async def test_missing_required_column_is_rejected(tmp_path: Path):
    headers = HEADERS.replace("source_reference,", "source_reference,").replace(
        ",source_reference\n", "\n"
    )
    result = await GiftNiftyProvider(_csv(tmp_path, _row(), headers=headers)).fetch(
        evaluation_at=EVALUATION
    )
    assert result.status == "failed"
    assert "required columns missing" in (result.message or "")


@pytest.mark.asyncio
async def test_empty_manual_file_is_rejected(tmp_path: Path):
    result = await GiftNiftyProvider(_csv(tmp_path, "")).fetch(evaluation_at=EVALUATION)
    assert result.status == "failed"
    assert "no observations" in (result.message or "")


@pytest.mark.asyncio
async def test_invalid_market_or_expiry_date_is_rejected(tmp_path: Path):
    market = await GiftNiftyProvider(_csv(tmp_path, _row(market_date="08-01-2025"))).fetch(
        evaluation_at=EVALUATION
    )
    expiry = await GiftNiftyProvider(_csv(tmp_path, _row(expiry_date="30-01-2025"))).fetch(
        evaluation_at=EVALUATION
    )
    assert market.status == expiry.status == "failed"