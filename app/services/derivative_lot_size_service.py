"""Single derivative lot-size authority — G.11-OPT-14E-C.

This module is the one place where a derivative lot size is resolved for the
analytics readers (historical options analytics, advanced options, advanced
exposure) and where the underlying-scoped ``derivative_lot_size_histories``
timeline is *produced* from the per-contract metadata timeline added by
G.11-OPT-14E-A/14E-B.

Resolution precedence (identical for every reader):

1. ``derivative_contract_metadata_histories`` fact effective for the
   observation date **and** already knowable at the ``as_of`` date.
2. ``derivative_lot_size_histories`` underlying-scoped fact for the same
   window (what the three readers have always queried first).
3. The ``derivative_contracts`` insert-time snapshot, but **only** when it
   carries provenance (``source_name IS NOT NULL``).
4. Otherwise the value is explicitly ``UNAVAILABLE``.

Why provenance is required at every step: legacy bulk imports wrote fabricated
lot sizes (``1``) and the 14D audit found production rows with ``25`` while the
provider published ``75``; every one of those rows has ``source_name IS NULL``.
A value whose provenance cannot be established must never be multiplied into
premium, GEX, DEX, vanna or charm figures, so it resolves to ``UNAVAILABLE``
(``MISSING != ZERO``) instead of a guessed number.

Nothing here writes to ``derivative_contracts`` and nothing calls a provider.
The producer only appends to ``derivative_lot_size_histories``.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeContractMetadataHistory,
    DerivativeLotSizeHistory,
)
from app.repositories.derivative import DerivativeContractRepository

# ---------------------------------------------------------------------------
# Vocabulary shared by every reader (exported so tests and callers never
# invent a fifth spelling of the same fact).
# ---------------------------------------------------------------------------
SOURCE_CONTRACT_METADATA = "CONTRACT_METADATA_PIT"
SOURCE_UNDERLYING_HISTORY = "HISTORICAL_PIT"
SOURCE_CONTRACT_ROW = "SOURCE_VERIFIED"
SOURCE_UNAVAILABLE = "UNAVAILABLE"

QUALITY_VERIFIED_HISTORICAL = "VERIFIED_HISTORICAL"
QUALITY_SOURCE_VERIFIED = "SOURCE_VERIFIED"
QUALITY_UNAVAILABLE = "UNAVAILABLE"

REASON_NO_EFFECTIVE_FACT = "NO_EFFECTIVE_METADATA_FACT"
REASON_UNTRUSTED_METADATA_FACT = "UNTRUSTED_METADATA_FACT_PROVENANCE"
REASON_UNTRUSTED_CONTRACT_ROW = "UNTRUSTED_LEGACY_CONTRACT_ROW"
REASON_MISSING_CONTRACT = "CONTRACT_ROW_MISSING"
REASON_NON_POSITIVE = "NON_POSITIVE_LOT_SIZE"
REASON_UNDERLYING_HISTORY_ABSENT = "NO_UNDERLYING_HISTORY"

# Producer outcomes.
PRODUCER_CREATED = "CREATED"
PRODUCER_PROPOSED = "PROPOSED"
PRODUCER_EXISTS = "EXISTS"
PRODUCER_CONFLICTING = "CONFLICTING"
PRODUCER_NO_EVIDENCE = "NO_EVIDENCE"
PRODUCER_INVALID = "INVALID"

PRODUCER_SOURCE_NAME = "DERIVATIVE_CONTRACT_METADATA_DERIVED"


@dataclass(frozen=True)
class LotSizeResolution:
    """One contract's lot size plus where it came from and how good it is."""

    contract_id: int | None
    lot_size: int | None
    source: str = SOURCE_UNAVAILABLE
    quality: str = QUALITY_UNAVAILABLE
    reason: str | None = None

    @property
    def is_available(self) -> bool:
        return (
            self.lot_size is not None
            and int(self.lot_size) > 0
            and self.source != SOURCE_UNAVAILABLE
            and self.quality != QUALITY_UNAVAILABLE
        )

    def as_tuple(self) -> tuple[int | None, str, str]:
        """``(lot_size, source, quality)`` — the shape the readers already use."""
        return (self.lot_size if self.is_available else None, self.source, self.quality)


@dataclass(frozen=True)
class LotSizeHistoryProductionResult:
    """Outcome of one underlying-scoped lot-size history derivation."""

    status: str
    underlying_type: str
    underlying_id: int
    lot_size: int | None = None
    effective_from: date | None = None
    available_at: date | None = None
    history_id: int | None = None
    contributing_contract_ids: tuple[int, ...] = ()
    conflicting_lot_sizes: tuple[int, ...] = ()
    reason: str | None = None

    @property
    def wrote_history(self) -> bool:
        return self.status == PRODUCER_CREATED

    @property
    def proposed(self) -> bool:
        return self.status == PRODUCER_PROPOSED


class DerivativeLotSizeAuthority:
    """Resolve lot size for contracts / an underlying at a point in time."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DerivativeContractRepository(session)

    # -- fact lookup -------------------------------------------------------
    async def latest_metadata_facts(
        self,
        contract_ids: Iterable[int],
        *,
        trade_date: date,
        as_of: date,
        require_provenance: bool = True,
    ) -> dict[int, DerivativeContractMetadataHistory]:
        """Latest effective metadata fact per contract, deterministic.

        Same resolution rule as the repository's ``get_metadata_as_of``:
        ``effective_from <= trade_date``, the interval still open (or covering
        ``trade_date``), ``available_at <= as_of``, ordered by
        ``effective_from DESC, id DESC`` and reduced to the first row per
        contract. Batching keeps the readers at one query instead of one per
        contract.
        """
        ids = sorted({int(c) for c in contract_ids})
        if not ids:
            return {}

        stmt = (
            select(DerivativeContractMetadataHistory)
            .where(
                DerivativeContractMetadataHistory.contract_id.in_(ids),
                DerivativeContractMetadataHistory.effective_from <= trade_date,
                (
                    DerivativeContractMetadataHistory.effective_to.is_(None)
                    | (DerivativeContractMetadataHistory.effective_to >= trade_date)
                ),
                DerivativeContractMetadataHistory.available_at <= as_of,
            )
            .order_by(
                DerivativeContractMetadataHistory.contract_id.asc(),
                DerivativeContractMetadataHistory.effective_from.desc(),
                DerivativeContractMetadataHistory.id.desc(),
            )
        )
        if require_provenance:
            stmt = stmt.where(DerivativeContractMetadataHistory.source_name.is_not(None))

        result = await self.session.execute(stmt)
        latest: dict[int, DerivativeContractMetadataHistory] = {}
        for fact in result.scalars().all():
            latest.setdefault(fact.contract_id, fact)
        return latest

    async def untrusted_metadata_contract_ids(
        self,
        contract_ids: Iterable[int],
        *,
        trade_date: date,
        as_of: date,
    ) -> set[int]:
        """Contracts whose only effective facts carry no provenance."""
        ids = sorted({int(c) for c in contract_ids})
        if not ids:
            return set()

        stmt = (
            select(DerivativeContractMetadataHistory.contract_id)
            .where(
                DerivativeContractMetadataHistory.contract_id.in_(ids),
                DerivativeContractMetadataHistory.effective_from <= trade_date,
                (
                    DerivativeContractMetadataHistory.effective_to.is_(None)
                    | (DerivativeContractMetadataHistory.effective_to >= trade_date)
                ),
                DerivativeContractMetadataHistory.available_at <= as_of,
                DerivativeContractMetadataHistory.source_name.is_(None),
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return {int(row[0]) for row in result.all()}

    async def underlying_history(
        self,
        underlying_type: str,
        underlying_id: int,
        *,
        trade_date: date,
        as_of: date,
    ) -> LotSizeResolution:
        """Underlying-scoped lot size effective on ``trade_date`` (PIT)."""
        col = (
            DerivativeLotSizeHistory.underlying_index_id
            if underlying_type == "INDEX"
            else DerivativeLotSizeHistory.underlying_stock_id
        )
        stmt = (
            select(DerivativeLotSizeHistory)
            .where(col == underlying_id)
            .where(DerivativeLotSizeHistory.effective_from <= trade_date)
            .where(
                (DerivativeLotSizeHistory.effective_to.is_(None))
                | (DerivativeLotSizeHistory.effective_to >= trade_date)
            )
            .where(DerivativeLotSizeHistory.available_at <= as_of)
            .order_by(
                DerivativeLotSizeHistory.effective_from.desc(),
                DerivativeLotSizeHistory.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        row = result.scalars().first()
        if row is not None and row.lot_size is not None and int(row.lot_size) > 0:
            return LotSizeResolution(
                contract_id=None,
                lot_size=int(row.lot_size),
                source=SOURCE_UNDERLYING_HISTORY,
                quality=QUALITY_VERIFIED_HISTORICAL,
            )
        return LotSizeResolution(
            contract_id=None,
            lot_size=None,
            reason=REASON_UNDERLYING_HISTORY_ABSENT,
        )

    # -- the single precedence rule ---------------------------------------
    async def resolve_for_contracts(
        self,
        contract_ids: Iterable[int],
        *,
        underlying_type: str,
        underlying_id: int,
        trade_date: date,
        as_of: date,
    ) -> dict[int, LotSizeResolution]:
        """Resolve every requested contract with one shared precedence rule.

        Per-contract metadata fact -> underlying history -> provenanced contract
        row -> ``UNAVAILABLE``. Contract-specific values are never borrowed from
        a sibling contract, so two contracts of the same underlying can resolve
        differently (``G-15``).
        """
        ids = sorted({int(c) for c in contract_ids})
        if not ids:
            return {}

        rows = await self.session.execute(
            select(
                DerivativeContract.id,
                DerivativeContract.lot_size,
                DerivativeContract.source_name,
            ).where(DerivativeContract.id.in_(ids))
        )
        contract_rows: dict[int, tuple[int | None, str | None]] = {
            int(c_id): (lot, source_name) for c_id, lot, source_name in rows.all()
        }

        facts = await self.latest_metadata_facts(ids, trade_date=trade_date, as_of=as_of)
        untrusted_facts = await self.untrusted_metadata_contract_ids(
            ids, trade_date=trade_date, as_of=as_of
        )
        underlying = await self.underlying_history(
            underlying_type, underlying_id, trade_date=trade_date, as_of=as_of
        )

        resolutions: dict[int, LotSizeResolution] = {}
        for c_id in ids:
            fact = facts.get(c_id)
            if fact is not None and fact.lot_size is not None and int(fact.lot_size) > 0:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id,
                    lot_size=int(fact.lot_size),
                    source=SOURCE_CONTRACT_METADATA,
                    quality=QUALITY_VERIFIED_HISTORICAL,
                )
                continue

            if underlying.is_available:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id,
                    lot_size=underlying.lot_size,
                    source=SOURCE_UNDERLYING_HISTORY,
                    quality=QUALITY_VERIFIED_HISTORICAL,
                )
                continue

            row = contract_rows.get(c_id)
            if row is None:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id, lot_size=None, reason=REASON_MISSING_CONTRACT
                )
                continue

            lot_size, source_name = row
            if c_id in untrusted_facts:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id, lot_size=None, reason=REASON_UNTRUSTED_METADATA_FACT
                )
            elif source_name is None:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id, lot_size=None, reason=REASON_UNTRUSTED_CONTRACT_ROW
                )
            elif lot_size is None or int(lot_size) <= 0:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id, lot_size=None, reason=REASON_NON_POSITIVE
                )
            else:
                resolutions[c_id] = LotSizeResolution(
                    contract_id=c_id,
                    lot_size=int(lot_size),
                    source=SOURCE_CONTRACT_ROW,
                    quality=QUALITY_SOURCE_VERIFIED,
                )
        return resolutions

    async def resolve_one(
        self,
        contract_id: int,
        *,
        underlying_type: str,
        underlying_id: int,
        trade_date: date,
        as_of: date,
    ) -> LotSizeResolution:
        resolved = await self.resolve_for_contracts(
            [contract_id],
            underlying_type=underlying_type,
            underlying_id=underlying_id,
            trade_date=trade_date,
            as_of=as_of,
        )
        return resolved.get(
            int(contract_id),
            LotSizeResolution(contract_id=int(contract_id), lot_size=None),
        )


class DerivativeLotSizeHistoryProducer:
    """Append an underlying-scoped lot-size fact from per-contract metadata.

    The producer never reads a contract row and never calls a provider: the
    per-contract metadata timeline is the only evidence. A fact is written only
    when the trusted, effective facts of **every** observed contract for the
    underlying agree on one lot size; otherwise the run is reported as
    ``CONFLICTING`` and nothing is written. That is what stops a transition
    window (old and new contracts coexisting with different lot sizes) from
    being flattened into one false underlying-wide value.

    ``effective_from`` is the latest ``effective_from`` among the contributing
    facts — a date the source actually supports. Nothing is back-dated, no
    ``effective_to`` is ever written or rewritten, and a value already in force
    is not re-appended, so repeated runs are idempotent.

    ``dry_run=True`` is a planning-only mode: the decision is computed exactly as
    for a write, but the would-be fact is **never** added to the session. It is
    reported as ``PROPOSED`` (not ``CREATED``) so no caller can mistake a plan for
    a committed write. Nothing is written and nothing is rolled back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DerivativeContractRepository(session)
        self.authority = DerivativeLotSizeAuthority(session)

    async def produce(
        self,
        *,
        underlying_type: str,
        underlying_id: int,
        as_of: date,
        dry_run: bool = False,
    ) -> LotSizeHistoryProductionResult:
        if underlying_type not in ("INDEX", "STOCK"):
            return LotSizeHistoryProductionResult(
                status=PRODUCER_INVALID,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                reason="UNKNOWN_UNDERLYING_TYPE",
            )
        if underlying_id is None or int(underlying_id) <= 0:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_INVALID,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                reason="MISSING_UNDERLYING_ID",
            )
        underlying_id = int(underlying_id)

        col = (
            DerivativeContract.underlying_index_id
            if underlying_type == "INDEX"
            else DerivativeContract.underlying_stock_id
        )
        contract_rows = await self.session.execute(
            select(DerivativeContract.id).where(
                col == underlying_id,
                DerivativeContract.instrument_type == "OPTION",
                DerivativeContract.available_at <= as_of,
            )
        )
        contract_ids = sorted({int(row[0]) for row in contract_rows.all()})
        if not contract_ids:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_NO_EVIDENCE,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                reason="NO_OPTION_CONTRACTS",
            )

        # Only the per-contract metadata facts are evidence. Contract rows are
        # deliberately not consulted: a legacy fabricated lot size must never be
        # promoted to an underlying-wide assertion.
        facts = await self.authority.latest_metadata_facts(
            contract_ids, trade_date=as_of, as_of=as_of
        )
        if not facts:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_NO_EVIDENCE,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                contributing_contract_ids=(),
                reason="NO_PROVENANCED_METADATA_FACTS",
            )

        contributing = sorted(facts.keys())
        lot_sizes = sorted({int(facts[c_id].lot_size) for c_id in contributing})
        if len(lot_sizes) > 1:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_CONFLICTING,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                contributing_contract_ids=tuple(contributing),
                conflicting_lot_sizes=tuple(lot_sizes),
                reason="CONTRACT_METADATA_DISAGREES",
            )

        lot_size = lot_sizes[0]
        if lot_size <= 0:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_INVALID,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                contributing_contract_ids=tuple(contributing),
                reason=REASON_NON_POSITIVE,
            )

        effective_from = max(facts[c_id].effective_from for c_id in contributing)
        available_at = max(facts[c_id].available_at for c_id in contributing)

        latest = await self.repository.get_latest_lot_size_history(
            underlying_stock_id=underlying_id if underlying_type == "STOCK" else None,
            underlying_index_id=underlying_id if underlying_type == "INDEX" else None,
        )
        if latest is not None and latest.effective_to is None and int(latest.lot_size) == lot_size:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_EXISTS,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                lot_size=lot_size,
                effective_from=latest.effective_from,
                available_at=latest.available_at,
                history_id=latest.id,
                contributing_contract_ids=tuple(contributing),
                reason="LOT_SIZE_ALREADY_IN_FORCE",
            )

        if dry_run:
            return LotSizeHistoryProductionResult(
                status=PRODUCER_PROPOSED,
                underlying_type=underlying_type,
                underlying_id=underlying_id,
                lot_size=lot_size,
                effective_from=effective_from,
                available_at=available_at,
                history_id=None,
                contributing_contract_ids=tuple(contributing),
                reason="DRY_RUN_NO_WRITE",
            )

        history = DerivativeLotSizeHistory(
            underlying_stock_id=underlying_id if underlying_type == "STOCK" else None,
            underlying_index_id=underlying_id if underlying_type == "INDEX" else None,
            lot_size=lot_size,
            effective_from=effective_from,
            effective_to=None,
            available_at=available_at,
            source_name=PRODUCER_SOURCE_NAME,
            source_reference=(
                f"derived_from=derivative_contract_metadata_histories;"
                f"contracts={len(contributing)};as_of={as_of.isoformat()}"
            ),
        )
        created = await self.repository.add_lot_size_history(history)
        return LotSizeHistoryProductionResult(
            status=PRODUCER_CREATED,
            underlying_type=underlying_type,
            underlying_id=underlying_id,
            lot_size=lot_size,
            effective_from=effective_from,
            available_at=available_at,
            history_id=created.id,
            contributing_contract_ids=tuple(contributing),
        )
