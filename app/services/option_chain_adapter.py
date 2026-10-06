"""Option Chain Source Adapters & Normalization — G.11-OPT-1.

Milestone: G.11-OPT-1 (SPEC-R2 §22, §24 & Implementation Specification §24, §32, §41, §48).
Provides:
  1. OptionChainSourceAdapter: Abstract base provider-neutral adapter contract.
  2. UpstoxOptionChainAdapter: Production integration with existing UpstoxLiveProvider.
  3. MockOptionChainAdapter: Deterministic offline mock adapter supporting all test scenarios.
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.providers.models import (
    FreshnessStatus,
    NormalizedOptionChain,
    NormalizedQuote,
    PriceType,
)
from app.providers.upstox_historical_provider import redact_secrets
from app.providers.upstox_live_provider import UpstoxLiveProvider
from app.services.option_chain_config import (
    DQ_FLAG_OFF_GRID_STRIKE,
    DQ_FLAG_STALE_QUOTE,
    DQ_FLAG_TRUNCATED_BY_SOURCE,
    DQ_STATUS_ACCEPT_WITH_FLAG,
    DQ_STATUS_VALID,
    DQ_STATUS_WARNING,
    get_strike_step,
    normalize_index_symbol,
)
from app.services.option_chain_ladder import OptionChainInstrumentMapper

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. Provider-Neutral Option-Chain Source Adapter (Spec §24 & §41)
# ===========================================================================

class OptionChainSourceAdapter(ABC):
    """Abstract base class for option chain market data adapters.

    Strict architectural requirement: Zero trading logic, zero strategy decisions.
    Adapters are purely for ingestion, normalization, and provenance preservation.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Normalized source name identifier (e.g. 'UPSTOX', 'NSE', 'MOCK_PROVIDER')."""
        pass

    @property
    def normalization_version(self) -> str:
        """Version string of the normalization parser."""
        return "1.0.0"

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Check provider connectivity, API health, or token validity."""
        pass

    @abstractmethod
    async def fetch_chain_snapshot(
        self,
        underlying_symbol: str,
        expiry_date: date | None = None,
        chain_scope: str = "SINGLE_EXPIRY",
    ) -> NormalizedOptionChain:
        """Fetch and normalize an option chain snapshot for an underlying symbol."""
        pass

    @abstractmethod
    async def get_available_expiries(self, underlying_symbol: str) -> list[date]:
        """Return all available, non-expired contract expiry dates for the underlying."""
        pass

    @staticmethod
    def compute_payload_hash(raw_payload: str | bytes | dict[str, Any]) -> str:
        """Compute SHA-256 provenance checksum with secret redaction.

        Guarantees that sensitive credentials or tokens never influence the hash
        or leak into persistent storage.
        """
        if isinstance(raw_payload, dict):
            serialized = json.dumps(raw_payload, sort_keys=True)
        elif isinstance(raw_payload, bytes):
            serialized = raw_payload.decode("utf-8", errors="replace")
        else:
            serialized = str(raw_payload)

        redacted = redact_secrets(serialized)
        return hashlib.sha256(redacted.encode("utf-8")).hexdigest()


# ===========================================================================
# 2. Upstox Live Provider Adapter (Spec §24, §25, §32)
# ===========================================================================

class UpstoxOptionChainAdapter(OptionChainSourceAdapter):
    """Option chain source adapter wrapping the existing UpstoxLiveProvider."""

    def __init__(
        self,
        provider: UpstoxLiveProvider | None = None,
        instrument_mapper: OptionChainInstrumentMapper | None = None,
        mock_mode: bool = False,
    ) -> None:
        self._provider = provider or UpstoxLiveProvider(mock_mode=mock_mode)
        self._instrument_mapper = instrument_mapper or OptionChainInstrumentMapper()

    @property
    def source_name(self) -> str:
        return "UPSTOX"

    @property
    def provider(self) -> UpstoxLiveProvider:
        return self._provider

    @property
    def instrument_mapper(self) -> OptionChainInstrumentMapper:
        return self._instrument_mapper

    async def is_healthy(self) -> bool:
        """Check if Upstox live provider is available and configured."""
        try:
            return await self._provider.is_available()
        except Exception as exc:
            logger.error(redact_secrets(f"Upstox health check error: {exc}"))
            return False

    async def fetch_chain_snapshot(
        self,
        underlying_symbol: str,
        expiry_date: date | None = None,
        chain_scope: str = "SINGLE_EXPIRY",
    ) -> NormalizedOptionChain:
        """Fetch live option chain snapshot from Upstox and normalize for OPT-1.

        Preserves:
          - Spot price and underlying index identity
          - Expiry and strike alignment
          - LTP, Bid, Ask, Quantities, Volume, Open Interest
          - Source OI change and provider-supplied Option Greeks
          - Source timestamps and provider instrument keys
        """
        try:
            # Delegate to existing provider without duplicate transport logic
            chain = await self._provider.get_live_option_chain(
                underlying_symbol=underlying_symbol,
                expiry_date=expiry_date,
            )
        except Exception as exc:
            redacted_msg = redact_secrets(f"Upstox fetch_chain_snapshot error for {underlying_symbol}: {exc}")
            logger.error(redacted_msg)
            raise RuntimeError(redacted_msg) from exc

        # Enrich NormalizedOptionChain with OPT-1 metadata
        chain.source_name = self.source_name
        chain.chain_scope = chain_scope
        chain.expiry_scope = expiry_date
        chain.source_timestamp = chain.as_of_timestamp
        chain.market_timestamp = chain.as_of_timestamp

        all_quotes = chain.calls + chain.puts
        for q in all_quotes:
            # 1. Map provider instrument identifier
            ik = q.raw_provider_meta.get("instrument_key")
            if ik:
                q.provider_instrument_id = str(ik)

            # 2. Extract source-supplied Greeks
            greeks = q.raw_provider_meta.get("option_greeks")
            if greeks:
                q.source_greeks = greeks

            # 3. Preserve source OI change
            if q.change_in_open_interest is not None:
                q.source_change_in_oi = q.change_in_open_interest

            # 4. Resolve canonical contract ID in-memory (0 DB queries)
            c_id = self._instrument_mapper.resolve_quote(q, underlying_symbol)
            if c_id is not None:
                q.contract_id = c_id

        # Compute provenance SHA-256 hash
        serialized_quotes = [
            {
                "symbol": q.symbol,
                "strike": str(q.strike_price),
                "type": q.option_type,
                "ltp": str(q.ltp) if q.ltp is not None else None,
                "oi": q.open_interest,
                "ik": q.provider_instrument_id,
            }
            for q in sorted(all_quotes, key=lambda x: (x.expiry_date or date.min, x.strike_price or 0, x.option_type or ""))
        ]
        chain.raw_payload_hash = self.compute_payload_hash(json.dumps(serialized_quotes, sort_keys=True))
        chain.observed_strike_count = len({q.strike_price for q in all_quotes if q.strike_price is not None})

        return chain

    async def get_available_expiries(self, underlying_symbol: str) -> list[date]:
        """Resolve all non-expired listed expiries from the underlying provider."""
        return await self._provider.get_listed_expiries(underlying_symbol)


# ===========================================================================
# 3. Deterministic Mock Option-Chain Adapter (Spec §48)
# ===========================================================================

class MockOptionChainAdapter(OptionChainSourceAdapter):
    """Deterministic offline mock adapter for testing all G.11-OPT-1 scenarios.

    Features:
      - 100% offline (0 network requests).
      - Configurable test scenarios: complete, multi-expiry, duplicate, off-grid,
        incomplete/truncated, timestamp skew, source Greeks, missing Greeks, stale quotes.
      - Simulated tick stream generator for ladder buffer testing.
    """

    def __init__(
        self,
        healthy: bool = True,
        scenarios: dict[str, Any] | None = None,
        instrument_mapper: OptionChainInstrumentMapper | None = None,
        underlying_spot_map: dict[str, Decimal] | None = None,
    ) -> None:
        self._healthy = healthy
        self._scenarios = scenarios or {}
        self._instrument_mapper = instrument_mapper or OptionChainInstrumentMapper()
        self._spot_map = underlying_spot_map or {
            "NIFTY": Decimal("24500.00"),
            "BANKNIFTY": Decimal("52000.00"),
            "FINNIFTY": Decimal("23000.00"),
            "MIDCPNIFTY": Decimal("12500.00"),
            "SENSEX": Decimal("80000.00"),
        }

    @property
    def source_name(self) -> str:
        return "MOCK_PROVIDER"

    @property
    def instrument_mapper(self) -> OptionChainInstrumentMapper:
        return self._instrument_mapper

    def set_healthy(self, healthy: bool) -> None:
        """Simulate provider health changes for testing."""
        self._healthy = healthy

    async def is_healthy(self) -> bool:
        return self._healthy

    async def get_available_expiries(self, underlying_symbol: str) -> list[date]:
        """Resolve all non-expired listed expiries deterministically for mock mode."""
        from app.providers.upstox_live_provider import calculate_dynamic_index_expiries
        return calculate_dynamic_index_expiries()

    async def fetch_chain_snapshot(
        self,
        underlying_symbol: str,
        expiry_date: date | None = None,
        chain_scope: str = "SINGLE_EXPIRY",
    ) -> NormalizedOptionChain:
        """Generate deterministic normalized option chain snapshot."""
        if not self._healthy:
            raise RuntimeError("Mock provider is currently in an unhealthy / disconnected state.")

        norm_underlying = normalize_index_symbol(underlying_symbol)
        spot = self._spot_map.get(norm_underlying, Decimal("24500.00"))
        step = get_strike_step(norm_underlying)

        now = datetime.now(UTC)
        exp_target = expiry_date or (date.today() + timedelta(days=7))

        scenario = self._scenarios.get("scenario", "complete")
        skew_ms = self._scenarios.get("skew_ms", 0)
        source_ts = now - timedelta(milliseconds=skew_ms)
        market_ts = source_ts

        if expiry_date is not None:
            expiries = [expiry_date]
        elif scenario == "multi_expiry":
            expiries = [exp_target, exp_target + timedelta(days=7)]
        else:
            from app.providers.upstox_live_provider import calculate_dynamic_index_expiries
            dynamic_exp = calculate_dynamic_index_expiries()
            expiries = dynamic_exp if dynamic_exp else [exp_target, exp_target + timedelta(days=7)]

        calls: list[NormalizedQuote] = []
        puts: list[NormalizedQuote] = []

        for exp in expiries:
            # Generate strikes centered around spot
            strike_count = self._scenarios.get("strike_count", 11)  # 5 OTM, 1 ATM, 5 ITM
            half = strike_count // 2
            atm_strike = (spot // step) * step

            strikes = [atm_strike + (i * step) for i in range(-half, half + 1)]

            if scenario == "off_grid_strike":
                strikes.append(atm_strike + Decimal("13.5"))  # Off-grid strike

            if scenario == "duplicate_quote":
                strikes.append(strikes[0])  # Duplicate strike

            for strike in strikes:
                # CE Quote
                ce_diff = max(Decimal("0"), spot - strike)
                ce_ltp = ce_diff + Decimal("150.00")
                ce_ik = f"NSE_FO|{norm_underlying}_{exp.strftime('%y%b').upper()}_{strike}_CE"
                ce_c_id = self._instrument_mapper.resolve(ce_ik)

                # Varied price change to generate all 4 buildup states while keeping source_change_in_oi constant (1200 for CE, -800 for PE)
                if strike < spot:  # ITM Call -> Long Buildup (Price ↑, OI ↑)
                    ce_change = Decimal("15.50")
                else:  # ATM & OTM Call -> Short Buildup (Price ↓, OI ↑)
                    ce_change = Decimal("-4.50")
                ce_doi = 1200

                ce_quote = NormalizedQuote(
                    symbol=f"{norm_underlying}{exp.strftime('%d%b%y').upper()}{strike}CE",
                    instrument_type="OPTION",
                    ltp=ce_ltp,
                    previous_close=ce_ltp - ce_change,
                    change=ce_change,
                    change_pct=Decimal("3.5"),
                    bid=ce_ltp - Decimal("0.50"),
                    ask=ce_ltp + Decimal("0.50"),
                    bid_qty=100,
                    ask_qty=100,
                    volume=5000,
                    open_interest=50000,
                    change_in_open_interest=ce_doi,
                    strike_price=strike,
                    option_type="CE",
                    expiry_date=exp,
                    timestamp=market_ts,
                    price_type=PriceType.GENERIC_LTP,
                    source_name=self.source_name,
                    provider_id="MOCK_PROVIDER_ID",
                    freshness_status=FreshnessStatus.STALE if scenario == "stale_quotes" else FreshnessStatus.LIVE,
                    raw_provider_meta={"instrument_key": ce_ik},
                    contract_id=ce_c_id,
                    provider_instrument_id=ce_ik,
                    source_change_in_oi=ce_doi,
                    source_greeks={
                        "iv": 0.155,
                        "delta": 0.52,
                        "gamma": 0.002,
                        "theta": -12.4,
                        "vega": 18.1,
                    } if scenario != "missing_source_greeks" else None,
                )
                calls.append(ce_quote)

                # PE Quote
                pe_diff = max(Decimal("0"), strike - spot)
                pe_ltp = pe_diff + Decimal("150.00")
                pe_ik = f"NSE_FO|{norm_underlying}_{exp.strftime('%y%b').upper()}_{strike}_PE"
                pe_c_id = self._instrument_mapper.resolve(pe_ik)

                if strike > spot:  # ITM Put -> Short Covering (Price ↑, OI ↓)
                    pe_change = Decimal("14.00")
                else:  # ATM & OTM Put -> Long Unwinding (Price ↓, OI ↓)
                    pe_change = Decimal("-6.00")
                pe_doi = -800

                pe_quote = NormalizedQuote(
                    symbol=f"{norm_underlying}{exp.strftime('%d%b%y').upper()}{strike}PE",
                    instrument_type="OPTION",
                    ltp=pe_ltp,
                    previous_close=pe_ltp - pe_change,
                    change=pe_change,
                    change_pct=Decimal("-2.5"),
                    bid=pe_ltp - Decimal("0.50"),
                    ask=pe_ltp + Decimal("0.50"),
                    bid_qty=100,
                    ask_qty=100,
                    volume=4500,
                    open_interest=48000,
                    change_in_open_interest=pe_doi,
                    strike_price=strike,
                    option_type="PE",
                    expiry_date=exp,
                    timestamp=market_ts,
                    price_type=PriceType.GENERIC_LTP,
                    source_name=self.source_name,
                    provider_id="MOCK_PROVIDER_ID",
                    freshness_status=FreshnessStatus.STALE if scenario == "stale_quotes" else FreshnessStatus.LIVE,
                    raw_provider_meta={"instrument_key": pe_ik},
                    contract_id=pe_c_id,
                    provider_instrument_id=pe_ik,
                    source_change_in_oi=pe_doi,
                    source_greeks={
                        "iv": 0.160,
                        "delta": -0.48,
                        "gamma": 0.002,
                        "theta": -11.9,
                        "vega": 17.8,
                    } if scenario != "missing_source_greeks" else None,
                )
                puts.append(pe_quote)

        is_trunc = scenario == "incomplete_chain"
        status = DQ_STATUS_VALID
        flags: dict[str, Any] = {}
        if scenario == "incomplete_chain":
            is_trunc = True
            flags[DQ_FLAG_TRUNCATED_BY_SOURCE] = True
            status = DQ_STATUS_WARNING
        elif scenario == "off_grid_strike":
            flags[DQ_FLAG_OFF_GRID_STRIKE] = True
            status = DQ_STATUS_WARNING
        elif scenario == "stale_quotes":
            flags[DQ_FLAG_STALE_QUOTE] = True
            status = DQ_STATUS_ACCEPT_WITH_FLAG

        all_q = calls + puts
        observed_strikes = len({q.strike_price for q in all_q if q.strike_price is not None})
        raw_hash = self.compute_payload_hash(f"{norm_underlying}_{exp_target}_{len(all_q)}")

        return NormalizedOptionChain(
            underlying_symbol=norm_underlying,
            as_of_timestamp=now,
            spot_price=spot,
            expiries=expiries,
            calls=calls,
            puts=puts,
            ce_pe_paired_pct=100.0,
            provider_id="MOCK_PROVIDER_ID",
            source_name=self.source_name,
            chain_scope=chain_scope,
            expiry_scope=expiry_date if chain_scope == "SINGLE_EXPIRY" else None,
            market_timestamp=market_ts,
            source_timestamp=source_ts,
            raw_payload_hash=raw_hash,
            is_truncated=is_trunc,
            expected_strike_count=observed_strikes if not is_trunc else observed_strikes + 10,
            observed_strike_count=observed_strikes,
            quality_status=status,
            quality_flags=flags,
        )

    async def stream_mock_ticks(
        self,
        underlying_symbol: str,
        expiry_date: date,
        strikes: list[Decimal],
        interval_seconds: float = 0.001,
        repeat_count: int = 1,
    ) -> AsyncGenerator[NormalizedQuote]:
        """Stream simulated ticks into the ladder buffer for concurrent testing."""
        norm_underlying = normalize_index_symbol(underlying_symbol)
        now = datetime.now(UTC)

        for _ in range(repeat_count):
            for strike in strikes:
                for opt_type in ["CE", "PE"]:
                    ik = f"NSE_FO|{norm_underlying}_{expiry_date.strftime('%y%b').upper()}_{strike}_{opt_type}"
                    c_id = self._instrument_mapper.resolve(ik)

                    quote = NormalizedQuote(
                        symbol=f"{norm_underlying}{expiry_date.strftime('%d%b%y').upper()}{strike}{opt_type}",
                        instrument_type="OPTION",
                        ltp=Decimal("150.00"),
                        volume=1000,
                        open_interest=25000,
                        strike_price=strike,
                        option_type=opt_type,
                        expiry_date=expiry_date,
                        timestamp=now,
                        source_name=self.source_name,
                        provider_id="MOCK_PROVIDER_ID",
                        contract_id=c_id,
                        provider_instrument_id=ik,
                        raw_provider_meta={"instrument_key": ik},
                    )
                    yield quote
                    if interval_seconds > 0:
                        import asyncio
                        await asyncio.sleep(interval_seconds)
