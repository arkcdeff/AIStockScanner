"""Add the BANKNIFTY MarketIndex required by derivative persistence.

Milestone: G.11-OPT-14C — Capture-Flag Enforcement + MarketIndex Reconciliation

The derivatives option-chain pipeline resolves its underlying through
``MarketIndex`` (``OptionChainPersistenceService._resolve_underlying_index``),
which matches on ``code``/``provider_symbol``. G.11-OPT-14A proved that
``market_indices`` held a single ``NIFTY`` row and no ``BANKNIFTY`` row, so
BANKNIFTY observations could not be persisted at all.

This migration is DATA-ONLY: no schema is altered. It is deterministic and
idempotent — it inserts the canonical ``BANKNIFTY`` row only when neither the
``BANKNIFTY`` code nor the ``BANKNIFTY`` provider symbol is already present. It
never modifies or deletes the existing NIFTY row and never touches derivative
history.

``code`` is deliberately the short provider code (not the catalog's
``NIFTY_BANK``) because that is the identifier the derivative resolution path and
the NSE derivative importers already look up; ``index_catalog`` now reconciles
the catalog code ``NIFTY_BANK`` onto this row instead of creating a duplicate.

Revision ID: c4d5e6f7a8b9
Revises: b3a4c5d6e7f8
Create Date: 2026-10-06 17:10:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3a4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INSERT_BANKNIFTY = """
INSERT INTO market_indices
    (code, name, country, exchange, category, currency, timezone, provider_symbol, is_active)
SELECT 'BANKNIFTY', 'Nifty Bank', 'INDIA', 'NSE', 'indian_index', 'INR',
       'Asia/Kolkata', 'BANKNIFTY', TRUE
WHERE NOT EXISTS (SELECT 1 FROM market_indices WHERE UPPER(code) = 'BANKNIFTY')
  AND NOT EXISTS (
      SELECT 1 FROM market_indices WHERE UPPER(provider_symbol) = 'BANKNIFTY'
  )
"""

_DELETE_BANKNIFTY = """
DELETE FROM market_indices
WHERE UPPER(code) = 'BANKNIFTY'
  AND NOT EXISTS (
      SELECT 1 FROM derivative_contracts dc
      WHERE dc.underlying_index_id = market_indices.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM option_chain_snapshots s
      WHERE s.underlying_index_id = market_indices.id
  )
"""


def upgrade() -> None:
    op.execute(_INSERT_BANKNIFTY)


def downgrade() -> None:
    # Data-preserving by design: the row is removed only when nothing references
    # it, so a downgrade can never cascade into historical derivatives.
    op.execute(_DELETE_BANKNIFTY)
