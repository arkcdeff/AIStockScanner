"""Option Chain Persistence Service — Milestone G.11-OPT-1 (Step 4).

Authority: `docs/G11-OPT1-ARCHITECTURE.md` (SPEC-R2 §11, §12, §13, §18, §21.3)
and `docs/G11-OPT1-IMPLEMENTATION-SPECIFICATION-v1.1.md` (§8, §9, §10, §11, §17, §18).

Provides:
  1. ``OptionChainPersistenceService.persist_snapshot()`` — atomic, idempotent,
     revision-safe persistence of one provider-neutral ``NormalizedOptionChain``
     header plus every associated canonical quote row.
  2. ``OptionChainPersistenceService.get_pit_snapshot()`` — point-in-time safe
     retrieval implementing the exact SPEC-R2 §17/§18 selection order
     (PIT visibility -> quality filter -> highest visible revision -> latest
     eligible market observation).

No analytics are computed here: source Greeks/IV, ΔOI, prices and contract
identities are persisted exactly as supplied (or resolved through existing
authoritative mappings). No new pricing engine, no migration, no schema change.

TRANSACTION OWNERSHIP CONTRACT
------------------------------
* ``persist_snapshot(..., session=None)``
    The service creates and owns its own ``AsyncSession``. The partition DDL,
    the snapshot header and every accepted quote row are inserted inside that
    session's single transaction. The service commits exactly once on success
    and rolls the whole transaction back on failure, leaving no partial
    snapshot (and no orphan partition, because PostgreSQL DDL is transactional).
* ``persist_snapshot(..., session=<caller session>)``
    The caller owns the transaction. The service **never** calls ``commit()``
    on a caller-owned session. All work is performed inside a SAVEPOINT
    (``session.begin_nested()``): success releases the savepoint, and any quote
    insertion failure rolls the savepoint back, undoing the header, every quote
    row and any partition DDL issued inside the savepoint while leaving the
    caller's outer transaction usable and uncommitted.
    The caller remains responsible for committing or rolling back its own
    transaction. If the caller rolls back, the persisted snapshot disappears
    with it — that is correct ownership semantics.

Concurrency note: transaction-level advisory locks (revision allocation) and
transactional DDL (partition provisioning) are held until the *outermost*
transaction completes, so a caller that keeps its transaction open also holds
the logical-observation revision lock.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, TypeVar

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.providers.models import NormalizedOptionChain, NormalizedQuote
from app.providers.upstox_historical_provider import redact_secrets
from app.services.option_chain_config import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    DQ_FLAG_MISSING_CONTRACT,
    DQ_STATUS_ACCEPT_WITH_FLAG,
    DQ_STATUS_REJECTED,
    DQ_STATUS_VALID,
    get_strike_step,
    normalize_index_symbol,
)
from app.services.option_chain_partition_service import (
    ensure_quote_partition_exists,
    get_monthly_partition_bounds,
)
from app.services.option_chain_validation_service import (
    ValidationResult,
    validate_snapshot_header,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# 1. Constants, exceptions and canonical-identity helpers
# ===========================================================================

#: Persistence outcome vocabulary (Step 4 contract).
PERSIST_STATUS_PERSISTED = "PERSISTED"
PERSIST_STATUS_DUPLICATE_IGNORED = "DUPLICATE_IGNORED"

#: Supported chain scopes (mirrors the DB CheckConstraint).
CHAIN_SCOPE_ALL_EXPIRIES = "ALL_SUPPORTED_EXPIRIES"
CHAIN_SCOPE_SINGLE_EXPIRY = "SINGLE_EXPIRY"

#: Canonical payload hashing version. Bump only with an explicit Step-4 review.
CANONICAL_PAYLOAD_VERSION = "OPT1-CANONICAL-PAYLOAD-V1"

#: Default market timezone used for trade-date derivation (SPEC-R2 §9.1).
DEFAULT_MARKET_TIMEZONE_NAME = "Asia/Kolkata"
_IST_FALLBACK = timezone(timedelta(hours=5, minutes=30), name="IST")

#: Advisory-lock namespace for concurrency-safe revision allocation.
_REVISION_LOCK_NAMESPACE = "option_chain_snapshot_revision_lock"

#: PostgreSQL SQLSTATE for unique_violation.
_PG_UNIQUE_VIOLATION = "23505"

#: Column scale mapping used for deterministic numeric canonicalisation.
_PRICE_SCALE = 4
_RATIO_SCALE = 6

#: Source Greek key -> persisted OptionChainQuote column.
SOURCE_GREEK_COLUMNS: dict[str, str] = {
    "iv": "source_iv",
    "delta": "source_delta",
    "gamma": "source_gamma",
    "theta": "source_theta",
    "vega": "source_vega",
    "rho": "source_rho",
}


class OptionChainPersistenceError(RuntimeError):
    """Raised when a snapshot cannot be persisted or safely resolved."""


class UnderlyingResolutionError(OptionChainPersistenceError):
    """Raised when no authoritative MarketIndex mapping exists for an underlying."""


class OptionChainValidationRejectedError(OptionChainPersistenceError):
    """Raised when OPT-1 validation classes an observation as REJECTED.

    Nothing is persisted when this is raised (``allow_rejected=False``).
    """

    def __init__(self, message: str, validation: ValidationResult) -> None:
        super().__init__(message)
        self.validation = validation


class SnapshotRevisionConflictError(OptionChainPersistenceError):
    """Raised when a revision uniqueness conflict cannot be proven to be a replay."""


@lru_cache(maxsize=16)
def get_market_timezone(timezone_name: str = DEFAULT_MARKET_TIMEZONE_NAME) -> tzinfo:
    """Resolve a market timezone name to a ``tzinfo`` with a safe IST fallback.

    ``zoneinfo`` requires the ``tzdata`` package on Windows; when the zone
    database is unavailable the function falls back to a fixed IST offset
    (India has observed no DST since 1945) so trade-date derivation stays
    deterministic and never silently degrades to UTC.
    """
    name = (timezone_name or DEFAULT_MARKET_TIMEZONE_NAME).strip()
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # pragma: no cover - depends on host tz database
        if name.upper() in {"ASIA/KOLKATA", "IST", "ASIA/CALCUTTA"}:
            logger.warning(
                "ZoneInfo unavailable for '%s'; using fixed +05:30 IST offset", name
            )
            return _IST_FALLBACK
        return UTC


def to_utc(value: datetime) -> datetime:
    """Normalise a datetime to timezone-aware UTC (naive input is assumed UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def derive_trade_date(
    market_timestamp: datetime,
    timezone_name: str = DEFAULT_MARKET_TIMEZONE_NAME,
) -> date:
    """Derive the partition trade date from the validated market observation.

    The observation timestamp is converted into the market timezone
    (``Asia/Kolkata`` by default, matching ``MarketIndex.timezone``) before the
    calendar date is taken, so a 20:00 UTC observation correctly routes to the
    next IST trading date.
    """
    return to_utc(market_timestamp).astimezone(get_market_timezone(timezone_name)).date()


def _canonical_number(value: Any) -> str | None:
    """Deterministic, scale-insensitive decimal canonicalisation for hashing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    normalized = decimal_value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _canonical_datetime(value: datetime | None) -> str | None:
    """Canonical UTC ISO-8601 rendering for hashing."""
    if value is None:
        return None
    return to_utc(value).isoformat()


def _canonical_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_decimal(value: Any, scale: int) -> Decimal | None:
    """Convert a market value to a Decimal quantised to the column scale.

    PostgreSQL ``NUMERIC(p, s)`` silently rounds excess precision; quantising
    here keeps the persisted value identical to the value covered by the
    canonical payload hash, so replay detection stays exact.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise OptionChainPersistenceError(
                f"Non-numeric market value {value!r} cannot be persisted"
            ) from exc
    return decimal_value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OptionChainPersistenceError(
            f"Non-integer quantity value {value!r} cannot be persisted"
        ) from exc


def build_canonical_payload_hash(
    header_fields: Mapping[str, Any],
    quote_fields: Sequence[Mapping[str, Any]],
) -> str:
    """Compute the canonical SHA-256 payload hash of one logical observation.

    The canonical payload covers immutable market *facts and identity* only:
    metadata that legitimately differs between two observations of the same
    facts (``ingestion_timestamp``, ``available_at``, ``revision``,
    ``batch_id``, ``source_reference``, ``previous_snapshot_id``, DQ
    annotations) is excluded, while computed-but-persisted analytics that the
    specification treats as immutable observation content (interval/session
    ΔOI, calculated analytics, calculation context) are included.

    Secrets are redacted (``redact_secrets``) before hashing so credentials can
    never influence the hash or leak through provenance.
    """
    payload = {
        "canonical_version": CANONICAL_PAYLOAD_VERSION,
        "header": dict(header_fields),
        "quotes": [dict(row) for row in quote_fields],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(redact_secrets(serialized).encode("utf-8")).hexdigest()


def build_pit_quote_statement(snapshot_id: int, trade_date: date) -> Select:
    """Build the canonical quote statement filtered by snapshot AND trade_date.

    Filtering on both ``snapshot_id`` and ``trade_date`` lets PostgreSQL prune
    every monthly partition except the one holding the snapshot's quotes.
    """
    return (
        select(OptionChainQuote)
        .where(
            OptionChainQuote.snapshot_id == snapshot_id,
            OptionChainQuote.trade_date == trade_date,
        )
        .order_by(OptionChainQuote.strike_price.asc(), OptionChainQuote.option_type.asc())
    )


# ===========================================================================
# 2. Request/result data structures
# ===========================================================================

@dataclass(frozen=True, slots=True)
class CalculationContext:
    """Immutable Greek-calculation reproducibility context (SPEC-R2 §16).

    Values are persisted verbatim. This service never derives them, so an
    observation carries a calculation context only when a caller supplies one.
    """

    calculation_spot_price: Decimal | None = None
    risk_free_rate_used: Decimal | None = None
    dividend_yield_used: Decimal | None = None
    time_to_expiry_years: Decimal | None = None
    time_to_expiry_convention: str | None = None
    pricing_model_version: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteEnrichment:
    """Optional per-contract enrichment persisted verbatim (no computation).

    ``previous_snapshot_id`` / ``interval_seconds`` / ``interval_change_in_oi``
    / ``session_change_in_oi`` carry the SPEC-R2 §14.2/§14.3 ΔOI references that
    a caller may already have derived. ``calculated_*`` carries previously
    computed analytics (EXISTING engine output only) — Step 4 never solves
    Greeks or IV and never invents missing values.
    """

    previous_snapshot_id: int | None = None
    interval_seconds: int | None = None
    interval_change_in_oi: int | None = None
    session_change_in_oi: int | None = None
    calculated_iv: Decimal | None = None
    calculated_delta: Decimal | None = None
    calculated_gamma: Decimal | None = None
    calculated_theta: Decimal | None = None
    calculated_vega: Decimal | None = None
    calculated_rho: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SkippedQuote:
    """A quote that could not be persisted, with the reason it was excluded."""

    symbol: str | None
    expiry_date: date | None
    strike_price: str | None
    option_type: str | None
    provider_instrument_id: str | None
    reason: str

    def to_flag_detail(self) -> dict[str, Any]:
        """JSON-serialisable detail entry for the DQ flag payload."""
        return {
            "symbol": self.symbol,
            "expiry_date": _canonical_date(self.expiry_date),
            "strike_price": self.strike_price,
            "option_type": self.option_type,
            "provider_instrument_id": self.provider_instrument_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PersistSnapshotResult:
    """Outcome of ``persist_snapshot()``."""

    status: str
    snapshot_id: int
    revision: int
    underlying_index_id: int
    chain_scope: str
    expiry_scope: date | None
    market_timestamp: datetime
    available_at: datetime
    trade_date: date
    partition_name: str
    quote_count: int
    canonical_payload_hash: str
    raw_payload_hash: str
    quality_status: str
    quality_flags: dict[str, Any]
    is_truncated: bool
    validation: ValidationResult
    skipped_quotes: tuple[SkippedQuote, ...] = ()

    @property
    def is_duplicate(self) -> bool:
        """True when the observation was already persisted (no new revision)."""
        return self.status == PERSIST_STATUS_DUPLICATE_IGNORED


@dataclass(frozen=True, slots=True)
class PitQuoteRecord:
    """Immutable, detached quote row returned by point-in-time retrieval."""

    id: int
    snapshot_id: int
    contract_id: int
    trade_date: date
    strike_price: Decimal
    option_type: str
    expiry_date: date
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    bid_qty: int | None
    ask_qty: int | None
    volume: int | None
    open_interest: int | None
    source_change_in_oi: int | None
    interval_change_in_oi: int | None
    interval_seconds: int | None
    previous_snapshot_id: int | None
    session_change_in_oi: int | None
    source_iv: Decimal | None
    source_delta: Decimal | None
    source_gamma: Decimal | None
    source_theta: Decimal | None
    source_vega: Decimal | None
    source_rho: Decimal | None
    calculated_iv: Decimal | None
    calculated_delta: Decimal | None
    calculated_gamma: Decimal | None
    calculated_theta: Decimal | None
    calculated_vega: Decimal | None
    calculated_rho: Decimal | None
    quality_flags: dict[str, Any]

    @classmethod
    def from_model(cls, row: OptionChainQuote) -> PitQuoteRecord:
        """Build a detached record from an ORM quote row."""
        return cls(
            id=row.id,
            snapshot_id=row.snapshot_id,
            contract_id=row.contract_id,
            trade_date=row.trade_date,
            strike_price=row.strike_price,
            option_type=row.option_type,
            expiry_date=row.expiry_date,
            ltp=row.ltp,
            bid=row.bid,
            ask=row.ask,
            bid_qty=row.bid_qty,
            ask_qty=row.ask_qty,
            volume=row.volume,
            open_interest=row.open_interest,
            source_change_in_oi=row.source_change_in_oi,
            interval_change_in_oi=row.interval_change_in_oi,
            interval_seconds=row.interval_seconds,
            previous_snapshot_id=row.previous_snapshot_id,
            session_change_in_oi=row.session_change_in_oi,
            source_iv=row.source_iv,
            source_delta=row.source_delta,
            source_gamma=row.source_gamma,
            source_theta=row.source_theta,
            source_vega=row.source_vega,
            source_rho=row.source_rho,
            calculated_iv=row.calculated_iv,
            calculated_delta=row.calculated_delta,
            calculated_gamma=row.calculated_gamma,
            calculated_theta=row.calculated_theta,
            calculated_vega=row.calculated_vega,
            calculated_rho=row.calculated_rho,
            quality_flags=dict(row.quality_flags or {}),
        )


@dataclass(frozen=True, slots=True)
class PitSnapshotResult:
    """Point-in-time snapshot header plus its canonically filtered quotes."""

    snapshot_id: int
    underlying_index_id: int
    chain_scope: str
    expiry_scope: date | None
    spot_price: Decimal | None
    market_timestamp: datetime
    source_timestamp: datetime | None
    ingestion_timestamp: datetime
    available_at: datetime
    source_name: str
    source_reference: str | None
    raw_payload_hash: str
    normalization_version: str
    batch_id: str
    revision: int
    quality_status: str
    quality_flags: dict[str, Any]
    is_truncated: bool
    expected_strike_count: int | None
    observed_strike_count: int | None
    near_futures_price: Decimal | None
    near_futures_contract_id: int | None
    calculation_spot_price: Decimal | None
    risk_free_rate_used: Decimal | None
    dividend_yield_used: Decimal | None
    time_to_expiry_years: Decimal | None
    time_to_expiry_convention: str | None
    pricing_model_version: str | None
    trade_date: date
    evaluation_timestamp: datetime
    quotes: tuple[PitQuoteRecord, ...]

    def quote_by_contract(self, contract_id: int) -> PitQuoteRecord | None:
        """Return the quote record for a canonical contract id, if present."""
        for quote in self.quotes:
            if quote.contract_id == contract_id:
                return quote
        return None


def is_unique_violation(exc: IntegrityError) -> bool:
    """Detect a PostgreSQL unique_violation without importing the asyncpg module."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == _PG_UNIQUE_VIOLATION:
        return True
    return "uq_opt_snapshots" in str(orig if orig is not None else exc)


@dataclass(frozen=True, slots=True)
class _HeaderPlan:
    """Internal, quantised header payload used for both hashing and insert."""

    underlying_index_id: int
    chain_scope: str
    expiry_scope: date | None
    spot_price: Decimal | None
    market_timestamp: datetime
    source_timestamp: datetime | None
    ingestion_timestamp: datetime
    available_at: datetime
    source_name: str
    source_reference: str | None
    normalization_version: str
    batch_id: str
    raw_payload_hash: str | None
    is_truncated: bool
    expected_strike_count: int | None
    observed_strike_count: int | None
    near_futures_price: Decimal | None
    near_futures_contract_id: int | None
    quality_status: str
    quality_flags: dict[str, Any]
    calculation_context: CalculationContext
    trade_date: date
    timezone_name: str

    def canonical_fields(self) -> dict[str, Any]:
        """Canonical, immutable-fact projection used for the payload hash."""
        return {
            "underlying_index_id": self.underlying_index_id,
            "chain_scope": self.chain_scope,
            "expiry_scope": _canonical_date(self.expiry_scope),
            "spot_price": _canonical_number(self.spot_price),
            "market_timestamp": _canonical_datetime(self.market_timestamp),
            "source_timestamp": _canonical_datetime(self.source_timestamp),
            "source_name": self.source_name,
            "is_truncated": self.is_truncated,
            "expected_strike_count": self.expected_strike_count,
            "observed_strike_count": self.observed_strike_count,
            "near_futures_price": _canonical_number(self.near_futures_price),
            "near_futures_contract_id": self.near_futures_contract_id,
            "calculation_spot_price": _canonical_number(
                self.calculation_context.calculation_spot_price
            ),
            "risk_free_rate_used": _canonical_number(
                self.calculation_context.risk_free_rate_used
            ),
            "dividend_yield_used": _canonical_number(
                self.calculation_context.dividend_yield_used
            ),
            "time_to_expiry_years": _canonical_number(
                self.calculation_context.time_to_expiry_years
            ),
            "time_to_expiry_convention": self.calculation_context.time_to_expiry_convention,
            "pricing_model_version": self.calculation_context.pricing_model_version,
        }


@dataclass(frozen=True, slots=True)
class _QuotePlan:
    """Internal, quantised quote payload used for both hashing and insert."""

    contract_id: int
    strike_price: Decimal
    option_type: str
    expiry_date: date
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    bid_qty: int | None
    ask_qty: int | None
    volume: int | None
    open_interest: int | None
    source_change_in_oi: int | None
    interval_change_in_oi: int | None
    interval_seconds: int | None
    previous_snapshot_id: int | None
    session_change_in_oi: int | None
    source_iv: Decimal | None
    source_delta: Decimal | None
    source_gamma: Decimal | None
    source_theta: Decimal | None
    source_vega: Decimal | None
    source_rho: Decimal | None
    calculated_iv: Decimal | None
    calculated_delta: Decimal | None
    calculated_gamma: Decimal | None
    calculated_theta: Decimal | None
    calculated_vega: Decimal | None
    calculated_rho: Decimal | None

    def canonical_fields(self) -> dict[str, Any]:
        """Canonical, immutable-fact projection used for the payload hash."""
        return {
            "contract_id": self.contract_id,
            "strike_price": _canonical_number(self.strike_price),
            "option_type": self.option_type,
            "expiry_date": _canonical_date(self.expiry_date),
            "ltp": _canonical_number(self.ltp),
            "bid": _canonical_number(self.bid),
            "ask": _canonical_number(self.ask),
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "source_change_in_oi": self.source_change_in_oi,
            "interval_change_in_oi": self.interval_change_in_oi,
            "interval_seconds": self.interval_seconds,
            "session_change_in_oi": self.session_change_in_oi,
            "source_iv": _canonical_number(self.source_iv),
            "source_delta": _canonical_number(self.source_delta),
            "source_gamma": _canonical_number(self.source_gamma),
            "source_theta": _canonical_number(self.source_theta),
            "source_vega": _canonical_number(self.source_vega),
            "source_rho": _canonical_number(self.source_rho),
            "calculated_iv": _canonical_number(self.calculated_iv),
            "calculated_delta": _canonical_number(self.calculated_delta),
            "calculated_gamma": _canonical_number(self.calculated_gamma),
            "calculated_theta": _canonical_number(self.calculated_theta),
            "calculated_vega": _canonical_number(self.calculated_vega),
            "calculated_rho": _canonical_number(self.calculated_rho),
        }


@dataclass(frozen=True, slots=True)
class _PersistOverrides:
    """Explicit per-call overrides applied on top of the normalized chain."""

    underlying_index_id: int | None = None
    chain_scope: str | None = None
    expiry_scope: date | None = None
    market_timestamp: datetime | None = None
    source_timestamp: datetime | None = None
    ingestion_timestamp: datetime | None = None
    available_at: datetime | None = None
    source_name: str | None = None
    source_reference: str | None = None
    batch_id: str | None = None
    normalization_version: str | None = None
    exchange: str | None = None
    segment: str | None = None
    calculation_context: CalculationContext | None = None
    quote_enrichments: Mapping[int, QuoteEnrichment] | None = None
    near_futures_price: Decimal | None = None
    near_futures_contract_id: int | None = None
    allow_rejected: bool = False
    skew_tolerance_ms: int | None = None


_ResultT = TypeVar("_ResultT")


class OptionChainPersistenceService:
    """Atomic, idempotent, point-in-time safe OPT-1 option-chain persistence.

    Parameters
    ----------
    session:
        Optional session used when a per-call ``session`` is not supplied.
        When neither is present the service owns the transaction itself.
    exchange / segment:
        Exchange and segment components of the canonical contract identity
        used when resolving quotes that do not carry a ``contract_id``.
    skew_tolerance_ms:
        Clock-skew tolerance forwarded to OPT-1 timestamp validation.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        exchange: str = "NSE",
        segment: str = "NFO",
        skew_tolerance_ms: int = DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    ) -> None:
        self._session = session
        self._exchange = exchange
        self._segment = segment
        self._skew_tolerance_ms = skew_tolerance_ms

    # -----------------------------------------------------------------------
    # 3.1 Public API — atomic persistence
    # -----------------------------------------------------------------------

    async def persist_snapshot(
        self,
        chain: NormalizedOptionChain,
        *,
        session: AsyncSession | None = None,
        underlying_index_id: int | None = None,
        chain_scope: str | None = None,
        expiry_scope: date | None = None,
        market_timestamp: datetime | None = None,
        source_timestamp: datetime | None = None,
        ingestion_timestamp: datetime | None = None,
        available_at: datetime | None = None,
        source_name: str | None = None,
        source_reference: str | None = None,
        batch_id: str | None = None,
        normalization_version: str | None = None,
        exchange: str | None = None,
        segment: str | None = None,
        calculation_context: CalculationContext | None = None,
        quote_enrichments: Mapping[int, QuoteEnrichment] | None = None,
        near_futures_price: Decimal | None = None,
        near_futures_contract_id: int | None = None,
        allow_rejected: bool = False,
        skew_tolerance_ms: int | None = None,
    ) -> PersistSnapshotResult:
        """Atomically persist one option-chain observation.

        The snapshot header and every accepted quote row commit together as one
        logical unit. Idempotent replays of an identical logical key and
        identical canonical payload hash return
        ``PERSIST_STATUS_DUPLICATE_IGNORED`` without creating a new revision,
        while a corrected payload creates a new immutable revision.

        Raises
        ------
        UnderlyingResolutionError:
            No authoritative ``MarketIndex`` mapping exists for the underlying.
        OptionChainValidationRejectedError:
            OPT-1 validation returned REJECTED and ``allow_rejected`` is False.
        SnapshotRevisionConflictError:
            A revision uniqueness conflict could not be proven to be a replay.
        IntegrityError:
            Any other database constraint violation (full rollback).
        """
        overrides = _PersistOverrides(
            underlying_index_id=underlying_index_id,
            chain_scope=chain_scope,
            expiry_scope=expiry_scope,
            market_timestamp=market_timestamp,
            source_timestamp=source_timestamp,
            ingestion_timestamp=ingestion_timestamp,
            available_at=available_at,
            source_name=source_name,
            source_reference=source_reference,
            batch_id=batch_id,
            normalization_version=normalization_version,
            exchange=exchange,
            segment=segment,
            calculation_context=calculation_context,
            quote_enrichments=quote_enrichments,
            near_futures_price=near_futures_price,
            near_futures_contract_id=near_futures_contract_id,
            allow_rejected=allow_rejected,
            skew_tolerance_ms=skew_tolerance_ms,
        )

        active_session = session or self._session
        if active_session is not None:
            return await self._persist_impl(
                chain, active_session, owns_transaction=False, overrides=overrides
            )

        factory = get_session_factory()
        async with factory() as owned_session:
            return await self._persist_impl(
                chain, owned_session, owns_transaction=True, overrides=overrides
            )


    async def _persist_impl(
        self,
        chain: NormalizedOptionChain,
        session: AsyncSession,
        *,
        owns_transaction: bool,
        overrides: _PersistOverrides,
    ) -> PersistSnapshotResult:
        """Orchestrate planning, duplicate detection and the atomic write."""
        header_plan, quote_plans, skipped, validation = await self._plan_snapshot(
            session, chain, overrides
        )
        canonical_hash = self._canonical_hash(header_plan, quote_plans)
        partition_name = get_monthly_partition_bounds(header_plan.trade_date)[2]
        lock_key = self._revision_lock_key(header_plan)

        async def _write() -> PersistSnapshotResult:
            # D4 — serialise revision allocation for this logical observation.
            await self._acquire_revision_lock(session, lock_key)

            existing = await self._find_matching_revision(session, header_plan, canonical_hash)
            if existing is not None:
                return await self._duplicate_result(
                    session, existing, header_plan, canonical_hash, partition_name,
                    validation, skipped,
                )

            revision = await self._next_revision(session, header_plan)

            # D2 — provision the monthly partition before quote insertion
            # (transactional DDL: rolled back with the snapshot on failure).
            await ensure_quote_partition_exists(header_plan.trade_date, session=session)

            snapshot = self._insert_header(session, header_plan, revision, canonical_hash)
            await session.flush()  # obtain the identity id; surface header errors

            quote_rows = [
                self._build_quote_row(plan, snapshot.id, header_plan.trade_date)
                for plan in quote_plans
            ]
            session.add_all(quote_rows)
            await session.flush()  # surface quote constraint errors inside this unit

            return PersistSnapshotResult(
                status=PERSIST_STATUS_PERSISTED,
                snapshot_id=snapshot.id,
                revision=revision,
                underlying_index_id=header_plan.underlying_index_id,
                chain_scope=header_plan.chain_scope,
                expiry_scope=header_plan.expiry_scope,
                market_timestamp=header_plan.market_timestamp,
                available_at=header_plan.available_at,
                trade_date=header_plan.trade_date,
                partition_name=partition_name,
                quote_count=len(quote_rows),
                canonical_payload_hash=canonical_hash,
                raw_payload_hash=header_plan.raw_payload_hash or canonical_hash,
                quality_status=header_plan.quality_status,
                quality_flags=dict(header_plan.quality_flags),
                is_truncated=header_plan.is_truncated,
                validation=validation,
                skipped_quotes=skipped,
            )

        try:
            return await self._run_in_ownership(session, owns_transaction, _write)
        except IntegrityError as exc:
            if not is_unique_violation(exc):
                # A genuine constraint failure (e.g. a quote CHECK violation):
                # the transaction/savepoint has already been rolled back by
                # ``_run_in_ownership``, so no partial snapshot exists.
                raise
            # Concurrency safety net: a competing writer may have committed the
            # revision we computed. Verify the resulting persisted logical
            # snapshot before reporting an idempotent replay — never report
            # success on the strength of a caught uniqueness violation alone.
            existing = await self._find_matching_revision(session, header_plan, canonical_hash)
            if existing is None:
                raise SnapshotRevisionConflictError(
                    "Revision uniqueness conflict for logical snapshot key "
                    f"'{lock_key}' could not be verified as an idempotent replay; "
                    "no snapshot was persisted"
                ) from exc
            return await self._duplicate_result(
                session, existing, header_plan, canonical_hash, partition_name,
                validation, skipped,
            )

    async def _run_in_ownership(
        self,
        session: AsyncSession,
        owns_transaction: bool,
        writer: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Execute ``writer`` under the documented transaction ownership rules."""
        if owns_transaction:
            try:
                result = await writer()
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result

        # Caller-owned transaction: use a SAVEPOINT and never commit the
        # caller's transaction. A failure rolls the savepoint back.
        async with session.begin_nested():
            return await writer()

    # -----------------------------------------------------------------------
    # 3.2 Planning — scope, authoritative underlying, contracts, validation
    # -----------------------------------------------------------------------

    async def _plan_snapshot(
        self,
        session: AsyncSession,
        chain: NormalizedOptionChain,
        overrides: _PersistOverrides,
    ) -> tuple[_HeaderPlan, list[_QuotePlan], tuple[SkippedQuote, ...], ValidationResult]:
        """Build the validated, quantised persistence plan for one observation.

        This runs OPT-1 validation (SPEC-R2 §18) before any write, resolves the
        underlying through authoritative ``MarketIndex`` mappings and resolves
        every quote to an existing canonical ``DerivativeContract`` without
        fabricating contracts or identifiers.
        """
        chain_scope = overrides.chain_scope or chain.chain_scope or CHAIN_SCOPE_SINGLE_EXPIRY
        if chain_scope not in (CHAIN_SCOPE_ALL_EXPIRIES, CHAIN_SCOPE_SINGLE_EXPIRY):
            raise OptionChainPersistenceError(
                f"Unsupported chain_scope '{chain_scope}'; expected "
                f"'{CHAIN_SCOPE_ALL_EXPIRIES}' or '{CHAIN_SCOPE_SINGLE_EXPIRY}'"
            )

        expiry_scope_value = (
            overrides.expiry_scope
            if overrides.expiry_scope is not None
            else chain.expiry_scope
        )
        if chain_scope == CHAIN_SCOPE_ALL_EXPIRIES:
            expiry_scope_value = None
        elif expiry_scope_value is None:
            raise OptionChainPersistenceError(
                "chain_scope='SINGLE_EXPIRY' requires an expiry_scope value"
            )

        index = await self._resolve_underlying_index(
            session, overrides.underlying_index_id, chain.underlying_symbol
        )
        timezone_name = (index.timezone or "").strip() or DEFAULT_MARKET_TIMEZONE_NAME

        market_ts = to_utc(
            overrides.market_timestamp or chain.market_timestamp or chain.as_of_timestamp
        )
        raw_source_ts = overrides.source_timestamp or chain.source_timestamp
        source_ts = to_utc(raw_source_ts) if raw_source_ts is not None else None
        ingestion_ts = (
            to_utc(overrides.ingestion_timestamp)
            if overrides.ingestion_timestamp is not None
            else datetime.now(UTC)
        )
        available_ts = (
            to_utc(overrides.available_at)
            if overrides.available_at is not None
            else ingestion_ts
        )

        source_name_value = self._bounded_text(
            overrides.source_name or chain.source_name or "UNKNOWN", 64, "source_name"
        )
        raw_reference = overrides.source_reference or chain.source_reference
        source_reference_value = (
            self._bounded_text(raw_reference, 256, "source_reference")
            if raw_reference is not None
            else None
        )
        normalization_version_value = self._bounded_text(
            overrides.normalization_version or "1.0.0", 32, "normalization_version"
        )
        batch_id_value = self._bounded_text(
            overrides.batch_id
            or chain.batch_id
            or self._default_batch_id(source_name_value, market_ts),
            64,
            "batch_id",
        )

        quote_plans, skipped = await self._resolve_quote_plans(session, index, chain, overrides)


        strikes = sorted({plan.strike_price for plan in quote_plans})
        strike_step_key = self._resolve_strike_step_key(index, chain.underlying_symbol)
        computed_validation = validate_snapshot_header(
            underlying=strike_step_key,
            strikes=list(strikes),
            market_timestamp=market_ts,
            source_timestamp=source_ts,
            ingestion_timestamp=ingestion_ts,
            available_at=available_ts,
            expected_strike_count=chain.expected_strike_count,
            is_truncated=bool(chain.is_truncated),
            skew_tolerance_ms=(
                overrides.skew_tolerance_ms
                if overrides.skew_tolerance_ms is not None
                else self._skew_tolerance_ms
            ),
        )

        incoming_status = (chain.quality_status or DQ_STATUS_VALID).strip().upper()
        incoming_validation = ValidationResult(
            status=incoming_status,
            is_valid=incoming_status != DQ_STATUS_REJECTED,
            flags=dict(chain.quality_flags or {}),
        )
        combined = incoming_validation.merge(computed_validation)

        flags = dict(combined.flags)
        status = combined.status
        if skipped:
            flags[DQ_FLAG_MISSING_CONTRACT] = {
                "count": len(skipped),
                "quotes": [item.to_flag_detail() for item in skipped],
            }
            if status == DQ_STATUS_VALID:
                status = DQ_STATUS_ACCEPT_WITH_FLAG
        combined = ValidationResult(
            status=status,
            is_valid=combined.is_valid,
            flags=flags,
            errors=list(combined.errors),
            warnings=list(combined.warnings),
        )

        if not quote_plans or not combined.is_valid or combined.status == DQ_STATUS_REJECTED:
            if not overrides.allow_rejected:
                raise OptionChainValidationRejectedError(
                    "Option-chain observation rejected by OPT-1 validation "
                    f"(status={combined.status}, errors={combined.errors})",
                    combined,
                )

        observed_strike_count = (
            chain.observed_strike_count
            if chain.observed_strike_count is not None
            else len(strikes)
        )
        header_plan = _HeaderPlan(
            underlying_index_id=index.id,
            chain_scope=chain_scope,
            expiry_scope=expiry_scope_value,
            spot_price=_to_decimal(chain.spot_price, _PRICE_SCALE),
            market_timestamp=market_ts,
            source_timestamp=source_ts,
            ingestion_timestamp=ingestion_ts,
            available_at=available_ts,
            source_name=source_name_value,
            source_reference=source_reference_value,
            normalization_version=normalization_version_value,
            batch_id=batch_id_value,
            raw_payload_hash=chain.raw_payload_hash,
            is_truncated=bool(chain.is_truncated),
            expected_strike_count=chain.expected_strike_count,
            observed_strike_count=observed_strike_count,
            near_futures_price=_to_decimal(overrides.near_futures_price, _PRICE_SCALE),
            near_futures_contract_id=overrides.near_futures_contract_id,
            quality_status=combined.status,
            quality_flags=self._json_safe_flags(flags),
            calculation_context=overrides.calculation_context or CalculationContext(),
            trade_date=derive_trade_date(market_ts, timezone_name),
            timezone_name=timezone_name,
        )
        return header_plan, quote_plans, skipped, combined


    async def _resolve_quote_plans(
        self,
        session: AsyncSession,
        index: MarketIndex,
        chain: NormalizedOptionChain,
        overrides: _PersistOverrides,
    ) -> tuple[list[_QuotePlan], tuple[SkippedQuote, ...]]:
        """Resolve quotes to existing canonical contracts; never fabricate one.

        Quotes whose identity is incomplete, whose ``contract_id`` does not
        exist, or whose economic identity has no authoritative
        ``DerivativeContract`` are excluded and recorded as DQ detail — they
        are never silently invented.
        """
        exchange = overrides.exchange or self._exchange
        segment = overrides.segment or self._segment
        enrichments = overrides.quote_enrichments or {}

        candidates: list[NormalizedQuote] = []
        skipped: list[SkippedQuote] = []
        for quote in [*chain.calls, *chain.puts]:
            problem = self._quote_identity_problem(quote)
            if problem is not None:
                skipped.append(self._skipped_quote(quote, problem))
            else:
                candidates.append(quote)

        resolved, unresolved = await self._resolve_contract_ids(
            session, index.id, candidates, exchange, segment
        )
        skipped.extend(unresolved)

        plans: dict[int, _QuotePlan] = {}
        for position, quote in enumerate(candidates):
            contract_id = resolved.get(position)
            if contract_id is None:
                continue
            plan = self._build_quote_plan(quote, contract_id, enrichments.get(contract_id))
            existing = plans.get(contract_id)
            if existing is None:
                plans[contract_id] = plan
                continue
            if existing.canonical_fields() != plan.canonical_fields():
                raise OptionChainValidationRejectedError(
                    f"Duplicate contract_id={contract_id} inside one snapshot carries "
                    "conflicting facts",
                    ValidationResult(
                        status=DQ_STATUS_REJECTED,
                        is_valid=False,
                        errors=[
                            f"Duplicate contract_id={contract_id} with differing payload "
                            "inside one snapshot"
                        ],
                    ),
                )
            # Byte-identical duplicate of the same observation: one row is enough.
            logger.debug(
                "Ignoring identical duplicate quote for contract %s in one snapshot",
                contract_id,
            )

        ordered = [plans[contract_id] for contract_id in sorted(plans)]
        return ordered, tuple(skipped)


    async def _resolve_contract_ids(
        self,
        session: AsyncSession,
        index_id: int,
        quotes: Sequence[NormalizedQuote],
        exchange: str,
        segment: str,
    ) -> tuple[dict[int, int], list[SkippedQuote]]:
        """Map each position in ``quotes`` to an existing canonical contract id."""
        resolved: dict[int, int] = {}
        skipped: list[SkippedQuote] = []
        with_contract_id: list[tuple[int, NormalizedQuote, int]] = []
        without_contract_id: list[tuple[int, NormalizedQuote]] = []

        for position, quote in enumerate(quotes):
            if quote.contract_id is not None:
                with_contract_id.append((position, quote, int(quote.contract_id)))
            else:
                without_contract_id.append((position, quote))

        if with_contract_id:
            requested_ids = sorted({entry[2] for entry in with_contract_id})
            rows = (
                await session.execute(
                    select(
                        DerivativeContract.id,
                        DerivativeContract.instrument_type,
                    ).where(DerivativeContract.id.in_(requested_ids))
                )
            ).all()
            known = {row[0]: row[1] for row in rows}
            for position, quote, contract_id in with_contract_id:
                instrument_type = known.get(contract_id)
                if instrument_type is None:
                    skipped.append(
                        self._skipped_quote(
                            quote,
                            f"contract_id={contract_id} does not exist in derivative_contracts",
                        )
                    )
                    continue
                if instrument_type != "OPTION":
                    skipped.append(
                        self._skipped_quote(
                            quote,
                            f"contract_id={contract_id} is a {instrument_type} contract, "
                            "not an OPTION",
                        )
                    )
                    continue
                resolved[position] = contract_id

        if without_contract_id:
            expiries = {
                quote.expiry_date
                for _, quote in without_contract_id
                if quote.expiry_date is not None
            }
            cache = await self._preload_option_contract_cache(
                session, exchange, segment, index_id, expiries
            )
            for position, quote in without_contract_id:
                cache_key = (
                    quote.expiry_date.isoformat(),
                    _canonical_number(quote.strike_price),
                    str(quote.option_type).strip().upper(),
                )
                contract_id = cache.get(cache_key)
                if contract_id is None:
                    skipped.append(
                        self._skipped_quote(
                            quote,
                            "no canonical DerivativeContract matches "
                            "(exchange, segment, underlying, OPTION, expiry, strike, "
                            "option_type)",
                        )
                    )
                    continue
                resolved[position] = contract_id

        return resolved, skipped

    async def _preload_option_contract_cache(
        self,
        session: AsyncSession,
        exchange: str,
        segment: str,
        index_id: int,
        expiries: set[date],
    ) -> dict[tuple[str, str | None, str], int]:
        """Load existing OPTION contracts for the requested expiries once."""
        if not expiries:
            return {}
        statement = select(
            DerivativeContract.id,
            DerivativeContract.expiry_date,
            DerivativeContract.strike_price,
            DerivativeContract.option_type,
        ).where(
            DerivativeContract.exchange == exchange,
            DerivativeContract.segment == segment,
            DerivativeContract.underlying_index_id == index_id,
            DerivativeContract.instrument_type == "OPTION",
            DerivativeContract.expiry_date.in_(sorted(expiries)),
        )
        rows = (await session.execute(statement)).all()
        cache: dict[tuple[str, str | None, str], int] = {}
        for contract_id, expiry, strike, option_type in rows:
            if expiry is None or strike is None or option_type is None:
                continue
            cache[
                (
                    expiry.isoformat(),
                    _canonical_number(strike),
                    str(option_type).strip().upper(),
                )
            ] = contract_id
        return cache


    @staticmethod
    def _quote_identity_problem(quote: NormalizedQuote) -> str | None:
        """Return the reason a quote identity cannot be persisted, else None."""
        if quote.strike_price is None:
            return "missing strike_price (canonical contract identity incomplete)"
        option_type = str(quote.option_type).strip().upper() if quote.option_type else ""
        if option_type not in ("CE", "PE"):
            return f"invalid or missing option_type '{quote.option_type}'"
        if quote.expiry_date is None:
            return "missing expiry_date (canonical contract identity incomplete)"
        return None

    @staticmethod
    def _skipped_quote(quote: NormalizedQuote, reason: str) -> SkippedQuote:
        """Build a JSON-safe record of a quote that could not be persisted."""
        return SkippedQuote(
            symbol=quote.symbol,
            expiry_date=quote.expiry_date,
            strike_price=(
                _canonical_number(quote.strike_price)
                if quote.strike_price is not None
                else None
            ),
            option_type=quote.option_type,
            provider_instrument_id=quote.provider_instrument_id,
            reason=reason,
        )

    def _build_quote_plan(
        self,
        quote: NormalizedQuote,
        contract_id: int,
        enrichment: QuoteEnrichment | None,
    ) -> _QuotePlan:
        """Quantise a normalized quote into its canonical persisted form."""
        extra = enrichment or QuoteEnrichment()
        greeks: Mapping[str, Any] = (
            quote.source_greeks if isinstance(quote.source_greeks, Mapping) else {}
        )
        source_values = {
            column: _to_decimal(greeks.get(greek_key), _RATIO_SCALE)
            for greek_key, column in SOURCE_GREEK_COLUMNS.items()
        }
        source_change_in_oi = (
            quote.source_change_in_oi
            if quote.source_change_in_oi is not None
            else quote.change_in_open_interest
        )
        return _QuotePlan(
            contract_id=contract_id,
            strike_price=_to_decimal(quote.strike_price, _PRICE_SCALE) or Decimal("0"),
            option_type=str(quote.option_type).strip().upper(),
            expiry_date=quote.expiry_date,
            ltp=_to_decimal(quote.ltp, _PRICE_SCALE),
            bid=_to_decimal(quote.bid, _PRICE_SCALE),
            ask=_to_decimal(quote.ask, _PRICE_SCALE),
            bid_qty=_to_int(quote.bid_qty),
            ask_qty=_to_int(quote.ask_qty),
            volume=_to_int(quote.volume),
            open_interest=_to_int(quote.open_interest),
            source_change_in_oi=_to_int(source_change_in_oi),
            interval_change_in_oi=_to_int(extra.interval_change_in_oi),
            interval_seconds=_to_int(extra.interval_seconds),
            previous_snapshot_id=extra.previous_snapshot_id,
            session_change_in_oi=_to_int(extra.session_change_in_oi),
            source_iv=source_values["source_iv"],
            source_delta=source_values["source_delta"],
            source_gamma=source_values["source_gamma"],
            source_theta=source_values["source_theta"],
            source_vega=source_values["source_vega"],
            source_rho=source_values["source_rho"],
            calculated_iv=_to_decimal(extra.calculated_iv, _RATIO_SCALE),
            calculated_delta=_to_decimal(extra.calculated_delta, _RATIO_SCALE),
            calculated_gamma=_to_decimal(extra.calculated_gamma, _RATIO_SCALE),
            calculated_theta=_to_decimal(extra.calculated_theta, _RATIO_SCALE),
            calculated_vega=_to_decimal(extra.calculated_vega, _RATIO_SCALE),
            calculated_rho=_to_decimal(extra.calculated_rho, _RATIO_SCALE),
        )


    # -----------------------------------------------------------------------
    # 3.3 Authoritative underlying / timezone / text resolution
    # -----------------------------------------------------------------------

    async def _resolve_underlying_index(
        self,
        session: AsyncSession,
        underlying_index_id: int | None,
        underlying_symbol: str | None,
    ) -> MarketIndex:
        """Resolve the underlying through authoritative ``MarketIndex`` mappings."""
        if underlying_index_id is not None:
            row = (
                await session.execute(
                    select(MarketIndex).where(MarketIndex.id == underlying_index_id)
                )
            ).scalar_one_or_none()
            if row is None:
                raise UnderlyingResolutionError(
                    f"No MarketIndex exists with id={underlying_index_id}; "
                    "refusing to fabricate an underlying identifier"
                )
            return row

        if not underlying_symbol or not underlying_symbol.strip():
            raise UnderlyingResolutionError(
                "Cannot resolve underlying: neither underlying_index_id nor "
                "NormalizedOptionChain.underlying_symbol was supplied"
            )

        raw_symbol = underlying_symbol.strip().upper()
        normalized = normalize_index_symbol(underlying_symbol)
        candidates = {raw_symbol, normalized}
        if normalized in {"NIFTY_NEXT_50", "NIFTYNXT50"}:
            candidates.update({"NIFTY_NEXT_50", "NIFTYNXT50"})

        ordering = (MarketIndex.is_active.desc(), MarketIndex.id.asc())
        rows = list(
            (
                await session.execute(
                    select(MarketIndex)
                    .where(
                        or_(
                            func.upper(MarketIndex.code).in_(candidates),
                            func.upper(MarketIndex.provider_symbol).in_(candidates),
                        )
                    )
                    .order_by(*ordering)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if (row.code or "").strip().upper() in candidates:
                return row
        for row in rows:
            if (row.provider_symbol or "").strip().upper() in candidates:
                return row
        if rows:
            return rows[0]

        # Authoritative name mapping (mirrors DerivativeContract import order).
        all_indices = list(
            (await session.execute(select(MarketIndex).order_by(*ordering))).scalars().all()
        )
        for row in all_indices:
            if row.name and normalize_index_symbol(row.name) in candidates:
                return row

        raise UnderlyingResolutionError(
            f"No authoritative MarketIndex mapping for underlying "
            f"'{underlying_symbol}'; refusing to invent an underlying identifier"
        )

    async def _market_timezone_name(
        self, session: AsyncSession, underlying_index_id: int
    ) -> str:
        """Return the market timezone used for this underlying's trade dates."""
        value = (
            await session.execute(
                select(MarketIndex.timezone).where(MarketIndex.id == underlying_index_id)
            )
        ).scalar()
        return (value or "").strip() or DEFAULT_MARKET_TIMEZONE_NAME

    @staticmethod
    def _resolve_strike_step_key(index: MarketIndex, underlying_symbol: str | None) -> str:
        """Return the configured strike-grid key for OPT-1 grid validation.

        Grid validation is never skipped: when no candidate identifier is
        configured the observation is refused with an explicit error rather
        than silently bypassing strike-grid data quality.
        """
        for candidate in (
            index.code,
            index.provider_symbol,
            index.name,
            underlying_symbol,
        ):
            if not candidate:
                continue
            try:
                get_strike_step(candidate)
            except ValueError:
                continue
            return candidate
        raise OptionChainPersistenceError(
            f"No strike-step grid configuration exists for underlying index "
            f"'{index.code}'; OPT-1 grid validation cannot be applied and the "
            "observation was not persisted"
        )

    @staticmethod
    def _bounded_text(value: str, limit: int, field_name: str) -> str:
        """Validate a persisted text value against its column length."""
        text_value = str(value)
        if len(text_value) > limit:
            raise OptionChainPersistenceError(
                f"{field_name} exceeds the maximum supported length of {limit} characters"
            )
        return text_value

    @staticmethod
    def _default_batch_id(source_name: str, market_timestamp: datetime) -> str:
        """Deterministic provenance batch id for an observation."""
        digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:8]
        return f"OPT1-{to_utc(market_timestamp).strftime('%Y%m%dT%H%M%SZ')}-{digest}"

    @staticmethod
    def _json_safe_flags(flags: Mapping[str, Any]) -> dict[str, Any]:
        """Ensure DQ flags are storable JSONB without altering their meaning."""
        try:
            return json.loads(json.dumps(dict(flags), default=str))
        except (TypeError, ValueError):
            return {str(key): str(value) for key, value in dict(flags).items()}


    # -----------------------------------------------------------------------
    # 3.4 Concurrency-safe revision allocation and idempotency
    # -----------------------------------------------------------------------

    async def _acquire_revision_lock(self, session: AsyncSession, lock_key: str) -> None:
        """Serialise writers of one logical observation with a transaction lock.

        ``pg_advisory_xact_lock`` is transaction-scoped: it is released when the
        owning transaction commits or rolls back, so concurrent ingestors of the
        same logical observation cannot allocate the same revision.
        """
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )

    @staticmethod
    def _revision_lock_key(plan: _HeaderPlan) -> str:
        """Deterministic advisory-lock key for a logical observation."""
        return (
            f"{_REVISION_LOCK_NAMESPACE}|{plan.underlying_index_id}|{plan.chain_scope}|"
            f"{_canonical_date(plan.expiry_scope)}|"
            f"{_canonical_datetime(plan.market_timestamp)}|{plan.source_name}"
        )

    @staticmethod
    def _logical_key_predicates(plan: _HeaderPlan) -> list[Any]:
        """SQL predicates of the specification-defined logical snapshot key.

        Mirrors the ``uq_opt_snapshots_all`` / ``uq_opt_snapshots_single``
        partial unique indexes minus the ``revision`` column (revision is what
        a correction increments).
        """
        predicates: list[Any] = [
            OptionChainSnapshot.underlying_index_id == plan.underlying_index_id,
            OptionChainSnapshot.chain_scope == plan.chain_scope,
            OptionChainSnapshot.market_timestamp == plan.market_timestamp,
            OptionChainSnapshot.source_name == plan.source_name,
        ]
        if plan.chain_scope == CHAIN_SCOPE_ALL_EXPIRIES:
            predicates.append(OptionChainSnapshot.expiry_scope.is_(None))
        else:
            predicates.append(OptionChainSnapshot.expiry_scope == plan.expiry_scope)
        return predicates

    async def _next_revision(self, session: AsyncSession, plan: _HeaderPlan) -> int:
        """Allocate the next immutable revision for a logical observation.

        Only ever called while holding the observation's advisory lock.
        """
        current = (
            await session.execute(
                select(func.max(OptionChainSnapshot.revision)).where(
                    *self._logical_key_predicates(plan)
                )
            )
        ).scalar()
        return int(current or 0) + 1

    @staticmethod
    def _canonical_hash(header_plan: _HeaderPlan, quote_plans: Sequence[_QuotePlan]) -> str:
        """Canonical payload hash of the in-flight observation."""
        return build_canonical_payload_hash(
            header_plan.canonical_fields(),
            [plan.canonical_fields() for plan in quote_plans],
        )

    async def _find_matching_revision(
        self,
        session: AsyncSession,
        plan: _HeaderPlan,
        canonical_hash: str,
    ) -> OptionChainSnapshot | None:
        """Return the persisted revision whose canonical payload matches.

        Comparison is performed against the canonical payload recomputed from
        the persisted rows (never against a lone hash column), so an idempotent
        replay is only ever reported when the stored observation genuinely
        matches the incoming facts.
        """
        statement = (
            select(OptionChainSnapshot)
            .where(*self._logical_key_predicates(plan))
            .order_by(OptionChainSnapshot.revision.desc())
        )
        revisions = list((await session.execute(statement)).scalars().all())
        for revision_row in revisions:
            if await self._persisted_canonical_hash(session, revision_row) == canonical_hash:
                return revision_row
        return None

    async def _persisted_canonical_hash(
        self, session: AsyncSession, snapshot: OptionChainSnapshot
    ) -> str:
        """Recompute the canonical payload hash of a persisted snapshot."""
        timezone_name = await self._market_timezone_name(
            session, snapshot.underlying_index_id
        )
        trade_date = derive_trade_date(snapshot.market_timestamp, timezone_name)
        quote_rows = list(
            (
                await session.execute(build_pit_quote_statement(snapshot.id, trade_date))
            )
            .scalars()
            .all()
        )
        quote_rows.sort(key=lambda row: row.contract_id)
        return build_canonical_payload_hash(
            self._canonical_fields_from_snapshot(snapshot),
            [self._canonical_fields_from_row(row) for row in quote_rows],
        )


    @staticmethod
    def _canonical_fields_from_snapshot(snapshot: OptionChainSnapshot) -> dict[str, Any]:
        """Project a persisted header onto the canonical payload shape."""
        return {
            "underlying_index_id": snapshot.underlying_index_id,
            "chain_scope": snapshot.chain_scope,
            "expiry_scope": _canonical_date(snapshot.expiry_scope),
            "spot_price": _canonical_number(snapshot.spot_price),
            "market_timestamp": _canonical_datetime(snapshot.market_timestamp),
            "source_timestamp": _canonical_datetime(snapshot.source_timestamp),
            "source_name": snapshot.source_name,
            "is_truncated": bool(snapshot.is_truncated),
            "expected_strike_count": snapshot.expected_strike_count,
            "observed_strike_count": snapshot.observed_strike_count,
            "near_futures_price": _canonical_number(snapshot.near_futures_price),
            "near_futures_contract_id": snapshot.near_futures_contract_id,
            "calculation_spot_price": _canonical_number(snapshot.calculation_spot_price),
            "risk_free_rate_used": _canonical_number(snapshot.risk_free_rate_used),
            "dividend_yield_used": _canonical_number(snapshot.dividend_yield_used),
            "time_to_expiry_years": _canonical_number(snapshot.time_to_expiry_years),
            "time_to_expiry_convention": snapshot.time_to_expiry_convention,
            "pricing_model_version": snapshot.pricing_model_version,
        }

    @staticmethod
    def _canonical_fields_from_row(row: OptionChainQuote) -> dict[str, Any]:
        """Project a persisted quote row onto the canonical payload shape."""
        return {
            "contract_id": row.contract_id,
            "strike_price": _canonical_number(row.strike_price),
            "option_type": row.option_type,
            "expiry_date": _canonical_date(row.expiry_date),
            "ltp": _canonical_number(row.ltp),
            "bid": _canonical_number(row.bid),
            "ask": _canonical_number(row.ask),
            "bid_qty": row.bid_qty,
            "ask_qty": row.ask_qty,
            "volume": row.volume,
            "open_interest": row.open_interest,
            "source_change_in_oi": row.source_change_in_oi,
            "interval_change_in_oi": row.interval_change_in_oi,
            "interval_seconds": row.interval_seconds,
            "session_change_in_oi": row.session_change_in_oi,
            "source_iv": _canonical_number(row.source_iv),
            "source_delta": _canonical_number(row.source_delta),
            "source_gamma": _canonical_number(row.source_gamma),
            "source_theta": _canonical_number(row.source_theta),
            "source_vega": _canonical_number(row.source_vega),
            "source_rho": _canonical_number(row.source_rho),
            "calculated_iv": _canonical_number(row.calculated_iv),
            "calculated_delta": _canonical_number(row.calculated_delta),
            "calculated_gamma": _canonical_number(row.calculated_gamma),
            "calculated_theta": _canonical_number(row.calculated_theta),
            "calculated_vega": _canonical_number(row.calculated_vega),
            "calculated_rho": _canonical_number(row.calculated_rho),
        }


    # -----------------------------------------------------------------------
    # 3.5 Row construction and duplicate results
    # -----------------------------------------------------------------------

    @staticmethod
    def _insert_header(
        session: AsyncSession,
        plan: _HeaderPlan,
        revision: int,
        canonical_hash: str,
    ) -> OptionChainSnapshot:
        """Stage the snapshot header row (flushed by the caller to get its id)."""
        context = plan.calculation_context
        snapshot = OptionChainSnapshot(
            underlying_index_id=plan.underlying_index_id,
            chain_scope=plan.chain_scope,
            expiry_scope=plan.expiry_scope,
            spot_price=plan.spot_price,
            market_timestamp=plan.market_timestamp,
            source_timestamp=plan.source_timestamp,
            ingestion_timestamp=plan.ingestion_timestamp,
            available_at=plan.available_at,
            source_name=plan.source_name,
            source_reference=plan.source_reference,
            raw_payload_hash=plan.raw_payload_hash or canonical_hash,
            normalization_version=plan.normalization_version,
            batch_id=plan.batch_id,
            revision=revision,
            quality_status=plan.quality_status,
            quality_flags=dict(plan.quality_flags),
            is_truncated=plan.is_truncated,
            expected_strike_count=plan.expected_strike_count,
            observed_strike_count=plan.observed_strike_count,
            near_futures_price=plan.near_futures_price,
            near_futures_contract_id=plan.near_futures_contract_id,
            calculation_spot_price=context.calculation_spot_price,
            risk_free_rate_used=context.risk_free_rate_used,
            dividend_yield_used=context.dividend_yield_used,
            time_to_expiry_years=context.time_to_expiry_years,
            time_to_expiry_convention=context.time_to_expiry_convention,
            pricing_model_version=context.pricing_model_version,
        )
        session.add(snapshot)
        return snapshot

    @staticmethod
    def _build_quote_row(
        plan: _QuotePlan, snapshot_id: int, trade_date: date
    ) -> OptionChainQuote:
        """Build one canonical quote row for a snapshot."""
        return OptionChainQuote(
            snapshot_id=snapshot_id,
            contract_id=plan.contract_id,
            trade_date=trade_date,
            strike_price=plan.strike_price,
            option_type=plan.option_type,
            expiry_date=plan.expiry_date,
            ltp=plan.ltp,
            bid=plan.bid,
            ask=plan.ask,
            bid_qty=plan.bid_qty,
            ask_qty=plan.ask_qty,
            volume=plan.volume,
            open_interest=plan.open_interest,
            source_change_in_oi=plan.source_change_in_oi,
            interval_change_in_oi=plan.interval_change_in_oi,
            interval_seconds=plan.interval_seconds,
            previous_snapshot_id=plan.previous_snapshot_id,
            session_change_in_oi=plan.session_change_in_oi,
            source_iv=plan.source_iv,
            source_delta=plan.source_delta,
            source_gamma=plan.source_gamma,
            source_theta=plan.source_theta,
            source_vega=plan.source_vega,
            source_rho=plan.source_rho,
            calculated_iv=plan.calculated_iv,
            calculated_delta=plan.calculated_delta,
            calculated_gamma=plan.calculated_gamma,
            calculated_theta=plan.calculated_theta,
            calculated_vega=plan.calculated_vega,
            calculated_rho=plan.calculated_rho,
            quality_flags={},
        )


    async def _duplicate_result(
        self,
        session: AsyncSession,
        existing: OptionChainSnapshot,
        plan: _HeaderPlan,
        canonical_hash: str,
        partition_name: str,
        validation: ValidationResult,
        skipped: tuple[SkippedQuote, ...],
    ) -> PersistSnapshotResult:
        """Build the DUPLICATE_IGNORED result for an already persisted snapshot."""
        trade_date = derive_trade_date(existing.market_timestamp, plan.timezone_name)
        quote_count = (
            await session.execute(
                select(func.count())
                .select_from(OptionChainQuote)
                .where(
                    OptionChainQuote.snapshot_id == existing.id,
                    OptionChainQuote.trade_date == trade_date,
                )
            )
        ).scalar() or 0
        return PersistSnapshotResult(
            status=PERSIST_STATUS_DUPLICATE_IGNORED,
            snapshot_id=existing.id,
            revision=existing.revision,
            underlying_index_id=existing.underlying_index_id,
            chain_scope=existing.chain_scope,
            expiry_scope=existing.expiry_scope,
            market_timestamp=existing.market_timestamp,
            available_at=existing.available_at,
            trade_date=trade_date,
            partition_name=partition_name,
            quote_count=int(quote_count),
            canonical_payload_hash=canonical_hash,
            raw_payload_hash=existing.raw_payload_hash,
            quality_status=existing.quality_status,
            quality_flags=dict(existing.quality_flags or {}),
            is_truncated=bool(existing.is_truncated),
            validation=validation,
            skipped_quotes=skipped,
        )

    # -----------------------------------------------------------------------
    # 3.6 Public API — point-in-time retrieval
    # -----------------------------------------------------------------------

    async def get_pit_snapshot(
        self,
        *,
        underlying_index_id: int,
        chain_scope: str,
        evaluation_timestamp: datetime,
        expiry_scope: date | None = None,
        market_timestamp: datetime | None = None,
        source_name: str | None = None,
        include_rejected: bool = False,
        session: AsyncSession | None = None,
    ) -> PitSnapshotResult | None:
        """Resolve the point-in-time eligible snapshot for an evaluation time.

        Selection order (SPEC-R2 §17/§18, adapted to the schema contract):

        1. filter by underlying, chain scope and (for SINGLE_EXPIRY) expiry;
        2. ``available_at <= evaluation_timestamp`` — a revision that only
           became available afterwards can never be exposed;
        3. exclude REJECTED observations;
        4. select the latest eligible market observation
           (``MAX(market_timestamp)``) *after* the PIT and quality filters;
        5. select that logical observation's highest PIT-visible accepted
           revision;
        6. return its quotes filtered by ``snapshot_id`` and ``trade_date`` so
           PostgreSQL prunes to the snapshot's monthly partition.
        """
        if chain_scope not in (CHAIN_SCOPE_ALL_EXPIRIES, CHAIN_SCOPE_SINGLE_EXPIRY):
            raise OptionChainPersistenceError(
                f"Unsupported chain_scope '{chain_scope}'; expected "
                f"'{CHAIN_SCOPE_ALL_EXPIRIES}' or '{CHAIN_SCOPE_SINGLE_EXPIRY}'"
            )
        if chain_scope == CHAIN_SCOPE_SINGLE_EXPIRY and expiry_scope is None:
            raise OptionChainPersistenceError(
                "chain_scope='SINGLE_EXPIRY' requires an expiry_scope value"
            )
        if chain_scope == CHAIN_SCOPE_ALL_EXPIRIES and expiry_scope is not None:
            raise OptionChainPersistenceError(
                "chain_scope='ALL_SUPPORTED_EXPIRIES' requires expiry_scope=None"
            )

        active_session = session or self._session
        if active_session is not None:
            return await self._get_pit_impl(
                active_session,
                underlying_index_id=underlying_index_id,
                chain_scope=chain_scope,
                expiry_scope=expiry_scope,
                evaluation_timestamp=evaluation_timestamp,
                market_timestamp=market_timestamp,
                source_name=source_name,
                include_rejected=include_rejected,
            )

        factory = get_session_factory()
        async with factory() as owned_session:
            return await self._get_pit_impl(
                owned_session,
                underlying_index_id=underlying_index_id,
                chain_scope=chain_scope,
                expiry_scope=expiry_scope,
                evaluation_timestamp=evaluation_timestamp,
                market_timestamp=market_timestamp,
                source_name=source_name,
                include_rejected=include_rejected,
            )


    async def _get_pit_impl(
        self,
        session: AsyncSession,
        *,
        underlying_index_id: int,
        chain_scope: str,
        expiry_scope: date | None,
        evaluation_timestamp: datetime,
        market_timestamp: datetime | None,
        source_name: str | None,
        include_rejected: bool,
    ) -> PitSnapshotResult | None:
        """Point-in-time selection executed inside a resolved session."""
        evaluation = to_utc(evaluation_timestamp)

        predicates: list[Any] = [
            OptionChainSnapshot.underlying_index_id == underlying_index_id,
            OptionChainSnapshot.chain_scope == chain_scope,
            OptionChainSnapshot.available_at <= evaluation,
        ]
        if chain_scope == CHAIN_SCOPE_ALL_EXPIRIES:
            predicates.append(OptionChainSnapshot.expiry_scope.is_(None))
        else:
            predicates.append(OptionChainSnapshot.expiry_scope == expiry_scope)
        if not include_rejected:
            predicates.append(OptionChainSnapshot.quality_status != DQ_STATUS_REJECTED)
        if market_timestamp is not None:
            predicates.append(
                OptionChainSnapshot.market_timestamp == to_utc(market_timestamp)
            )
        if source_name is not None:
            predicates.append(OptionChainSnapshot.source_name == source_name)

        # Step 4: latest eligible market observation AFTER PIT + quality filters.
        latest_market_timestamp = (
            await session.execute(
                select(func.max(OptionChainSnapshot.market_timestamp)).where(*predicates)
            )
        ).scalar()
        if latest_market_timestamp is None:
            return None

        # Step 5: highest PIT-visible revision of that logical observation.
        statement = (
            select(OptionChainSnapshot)
            .where(
                *predicates,
                OptionChainSnapshot.market_timestamp == latest_market_timestamp,
            )
            .order_by(OptionChainSnapshot.revision.desc())
            .limit(1)
        )
        snapshot = (await session.execute(statement)).scalar_one_or_none()
        if snapshot is None:
            return None

        timezone_name = await self._market_timezone_name(
            session, snapshot.underlying_index_id
        )
        trade_date = derive_trade_date(snapshot.market_timestamp, timezone_name)
        quote_rows = list(
            (
                await session.execute(build_pit_quote_statement(snapshot.id, trade_date))
            )
            .scalars()
            .all()
        )

        return PitSnapshotResult(
            snapshot_id=snapshot.id,
            underlying_index_id=snapshot.underlying_index_id,
            chain_scope=snapshot.chain_scope,
            expiry_scope=snapshot.expiry_scope,
            spot_price=snapshot.spot_price,
            market_timestamp=snapshot.market_timestamp,
            source_timestamp=snapshot.source_timestamp,
            ingestion_timestamp=snapshot.ingestion_timestamp,
            available_at=snapshot.available_at,
            source_name=snapshot.source_name,
            source_reference=snapshot.source_reference,
            raw_payload_hash=snapshot.raw_payload_hash,
            normalization_version=snapshot.normalization_version,
            batch_id=snapshot.batch_id,
            revision=snapshot.revision,
            quality_status=snapshot.quality_status,
            quality_flags=dict(snapshot.quality_flags or {}),
            is_truncated=bool(snapshot.is_truncated),
            expected_strike_count=snapshot.expected_strike_count,
            observed_strike_count=snapshot.observed_strike_count,
            near_futures_price=snapshot.near_futures_price,
            near_futures_contract_id=snapshot.near_futures_contract_id,
            calculation_spot_price=snapshot.calculation_spot_price,
            risk_free_rate_used=snapshot.risk_free_rate_used,
            dividend_yield_used=snapshot.dividend_yield_used,
            time_to_expiry_years=snapshot.time_to_expiry_years,
            time_to_expiry_convention=snapshot.time_to_expiry_convention,
            pricing_model_version=snapshot.pricing_model_version,
            trade_date=trade_date,
            evaluation_timestamp=evaluation,
            quotes=tuple(PitQuoteRecord.from_model(row) for row in quote_rows),
        )
