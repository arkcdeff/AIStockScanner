"""Add provider_segment and the provider-identity partial unique index.

Milestone: G.11-OPT-14D — Derivatives Contract Master Synchronization Foundation

Two additive, non-destructive schema changes enable provider contract-master
synchronization without touching any existing economic identity:

1. ``derivative_contracts.provider_segment`` (VARCHAR(16), nullable)
   The provider's raw segment (Upstox returns ``NSE_FO``) is preserved here while
   the application's canonical ``segment`` column keeps the value every existing
   consumer filters on (``NFO``). G.11-OPT-14C proved that the OPT-1 option-chain
   persistence path resolves contracts by ``(exchange, segment, underlying,
   OPTION, expiry, strike, option_type)`` with the application segment, so
   writing the provider segment into ``segment`` would make synchronized
   contracts invisible to the only production consumer.

2. ``uq_derivative_contracts_source_contract_id``
   A **partial** unique index (``WHERE source_contract_id IS NOT NULL``) so that
   one provider instrument key can identify at most one canonical contract.
   Legacy rows legitimately carry NULL and remain unaffected. The economic
   fingerprint (``idempotency_hash``) stays the primary natural identity; this
   index is the provider-identity assertion.

``segment``, ``exchange``, ``idempotency_hash`` and every existing constraint and
row are deliberately left untouched: the four historical contracts are
referenced by historical option-chain quotes through ``ON DELETE CASCADE``.

Duplicate ``source_contract_id`` values are impossible to index uniquely, so the
upgrade verifies that none exist and fails closed rather than silently dropping
a pre-existing duplicate.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-07 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DUPLICATE_PROVIDER_IDS = (
    "SELECT source_contract_id, COUNT(*) AS occurrences "
    "FROM derivative_contracts "
    "WHERE source_contract_id IS NOT NULL "
    "GROUP BY source_contract_id "
    "HAVING COUNT(*) > 1"
)


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(sa.text(_DUPLICATE_PROVIDER_IDS)).fetchall()
    if duplicates:
        listing = ", ".join(
            f"{row[0]!r}x{row[1]}" for row in duplicates[:10]
        )
        raise RuntimeError(
            "Refusing to create uq_derivative_contracts_source_contract_id: "
            f"duplicate non-null source_contract_id values exist ({listing}). "
            "One provider instrument key must identify one canonical contract; "
            "reconcile the duplicates explicitly before migrating."
        )

    op.add_column(
        "derivative_contracts",
        sa.Column("provider_segment", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "uq_derivative_contracts_source_contract_id",
        "derivative_contracts",
        ["source_contract_id"],
        unique=True,
        postgresql_where=sa.text("source_contract_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_derivative_contracts_source_contract_id",
        table_name="derivative_contracts",
    )
    op.drop_column("derivative_contracts", "provider_segment")
