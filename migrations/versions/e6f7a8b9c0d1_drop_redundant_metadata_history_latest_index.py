"""Drop redundant derivative-contract metadata-history index.

Revision ID: e6f7a8b9c0d1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-27

This migration removes only the duplicate (contract_id, effective_from) index.
The historical migration that created both indexes is intentionally unchanged.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_derivative_contract_metadata_histories_contract_id_latest",
        table_name="derivative_contract_metadata_histories",
    )


def downgrade() -> None:
    op.create_index(
        "ix_derivative_contract_metadata_histories_contract_id_latest",
        "derivative_contract_metadata_histories",
        ["contract_id", "effective_from"],
        unique=False,
    )
