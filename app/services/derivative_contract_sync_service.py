"""Derivatives Contract Master Synchronization Service — G.11-OPT-14D + 14E-B.

Bridges a verified, read-only provider contract-master read (Upstox
``/option/contract``) into the canonical ``derivative_contracts`` table and
reconciles contract lifecycle and mutable metadata without ever mutating a
persisted economic contract row.

DESIGN & SAFETY RULES
  1. Disabled by default and independently gated by
     ``settings.derivatives_contract_sync_enabled`` — completely separate from
     ``derivatives_observation_capture_enabled``. Neither flag implies the other.
     A synchronization is not an option-chain observation.
  2. Dry-run by default. A real write requires the configuration gate to be
     enabled AND explicit ``authorized=True`` AND ``dry_run=False``.
  3. Fail-closed atomicity: a payload is fully normalized, validated and
     classified *before* any write. If any row of an underlying's payload is
     rejected or conflicting, NO row from that payload is written — never a
     partial subset. One transaction per underlying (``sync_many`` runs one
     transaction per underlying; it does not claim global atomicity).
  4. Economic identity is the existing ``idempotency_hash`` fingerprint (exactly
     ``exchange | underlying_stock_id | underlying_index_id | instrument_type |
     expiry_date | strike_price | option_type``). The synchronizer never rewrites
     it, never deletes, and is APPEND-ONLY for new economic identities. No
     metadata field participates in the identity.
  5. Provider identity is ``source_contract_id`` (the provider instrument key).
     It is asserted, never assumed to be the primary identity, and never
     backfilled onto legacy rows. Provider identity is preloaded *globally*
     (G.11-OPT-14E-B) so a key already asserted by another contract — under this
     or any other underlying — is classified as a conflict before any write,
     instead of surfacing as a database rollback.
  6. The provider segment (``NSE_FO``) is preserved in ``provider_segment`` while
     the application segment stays ``NFO`` — the value every existing consumer
     filters on. Unmapped provider segments are rejected, never defaulted.
  7. Mutable metadata (``lot_size``, ``tick_size``, ``freeze_quantity``,
     ``minimum_lot``, ``is_weekly``) is versioned as append-only, effective-dated
     facts in ``derivative_contract_metadata_histories`` (G.11-OPT-14E-A/B).
     A provider-side metadata change NEVER rewrites ``DerivativeContract.lot_size``
     or any other persisted contract value: historical analytics read that column
     live, so an in-place update would silently change the meaning of every
     already-captured observation.
  8. Descriptive drift (``trading_symbol``, ``lifecycle_status``) is reported and
     never written. Legacy rows without a provider identity are additionally
     flagged ``stale`` and are never backfilled, never versioned and never
     deleted — their historical lot size is left exactly as stored.
  9. Lifecycle absence is only ever *classified*, never applied destructively:
     a previously known contract missing from a complete, validated payload is
     ``EXPIRED_ABSENT`` (no write) when it is already past expiry, and
     ``MISSING_WHILE_UNEXPIRED`` (payload abort) when it is not. A payload the
     parser dropped rows from can never produce an absence classification.
 10. No fabricated data: contracts, strikes, expiries, lot sizes, symbols,
     instrument keys and payload hashes are only ever what the provider returned
     (or a hash computed from exactly those normalized records, labelled as such).
 11. Every run writes one durable audit row (``DerivativeContractSyncRun``) in
     the same transaction as the writes it describes. A dry run is recorded as
     ``DRY_RUN`` with ``committed=False`` and never persists contracts or facts.
 12. No broker orders, no trading, no capture, no observation ingestion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeContractMetadataHistory,
    DerivativeContractSyncRun,
)
from app.models.market_index import MarketIndex
from app.providers.upstox_historical_provider import redact_secrets
from app.repositories.derivative import DerivativeContractRepository
from app.services.derivatives_scope import (
    PROV_PROVIDER_LISTED,
    ContractMasterResult,
    DerivativesContractMasterClient,
    OptionContractRow,
    map_provider_segment,
    normalize_underlying,
)

logger = logging.getLogger(__name__)

#: Provenance stamped onto every row/fact this service writes.
SOURCE_NAME = "UPSTOX_OPTION_CONTRACT_MASTER"
SOURCE_REFERENCE = "GET /option/contract"
DEFAULT_EXCHANGE = "NSE"
#: Application segment written for a mapped provider ``NSE_FO`` payload.
APPLICATION_SEGMENT = "NFO"
#: Bounded flush cadence inside one underlying transaction.
INSERT_FLUSH_BATCH = 500
#: Underlyings covered by the G.11-OPT-14D certification and used as the CLI default.
CERTIFIED_SYNC_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
#: Descriptive (non-identity, non-versioned) fields whose divergence is reported.
DESCRIPTIVE_DRIFT_FIELDS: tuple[str, ...] = ("trading_symbol", "lifecycle_status")
#: Descriptive fields whose divergence is reported, never written.
DRIFT_FIELDS: tuple[str, ...] = (
    "lot_size",
    "tick_size",
    "trading_symbol",
    "lifecycle_status",
)
#: Mutable metadata versioned as effective-dated facts. Never identity fields.
METADATA_FIELDS: tuple[str, ...] = (
    "lot_size",
    "tick_size",
    "freeze_quantity",
    "minimum_lot",
    "is_weekly",
)
#: Payload-hash provenance labels. See ``canonical_payload_hash``.
PAYLOAD_HASH_METHOD_CANONICAL = "CANONICAL_RECORD_SHA256"
PAYLOAD_HASH_METHOD_PROVIDER_RAW = "PROVIDER_RAW_BODY_SHA256"
#: Durable run-audit statuses (mirrors the table CHECK constraint).
AUDIT_STATUS_DRY_RUN = "DRY_RUN"
AUDIT_STATUS_COMMITTED = "COMMITTED"
AUDIT_STATUS_NO_WRITE = "NO_WRITE_NEEDED"
AUDIT_STATUS_ABORTED = "ABORTED"
#: Provider source tags that must never be synchronized. Mirrors the existing
#: non-real-chain semantics used by the capture path.
_FORBIDDEN_PROVIDER_MARKERS: tuple[str, ...] = (
    "UPSTOX_MOCK",
    "MOCK",
    "TEST",
    "UNAVAILABLE",
    "UNKNOWN",
)
_OPTION_TYPES: frozenset[str] = frozenset({"CE", "PE"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def contract_sync_enabled() -> bool:
    """Single source of truth for the contract-synchronization operator gate.

    Independent of ``observation_capture_enabled()``: contract metadata is not a
    market observation, and enabling either flag never enables the other.
    """
    return bool(getattr(settings, "derivatives_contract_sync_enabled", False))


class ContractSyncDisabledError(RuntimeError):
    """Raised when a synchronization is refused before any provider read."""


class ContractSyncError(RuntimeError):
    """Raised when a synchronization write fails and was rolled back."""


class ContractSyncOutcome(StrEnum):
    """Per-row classification of one normalized provider contract row.

    ``INSERTED`` keeps its 14D name for compatibility and carries the 14E-B
    ``NEWLY_LISTED`` value/alias: one genuinely new economic identity. The two
    names are the same enum member.
    """

    INSERTED = "NEWLY_LISTED"
    NEWLY_LISTED = "NEWLY_LISTED"
    UNCHANGED = "UNCHANGED"
    #: Metadata differs and is versioned as a new effective-dated fact.
    METADATA_CHANGED = "METADATA_CHANGED"
    #: Metadata (or descriptive fields) differ but must not be written (legacy row).
    METADATA_DRIFT = "METADATA_DRIFT"
    #: Previously known, absent from a complete payload, already past expiry.
    EXPIRED_ABSENT = "EXPIRED_ABSENT"
    #: Previously known, absent from a complete payload, still unexpired.
    MISSING_WHILE_UNEXPIRED = "MISSING_WHILE_UNEXPIRED"
    #: A metadata fact cannot be appended deterministically for this date.
    METADATA_FACT_CONFLICT = "METADATA_FACT_CONFLICT"
    REJECTED_UNKNOWN_UNDERLYING = "REJECTED_UNKNOWN_UNDERLYING"
    REJECTED_MALFORMED = "REJECTED_MALFORMED"
    REJECTED_UNSUPPORTED_INSTRUMENT = "REJECTED_UNSUPPORTED_INSTRUMENT"
    REJECTED_PROVIDER_UNAVAILABLE = "REJECTED_PROVIDER_UNAVAILABLE"
    CONFLICT_PROVIDER_IDENTITY = "CONFLICT_PROVIDER_IDENTITY"
    CONFLICT_NATURAL_IDENTITY = "CONFLICT_NATURAL_IDENTITY"
    SKIPPED_PROVIDER_MISMATCH = "SKIPPED_PROVIDER_MISMATCH"


#: Outcomes that block the whole payload (fail-closed atomicity).
_BLOCKING_OUTCOMES: frozenset[ContractSyncOutcome] = frozenset(
    {
        ContractSyncOutcome.REJECTED_UNKNOWN_UNDERLYING,
        ContractSyncOutcome.REJECTED_MALFORMED,
        ContractSyncOutcome.REJECTED_UNSUPPORTED_INSTRUMENT,
        ContractSyncOutcome.REJECTED_PROVIDER_UNAVAILABLE,
        ContractSyncOutcome.CONFLICT_PROVIDER_IDENTITY,
        ContractSyncOutcome.CONFLICT_NATURAL_IDENTITY,
        ContractSyncOutcome.SKIPPED_PROVIDER_MISMATCH,
        ContractSyncOutcome.MISSING_WHILE_UNEXPIRED,
        ContractSyncOutcome.METADATA_FACT_CONFLICT,
    }
)

#: Outcomes that represent a rejection (as opposed to a lifecycle classification).
_REJECTION_OUTCOMES: frozenset[ContractSyncOutcome] = frozenset(
    {
        ContractSyncOutcome.REJECTED_UNKNOWN_UNDERLYING,
        ContractSyncOutcome.REJECTED_MALFORMED,
        ContractSyncOutcome.REJECTED_UNSUPPORTED_INSTRUMENT,
        ContractSyncOutcome.REJECTED_PROVIDER_UNAVAILABLE,
    }
)

#: Outcome -> summary counter attribute.
_OUTCOME_COUNTERS: Mapping[ContractSyncOutcome, str] = {
    ContractSyncOutcome.INSERTED: "inserted",
    ContractSyncOutcome.UNCHANGED: "unchanged",
    ContractSyncOutcome.METADATA_CHANGED: "metadata_changed",
    ContractSyncOutcome.METADATA_DRIFT: "metadata_drift",
    ContractSyncOutcome.EXPIRED_ABSENT: "expired_absent",
    ContractSyncOutcome.MISSING_WHILE_UNEXPIRED: "missing_while_unexpired",
    ContractSyncOutcome.METADATA_FACT_CONFLICT: "metadata_fact_conflicts",
    ContractSyncOutcome.REJECTED_UNKNOWN_UNDERLYING: "rejected",
    ContractSyncOutcome.REJECTED_MALFORMED: "rejected",
    ContractSyncOutcome.REJECTED_UNSUPPORTED_INSTRUMENT: "rejected",
    ContractSyncOutcome.REJECTED_PROVIDER_UNAVAILABLE: "rejected",
    ContractSyncOutcome.CONFLICT_PROVIDER_IDENTITY: "conflicts",
    ContractSyncOutcome.CONFLICT_NATURAL_IDENTITY: "conflicts",
}


@dataclass(frozen=True, slots=True)
class ContractSyncRow:
    """Outcome for one provider contract row (or one absent local contract)."""

    outcome: ContractSyncOutcome
    instrument_key: str | None
    trading_symbol: str | None
    contract_id: int | None = None
    reason: str | None = None
    drift: Mapping[str, tuple[Any, Any]] = field(default_factory=dict)
    #: True when the local row carries no provider identity (legacy/STALE).
    stale: bool = False
    #: True when a metadata fact was (or, in a dry run, would be) appended.
    versioned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "contract_id": self.contract_id,
            "reason": self.reason,
            "drift": {key: list(value) for key, value in self.drift.items()},
            "stale": self.stale,
            "versioned": self.versioned,
        }


@dataclass(slots=True)
class ContractSyncSummary:
    """Structured, JSON-safe result of one underlying reconciliation."""

    underlying: str
    provider_source: str | None = None
    provider_reason: str | None = None
    underlying_index_id: int | None = None
    as_of_date: date | None = None
    dry_run: bool = True
    total_rows: int = 0
    inserted: int = 0
    unchanged: int = 0
    metadata_changed: int = 0
    metadata_drift: int = 0
    expired_absent: int = 0
    missing_while_unexpired: int = 0
    stale: int = 0
    conflicts: int = 0
    metadata_fact_conflicts: int = 0
    rejected: int = 0
    skipped: int = 0
    metadata_facts_written: int = 0
    provider_row_count: int = 0
    parse_failures: int = 0
    payload_hash: str | None = None
    payload_hash_method: str | None = None
    audit_run_id: int | None = None
    audit_error: str | None = None
    rows: tuple[ContractSyncRow, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None
    aborted: bool = False
    abort_reason: str | None = None

    @property
    def blocking_rows(self) -> tuple[ContractSyncRow, ...]:
        """Rows that make the payload fail closed."""
        return tuple(row for row in self.rows if row.outcome in _BLOCKING_OUTCOMES)

    def is_clean(self) -> bool:
        """True when nothing was rejected or conflicting."""
        return not self.aborted and not self.blocking_rows

    def validation_summary(self) -> dict[str, Any]:
        """JSON-safe validation/conflict digest for the durable run audit."""
        outcomes: dict[str, int] = {}
        for row in self.rows:
            outcomes[row.outcome.value] = outcomes.get(row.outcome.value, 0) + 1
        return {
            "outcomes": outcomes,
            "blocking": [
                {
                    "outcome": row.outcome.value,
                    "instrument_key": row.instrument_key,
                    "trading_symbol": row.trading_symbol,
                    "reason": row.reason,
                }
                for row in self.blocking_rows[:25]
            ],
            "provider_row_count": self.provider_row_count,
            "parse_failures": self.parse_failures,
            "metadata_facts_written": self.metadata_facts_written,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "provider_source": self.provider_source,
            "provider_reason": self.provider_reason,
            "underlying_index_id": self.underlying_index_id,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "dry_run": self.dry_run,
            "total_rows": self.total_rows,
            "inserted": self.inserted,
            "newly_listed": self.inserted,
            "unchanged": self.unchanged,
            "metadata_changed": self.metadata_changed,
            "metadata_drift": self.metadata_drift,
            "expired_absent": self.expired_absent,
            "missing_while_unexpired": self.missing_while_unexpired,
            "stale": self.stale,
            "conflicts": self.conflicts,
            "metadata_fact_conflicts": self.metadata_fact_conflicts,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "metadata_facts_written": self.metadata_facts_written,
            "provider_row_count": self.provider_row_count,
            "parse_failures": self.parse_failures,
            "payload_hash": self.payload_hash,
            "payload_hash_method": self.payload_hash_method,
            "audit_run_id": self.audit_run_id,
            "audit_error": self.audit_error,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(slots=True)
class _SyncPlan:
    """A fully validated provider row, ready to become a DerivativeContract."""

    instrument_key: str
    trading_symbol: str
    exchange: str
    segment: str
    provider_segment: str
    underlying_symbol: str
    expiry_date: date
    strike_price: Decimal
    option_type: str
    lot_size: int
    tick_size: Decimal | None
    underlying_index_id: int
    as_of_date: date
    freeze_quantity: int | None = None
    minimum_lot: int | None = None
    is_weekly: bool = False
    provider_meta: Mapping[str, Any] = field(default_factory=dict)

    def build_contract(self) -> DerivativeContract:
        """Materialise the canonical row (nothing is added to a session here)."""
        lifecycle = "EXPIRED" if self.expiry_date < self.as_of_date else "ACTIVE"
        contract = DerivativeContract(
            exchange=self.exchange,
            segment=self.segment,
            instrument_type="OPTION",
            underlying_stock_id=None,
            underlying_index_id=self.underlying_index_id,
            trading_symbol=self.trading_symbol,
            source_contract_id=self.instrument_key,
            expiry_date=self.expiry_date,
            strike_price=self.strike_price,
            option_type=self.option_type,
            lot_size=self.lot_size,
            tick_size=self.tick_size,
            lifecycle_status=lifecycle,
            available_at=self.as_of_date,
            source_name=SOURCE_NAME,
            source_reference=SOURCE_REFERENCE,
            fetched_at=datetime.now(UTC),
        )
        contract.provider_segment = self.provider_segment
        # Set explicitly so classification and insertion use the identical value
        # (the model listener would otherwise compute the same fingerprint later).
        contract.idempotency_hash = contract.compute_fingerprint()
        return contract

    def metadata_values(self) -> dict[str, Any]:
        """The five mutable metadata facts this provider row asserts."""
        return {
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "freeze_quantity": self.freeze_quantity,
            "minimum_lot": self.minimum_lot,
            "is_weekly": self.is_weekly,
        }

    def drift_values(self) -> dict[str, Any]:
        """Descriptive values compared against an existing row."""
        return {
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "trading_symbol": self.trading_symbol,
            "lifecycle_status": (
                "EXPIRED" if self.expiry_date < self.as_of_date else "ACTIVE"
            ),
        }


@dataclass(slots=True)
class _PendingFact:
    """One metadata fact to append once the owning contract has an id."""

    contract: DerivativeContract
    values: Mapping[str, Any]
    effective_from: date


def canonical_payload_hash(rows: Sequence[Any]) -> str:
    """SHA-256 over the *normalized* provider records of one payload.

    Canonicalization is explicit and deterministic: each record is reduced to the
    fields this service actually reconciles, decimals are normalized
    (``Decimal(str(value)).normalize()``), dates are ISO-8601, and the record list
    is serialized as JSON with sorted keys, no insignificant whitespace, in the
    provider's row order.

    This is a **normalized-record hash**, deliberately NOT the provider's
    raw-body hash: the synchronizer never sees the raw body. Callers that do hold
    the genuine bytes (an operator replaying a preserved payload file) may pass
    the real raw-body digest explicitly; the audit then records it with
    ``PAYLOAD_HASH_METHOD_PROVIDER_RAW``. A hash is never invented, and the two
    methods are never conflated.
    """
    records: list[dict[str, Any]] = []
    for row in rows:
        records.append(
            {
                "instrument_key": str(getattr(row, "instrument_key", "") or ""),
                "trading_symbol": str(getattr(row, "trading_symbol", "") or ""),
                "exchange": str(getattr(row, "exchange", "") or "").strip().upper(),
                "segment": str(getattr(row, "segment", "") or "").strip().upper(),
                "underlying_symbol": _optional_str(getattr(row, "underlying_symbol", None)),
                "underlying_type": _optional_str(getattr(row, "underlying_type", None)),
                "expiry_date": (
                    row.expiry_date.isoformat()
                    if getattr(row, "expiry_date", None) is not None
                    else None
                ),
                "strike_price": _canonical_decimal(getattr(row, "strike_price", None)),
                "option_type": str(getattr(row, "option_type", "") or "").strip().upper(),
                "lot_size": _canonical_int(getattr(row, "lot_size", None)),
                "tick_size": _canonical_decimal(getattr(row, "tick_size", None)),
                "freeze_quantity": _canonical_int(getattr(row, "freeze_quantity", None)),
                "minimum_lot": _canonical_int(getattr(row, "minimum_lot", None)),
                "is_weekly": bool(getattr(row, "is_weekly", False)),
            }
        )
    blob = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _canonical_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except (ArithmeticError, TypeError, ValueError):
            return None


def _canonical_decimal(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return str(parsed.normalize())


def _normalize_sha256(value: str | None, field_name: str) -> str | None:
    """Validate a caller-supplied SHA-256 digest, or refuse it loudly."""
    if value is None:
        return None
    digest = str(value).strip().lower()
    if not _SHA256_PATTERN.match(digest):
        raise ValueError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest "
            f"(received {len(digest)} characters); refusing to record an "
            "unverifiable payload hash"
        )
    return digest


class DerivativesContractSyncService:
    """Operator-gated, idempotent, append-only contract-master reconciler."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        master_client: DerivativesContractMasterClient | None = None,
    ) -> None:
        self._session = session
        self._master_client = master_client

    # ------------------------------------------------------------------ gates

    @staticmethod
    def _require_authorized(authorized: bool) -> None:
        """Refuse before any provider read unless both gate layers are satisfied."""
        if not authorized:
            raise ContractSyncDisabledError(
                "CONTRACT_SYNC_UNAUTHORIZED: contract synchronization requires an "
                "explicit operator authorization (authorized=True); no provider "
                "call was made and nothing was written"
            )
        if not contract_sync_enabled():
            raise ContractSyncDisabledError(
                "CONTRACT_SYNC_DISABLED: contract synchronization is disabled "
                "(derivatives_contract_sync_enabled=False); no provider call was "
                "made and nothing was written"
            )

    # ------------------------------------------------------------------ public

    async def sync_underlying(
        self,
        underlying: str,
        *,
        as_of_date: date,
        dry_run: bool = True,
        authorized: bool = False,
        record_audit: bool = True,
        source_payload_sha256: str | None = None,
        payload_hash_method: str | None = None,
    ) -> ContractSyncSummary:
        """Reconcile one underlying's provider contract master.

        One transaction per underlying. Nothing is written unless the whole
        payload classified cleanly, ``dry_run`` is False and both gate layers
        are satisfied.

        ``source_payload_sha256`` is optional: when the caller holds the genuine
        provider bytes it may pass the raw-body digest, which is then recorded as
        such. Otherwise the run audit records the deterministic normalized-record
        digest produced by :func:`canonical_payload_hash`.
        """
        canonical = normalize_underlying(underlying) or str(underlying).strip().upper()
        started = datetime.now(UTC)
        self._require_authorized(authorized)

        if as_of_date is None:
            raise ValueError(
                "as_of_date is required; refusing to guess via date.today()"
            )
        if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
            raise ValueError(
                f"as_of_date must be a date, received {type(as_of_date).__name__}"
            )

        raw_hash = _normalize_sha256(source_payload_sha256, "source_payload_sha256")
        if raw_hash is not None and payload_hash_method is None:
            payload_hash_method = PAYLOAD_HASH_METHOD_PROVIDER_RAW
        if raw_hash is not None and payload_hash_method != PAYLOAD_HASH_METHOD_PROVIDER_RAW:
            raise ValueError(
                "source_payload_sha256 describes the provider raw body; "
                f"payload_hash_method must be {PAYLOAD_HASH_METHOD_PROVIDER_RAW!r} "
                f"when it is supplied (received {payload_hash_method!r})"
            )

        provider_result = await self._read_provider(canonical)

        if self._session is not None:
            return await self._execute(
                self._session,
                canonical,
                provider_result,
                as_of_date,
                dry_run,
                started,
                owns_transaction=False,
                record_audit=record_audit,
                source_payload_sha256=raw_hash,
                payload_hash_method=payload_hash_method,
            )

        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            return await self._execute(
                session,
                canonical,
                provider_result,
                as_of_date,
                dry_run,
                started,
                owns_transaction=True,
                record_audit=record_audit,
                source_payload_sha256=raw_hash,
                payload_hash_method=payload_hash_method,
            )

    async def sync_many(
        self,
        underlyings: Sequence[str],
        *,
        as_of_date: date,
        dry_run: bool = True,
        authorized: bool = False,
        record_audit: bool = True,
    ) -> tuple[ContractSyncSummary, ...]:
        """Reconcile several underlyings, one transaction each.

        Deliberately NOT globally atomic: a conflict in one underlying aborts only
        that underlying's transaction. The isolation is intentional and is what
        lets one unexpired omission in NIFTY be diagnosed without blocking an
        unrelated, clean BANKNIFTY reconciliation.
        """
        summaries: list[ContractSyncSummary] = []
        for underlying in underlyings:
            summaries.append(
                await self.sync_underlying(
                    underlying,
                    as_of_date=as_of_date,
                    dry_run=dry_run,
                    authorized=authorized,
                    record_audit=record_audit,
                )
            )
        return tuple(summaries)

    # ----------------------------------------------------------------- provider

    async def _read_provider(self, underlying: str) -> ContractMasterResult:
        """Read the provider contract master. Never substitutes synthetic rows."""
        client = self._master_client or DerivativesContractMasterClient()
        try:
            return await client.get_option_contracts(underlying)
        except Exception as exc:  # noqa: BLE001 - transport failure is not a sync
            logger.warning(
                redact_secrets(
                    f"Contract sync: provider read failed for {underlying}: "
                    f"{type(exc).__name__}"
                )
            )
            return ContractMasterResult(
                rows=[],
                source="UNAVAILABLE",
                reason=f"PROVIDER_READ_FAILED:{type(exc).__name__}",
            )

    @staticmethod
    def _provider_rejection(result: ContractMasterResult) -> str | None:
        """Return the refusal reason when a provider result must not be synced."""
        source = str(getattr(result, "source", "") or "").strip().upper()
        if not source or source in {"UNAVAILABLE", "UNKNOWN"}:
            return f"PROVIDER_NOT_LISTED:{source or 'EMPTY'}"
        for marker in _FORBIDDEN_PROVIDER_MARKERS:
            if marker in source:
                return f"NON_REAL_PROVIDER_SOURCE_{source}"
        if source != str(PROV_PROVIDER_LISTED).strip().upper():
            return f"PROVIDER_SOURCE_NOT_AUTHORITATIVE:{source}"
        if not result.rows:
            return "PROVIDER_RETURNED_NO_ROWS"
        return None

    # ------------------------------------------------------------------ execute

    async def _execute(
        self,
        session: AsyncSession,
        underlying: str,
        provider_result: ContractMasterResult,
        as_of_date: date,
        dry_run: bool,
        started: datetime,
        *,
        owns_transaction: bool,
        record_audit: bool = True,
        source_payload_sha256: str | None = None,
        payload_hash_method: str | None = None,
    ) -> ContractSyncSummary:
        """Normalize, validate, classify, then (only if clean) write."""
        summary = ContractSyncSummary(
            underlying=underlying,
            provider_source=str(getattr(provider_result, "source", "") or "") or None,
            provider_reason=getattr(provider_result, "reason", None),
            as_of_date=as_of_date,
            dry_run=dry_run,
            started_at=started,
        )
        provider_rows = list(provider_result.rows or ())
        summary.provider_row_count = len(provider_rows)
        summary.parse_failures = int(getattr(provider_result, "parse_failures", 0) or 0)
        if provider_rows:
            summary.payload_hash = source_payload_sha256 or canonical_payload_hash(
                provider_rows
            )
            summary.payload_hash_method = (
                payload_hash_method or PAYLOAD_HASH_METHOD_CANONICAL
            )

        rejection = self._provider_rejection(provider_result)
        if rejection is not None:
            summary.aborted = True
            summary.abort_reason = rejection
            summary.skipped = len(provider_rows)
            summary.total_rows = summary.skipped
            await self._finalize_run(
                session,
                summary,
                status=AUDIT_STATUS_ABORTED,
                record_audit=record_audit,
                owns_transaction=owns_transaction,
            )
            logger.warning(
                redact_secrets(
                    f"Contract sync: refusing provider payload for {underlying} ({rejection})"
                )
            )
            return summary

        # A payload the parser partially dropped cannot prove absence. Refuse it
        # before any classification so no contract can be called "expired absent"
        # merely because its row failed to parse.
        if not provider_result.is_parse_complete:
            summary.aborted = True
            summary.abort_reason = (
                "PAYLOAD_INCOMPLETE:PARSE_FAILURES_"
                f"{summary.parse_failures}_OF_{provider_result.raw_row_count}"
            )
            summary.skipped = len(provider_rows)
            summary.total_rows = summary.skipped
            await self._finalize_run(
                session,
                summary,
                status=AUDIT_STATUS_ABORTED,
                record_audit=record_audit,
                owns_transaction=owns_transaction,
            )
            logger.warning(
                "Contract sync: %s payload incomplete (%s raw row(s), %s parse "
                "failure(s)); refusing absence-based classification",
                underlying,
                provider_result.raw_row_count,
                summary.parse_failures,
            )
            return summary

        index = await self._resolve_underlying_index(session, underlying)
        if index is None:
            summary.aborted = True
            summary.abort_reason = "REJECTED_UNKNOWN_UNDERLYING"
            summary.rejected = len(provider_rows)
            summary.total_rows = summary.rejected
            await self._finalize_run(
                session,
                summary,
                status=AUDIT_STATUS_ABORTED,
                record_audit=record_audit,
                owns_transaction=owns_transaction,
            )
            return summary

        summary.underlying_index_id = index.id

        plans: list[_SyncPlan] = []
        rows: list[ContractSyncRow] = []
        for provider_row in provider_rows:
            plan, rejected_row = self._normalize_row(
                provider_row, underlying=underlying, index=index, as_of_date=as_of_date
            )
            if plan is None:
                assert rejected_row is not None
                rows.append(rejected_row)
            else:
                plans.append(plan)

        repository = DerivativeContractRepository(session)
        existing = await repository.iter_existing_for_underlying(
            exchange=DEFAULT_EXCHANGE, underlying_index_id=index.id
        )
        by_fingerprint = {row.idempotency_hash: row for row in existing}
        by_provider_id = {
            row.source_contract_id: row
            for row in existing
            if row.source_contract_id is not None
        }
        # Global provider-identity preload: a key already asserted anywhere (this
        # or another underlying) is a conflict, classified before any write.
        global_contracts = await repository.iter_by_source_contract_ids(
            [plan.instrument_key for plan in plans]
        )
        by_global_provider_id = {
            row.source_contract_id: row
            for row in global_contracts
            if row.source_contract_id is not None
        }
        facts_by_contract = _group_metadata_facts(
            await repository.iter_metadata_for_contract_ids([row.id for row in existing])
        )

        inserts: list[DerivativeContract] = []
        pending_facts: list[_PendingFact] = []
        for plan in plans:
            contract = plan.build_contract()
            classified, fact = self._classify(
                contract,
                plan,
                by_fingerprint=by_fingerprint,
                by_provider_id=by_provider_id,
                by_global_provider_id=by_global_provider_id,
                facts_by_contract=facts_by_contract,
            )
            rows.append(classified)
            if classified.outcome is ContractSyncOutcome.INSERTED:
                inserts.append(contract)
                # Register immediately so duplicate identities inside one payload
                # cannot both become inserts.
                by_fingerprint[contract.idempotency_hash] = contract
                by_provider_id[plan.instrument_key] = contract
                by_global_provider_id[plan.instrument_key] = contract
            if fact is not None:
                pending_facts.append(fact)

        # Absence-based lifecycle classification is only sound for a complete,
        # fully validated payload: never for a payload with any blocking row.
        if not any(row.outcome in _BLOCKING_OUTCOMES for row in rows):
            matched_ids = {
                row.contract_id for row in rows if row.contract_id is not None
            }
            for contract in existing:
                if contract.id in matched_ids:
                    continue
                rows.append(self._classify_absence(contract, as_of_date))

        summary.rows = tuple(rows)
        summary.total_rows = len(rows)
        for row in rows:
            counter = _OUTCOME_COUNTERS.get(row.outcome)
            if counter is not None:
                setattr(summary, counter, getattr(summary, counter) + 1)
            if row.stale:
                summary.stale += 1
        # METADATA_FACT_CONFLICT rows are integrity conflicts, not "conflicts" in
        # the provider-identity sense; count them separately for diagnosis.
        summary.conflicts = sum(
            1
            for row in rows
            if row.outcome
            in {
                ContractSyncOutcome.CONFLICT_PROVIDER_IDENTITY,
                ContractSyncOutcome.CONFLICT_NATURAL_IDENTITY,
            }
        )

        blocking = summary.blocking_rows
        if blocking:
            # Fail-closed atomicity: never write a partial subset of a payload.
            summary.aborted = True
            summary.abort_reason = (
                f"PAYLOAD_NOT_CLEAN:{len(blocking)}_ROW(S)_"
                f"{blocking[0].outcome.value}"
            )
            summary.inserted = 0
            summary.metadata_changed = 0
            summary.metadata_facts_written = 0
            await self._finalize_run(
                session,
                summary,
                status=AUDIT_STATUS_ABORTED,
                record_audit=record_audit,
                owns_transaction=owns_transaction,
            )
            logger.warning(
                "Contract sync: %s payload aborted, %s blocking row(s); 0 rows written",
                underlying,
                len(blocking),
            )
            return summary

        if dry_run or (not inserts and not pending_facts):
            await self._finalize_run(
                session,
                summary,
                status=(
                    AUDIT_STATUS_DRY_RUN if dry_run else AUDIT_STATUS_NO_WRITE
                ),
                record_audit=record_audit,
                owns_transaction=owns_transaction,
            )
            return summary

        await self._write(
            session,
            summary,
            inserts,
            pending_facts,
            index,
            owns_transaction=owns_transaction,
            record_audit=record_audit,
        )
        summary.finished_at = datetime.now(UTC)
        logger.info(
            "Contract sync: %s inserted %s contract(s), %s metadata fact(s), as_of=%s",
            underlying,
            summary.inserted,
            summary.metadata_facts_written,
            as_of_date.isoformat(),
        )
        return summary

    async def _write(
        self,
        session: AsyncSession,
        summary: ContractSyncSummary,
        inserts: list[DerivativeContract],
        pending_facts: list[_PendingFact],
        index: MarketIndex,
        *,
        owns_transaction: bool,
        record_audit: bool,
    ) -> None:
        """Insert contracts and metadata facts under one advisory-locked transaction."""
        lock_key = (
            f"derivative_contract_sync|{DEFAULT_EXCHANGE}|{APPLICATION_SEGMENT}|{index.id}"
        )
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )
            for position, contract in enumerate(inserts, start=1):
                session.add(contract)
                if position % INSERT_FLUSH_BATCH == 0:
                    await session.flush()
            if inserts:
                await session.flush()
            for position, fact_plan in enumerate(pending_facts, start=1):
                if fact_plan.contract.id is None:  # pragma: no cover - defensive
                    await session.flush()
                session.add(
                    DerivativeContractMetadataHistory(
                        contract_id=fact_plan.contract.id,
                        lot_size=int(fact_plan.values["lot_size"]),
                        tick_size=fact_plan.values["tick_size"],
                        freeze_quantity=fact_plan.values["freeze_quantity"],
                        minimum_lot=fact_plan.values["minimum_lot"],
                        is_weekly=bool(fact_plan.values["is_weekly"]),
                        effective_from=fact_plan.effective_from,
                        effective_to=None,
                        available_at=fact_plan.effective_from,
                        source_name=SOURCE_NAME,
                        source_reference=SOURCE_REFERENCE,
                        provider_snapshot_hash=summary.payload_hash,
                    )
                )
                summary.metadata_facts_written += 1
                if position % INSERT_FLUSH_BATCH == 0:
                    await session.flush()
            if pending_facts:
                await session.flush()
            if record_audit:
                run = self._build_run(
                    summary, status=AUDIT_STATUS_COMMITTED, committed=True
                )
                session.add(run)
                await session.flush()
                summary.audit_run_id = run.id
            if owns_transaction:
                await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            summary.inserted = 0
            summary.metadata_changed = 0
            summary.metadata_facts_written = 0
            summary.aborted = True
            summary.abort_reason = "INTEGRITY_ERROR_ROLLED_BACK"
            raise ContractSyncError(
                "Contract synchronization failed and was rolled back; no contract "
                f"row from the '{summary.underlying}' payload was written "
                f"({type(exc).__name__})"
            ) from exc

    async def _finalize_run(
        self,
        session: AsyncSession,
        summary: ContractSyncSummary,
        *,
        status: str,
        record_audit: bool,
        owns_transaction: bool,
    ) -> None:
        """Close a run and persist its audit row (never a committed write)."""
        summary.finished_at = datetime.now(UTC)
        if not record_audit or not owns_transaction:
            return
        audit_status = AUDIT_STATUS_DRY_RUN if summary.dry_run else status
        run = self._build_run(summary, status=audit_status, committed=False)
        try:
            session.add(run)
            await session.flush()
            summary.audit_run_id = run.id
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            summary.audit_error = f"AUDIT_WRITE_FAILED:{type(exc).__name__}"
            logger.warning(
                "Contract sync: could not record the run audit for %s (%s)",
                summary.underlying,
                type(exc).__name__,
            )

    @staticmethod
    def _build_run(
        summary: ContractSyncSummary, *, status: str, committed: bool
    ) -> DerivativeContractSyncRun:
        return DerivativeContractSyncRun(
            underlying=summary.underlying,
            underlying_index_id=summary.underlying_index_id,
            as_of_date=summary.as_of_date,
            status=status,
            dry_run=summary.dry_run,
            committed=committed and not summary.dry_run,
            provider_source=summary.provider_source,
            source_name=SOURCE_NAME,
            source_reference=SOURCE_REFERENCE,
            payload_hash=summary.payload_hash,
            payload_hash_method=summary.payload_hash_method,
            total_rows=summary.total_rows,
            newly_listed=summary.inserted,
            unchanged=summary.unchanged,
            metadata_changed=summary.metadata_changed,
            metadata_drift=summary.metadata_drift,
            expired_absent=summary.expired_absent,
            missing_while_unexpired=summary.missing_while_unexpired,
            stale=summary.stale,
            conflicts=summary.conflicts,
            rejected=summary.rejected,
            skipped=summary.skipped,
            metadata_facts_written=summary.metadata_facts_written,
            abort_reason=summary.abort_reason,
            validation_summary=summary.validation_summary(),
        )

    # --------------------------------------------------------------- resolution

    async def _resolve_underlying_index(
        self, session: AsyncSession, underlying: str
    ) -> MarketIndex | None:
        """Resolve the underlying through the authoritative OPT-1 resolver.

        Reused deliberately: the reconciler and the option-chain persistence
        path must never disagree about an underlying's identity. No MarketIndex
        is ever fabricated here.
        """
        from app.services.option_chain_persistence_service import (
            OptionChainPersistenceService,
            UnderlyingResolutionError,
        )

        try:
            return await OptionChainPersistenceService()._resolve_underlying_index(
                session, None, underlying
            )
        except UnderlyingResolutionError as exc:
            logger.warning(
                "Contract sync: no authoritative MarketIndex for '%s': %s",
                underlying,
                exc,
            )
            return None

    # ---------------------------------------------------------------- normalize

    def _normalize_row(
        self,
        provider_row: OptionContractRow,
        *,
        underlying: str,
        index: MarketIndex,
        as_of_date: date,
    ) -> tuple[_SyncPlan | None, ContractSyncRow | None]:
        """Validate one provider row into a plan, or record its rejection."""

        def _reject(
            outcome: ContractSyncOutcome, reason: str
        ) -> tuple[None, ContractSyncRow]:
            return None, ContractSyncRow(
                outcome=outcome,
                instrument_key=(
                    str(provider_row.instrument_key) if provider_row.instrument_key else None
                ),
                trading_symbol=(
                    str(provider_row.trading_symbol) if provider_row.trading_symbol else None
                ),
                reason=reason,
            )

        instrument_key = str(provider_row.instrument_key or "").strip()
        if not instrument_key:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED, "missing instrument_key"
            )
        if len(instrument_key) > 64:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"instrument_key exceeds 64 characters ({len(instrument_key)})",
            )

        trading_symbol = str(provider_row.trading_symbol or "").strip()
        if not trading_symbol:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED, "missing trading_symbol"
            )
        if len(trading_symbol) > 64:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"trading_symbol exceeds 64 characters ({len(trading_symbol)})",
            )

        exchange = str(provider_row.exchange or "").strip().upper()
        if not exchange:
            return _reject(ContractSyncOutcome.REJECTED_MALFORMED, "missing exchange")

        provider_segment = str(provider_row.segment or "").strip().upper()
        application_segment = map_provider_segment(exchange, provider_segment)
        if application_segment is None:
            return _reject(
                ContractSyncOutcome.REJECTED_UNSUPPORTED_INSTRUMENT,
                f"unmapped provider segment ({exchange!r}, {provider_segment!r}); "
                "refusing to default a segment",
            )
        if application_segment != APPLICATION_SEGMENT:
            return _reject(
                ContractSyncOutcome.REJECTED_UNSUPPORTED_INSTRUMENT,
                f"provider segment {provider_segment!r} maps to application segment "
                f"{application_segment!r}, which is outside the {APPLICATION_SEGMENT} "
                "scope of this milestone",
            )

        row_underlying = str(provider_row.underlying_symbol or "").strip()
        if row_underlying:
            row_canonical = normalize_underlying(row_underlying)
            if row_canonical != underlying:
                return _reject(
                    ContractSyncOutcome.REJECTED_UNKNOWN_UNDERLYING,
                    f"provider underlying {row_underlying!r} does not match the "
                    f"requested underlying {underlying!r}",
                )

        option_type = str(provider_row.option_type or "").strip().upper()
        if option_type not in _OPTION_TYPES:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"invalid option_type {provider_row.option_type!r}; expected CE or PE",
            )

        expiry = provider_row.expiry_date
        if expiry is None or isinstance(expiry, datetime) or not isinstance(expiry, date):
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"invalid expiry_date {expiry!r}; expected a calendar date",
            )

        try:
            strike = Decimal(str(provider_row.strike_price))
        except (InvalidOperation, TypeError, ValueError):
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"invalid strike_price {provider_row.strike_price!r}",
            )
        if not strike.is_finite() or strike <= 0:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"non-positive strike_price {provider_row.strike_price!r}",
            )

        lot_size_raw = provider_row.lot_size
        if lot_size_raw is None or isinstance(lot_size_raw, bool):
            return _reject(ContractSyncOutcome.REJECTED_MALFORMED, "missing lot_size")
        try:
            lot_size = int(lot_size_raw)
        except (TypeError, ValueError):
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"invalid lot_size {lot_size_raw!r}",
            )
        if lot_size <= 0:
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"non-positive lot_size {lot_size_raw!r}",
            )

        tick_size: Decimal | None = None
        if provider_row.tick_size is not None:
            try:
                tick_size = Decimal(str(provider_row.tick_size))
            except (InvalidOperation, TypeError, ValueError):
                return _reject(
                    ContractSyncOutcome.REJECTED_MALFORMED,
                    f"invalid tick_size {provider_row.tick_size!r}",
                )
            if not tick_size.is_finite() or tick_size <= 0:
                return _reject(
                    ContractSyncOutcome.REJECTED_MALFORMED,
                    f"non-positive tick_size {provider_row.tick_size!r}",
                )

        freeze_quantity = provider_row.freeze_quantity
        if freeze_quantity is not None and (
            isinstance(freeze_quantity, bool) or freeze_quantity <= 0
        ):
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"non-positive freeze_quantity {freeze_quantity!r}",
            )

        minimum_lot = provider_row.minimum_lot
        if minimum_lot is not None and (
            isinstance(minimum_lot, bool) or minimum_lot <= 0
        ):
            return _reject(
                ContractSyncOutcome.REJECTED_MALFORMED,
                f"non-positive minimum_lot {minimum_lot!r}",
            )

        plan = _SyncPlan(
            instrument_key=instrument_key,
            trading_symbol=trading_symbol,
            exchange=exchange,
            segment=application_segment,
            provider_segment=provider_segment,
            underlying_symbol=underlying,
            expiry_date=expiry,
            strike_price=strike,
            option_type=option_type,
            lot_size=lot_size,
            tick_size=tick_size,
            underlying_index_id=index.id,
            as_of_date=as_of_date,
            freeze_quantity=freeze_quantity,
            minimum_lot=minimum_lot,
            is_weekly=bool(provider_row.is_weekly),
            provider_meta={
                "name": provider_row.name,
                "underlying_type": provider_row.underlying_type,
                "freeze_quantity": provider_row.freeze_quantity,
                "minimum_lot": provider_row.minimum_lot,
                "exchange_token": provider_row.exchange_token,
                "is_weekly": provider_row.is_weekly,
            },
        )
        return plan, None

    # -------------------------------------------------------------- classify

    def _classify(
        self,
        contract: DerivativeContract,
        plan: _SyncPlan,
        *,
        by_fingerprint: Mapping[str, DerivativeContract],
        by_provider_id: Mapping[str, DerivativeContract],
        by_global_provider_id: Mapping[str, DerivativeContract],
        facts_by_contract: Mapping[int, Sequence[DerivativeContractMetadataHistory]],
    ) -> tuple[ContractSyncRow, _PendingFact | None]:
        """Classify one candidate against the preloaded existing identities."""
        existing_by_provider = by_provider_id.get(plan.instrument_key)
        existing_by_fingerprint = by_fingerprint.get(contract.idempotency_hash)

        if existing_by_provider is None:
            global_match = by_global_provider_id.get(plan.instrument_key)
            if global_match is not None and global_match is not existing_by_fingerprint:
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.CONFLICT_PROVIDER_IDENTITY,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=getattr(global_match, "id", None),
                        reason=(
                            "provider instrument_key is already asserted by contract "
                            f"#{getattr(global_match, 'id', None)} (economic identity "
                            "differs or belongs to another underlying); refusing "
                            "cross-underlying provider-identity reuse"
                        ),
                    ),
                    None,
                )

        if existing_by_provider is not None:
            provider_fingerprint = getattr(
                existing_by_provider, "idempotency_hash", None
            )
            if provider_fingerprint != contract.idempotency_hash:
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.CONFLICT_PROVIDER_IDENTITY,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=getattr(existing_by_provider, "id", None),
                        reason=(
                            "provider instrument_key already identifies a different "
                            f"economic identity (existing contract #{getattr(existing_by_provider, 'id', None)})"
                        ),
                    ),
                    None,
                )
            return self._classify_matched(
                existing_by_provider, plan, facts_by_contract
            )

        if existing_by_fingerprint is not None:
            existing_provider_id = existing_by_fingerprint.source_contract_id
            if (
                existing_provider_id is not None
                and existing_provider_id != plan.instrument_key
            ):
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.CONFLICT_NATURAL_IDENTITY,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=existing_by_fingerprint.id,
                        reason=(
                            "economic identity already carries a different provider "
                            f"instrument_key ({existing_provider_id!r})"
                        ),
                    ),
                    None,
                )
            return self._classify_matched(
                existing_by_fingerprint, plan, facts_by_contract
            )

        return (
            ContractSyncRow(
                outcome=ContractSyncOutcome.INSERTED,
                instrument_key=plan.instrument_key,
                trading_symbol=plan.trading_symbol,
                reason="new economic identity",
            ),
            _PendingFact(
                contract=contract,
                values=plan.metadata_values(),
                effective_from=plan.as_of_date,
            ),
        )

    def _classify_matched(
        self,
        existing: DerivativeContract,
        plan: _SyncPlan,
        facts_by_contract: Mapping[int, Sequence[DerivativeContractMetadataHistory]],
    ) -> tuple[ContractSyncRow, _PendingFact | None]:
        """Classify a row whose economic (and provider) identity already exists."""
        facts = facts_by_contract.get(existing.id, ())
        latest = facts[0] if facts else None
        stale = existing.source_contract_id is None

        metadata_diff = _metadata_diff(existing, latest, plan)
        descriptive_diff = _descriptive_diff(existing, plan)

        if metadata_diff:
            if stale:
                # Legacy row: reported (STALE), never versioned, never rewritten.
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.METADATA_DRIFT,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=existing.id,
                        reason=(
                            "existing economic identity retained; legacy row carries "
                            "no provider identity, so metadata is reported and never "
                            "versioned or backfilled"
                        ),
                        drift=metadata_diff,
                        stale=True,
                    ),
                    None,
                )
            if latest is not None and latest.effective_from > plan.as_of_date:
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.METADATA_FACT_CONFLICT,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=existing.id,
                        reason=(
                            "out-of-order metadata observation: contract #"
                            f"{existing.id} already carries an effective metadata "
                            f"fact dated {latest.effective_from.isoformat()}, which is "
                            f"later than the reconciliation date "
                            f"{plan.as_of_date.isoformat()}; appending a retroactive "
                            "fact needs an explicit correction policy"
                        ),
                        drift=metadata_diff,
                    ),
                    None,
                )
            if latest is not None and latest.effective_from == plan.as_of_date:
                return (
                    ContractSyncRow(
                        outcome=ContractSyncOutcome.METADATA_FACT_CONFLICT,
                        instrument_key=plan.instrument_key,
                        trading_symbol=plan.trading_symbol,
                        contract_id=existing.id,
                        reason=(
                            "ambiguous metadata for one effective date: contract #"
                            f"{existing.id} already carries a different metadata fact "
                            f"effective {plan.as_of_date.isoformat()}; two different "
                            "metadata states for the same effective date cannot be "
                            "resolved deterministically"
                        ),
                        drift=metadata_diff,
                    ),
                    None,
                )
            return (
                ContractSyncRow(
                    outcome=ContractSyncOutcome.METADATA_CHANGED,
                    instrument_key=plan.instrument_key,
                    trading_symbol=plan.trading_symbol,
                    contract_id=existing.id,
                    reason=(
                        "existing economic identity retained; metadata versioned as a "
                        "new effective-dated fact"
                    ),
                    drift=metadata_diff,
                    versioned=True,
                ),
                _PendingFact(
                    contract=existing,
                    values=plan.metadata_values(),
                    effective_from=plan.as_of_date,
                ),
            )

        if descriptive_diff:
            return (
                ContractSyncRow(
                    outcome=ContractSyncOutcome.METADATA_DRIFT,
                    instrument_key=plan.instrument_key,
                    trading_symbol=plan.trading_symbol,
                    contract_id=existing.id,
                    reason="existing economic identity retained; descriptive metadata differs",
                    drift=descriptive_diff,
                    stale=stale,
                ),
                None,
            )

        return (
            ContractSyncRow(
                outcome=ContractSyncOutcome.UNCHANGED,
                instrument_key=plan.instrument_key,
                trading_symbol=plan.trading_symbol,
                contract_id=existing.id,
                stale=stale,
            ),
            None,
        )

    @staticmethod
    def _classify_absence(
        existing: DerivativeContract, as_of_date: date
    ) -> ContractSyncRow:
        """Classify a previously known contract absent from a complete payload."""
        expired = existing.expiry_date < as_of_date
        return ContractSyncRow(
            outcome=(
                ContractSyncOutcome.EXPIRED_ABSENT
                if expired
                else ContractSyncOutcome.MISSING_WHILE_UNEXPIRED
            ),
            instrument_key=existing.source_contract_id,
            trading_symbol=existing.trading_symbol,
            contract_id=existing.id,
            reason=(
                "known contract absent from a complete provider payload and already "
                "past expiry; no write, no deletion"
                if expired
                else "known unexpired contract absent from a complete provider "
                "payload; treating this as an integrity anomaly and aborting the "
                "underlying reconciliation"
            ),
            stale=existing.source_contract_id is None,
        )


def _group_metadata_facts(
    facts: Sequence[DerivativeContractMetadataHistory],
) -> dict[int, list[DerivativeContractMetadataHistory]]:
    """Group the preloaded timeline newest-first per contract."""
    grouped: dict[int, list[DerivativeContractMetadataHistory]] = {}
    for fact in facts:
        grouped.setdefault(fact.contract_id, []).append(fact)
    for contract_facts in grouped.values():
        contract_facts.sort(
            key=lambda fact: (fact.effective_from, fact.id or 0), reverse=True
        )
    return grouped


def _metadata_diff(
    existing: DerivativeContract,
    latest: DerivativeContractMetadataHistory | None,
    plan: _SyncPlan,
) -> dict[str, tuple[Any, Any]]:
    """Metadata fields whose provider value differs from the recorded value.

    ``None`` on either side means "not published / not recorded" and is never
    treated as a change: a value the provider did not publish must not erase a
    known fact, and a value that was never recorded must not be invented as drift.
    """
    target = plan.metadata_values()
    recorded: dict[str, Any] = {
        "lot_size": latest.lot_size if latest is not None else existing.lot_size,
        "tick_size": latest.tick_size if latest is not None else existing.tick_size,
        "freeze_quantity": latest.freeze_quantity if latest is not None else None,
        "minimum_lot": latest.minimum_lot if latest is not None else None,
        "is_weekly": latest.is_weekly if latest is not None else None,
    }
    diff: dict[str, tuple[Any, Any]] = {}
    for field_name in METADATA_FIELDS:
        old_value = recorded.get(field_name)
        new_value = target.get(field_name)
        if old_value is None or new_value is None:
            continue
        if not _values_equal(old_value, new_value):
            diff[field_name] = (old_value, new_value)
    return diff


def _descriptive_diff(
    existing: DerivativeContract, plan: _SyncPlan
) -> dict[str, tuple[Any, Any]]:
    """Descriptive (non-versioned) fields whose provider value differs."""
    provider_values = plan.drift_values()
    diff: dict[str, tuple[Any, Any]] = {}
    for field_name in DESCRIPTIVE_DRIFT_FIELDS:
        existing_value = getattr(existing, field_name, None)
        provider_value = provider_values[field_name]
        if _normalize_comparable(existing_value) != _normalize_comparable(provider_value):
            diff[field_name] = (existing_value, provider_value)
    return diff


def _values_equal(left: Any, right: Any) -> bool:
    return _normalize_comparable(left) == _normalize_comparable(right)


def _normalize_comparable(value: Any) -> Any:
    """Comparable form of a stored/provider value (Decimal-scale insensitive)."""
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, str):
        return value.strip()
    return value
