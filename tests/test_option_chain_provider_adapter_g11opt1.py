"""Option Chain Provider Adapter & Normalization Tests — Milestone G.11-OPT-1 Step 3.

Tests the provider-neutral OptionChainSourceAdapter contract, UpstoxOptionChainAdapter,
and MockOptionChainAdapter according to Specification §24, §41, §48.

Test Matrix:
  1. Adapter can initialize.
  2. Provider health returns expected state.
  3. Provider payload maps to normalized option-chain representation.
  4. Underlying mapping is correct.
  5. Expiry mapping is correct.
  6. Strike mapping is correct.
  7. CE mapping is correct.
  8. PE mapping is correct.
  9. LTP mapping is correct.
  10. Bid/ask mapping is correct.
  11. Quantity mapping is correct.
  12. Volume mapping is correct.
  13. OI mapping is correct.
  14. Source OI change is preserved when available.
  15. Source Greeks are preserved when available.
  16. Source timestamp is preserved.
  17. Provider instrument identifier is preserved.
  18. Unknown/invalid provider instrument is handled safely.
  19. Provider error is surfaced correctly.
  20. Credentials/tokens never appear in logs/errors/hashes.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.models import NormalizedOptionChain
from app.providers.upstox_historical_provider import redact_secrets
from app.providers.upstox_live_provider import UpstoxLiveProvider
from app.services.option_chain_adapter import (
    MockOptionChainAdapter,
    OptionChainSourceAdapter,
    UpstoxOptionChainAdapter,
)
from app.services.option_chain_ladder import OptionChainInstrumentMapper


class TestOptionChainProviderAdapter:
    """Comprehensive test suite verifying OptionChainSourceAdapter implementations."""

    @pytest.mark.asyncio
    async def test_01_adapter_initialization(self) -> None:
        """1. Verify provider adapters initialize cleanly with correct identities."""
        mapper = OptionChainInstrumentMapper()
        upstox_adapter = UpstoxOptionChainAdapter(instrument_mapper=mapper, mock_mode=True)
        mock_adapter = MockOptionChainAdapter(instrument_mapper=mapper)

        assert isinstance(upstox_adapter, OptionChainSourceAdapter)
        assert isinstance(mock_adapter, OptionChainSourceAdapter)
        assert upstox_adapter.source_name == "UPSTOX"
        assert mock_adapter.source_name == "MOCK_PROVIDER"
        assert upstox_adapter.normalization_version == "1.0.0"
        assert mock_adapter.normalization_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_02_provider_health_returns_expected_state(self) -> None:
        """2. Verify is_healthy() correctly reports online and offline/unhealthy states."""
        mock_adapter = MockOptionChainAdapter(healthy=True)
        assert await mock_adapter.is_healthy() is True

        mock_adapter.set_healthy(False)
        assert await mock_adapter.is_healthy() is False

        # Upstox adapter in mock mode is available
        upstox_adapter = UpstoxOptionChainAdapter(mock_mode=True)
        assert await upstox_adapter.is_healthy() is True

        # When UpstoxLiveProvider is_available returns False
        mock_live = MagicMock(spec=UpstoxLiveProvider)
        mock_live.is_available = AsyncMock(return_value=False)
        adapter = UpstoxOptionChainAdapter(provider=mock_live)
        assert await adapter.is_healthy() is False

    @pytest.mark.asyncio
    async def test_03_provider_payload_maps_to_normalized_option_chain(self) -> None:
        """3. Verify provider payload maps to NormalizedOptionChain with required metadata."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        assert isinstance(chain, NormalizedOptionChain)
        assert chain.underlying_symbol == "NIFTY"
        assert chain.spot_price == Decimal("24500.00")
        assert len(chain.expiries) > 0
        assert len(chain.calls) > 0
        assert len(chain.puts) > 0
        assert chain.raw_payload_hash is not None
        assert len(chain.raw_payload_hash) == 64  # SHA-256 hex length

    @pytest.mark.asyncio
    async def test_04_underlying_mapping_is_correct(self) -> None:
        """4. Verify canonical underlying index code is normalized correctly across variants."""
        adapter = MockOptionChainAdapter()
        for raw_sym, canonical in [
            ("nifty", "NIFTY"),
            ("NIFTY 50", "NIFTY_50"),
            ("BANKNIFTY", "BANKNIFTY"),
            ("bank_nifty", "BANK_NIFTY"),
            ("sensex", "SENSEX"),
        ]:
            chain = await adapter.fetch_chain_snapshot(raw_sym)
            assert chain.underlying_symbol == canonical

    @pytest.mark.asyncio
    async def test_05_expiry_mapping_is_correct(self) -> None:
        """5. Verify requested and observed expiry dates map accurately without date mutation."""
        target_expiry = date.today() + timedelta(days=14)
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY", expiry_date=target_expiry)

        assert target_expiry in chain.expiries
        assert chain.expiry_scope == target_expiry
        assert all(q.expiry_date == target_expiry for q in chain.calls)
        assert all(q.expiry_date == target_expiry for q in chain.puts)

    @pytest.mark.asyncio
    async def test_06_strike_mapping_is_correct(self) -> None:
        """6. Verify strike prices are accurately converted to Decimal without precision loss."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert isinstance(q.strike_price, Decimal)
            assert q.strike_price > 0
            # NIFTY strike grid step is 50
            assert q.strike_price % Decimal("50") == 0

    @pytest.mark.asyncio
    async def test_07_ce_mapping_is_correct(self) -> None:
        """7. Verify Call option quotes are strictly segregated and tagged option_type='CE'."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        assert len(chain.calls) > 0
        for q in chain.calls:
            assert q.option_type == "CE"
            assert "CE" in q.symbol

    @pytest.mark.asyncio
    async def test_08_pe_mapping_is_correct(self) -> None:
        """8. Verify Put option quotes are strictly segregated and tagged option_type='PE'."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        assert len(chain.puts) > 0
        for q in chain.puts:
            assert q.option_type == "PE"
            assert "PE" in q.symbol

    @pytest.mark.asyncio
    async def test_09_ltp_mapping_is_correct(self) -> None:
        """9. Verify Last Traded Price (LTP) is positive Decimal and mapped accurately."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert isinstance(q.ltp, Decimal)
            assert q.ltp > 0

    @pytest.mark.asyncio
    async def test_10_bid_ask_mapping_is_correct(self) -> None:
        """10. Verify top-of-book bid and ask prices are mapped accurately."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert q.bid is not None
            assert q.ask is not None
            assert isinstance(q.bid, Decimal)
            assert isinstance(q.ask, Decimal)
            assert q.bid <= q.ask  # Bid cannot exceed ask

    @pytest.mark.asyncio
    async def test_11_quantity_mapping_is_correct(self) -> None:
        """11. Verify bid and ask quantities are preserved as integers."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert isinstance(q.bid_qty, int)
            assert isinstance(q.ask_qty, int)
            assert q.bid_qty > 0
            assert q.ask_qty > 0

    @pytest.mark.asyncio
    async def test_12_volume_mapping_is_correct(self) -> None:
        """12. Verify trading volume is mapped as a non-negative integer."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert isinstance(q.volume, int)
            assert q.volume >= 0

    @pytest.mark.asyncio
    async def test_13_oi_mapping_is_correct(self) -> None:
        """13. Verify open interest is mapped as a non-negative integer."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert isinstance(q.open_interest, int)
            assert q.open_interest >= 0

    @pytest.mark.asyncio
    async def test_14_source_oi_change_preserved_when_available(self) -> None:
        """14. Verify source-provided open interest change is preserved as a signed integer."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        # Calls in mock adapter have +1200 change, puts have -800 change
        for q in chain.calls:
            assert q.source_change_in_oi == 1200
        for q in chain.puts:
            assert q.source_change_in_oi == -800

    @pytest.mark.asyncio
    async def test_15_source_greeks_preserved_when_available(self) -> None:
        """15. Verify source-supplied Greeks are preserved without performing Greek calculations."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls:
            assert q.source_greeks is not None
            assert "iv" in q.source_greeks
            assert "delta" in q.source_greeks
            assert "gamma" in q.source_greeks
            assert "theta" in q.source_greeks
            assert "vega" in q.source_greeks
            assert q.source_greeks["delta"] > 0  # Call delta > 0

        for q in chain.puts:
            assert q.source_greeks is not None
            assert q.source_greeks["delta"] < 0  # Put delta < 0

        # Scenario: missing Greeks preserved as None without crash
        adapter_no_greeks = MockOptionChainAdapter(scenarios={"scenario": "missing_source_greeks"})
        chain_no_greeks = await adapter_no_greeks.fetch_chain_snapshot("NIFTY")
        for q in chain_no_greeks.calls + chain_no_greeks.puts:
            assert q.source_greeks is None

    @pytest.mark.asyncio
    async def test_16_source_timestamp_preserved(self) -> None:
        """16. Verify source observation timestamp is preserved."""
        skew_ms = 1500
        adapter = MockOptionChainAdapter(scenarios={"skew_ms": skew_ms})
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        assert chain.source_timestamp is not None
        assert chain.market_timestamp is not None
        assert chain.source_timestamp.tzinfo is not None

    @pytest.mark.asyncio
    async def test_17_provider_instrument_identifier_preserved(self) -> None:
        """17. Verify provider-specific instrument key is retained in provider_instrument_id."""
        adapter = MockOptionChainAdapter()
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        for q in chain.calls + chain.puts:
            assert q.provider_instrument_id is not None
            assert q.provider_instrument_id.startswith("NSE_FO|NIFTY_")

    @pytest.mark.asyncio
    async def test_18_unknown_invalid_provider_instrument_handled_safely(self) -> None:
        """18. Verify unmapped or unknown provider instrument does not crash the adapter."""
        mapper = OptionChainInstrumentMapper()  # Empty mapper
        adapter = MockOptionChainAdapter(instrument_mapper=mapper)
        chain = await adapter.fetch_chain_snapshot("NIFTY")

        # Ingestion succeeds, but contract_id is unmapped (None) and tracked in mapper
        assert len(chain.calls) > 0
        for q in chain.calls:
            assert q.contract_id is None
        assert len(mapper.get_unmapped_keys()) > 0

    @pytest.mark.asyncio
    async def test_19_provider_error_surfaced_correctly(self) -> None:
        """19. Verify provider transport errors are surfaced cleanly without leaking internals."""
        mock_live = MagicMock(spec=UpstoxLiveProvider)
        mock_live.get_live_option_chain = AsyncMock(side_effect=ConnectionError("Upstox Gateway Timeout"))
        adapter = UpstoxOptionChainAdapter(provider=mock_live)

        with pytest.raises(RuntimeError) as exc_info:
            await adapter.fetch_chain_snapshot("NIFTY")

        assert "Upstox fetch_chain_snapshot error" in str(exc_info.value)
        assert "Gateway Timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_20_credentials_tokens_never_appear_in_logs_errors_or_hashes(self) -> None:
        """20. Verify secrets, Bearer tokens, and API credentials are redacted from logs and hashes."""
        fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYW1lIjoiVGVzdCJ9"
        secret_payload = f'{{"access_token": "{fake_token}", "Authorization": "Bearer {fake_token}"}}'

        # Test redact_secrets directly
        redacted = redact_secrets(secret_payload)
        assert fake_token not in redacted
        assert "REDACTED" in redacted

        # Test compute_payload_hash scrubs secrets before hashing
        hash_1 = OptionChainSourceAdapter.compute_payload_hash(f"TEST_PAYLOAD_Bearer {fake_token}")
        hash_2 = OptionChainSourceAdapter.compute_payload_hash("TEST_PAYLOAD_Bearer [REDACTED_TOKEN]")
        assert hash_1 == hash_2  # Hashes match because secret was redacted before hashing

        # Test error logging redacts secret
        mock_live = MagicMock(spec=UpstoxLiveProvider)
        mock_live.get_live_option_chain = AsyncMock(
            side_effect=ValueError(f"Auth failed with token: Bearer {fake_token}")
        )
        adapter = UpstoxOptionChainAdapter(provider=mock_live)

        with pytest.raises(RuntimeError) as exc_info:
            await adapter.fetch_chain_snapshot("NIFTY")

        error_str = str(exc_info.value)
        assert fake_token not in error_str
        assert "Bearer [REDACTED_TOKEN]" in error_str
