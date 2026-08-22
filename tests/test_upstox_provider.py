"""Unit Tests for Upstox Historical & Live Providers — Sprint 19.1G-QA-MP."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.providers import (
    FreshnessStatus,
    NormalizedQuote,
    PriceType,
    ProviderCapability,
    ProviderMode,
    UpstoxHistoricalProvider,
    UpstoxLiveProvider,
    redact_secrets,
)


def test_upstox_provider_capabilities():
    """Test Upstox provider identity, mode, and capability metadata."""
    hist = UpstoxHistoricalProvider(token="test_token", mock_mode=True)
    assert hist.provider_id == "UPSTOX_HISTORICAL"
    assert hist.provider_mode == ProviderMode.HISTORICAL_ONLY
    assert ProviderCapability.CASH_EQUITY in hist.capabilities
    assert ProviderCapability.OPTIONS in hist.capabilities

    live = UpstoxLiveProvider(token="test_token", mock_mode=True)
    assert live.provider_id == "UPSTOX_LIVE"
    assert live.provider_mode == ProviderMode.LIVE_ONLY
    assert ProviderCapability.FUTURES in live.capabilities


def test_upstox_token_redaction_and_security():
    """Test secret token redaction across logs, exception messages, and bearer headers."""
    token = "secret_upstox_access_token_123456789"
    provider = UpstoxHistoricalProvider(token=token, mock_mode=True)
    headers = provider._get_headers()
    assert headers["Authorization"] == f"Bearer {token}"

    err_msg = f"HTTP 401 Unauthorized for Authorization: Bearer {token}"
    redacted = redact_secrets(err_msg)
    assert token not in redacted
    assert "Bearer [REDACTED_TOKEN]" in redacted or "REDACTED" in redacted


@pytest.mark.asyncio
async def test_upstox_historical_candle_parsing():
    """Test parsing raw Upstox candle payload into normalized bars."""
    provider = UpstoxHistoricalProvider(mock_mode=True)
    payload = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-08-10T00:00:00+05:30", 1500.0, 1520.0, 1490.0, 1515.0, 250000, 0],
                ["2026-08-09T00:00:00+05:30", 1480.0, 1505.0, 1475.0, 1495.0, 180000, 0],
            ]
        },
    }
    bars = provider._parse_upstox_candle_payload("RELIANCE", payload)
    assert len(bars) == 2
    b1 = bars[0]
    assert b1.symbol == "RELIANCE"
    assert b1.close == Decimal("1515.0")
    assert b1.price_type == PriceType.UPSTOX_HISTORICAL_CLOSE


@pytest.mark.asyncio
async def test_upstox_live_quote_parsing():
    """Test parsing raw Upstox live quote payload into normalized quotes."""
    provider = UpstoxLiveProvider(mock_mode=True)
    payload = {
        "status": "success",
        "data": {
            "NSE_EQ:TCS": {
                "last_price": 4200.50,
                "cp": 4150.00,
                "volume": 850000,
                "oi": 0,
                "ohlc": {"open": 4160.0, "high": 4210.0, "low": 4155.0, "close": 4200.50},
                "depth": {
                    "buy": [{"price": 4200.00, "quantity": 500}],
                    "sell": [{"price": 4200.50, "quantity": 750}],
                },
            }
        },
    }
    quote = provider._parse_upstox_quote_item("TCS", payload)
    assert quote is not None
    assert quote.symbol == "TCS"
    assert quote.ltp == Decimal("4200.5")
    assert quote.bid == Decimal("4200.0")
    assert quote.ask == Decimal("4200.5")
    assert quote.bid_qty == 500
    assert quote.ask_qty == 750
    assert quote.price_type == PriceType.UPSTOX_LIVE_LTP


def test_upstox_freshness_status_evaluation():
    """Test quote freshness status evaluation (LIVE vs DELAYED vs STALE vs DISCONNECTED)."""
    provider = UpstoxLiveProvider(mock_mode=True)
    provider._connected = True

    now = datetime.now(UTC)
    fresh_tick = now - timedelta(seconds=2)
    delayed_tick = now - timedelta(seconds=15)
    stale_tick = now - timedelta(seconds=60)

    assert provider._evaluate_freshness(fresh_tick) == FreshnessStatus.LIVE
    assert provider._evaluate_freshness(delayed_tick) == FreshnessStatus.DELAYED
    assert provider._evaluate_freshness(stale_tick) == FreshnessStatus.STALE

    provider._connected = False
    assert provider._evaluate_freshness(fresh_tick) == FreshnessStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_upstox_tick_subscription_mock_stream():
    """Test tick subscription stream emitting normalized quote objects."""
    provider = UpstoxLiveProvider(mock_mode=True)
    ticks = []
    async for tick in provider.subscribe_ticks(["SBIN", "INFY"]):
        ticks.append(tick)
    assert len(ticks) == 2
    assert ticks[0].symbol == "SBIN"
    assert ticks[1].symbol == "INFY"
    assert ticks[0].price_type == PriceType.UPSTOX_LIVE_LTP


def test_upstox_source_metadata_and_greek_preservation():
    """Test transport origin metadata and positive PE gamma preservation."""
    provider = UpstoxLiveProvider(mock_mode=True)
    payload = {
        "data": [
            {
                "strike_price": 24250.0,
                "expiry": "2026-08-25",
                "call_options": {
                    "instrument_key": "NSE_FO|61671",
                    "trading_symbol": "NIFTY 24250 CE 25 AUG 26",
                    "market_data": {"ltp": 96.25, "volume": 1000, "oi": 5000},
                    "option_greeks": {"iv": 7.87, "delta": 0.5641, "gamma": 0.002, "theta": -9.86, "vega": 10.001},
                },
                "put_options": {
                    "instrument_key": "NSE_FO|61684",
                    "trading_symbol": "NIFTY 24250 PE 25 AUG 26",
                    "market_data": {"ltp": 63.50, "volume": 2000, "oi": 6000},
                    "option_greeks": {"iv": 7.68, "delta": -0.4345, "gamma": 0.002, "theta": -9.6152, "vega": 9.9948},
                },
            }
        ]
    }
    chain = provider._parse_upstox_live_option_chain("NIFTY", payload)
    assert chain.source_name == "UPSTOX_REST"
    assert len(chain.calls) == 1
    assert len(chain.puts) == 1

    pe_quote = chain.puts[0]
    assert pe_quote.source_name == "UPSTOX_REST"
    assert pe_quote.raw_provider_meta["transport_origin"] == "UPSTOX_REST"
    assert pe_quote.raw_provider_meta["instrument_key"] == "NSE_FO|61684"

    # Verify Greek preservation: PE gamma is preserved as positive (+0.002) without negation
    pe_greeks = pe_quote.raw_provider_meta["option_greeks"]
    assert pe_greeks["gamma"] == 0.002
    assert pe_greeks["origin"] == "UPSTOX_PROVIDER_SUPPLIED"


def test_upstox_market_closed_actionability_suppression():
    """Test market closed state overrides recency to suppress live actionable decision."""
    provider = UpstoxLiveProvider(mock_mode=True)
    # Quote timestamp is fresh (received now), but market is closed
    now = datetime.now(UTC)
    quote = NormalizedQuote(
        symbol="NIFTY",
        instrument_type="INDEX",
        ltp=Decimal("24252.0"),
        timestamp=now,
        price_type=PriceType.UPSTOX_LIVE_LTP,
        source_name="UPSTOX_REST",
        provider_id="UPSTOX_LIVE",
        freshness_status=FreshnessStatus.LIVE,
    )
    # Outside trading hours (e.g. 21:00 IST), is_quote_actionable returns False, MARKET_CLOSED
    provider._is_market_open = lambda _dt: False
    is_act, reason = provider.is_quote_actionable(quote)
    assert is_act is False
    assert reason == "MARKET_CLOSED"

