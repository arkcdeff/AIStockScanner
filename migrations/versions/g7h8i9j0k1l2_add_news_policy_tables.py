"""add news policy tables

Revision ID: g7h8i9j0k1l2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-20 20:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7h8i9j0k1l2'
down_revision: str | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create news_events table
    op.create_table(
        "news_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("headline", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="OFFICIAL_PRIMARY"),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.Date(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False, server_default="COMPANY"),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="OTHER"),
        sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="STOCK"),
        sa.Column("government_body", sa.String(length=64), nullable=True),
        sa.Column("regulator", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=False, server_default="en"),
        sa.Column("deterministic_context", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.Column("event_severity", sa.String(length=32), nullable=False, server_default="MEDIUM"),
        sa.Column("event_risk_flag", sa.String(length=64), nullable=True),
        sa.Column("confidence_score", sa.Numeric(precision=4, scale=3), nullable=True, server_default="1.0"),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("sector_id", sa.Integer(), nullable=True),
        sa.Column("market_index_id", sa.Integer(), nullable=True),
        sa.Column("corporate_announcement_id", sa.Integer(), nullable=True),
        sa.Column("econ_event_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["market_index_id"], ["market_indices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["corporate_announcement_id"], ["corporate_announcements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["econ_event_id"], ["econ_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_hash"),
        sa.CheckConstraint(
            "source_type IN ('OFFICIAL_PRIMARY', 'OFFICIAL_SECONDARY', 'LICENSED_NEWS', 'VALIDATED_LOCAL', 'UNVERIFIED')",
            name="ck_news_events_source_type",
        ),
        sa.CheckConstraint(
            "event_type IN ('COMPANY', 'GOVERNMENT_POLICY', 'RBI_MONETARY', 'SEBI_REGULATORY', 'MACRO')",
            name="ck_news_events_event_type",
        ),
        sa.CheckConstraint(
            "scope_type IN ('STOCK', 'MULTI_STOCK', 'SECTOR', 'MARKET', 'MACRO')",
            name="ck_news_events_scope_type",
        ),
        sa.CheckConstraint(
            "deterministic_context IN ('POSITIVE_CONTEXT', 'NEGATIVE_CONTEXT', 'MIXED', 'NEUTRAL', 'UNKNOWN')",
            name="ck_news_events_context",
        ),
        sa.CheckConstraint(
            "event_severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_news_events_severity",
        ),
    )
    op.create_index("ix_news_events_stock_pub", "news_events", ["stock_id", "published_at"])
    op.create_index("ix_news_events_sector_pub", "news_events", ["sector_id", "published_at"])
    op.create_index("ix_news_events_pit", "news_events", ["available_at"])
    op.create_index("ix_news_events_event_type", "news_events", ["event_type"])
    op.create_index("ix_news_events_category", "news_events", ["category"])

    # 2. Create news_entity_links table
    op.create_table(
        "news_entity_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("news_event_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("sector_id", sa.Integer(), nullable=True),
        sa.Column("impact_relation", sa.String(length=32), nullable=False, server_default="DIRECT"),
        sa.Column("deterministic_context", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.ForeignKeyConstraint(["news_event_id"], ["news_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "impact_relation IN ('DIRECT', 'INDIRECT', 'UNKNOWN')",
            name="ck_news_entity_links_impact_relation",
        ),
        sa.CheckConstraint(
            "deterministic_context IN ('POSITIVE_CONTEXT', 'NEGATIVE_CONTEXT', 'MIXED', 'NEUTRAL', 'UNKNOWN')",
            name="ck_news_entity_links_context",
        ),
    )
    op.create_index("ix_news_entity_links_stock", "news_entity_links", ["stock_id"])
    op.create_index("ix_news_entity_links_sector", "news_entity_links", ["sector_id"])


def downgrade() -> None:
    op.drop_table("news_entity_links")
    op.drop_table("news_events")
