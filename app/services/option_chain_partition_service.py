"""Option Chain Partition Maintenance Service — Milestone G.11-OPT-1.

Implements declarative monthly range partition provisioning, idempotent maintenance,
and on-demand historical partition creation for `option_chain_quotes`.
Follows SPEC-R2 §7 & Implementation Specification §34, §35, §36, §37.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services.option_chain_config import (
    DEFAULT_PARTITION_AHEAD_MONTHS,
    PARTITION_TABLE_PREFIX,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)


class PartitionMaintenanceError(RuntimeError):
    """Raised when quote table partition provisioning or inspection fails."""


def get_monthly_partition_bounds(target_date: date) -> tuple[date, date, str]:
    """Calculate half-open monthly boundaries [month_start, next_month_start) and partition name.

    Naming convention: option_chain_quotes_YYYY_MM
    Boundary: [YYYY-MM-01, next_month_01)
    """
    month_start = date(target_date.year, target_date.month, 1)
    if target_date.month == 12:
        next_month_start = date(target_date.year + 1, 1, 1)
    else:
        next_month_start = date(target_date.year, target_date.month + 1, 1)

    partition_name = f"{PARTITION_TABLE_PREFIX}_{target_date.year}_{target_date.month:02d}"
    return month_start, next_month_start, partition_name


def _add_months(d: date, n: int) -> date:
    """Add n calendar months to date d, returning the first day of that month."""
    total_months = d.year * 12 + (d.month - 1) + n
    year = total_months // 12
    month = total_months % 12 + 1
    return date(year, month, 1)


# ===========================================================================
# Async Partition Service & Helpers
# ===========================================================================


async def ensure_quote_partition_exists(
    trade_date: date,
    session: AsyncSession | None = None,
) -> str:
    """Ensure that the monthly range partition for trade_date exists.

    Idempotent and concurrent-safe using PostgreSQL transaction advisory locking.

    Args:
        trade_date: Trade date requiring quote table storage.
        session: Optional active AsyncSession. If None, acquires an isolated session.

    Returns:
        The partition table name (e.g. 'option_chain_quotes_2026_09').

    Raises:
        PartitionMaintenanceError: If partition creation fails.
    """
    month_start, next_month_start, partition_name = get_monthly_partition_bounds(trade_date)

    if session is not None:
        return await _ensure_partition_in_session(
            session=session,
            partition_name=partition_name,
            month_start=month_start,
            next_month_start=next_month_start,
        )

    factory = get_session_factory()
    async with factory() as auto_session:
        result = await _ensure_partition_in_session(
            session=auto_session,
            partition_name=partition_name,
            month_start=month_start,
            next_month_start=next_month_start,
        )
        await auto_session.commit()
        return result


async def _ensure_partition_in_session(
    session: AsyncSession,
    partition_name: str,
    month_start: date,
    next_month_start: date,
) -> str:
    """Internal helper to create a partition within a given session."""
    try:
        # Transactional advisory lock on partition maintenance namespace to eliminate race conditions
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('option_chain_quotes_partition_lock'))")
        )

        # Check if partition already exists in catalog
        check_stmt = text(
            """
            SELECT 1
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = :parent_name AND c.relname = :part_name
            """
        )
        res = await session.execute(
            check_stmt,
            {"parent_name": PARTITION_TABLE_PREFIX, "part_name": partition_name},
        )
        if res.scalar() is not None:
            logger.debug("Partition %s already exists; no-op", partition_name)
            return partition_name

        # Execute idempotent declarative range partition DDL
        ddl = text(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name}
            PARTITION OF {PARTITION_TABLE_PREFIX}
            FOR VALUES FROM ('{month_start.isoformat()}') TO ('{next_month_start.isoformat()}');
            """
        )
        await session.execute(ddl)
        logger.info("Successfully provisioned partition %s [%s, %s)", partition_name, month_start, next_month_start)
        return partition_name

    except Exception as exc:
        logger.error("Failed to provision partition %s: %s", partition_name, exc)
        raise PartitionMaintenanceError(
            f"Failed to ensure quote partition '{partition_name}' for [{month_start}, {next_month_start}): {exc}"
        ) from exc


async def maintain_quote_partitions(
    reference_date: date | None = None,
    ahead_months: int = DEFAULT_PARTITION_AHEAD_MONTHS,
    session: AsyncSession | None = None,
) -> list[str]:
    """Maintain quote partitions for current month + ahead_months.

    Default policy: current month + next 3 calendar months (total 4 months).
    """
    ref = reference_date or date.today()
    ensured: list[str] = []

    if session is not None:
        for offset in range(ahead_months + 1):
            target = _add_months(ref, offset)
            p_name = await ensure_quote_partition_exists(target, session=session)
            ensured.append(p_name)
        return ensured

    factory = get_session_factory()
    async with factory() as auto_session:
        for offset in range(ahead_months + 1):
            target = _add_months(ref, offset)
            p_name = await ensure_quote_partition_exists(target, session=auto_session)
            ensured.append(p_name)
        await auto_session.commit()
        return ensured


async def check_quote_partition_exists(
    trade_date: date,
    session: AsyncSession | None = None,
) -> bool:
    """Check if the partition for trade_date is present in the database."""
    _, _, partition_name = get_monthly_partition_bounds(trade_date)
    check_stmt = text(
        """
        SELECT 1
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = :parent_name AND c.relname = :part_name
        """
    )

    if session is not None:
        res = await session.execute(
            check_stmt,
            {"parent_name": PARTITION_TABLE_PREFIX, "part_name": partition_name},
        )
        return res.scalar() is not None

    factory = get_session_factory()
    async with factory() as auto_session:
        res = await auto_session.execute(
            check_stmt,
            {"parent_name": PARTITION_TABLE_PREFIX, "part_name": partition_name},
        )
        return res.scalar() is not None


async def list_quote_partitions(session: AsyncSession | None = None) -> list[str]:
    """List all currently provisioned child partitions of option_chain_quotes."""
    list_stmt = text(
        """
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = :parent_name
        ORDER BY c.relname ASC
        """
    )

    if session is not None:
        res = await session.execute(list_stmt, {"parent_name": PARTITION_TABLE_PREFIX})
        return [r[0] for r in res.fetchall()]

    factory = get_session_factory()
    async with factory() as auto_session:
        res = await auto_session.execute(list_stmt, {"parent_name": PARTITION_TABLE_PREFIX})
        return [r[0] for r in res.fetchall()]


# ===========================================================================
# Synchronous Equivalents (for CLI, cron scripts, and sync maintenance)
# ===========================================================================


def ensure_quote_partition_exists_sync(
    trade_date: date,
    connection: Connection | None = None,
    sync_engine: Engine | None = None,
) -> str:
    """Synchronous version of ensure_quote_partition_exists."""
    month_start, next_month_start, partition_name = get_monthly_partition_bounds(trade_date)

    def _execute(conn: Connection) -> str:
        try:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('option_chain_quotes_partition_lock'))")
            )
            res = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_inherits i
                    JOIN pg_class c ON c.oid = i.inhrelid
                    JOIN pg_class p ON p.oid = i.inhparent
                    WHERE p.relname = :parent_name AND c.relname = :part_name
                    """
                ),
                {"parent_name": PARTITION_TABLE_PREFIX, "part_name": partition_name},
            )
            if res.scalar() is not None:
                return partition_name

            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {partition_name}
                    PARTITION OF {PARTITION_TABLE_PREFIX}
                    FOR VALUES FROM ('{month_start.isoformat()}') TO ('{next_month_start.isoformat()}');
                    """
                )
            )
            return partition_name
        except Exception as exc:
            raise PartitionMaintenanceError(
                f"Failed to ensure sync quote partition '{partition_name}': {exc}"
            ) from exc

    if connection is not None:
        return _execute(connection)

    if sync_engine is not None:
        with sync_engine.begin() as conn:
            return _execute(conn)

    raise ValueError("Either connection or sync_engine must be provided for synchronous execution")


def maintain_quote_partitions_sync(
    reference_date: date | None = None,
    ahead_months: int = DEFAULT_PARTITION_AHEAD_MONTHS,
    connection: Connection | None = None,
    sync_engine: Engine | None = None,
) -> list[str]:
    """Synchronous version of maintain_quote_partitions."""
    ref = reference_date or date.today()

    def _execute(conn: Connection) -> list[str]:
        results = []
        for offset in range(ahead_months + 1):
            target = _add_months(ref, offset)
            results.append(ensure_quote_partition_exists_sync(target, connection=conn))
        return results

    if connection is not None:
        return _execute(connection)

    if sync_engine is not None:
        with sync_engine.begin() as conn:
            return _execute(conn)

    raise ValueError("Either connection or sync_engine must be provided for synchronous execution")
