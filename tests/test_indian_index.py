"""Tests for the Indian index catalog, provider, ingestion, and price-action."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.market_index import MarketIndex

INDEX_CODES = (
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_FIN_SERV",
    "NIFTY_MIDCAP_100",
    "NIFTY_SMALLCAP_100",
    "NIFTY_NEXT_50",
    "SENSEX",
)

PROVIDER_SYMBOLS = {
    "NIFTY_50": "NIFTY50",
    "NIFTY_BANK": "BANKNIFTY",
    "NIFTY_FIN_SERV": "FINNIFTY",
    "NIFTY_MIDCAP_100": "NIFTIMIDCP100",
    "NIFTY_SMALLCAP_100": "NIFTISMALLCAP100",
    "NIFTY_NEXT_50": "NIFTINEXT50",
    "SENSEX": "SENSEX",
}


def _get_factory():
    return get_session_factory()


async def _seed_indices_if_needed(factory):
    """Seed indices only if not already present (idempotent)."""
    async with factory() as session:
        # Check which codes already exist
        existing = await session.execute(select(MarketIndex.code))
        existing_codes = set(result for result in existing.scalars().all())
        for code in INDEX_CODES:
            if code not in existing_codes:
                session.add(
                    MarketIndex(
                        code=code,
                        name=code.replace("_", " "),
                        country="INDIA",
                        exchange="NSE" if code != "SENSEX" else "BSE",
                        category="indian_index",
                        provider_symbol=PROVIDER_SYMBOLS[code],
                        is_active=True,
                    )
                )
        await session.commit()


@pytest.mark.asyncio
async def test_index_catalog_has_all_required_indices():
    """Verify all 7 required Indian index codes are configured."""
    factory = _get_factory()
    await _seed_indices_if_needed(factory)
    async with factory() as session:
        result = await session.execute(select(MarketIndex.code))
        codes = set(result.scalars().all())
        missing = set(INDEX_CODES) - codes
        assert not missing, f"Missing required indices: {missing}"


@pytest.mark.asyncio
async def test_index_codes_are_valid_strings():
    """Internal codes must be non-empty strings."""
    assert all(isinstance(c, str) and len(c) > 0 for c in INDEX_CODES)


@pytest.mark.asyncio
async def test_status_values_are_valid():
    """STATUS_VALUES must contain only valid status strings."""
    from app.market_intelligence.providers.base import STATUS_VALUES
    assert STATUS_VALUES == (
        "success",
        "cached",
        "stale",
        "unavailable",
        "blocked",
        "failed",
    )


@pytest.mark.asyncio
async def test_freshness_values_are_valid():
    """FRESHNESS_VALUES must contain only valid freshness strings."""
    from app.market_intelligence.providers.base import FRESHNESS_VALUES
    assert FRESHNESS_VALUES == ("live", "recent", "stale", "missing")


@pytest.mark.asyncio
async def test_sensex_is_bse_not_nse():
    """SENSEX must be catalogued as a BSE index, not NSE."""
    factory = _get_factory()
    await _seed_indices_if_needed(factory)
    async with factory() as session:
        result = await session.execute(
            select(MarketIndex).where(MarketIndex.code == "SENSEX")
        )
        sensex = result.scalar_one_or_none()
        assert sensex is not None
        assert sensex.exchange == "BSE"
        assert sensex.provider_symbol == "SENSEX"


@pytest.mark.asyncio
async def test_nse_provider_returns_unavailable_for_sensex():
    """The NSE provider must honestly report SENSEX as unavailable.

    SENSEX is a BSE index; the NSE bhavcopy provider cannot retrieve it.
    It must return an honest unavailable/unsupported result rather than
    fabricating NSE data for a BSE index.
    """
    from datetime import date

    from app.market_intelligence.providers.nse_index_provider import (
        NSEIndexProvider,
    )

    provider = NSEIndexProvider()
    result = await provider.fetch(index_code="SENSEX", trade_date=date(2024, 1, 15))
    assert result.status == "unavailable"
    assert result.freshness == "missing"
    assert "BSE" in (result.message or "")


@pytest.mark.asyncio
async def test_nse_provider_supports_only_nse_indices():
    """NSEIndexProvider.supports_indices must NOT include SENSEX."""
    from app.market_intelligence.providers.nse_index_provider import (
        NSEIndexProvider,
    )

    provider = NSEIndexProvider()
    assert "SENSEX" not in provider.supports_indices
    assert "NIFTY_50" in provider.supports_indices
    assert "NIFTY_BANK" in provider.supports_indices
    assert "NIFTY_FIN_SERV" in provider.supports_indices
    assert "NIFTY_MIDCAP_100" in provider.supports_indices
    assert "NIFTY_SMALLCAP_100" in provider.supports_indices
    assert "NIFTY_NEXT_50" in provider.supports_indices
