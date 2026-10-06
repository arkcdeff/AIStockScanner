"""Option Chain In-Memory Instrument Mapper & Ladder Buffer — G.11-OPT-1.

Milestone: G.11-OPT-1 (SPEC-R2 & Implementation Specification §25, §26, §27, §28).
Provides:
  1. OptionChainInstrumentMapper: Preloaded in-memory resolution of provider instrument
     keys / symbols / specs to canonical DerivativeContract.id with ZERO database queries
     in the live WebSocket tick path.
  2. OptionChainLadderBuffer: Asynchronous in-memory buffer aggregating live quotes/ticks
     per (expiry, strike, option_type) with asyncio.Lock freeze boundary.
  3. FrozenOptionChainLadder: Immutable snapshot-ready option chain state decoupled from
     active accumulation, supporting multi-expiry and Step-2 validation integration.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import DerivativeContract
from app.providers.models import FreshnessStatus, NormalizedQuote
from app.providers.upstox_historical_provider import redact_secrets
from app.services.option_chain_config import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    DQ_FLAG_STALE_QUOTE,
    DQ_FLAG_UNMAPPED_INSTRUMENT,
    DQ_STATUS_VALID,
    DQ_STATUS_WARNING,
    get_strike_step,
    normalize_index_symbol,
)
from app.services.option_chain_validation_service import (
    ValidationResult,
    validate_chain_completeness,
    validate_strike_sequence,
    validate_timestamps,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. Canonical Instrument Mapping (Spec §25)
# ===========================================================================

class OptionChainInstrumentMapper:
    """Preloaded in-memory instrument key to canonical DerivativeContract.id mapper.

    Strict architectural requirement: Zero database queries in the live tick path.
    All mappings must be pre-populated during initialization or subscription setup.
    """

    def __init__(self) -> None:
        self._key_to_id: dict[str, int] = {}
        self._symbol_to_id: dict[str, int] = {}
        self._spec_to_id: dict[tuple[str, date, Decimal, str], int] = {}
        self._unmapped_keys: set[str] = set()
        self._lookup_count: int = 0
        self._db_query_count: int = 0

    @property
    def lookup_count(self) -> int:
        """Total number of resolution lookups performed."""
        return self._lookup_count

    @property
    def db_query_count(self) -> int:
        """Total number of database queries performed (must be 0 during tick processing)."""
        return self._db_query_count

    def register_mapping(self, provider_instrument_key: str, contract_id: int) -> None:
        """Register a direct provider instrument key to canonical contract ID mapping."""
        clean_key = provider_instrument_key.strip()
        self._key_to_id[clean_key] = contract_id

    def register_symbol_mapping(self, trading_symbol: str, contract_id: int) -> None:
        """Register a trading symbol to canonical contract ID mapping."""
        clean_sym = trading_symbol.strip().upper()
        self._symbol_to_id[clean_sym] = contract_id

    def register_spec_mapping(
        self,
        underlying_symbol: str,
        expiry_date: date,
        strike_price: Decimal,
        option_type: str,
        contract_id: int,
    ) -> None:
        """Register an economic specification tuple to canonical contract ID mapping."""
        norm_underlying = normalize_index_symbol(underlying_symbol)
        norm_opt_type = option_type.strip().upper()
        norm_strike = Decimal(str(strike_price))
        self._spec_to_id[(norm_underlying, expiry_date, norm_strike, norm_opt_type)] = contract_id

    def preload_from_contracts(self, contracts: list[DerivativeContract], underlying_symbol: str | None = None) -> int:
        """Preload in-memory mapper from a list of DerivativeContract models.

        Args:
            contracts: List of DerivativeContract entities.
            underlying_symbol: Optional underlying symbol override for specification mapping.

        Returns:
            Number of contracts registered.
        """
        count = 0
        for contract in contracts:
            c_id = contract.id
            if contract.source_contract_id:
                self.register_mapping(contract.source_contract_id, c_id)
            if contract.trading_symbol:
                self.register_symbol_mapping(contract.trading_symbol, c_id)
                self.register_mapping(contract.trading_symbol, c_id)

            u_sym = underlying_symbol
            if not u_sym and contract.trading_symbol:
                # Attempt to extract index prefix if underlying not specified
                for prefix in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]:
                    if contract.trading_symbol.startswith(prefix):
                        u_sym = prefix
                        break

            if u_sym and contract.expiry_date and contract.strike_price is not None and contract.option_type:
                self.register_spec_mapping(
                    u_sym,
                    contract.expiry_date,
                    contract.strike_price,
                    contract.option_type,
                    c_id,
                )
            count += 1
        return count

    async def preload_from_db(
        self,
        session: AsyncSession,
        underlying_index_id: int | None = None,
        underlying_stock_id: int | None = None,
        underlying_symbol: str | None = None,
        expiries: list[date] | None = None,
        active_only: bool = True,
    ) -> int:
        """Preload in-memory map from the database during startup/setup.

        This method executes database queries, and MUST ONLY be called during
        initialization or contract discovery, NEVER inside the tick path.
        """
        self._db_query_count += 1
        stmt = select(DerivativeContract).where(DerivativeContract.instrument_type == "OPTION")
        if underlying_index_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)
        if underlying_stock_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if expiries:
            stmt = stmt.where(DerivativeContract.expiry_date.in_(expiries))
        if active_only:
            stmt = stmt.where(DerivativeContract.lifecycle_status == "ACTIVE")

        result = await session.execute(stmt)
        contracts = list(result.scalars().all())
        return self.preload_from_contracts(contracts, underlying_symbol=underlying_symbol)

    def resolve(self, provider_instrument_key: str) -> int | None:
        """Resolve a provider instrument key in-memory with O(1) lookup.

        Zero database queries are performed.
        """
        self._lookup_count += 1
        clean_key = provider_instrument_key.strip()
        res = self._key_to_id.get(clean_key)
        if res is None:
            self._unmapped_keys.add(clean_key)
        return res

    def resolve_by_symbol(self, trading_symbol: str) -> int | None:
        """Resolve a trading symbol in-memory with O(1) lookup."""
        self._lookup_count += 1
        clean_sym = trading_symbol.strip().upper()
        res = self._symbol_to_id.get(clean_sym)
        if res is None:
            self._unmapped_keys.add(clean_sym)
        return res

    def resolve_by_spec(
        self,
        underlying_symbol: str,
        expiry_date: date,
        strike_price: Decimal,
        option_type: str,
    ) -> int | None:
        """Resolve contract identity by economic specification tuple."""
        self._lookup_count += 1
        norm_underlying = normalize_index_symbol(underlying_symbol)
        norm_opt_type = option_type.strip().upper()
        norm_strike = Decimal(str(strike_price))
        spec_key = (norm_underlying, expiry_date, norm_strike, norm_opt_type)
        res = self._spec_to_id.get(spec_key)
        if res is None:
            self._unmapped_keys.add(f"{norm_underlying}_{expiry_date}_{norm_strike}_{norm_opt_type}")
        return res

    def resolve_quote(
        self,
        quote: NormalizedQuote,
        underlying_symbol: str | None = None,
    ) -> int | None:
        """Resolve canonical contract ID for a NormalizedQuote using layered in-memory lookups."""
        if quote.contract_id is not None:
            return quote.contract_id

        # 1. Try instrument_key from raw_provider_meta
        ik = quote.raw_provider_meta.get("instrument_key") or quote.provider_instrument_id
        if ik:
            res = self.resolve(str(ik))
            if res is not None:
                return res

        # 2. Try quote symbol
        if quote.symbol:
            res = self.resolve_by_symbol(quote.symbol)
            if res is not None:
                return res

        # 3. Try specification tuple
        target_underlying = underlying_symbol or quote.symbol
        if target_underlying and quote.expiry_date and quote.strike_price is not None and quote.option_type:
            res = self.resolve_by_spec(
                target_underlying,
                quote.expiry_date,
                quote.strike_price,
                quote.option_type,
            )
            if res is not None:
                return res

        return None

    def get_unmapped_keys(self) -> list[str]:
        """Return list of observed unresolved instrument keys."""
        return sorted(list(self._unmapped_keys))

    def clear(self) -> None:
        """Clear all in-memory mappings."""
        self._key_to_id.clear()
        self._symbol_to_id.clear()
        self._spec_to_id.clear()
        self._unmapped_keys.clear()
        self._lookup_count = 0
        self._db_query_count = 0


# ===========================================================================
# 2. Immutable Frozen Ladder Snapshot Representation
# ===========================================================================

@dataclass(frozen=True)
class FrozenOptionChainLadder:
    """Immutable option-chain ladder state captured at a freeze boundary.

    Guarantees:
      - Detached from active accumulation buffer.
      - Subsequent ticks never mutate this instance.
      - Segregates quotes by (expiry, strike, option_type).
      - Step-2 validation integrated directly.
    """

    underlying_symbol: str
    underlying_index_id: int | None
    chain_scope: str
    expiry_scope: date | None
    sequence: int
    frozen_at: datetime
    market_timestamp: datetime
    source_timestamp: datetime | None
    ingestion_timestamp: datetime
    spot_price: Decimal | None
    near_futures_price: Decimal | None
    quotes: tuple[NormalizedQuote, ...]
    quotes_by_contract: dict[int, NormalizedQuote]
    expiries: tuple[date, ...]
    observed_strike_count: int
    is_truncated: bool
    quality_status: str
    quality_flags: dict[str, Any]
    raw_payload_hash: str

    def get_quote(self, contract_id: int) -> NormalizedQuote | None:
        """Retrieve a frozen quote by canonical contract ID."""
        return self.quotes_by_contract.get(contract_id)

    def get_ladder(
        self,
        expiry: date,
    ) -> list[tuple[Decimal, NormalizedQuote | None, NormalizedQuote | None]]:
        """Return structured, sorted strike ladder for a specific expiry.

        Returns:
            List of (strike_price, call_quote_or_None, put_quote_or_None) sorted ascending.
        """
        expiry_quotes = [q for q in self.quotes if q.expiry_date == expiry]
        strikes = sorted(list({q.strike_price for q in expiry_quotes if q.strike_price is not None}))

        calls_by_strike = {q.strike_price: q for q in expiry_quotes if q.option_type == "CE"}
        puts_by_strike = {q.strike_price: q for q in expiry_quotes if q.option_type == "PE"}

        ladder: list[tuple[Decimal, NormalizedQuote | None, NormalizedQuote | None]] = []
        for strike in strikes:
            ladder.append((strike, calls_by_strike.get(strike), puts_by_strike.get(strike)))
        return ladder

    def validate(
        self,
        strike_step: int | None = None,
        skew_tolerance_ms: int = DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    ) -> ValidationResult:
        """Run Step-2 validation primitives against this frozen ladder state.

        Returns:
            ValidationResult with composite status, flags, and warnings.
        """
        step = Decimal(str(strike_step)) if strike_step is not None else get_strike_step(self.underlying_symbol)

        # 1. Strike sequence validation per expiry
        seq_res = ValidationResult(status=DQ_STATUS_VALID)
        for exp in self.expiries:
            exp_quotes = [q for q in self.quotes if q.expiry_date == exp]
            exp_strikes = sorted(list({q.strike_price for q in exp_quotes if q.strike_price is not None}))
            res = validate_strike_sequence(exp_strikes, self.underlying_symbol, strike_step=step)
            seq_res = seq_res.merge(res)

        # 2. Chain completeness validation
        comp_res = validate_chain_completeness(
            observed_strike_count=self.observed_strike_count,
            expected_strike_count=None,
            is_truncated=self.is_truncated,
        )

        # 3. Multi-timestamp ordering audit
        time_res = validate_timestamps(
            market_timestamp=self.market_timestamp,
            source_timestamp=self.source_timestamp,
            ingestion_timestamp=self.ingestion_timestamp,
            available_at=self.frozen_at,
            skew_tolerance_ms=skew_tolerance_ms,
        )

        # 4. Check quote-level freshness and unmapped warnings
        flag_map: dict[str, Any] = {}
        for q in self.quotes:
            if q.freshness_status == FreshnessStatus.STALE:
                flag_map[DQ_FLAG_STALE_QUOTE] = True
            if q.contract_id is None:
                flag_map[DQ_FLAG_UNMAPPED_INSTRUMENT] = True

        quote_status = DQ_STATUS_WARNING if flag_map else DQ_STATUS_VALID
        quote_res = ValidationResult(status=quote_status, flags=flag_map)

        return seq_res.merge(comp_res).merge(time_res).merge(quote_res)


# ===========================================================================
# 3. Asynchronous WebSocket Ladder Buffer (Spec §26, §27, §28)
# ===========================================================================

class OptionChainLadderBuffer:
    """In-memory option chain ladder buffer aggregating streaming quotes.

    Concurrency Contract:
      - Protected by an internal asyncio.Lock.
      - Lock is held ONLY during short-lived in-memory quote update and freeze copy.
      - Lock is NEVER held during database writes, networking, or heavy calculation.
      - Freeze produces an immutable, detached FrozenOptionChainLadder.
      - Subsequent ticks enter the new accumulation window without mutating previous freezes.
    """

    def __init__(
        self,
        underlying_symbol: str,
        instrument_mapper: OptionChainInstrumentMapper,
        underlying_index_id: int | None = None,
    ) -> None:
        self.underlying_symbol = underlying_symbol.strip().upper()
        self.underlying_index_id = underlying_index_id
        self.instrument_mapper = instrument_mapper

        self._lock = asyncio.Lock()
        # Key: (expiry_date, strike_price, option_type) -> NormalizedQuote
        self._active_quotes: dict[tuple[date, Decimal, str], NormalizedQuote] = {}
        self._active_spot_price: Decimal | None = None
        self._active_near_futures_price: Decimal | None = None
        self._last_market_timestamp: datetime | None = None
        self._last_source_timestamp: datetime | None = None
        self._sequence: int = 0
        self._unmapped_ticks: list[dict[str, Any]] = []

    @property
    def sequence(self) -> int:
        """Current accumulation sequence counter."""
        return self._sequence

    async def get_active_count(self) -> int:
        """Return the number of distinct active quotes currently buffered under lock."""
        async with self._lock:
            return len(self._active_quotes)

    async def update_tick(self, quote: NormalizedQuote) -> bool:
        """Ingest an incoming tick/quote into the active accumulation ladder.

        Returns:
            True if quote was mapped and buffered, False otherwise.
        """
        async with self._lock:
            # Normalize quote timestamp to UTC timezone-awareness
            q_ts = quote.timestamp
            if q_ts is not None:
                q_ts = q_ts.astimezone(UTC) if q_ts.tzinfo is None else q_ts
                quote.timestamp = q_ts

            # Handle index spot quotes
            if quote.instrument_type == "INDEX" or quote.symbol == self.underlying_symbol:
                self._active_spot_price = quote.ltp
                self._last_market_timestamp = q_ts
                return True

            # Handle futures quotes
            if quote.instrument_type == "FUTURE":
                self._active_near_futures_price = quote.ltp
                self._last_market_timestamp = q_ts
                return True

            # Resolve canonical contract ID in-memory (0 DB queries)
            c_id = self.instrument_mapper.resolve_quote(quote, self.underlying_symbol)
            if c_id is None:
                self._unmapped_ticks.append({
                    "symbol": quote.symbol,
                    "timestamp": q_ts,
                    "instrument_key": quote.raw_provider_meta.get("instrument_key"),
                })
                # Set DQ flag on quote metadata
                quote.raw_provider_meta["unmapped_contract"] = True
            else:
                quote.contract_id = c_id

            # Ensure minimal required contract dimensions exist
            if quote.expiry_date is None or quote.strike_price is None or not quote.option_type:
                logger.warning(
                    redact_secrets(
                        f"Incomplete option quote dimensions for {quote.symbol}: "
                        f"expiry={quote.expiry_date}, strike={quote.strike_price}, type={quote.option_type}"
                    )
                )
                return False

            key = (quote.expiry_date, Decimal(str(quote.strike_price)), quote.option_type.strip().upper())

            # Latest quote for (expiry, strike, option_type) overwrites prior active quote
            self._active_quotes[key] = quote
            self._last_market_timestamp = q_ts
            self._last_source_timestamp = q_ts
            return True

    async def freeze(
        self,
        chain_scope: str = "ALL_SUPPORTED_EXPIRIES",
        expiry_scope: date | None = None,
        reset_active: bool = True,
    ) -> FrozenOptionChainLadder:
        """Capture an immutable, detached snapshot of the current ladder buffer.

        Execution is protected by asyncio.Lock. The lock is released immediately
        after deep-copying active quotes and advancing the sequence. All post-freeze
        normalization, validation, and serialization occur outside the lock.
        """
        frozen_time = datetime.now(UTC)

        async with self._lock:
            # Capture and deepcopy active quotes under lock to guarantee immutability
            if expiry_scope is not None:
                captured_quotes = {
                    k: copy.deepcopy(v)
                    for k, v in self._active_quotes.items()
                    if k[0] == expiry_scope
                }
                if reset_active:
                    for k in list(captured_quotes.keys()):
                        del self._active_quotes[k]
            else:
                captured_quotes = {k: copy.deepcopy(v) for k, v in self._active_quotes.items()}
                if reset_active:
                    self._active_quotes.clear()

            captured_spot = self._active_spot_price
            captured_futures = self._active_near_futures_price
            captured_market_ts = self._last_market_timestamp or frozen_time
            captured_source_ts = self._last_source_timestamp
            current_seq = self._sequence
            self._sequence += 1

        # All remaining processing happens OUTSIDE the lock
        quotes_list = list(captured_quotes.values())
        quotes_by_contract: dict[int, NormalizedQuote] = {}
        expiries_set: set[date] = set()
        strikes_set: set[Decimal] = set()

        for q in quotes_list:
            if q.contract_id is not None:
                quotes_by_contract[q.contract_id] = q
            if q.expiry_date:
                expiries_set.add(q.expiry_date)
            if q.strike_price is not None:
                strikes_set.add(q.strike_price)

        sorted_expiries = tuple(sorted(list(expiries_set)))
        observed_strike_count = len(strikes_set)

        # Deterministic SHA-256 payload hash of captured quotes for provenance
        hash_payload = json.dumps(
            [
                {
                    "c_id": q.contract_id,
                    "strike": str(q.strike_price),
                    "type": q.option_type,
                    "exp": q.expiry_date.isoformat() if q.expiry_date else None,
                    "ltp": str(q.ltp) if q.ltp is not None else None,
                    "oi": q.open_interest,
                }
                for q in sorted(quotes_list, key=lambda x: (x.expiry_date or date.min, x.strike_price or 0, x.option_type or ""))
            ],
            sort_keys=True,
        )
        raw_hash = hashlib.sha256(redact_secrets(hash_payload).encode("utf-8")).hexdigest()

        return FrozenOptionChainLadder(
            underlying_symbol=self.underlying_symbol,
            underlying_index_id=self.underlying_index_id,
            chain_scope=chain_scope,
            expiry_scope=expiry_scope,
            sequence=current_seq,
            frozen_at=frozen_time,
            market_timestamp=captured_market_ts,
            source_timestamp=captured_source_ts,
            ingestion_timestamp=frozen_time,
            spot_price=captured_spot,
            near_futures_price=captured_futures,
            quotes=tuple(quotes_list),
            quotes_by_contract=quotes_by_contract,
            expiries=sorted_expiries,
            observed_strike_count=observed_strike_count,
            is_truncated=False,
            quality_status=DQ_STATUS_VALID,
            quality_flags={},
            raw_payload_hash=raw_hash,
        )
