"""Unit & Integration Tests for Multi-Source Market Data Architecture — Sprint 19.1G-QA-MP."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.providers.models import (
    FreshnessStatus,
    NormalizedBar,
    NormalizedQuote,
    PriceType,
)
from app.providers.nse_historical_provider import NSEHistoricalProvider
from app.providers.registry import ProviderRegistry
from app.providers.upstox_historical_provider import (
    UpstoxHistoricalProvider,
    redact_secrets,
)
from app.providers.upstox_live_provider import UpstoxLiveProvider


def test_provider_models_normalization():
    """Test normalized domain objects (NormalizedBar, NormalizedQuote, NormalizedOptionChain)."""
    bar = NormalizedBar(
        symbol="SBIN",
        trade_date=date(2026, 8, 10),
        open=Decimal("800.0"),
        high=Decimal("810.0"),
        low=Decimal("795.0"),
        close=Decimal("805.0"),
        volume=1000000,
        price_type=PriceType.NSE_CLOSE,
        source_name="NSE_OFFICIAL_ARCHIVE",
        provider_id="NSE_HISTORICAL",
    )
    assert bar.symbol == "SBIN"
    assert bar.price_type == PriceType.NSE_CLOSE
    assert bar.provider_id == "NSE_HISTORICAL"

    quote = NormalizedQuote(
        symbol="NIFTY26AUG24500CE",
        instrument_type="OPTION",
        ltp=Decimal("150.25"),
        strike_price=Decimal("24500"),
        option_type="CE",
        expiry_date=date(2026, 8, 28),
        price_type=PriceType.UPSTOX_LIVE_LTP,
        source_name="UPSTOX_WS_V2",
        provider_id="UPSTOX_LIVE",
        freshness_status=FreshnessStatus.LIVE,
    )
    assert quote.option_type == "CE"
    assert quote.price_type == PriceType.UPSTOX_LIVE_LTP
    assert quote.freshness_status == FreshnessStatus.LIVE


def test_token_redaction():
    """Test token redaction utility to ensure secret tokens are never exposed in strings/logs."""
    token = "secret_upstox_token_999xyz"
    raw_log = f"Failed request with header Bearer {token} for user."
    redacted = redact_secrets(raw_log)
    assert token not in redacted
    assert "REDACTED" in redacted


def test_provider_registry_auto_selection():
    """Test ProviderRegistry singleton, AUTO provider selection, and capability registration."""
    registry = ProviderRegistry.get_instance()
    upstox_hist = UpstoxHistoricalProvider(mock_mode=True)
    upstox_live = UpstoxLiveProvider(mock_mode=True)

    registry.register_historical_provider(upstox_hist)
    registry.register_live_provider(upstox_live)

    # Test AUTO selection
    hist_provider = registry.get_historical_provider("AUTO")
    assert hist_provider is not None

    live_provider = registry.get_live_provider("AUTO")
    assert live_provider is not None
    assert live_provider.provider_id == "UPSTOX_LIVE"

    # Test reserved future slots
    info = registry.list_registered_providers()
    assert "DHAN" in info["reserved_slots"]
    assert "ZERODHA" in info["reserved_slots"]


@pytest.mark.asyncio
async def test_upstox_historical_normalization():
    """Test UpstoxHistoricalProvider bar and option chain normalization in mock mode."""
    provider = UpstoxHistoricalProvider(mock_mode=True)
    assert await provider.is_available() is True

    bars = await provider.fetch_historical_bars("INFY", date(2026, 8, 1), date(2026, 8, 10))
    assert len(bars) >= 1
    assert bars[0].symbol == "INFY"
    assert bars[0].price_type == PriceType.UPSTOX_HISTORICAL_CLOSE

    chain = await provider.fetch_option_chain_history("NIFTY", date(2026, 8, 10))
    assert chain.underlying_symbol == "NIFTY"
    assert len(chain.calls) > 0
    assert len(chain.puts) > 0
    assert chain.ce_pe_paired_pct == 100.0


@pytest.mark.asyncio
async def test_upstox_live_quote_and_freshness():
    """Test UpstoxLiveProvider quote fetching and freshness status calculation."""
    provider = UpstoxLiveProvider(mock_mode=True)
    quote = await provider.get_live_quote("HDFCBANK")
    assert quote is not None
    assert quote.symbol == "HDFCBANK"
    assert quote.price_type == PriceType.UPSTOX_LIVE_LTP
    assert quote.freshness_status in {FreshnessStatus.LIVE, FreshnessStatus.DELAYED}


@pytest.mark.asyncio
async def test_simultaneous_historical_and_live_providers():
    """Test using NSE historical provider for EOD baseline and Upstox live provider for real-time ticks simultaneously."""
    from app.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        nse_provider = NSEHistoricalProvider(session)
        upstox_live = UpstoxLiveProvider(mock_mode=True)

        # 1. Fetch historical bar from NSE
        hist_bars = await nse_provider.fetch_historical_bars("SBIN", date(2026, 7, 6), date(2026, 8, 6))

        # 2. Fetch live quote from Upstox
        live_quote = await upstox_live.get_live_quote("SBIN")

        assert live_quote is not None
        assert live_quote.provider_id == "UPSTOX_LIVE"
        if hist_bars:
            assert hist_bars[0].provider_id == "NSE_HISTORICAL"
            assert hist_bars[0].price_type == PriceType.NSE_CLOSE
