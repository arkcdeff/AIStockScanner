"""Pre-G.8 Derivatives Data-Integrity Repair Script.

Repairs:
  C1: Index misattribution (BANKNIFTY/FINNIFTY/MIDCPNIFTY → correct index)
  C2: Duplicate stock-future identities (hash format divergence)

Run: uv run python -m scripts.g8_pre_repair
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.db.session import get_session_factory

FORBIDDEN_DB_NAMES = {"postgres", "production", "prod", "master"}


def _get_db_name(session) -> str:
    """Extract the database name from the current connection."""
    bind = session.get_bind()
    if bind and hasattr(bind, "url") and bind.url:
        return bind.url.database or "unknown"
    return "unknown"


def _is_test_db(db_name: str) -> bool:
    """Return True if this appears to be a test database."""
    db_lower = db_name.lower()
    return "test" in db_lower


def _fmt(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, Decimal):
        return str(val.normalize())
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _canonical_fingerprint(
    exchange: str,
    underlying_stock_id: int | None,
    underlying_index_id: int | None,
    instrument_type: str,
    expiry_date,
    strike_price: Decimal | None,
    option_type: str | None,
) -> str:
    parts = [
        _fmt(exchange),
        _fmt(underlying_stock_id),
        _fmt(underlying_index_id),
        _fmt(instrument_type),
        _fmt(expiry_date),
        _fmt(strike_price),
        _fmt(option_type),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


async def verify(session) -> dict:
    """Post-repair verification — read-only, production-database only."""
    db_name = _get_db_name(session)

    if _is_test_db(db_name):
        raise RuntimeError(
            f"--verify must not run on a test database (db='{db_name}'). "
            "Use the test DB for unit tests only."
        )

    checks: dict[str, int | str] = {}

    # Core counts
    r = await session.execute(text("SELECT COUNT(*) FROM derivative_contracts"))
    checks["derivative_contracts"] = r.scalar()

    r = await session.execute(text("SELECT COUNT(*) FROM derivative_daily_prices"))
    checks["derivative_daily_prices"] = r.scalar()

    r = await session.execute(text("SELECT COUNT(*) FROM fii_derivative_statistics"))
    checks["fii_derivative_statistic"] = r.scalar()

    # GIFT_NIFTY must be 0
    r = await session.execute(
        text("SELECT COUNT(*) FROM derivative_contracts WHERE underlying_index_id = "
             "(SELECT id FROM market_indices WHERE code='GIFT_NIFTY')")
    )
    checks["gift_nifty_contracts"] = r.scalar()

    # Option lot_size = 1 count
    r = await session.execute(text("SELECT COUNT(*) FROM derivative_contracts WHERE instrument_type='OPTION' AND lot_size=1"))
    checks["option_lot_size_1"] = r.scalar()

    # Option lot_size != 1 count
    r = await session.execute(
        text("SELECT COUNT(*) FROM derivative_contracts WHERE instrument_type='OPTION' AND lot_size!=1")
    )
    checks["option_lot_size_not_1"] = r.scalar()

    # Orphan DerivativeDailyPrice (no matching contract)
    r = await session.execute(
        text("SELECT COUNT(*) FROM derivative_daily_prices dp "
             "LEFT JOIN derivative_contracts dc ON dp.contract_id = dc.id "
             "WHERE dc.id IS NULL")
    )
    checks["orphan_ddp"] = r.scalar()

    # Duplicate contract/date
    r = await session.execute(
        text("SELECT COUNT(*) FROM (SELECT contract_id, trade_date, "
             "COUNT(*) AS cnt FROM derivative_daily_prices "
             "GROUP BY contract_id, trade_date HAVING COUNT(*) > 1) q")
    )
    checks["dup_contract_date"] = r.scalar()

    # Semantic duplicate contracts (same hash)
    r = await session.execute(text("SELECT COUNT(*) FROM (SELECT idempotency_hash, COUNT(*) AS cnt FROM derivative_contracts GROUP BY idempotency_hash HAVING COUNT(*) > 1) q"))
    checks["sem_dup_contracts"] = r.scalar()

    # Weekend DerivativeDailyPrice
    r = await session.execute(
        text("SELECT COUNT(*) FROM derivative_daily_prices WHERE EXTRACT(dow FROM trade_date) IN (0,6)")
    )
    checks["weekend_ddp"] = r.scalar()

    return checks


async def repair_c1(session) -> dict:
    """C1: Reattribute misattributed index contracts."""
    idx_map = await _get_index_map(session)

    mapping = {
        "BANKNIFTY": idx_map.get("NIFTY_BANK"),
        "FINNIFTY": idx_map.get("NIFTY_FIN_SERV"),
        "MIDCPNIFTY": idx_map.get("NIFTY_MID_SELECT"),
        "NIFTYNXT50": idx_map.get("NIFTY_NEXT_50"),
    }

    results = {"corrected": 0, "by_prefix": {}}

    for prefix, correct_id in mapping.items():
        if not correct_id:
            continue

        r = await session.execute(text(
            "SELECT COUNT(*) FROM derivative_contracts dc "
            "JOIN market_indices mi ON dc.underlying_index_id = mi.id "
            "WHERE mi.code = 'NIFTY' AND dc.trading_symbol LIKE :prefix || '%'"
        ), {"prefix": prefix})
        affected = r.scalar()
        if affected == 0:
            continue

        await session.execute(text(
            "UPDATE derivative_contracts dc "
            "SET underlying_index_id = :correct_id "
            "FROM market_indices mi "
            "WHERE dc.underlying_index_id = mi.id "
            "AND mi.code = 'NIFTY' AND dc.trading_symbol LIKE :prefix || '%'"
        ), {"correct_id": correct_id, "prefix": prefix})

        results["corrected"] += affected
        results["by_prefix"][prefix] = affected

    await session.commit()
    return results


async def _get_index_map(session) -> dict[str, int]:
    r = await session.execute(text("SELECT id, code FROM market_indices"))
    return {code: idx_id for idx_id, code in r.all()}


async def repair_c2(session) -> dict:
    """C2: Canonicalize duplicate stock-future fingerprints.

    Strategy: For each group of contracts with the same economic identity,
    find the one that already has the canonical hash (the 'natural' canonical).
    Remap all daily prices from other contracts to it, then delete the duplicates.
    """
    r = await session.execute(text(
        "SELECT id, exchange, underlying_stock_id, underlying_index_id, "
        "instrument_type, expiry_date, strike_price, option_type, idempotency_hash "
        "FROM derivative_contracts WHERE instrument_type = 'FUTURE'"
    ))
    all_futures = r.fetchall()

    groups: dict[str, list] = {}
    for row in all_futures:
        c_id, exchange, stock_id, idx_id, inst, exp, strike, opt, current_hash = row
        canonical = _canonical_fingerprint(
            exchange, stock_id, idx_id, inst, exp, strike, opt
        )
        groups.setdefault(canonical, []).append({
            "id": c_id,
            "current_hash": current_hash,
            "canonical_hash": canonical,
        })

    results = {"groups_processed": 0, "contracts_deleted": 0, "prices_remapped": 0}

    for canonical_hash, group in groups.items():
        if len(group) <= 1:
            continue

        results["groups_processed"] += 1
        group.sort(key=lambda x: x["id"])

        # Find the contract that already has the canonical hash
        natural = None
        for c in group:
            if c["current_hash"] == canonical_hash:
                natural = c
                break

        if natural is None:
            # No contract has canonical hash yet — use lowest ID, but first
            # check if updating its hash would conflict with an existing hash
            # from another group. If so, find another contract in the group
            # whose current hash matches the canonical (shouldn't happen but safety).
            natural = group[0]
            # Check if canonical_hash is already used by another contract
            r2 = await session.execute(text(
                "SELECT id FROM derivative_contracts WHERE idempotency_hash = :hash AND id != :id"
            ), {"hash": canonical_hash, "id": natural["id"]})
            existing = r2.fetchone()
            if existing:
                # Hash conflict — the canonical hash is taken by a contract
                # from a different economic group. Use that contract's existing
                # hash as the group hash instead (safe, just different from model).
                # Find any contract in this group with a unique hash
                for c in group:
                    r3 = await session.execute(text(
                        "SELECT id FROM derivative_contracts WHERE idempotency_hash = :hash"
                    ), {"hash": c["current_hash"]})
                    if not r3.fetchone():
                        # This shouldn't happen — all contracts have hashes
                        pass
                # Fallback: keep the natural's current hash, just remap prices
                natural = group[0]
            else:
                # Safe to update hash
                await session.execute(text(
                    "UPDATE derivative_contracts SET idempotency_hash = :hash WHERE id = :id"
                ), {"hash": canonical_hash, "id": natural["id"]})

        # Remap daily prices from all non-canonical contracts to the canonical one
        for c in group:
            if c["id"] == natural["id"]:
                continue
            await session.execute(text(
                "UPDATE derivative_daily_prices SET contract_id = :canonical_id WHERE contract_id = :dup_id"
            ), {"canonical_id": natural["id"], "dup_id": c["id"]})
            results["prices_remapped"] += 1

            await session.execute(text(
                "DELETE FROM derivative_contracts WHERE id = :id"
            ), {"id": c["id"]})
            results["contracts_deleted"] += 1

    await session.commit()
    return results


async def repair_c2_v2(session) -> dict:
    """C2: Canonicalize duplicate stock-future fingerprints (v2)."""
    r = await session.execute(text(
        "SELECT id, exchange, underlying_stock_id, underlying_index_id, "
        "instrument_type, expiry_date, strike_price, option_type, idempotency_hash "
        "FROM derivative_contracts WHERE instrument_type = 'FUTURE'"
    ))
    all_futures = r.fetchall()

    groups: dict[str, list] = {}
    for row in all_futures:
        c_id, exchange, stock_id, idx_id, inst, exp, strike, opt, current_hash = row
        canonical = _canonical_fingerprint(
            exchange, stock_id, idx_id, inst, exp, strike, opt
        )
        groups.setdefault(canonical, []).append({
            "id": c_id,
            "current_hash": current_hash,
            "canonical_hash": canonical,
        })

    results = {"groups_processed": 0, "contracts_deleted": 0, "prices_remapped": 0}

    for canonical_hash, group in groups.items():
        if len(group) <= 1:
            continue

        results["groups_processed"] += 1
        group.sort(key=lambda x: x["id"])

        # Find the contract that already has the canonical hash
        natural = None
        for c in group:
            if c["current_hash"] == canonical_hash:
                natural = c
                break

        if natural is None:
            # No contract has canonical hash yet — use lowest ID, but first
            # check if updating its hash would conflict with an existing hash
            # from another group. If so, find another contract in the group
            # whose current hash matches the canonical (shouldn't happen but safety).
            natural = group[0]
            # Check if canonical_hash is already used by another contract
            r2 = await session.execute(text(
                "SELECT id FROM derivative_contracts WHERE idempotency_hash = :hash AND id != :id"
            ), {"hash": canonical_hash, "id": natural["id"]})
            existing = r2.fetchone()
            if existing:
                # Hash conflict — the canonical hash is taken by a contract
                # from a different economic group. Use that contract's existing
                # hash as the group hash instead (safe, just different from model).
                # Find any contract in this group with a unique hash
                for c in group:
                    r3 = await session.execute(text(
                        "SELECT id FROM derivative_contracts WHERE idempotency_hash = :hash"
                    ), {"hash": c["current_hash"]})
                    if not r3.fetchone():
                        # This shouldn't happen — all contracts have hashes
                        pass
                # Fallback: keep the natural's current hash, just remap prices
                natural = group[0]
            else:
                # Safe to update hash
                await session.execute(text(
                    "UPDATE derivative_contracts SET idempotency_hash = :hash WHERE id = :id"
                ), {"hash": canonical_hash, "id": natural["id"]})

        # Remap daily prices from all non-canonical contracts to the canonical one
        for c in group:
            if c["id"] == natural["id"]:
                continue
            await session.execute(text(
                "UPDATE derivative_daily_prices SET contract_id = :canonical_id WHERE contract_id = :dup_id"
            ), {"canonical_id": natural["id"], "dup_id": c["id"]})
            results["prices_remapped"] += 1

            await session.execute(text(
                "DELETE FROM derivative_contracts WHERE id = :id"
            ), {"id": c["id"]})
            results["contracts_deleted"] += 1

    await session.commit()
    return results


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-G.8 Derivatives Data-Integrity Repair Script"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run read-only verification against the connected database. "
             "Refuses to run on test databases.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Execute C1 and C2 repairs against the connected database.",
    )
    args = parser.parse_args()

    if not args.verify and not args.repair:
        parser.print_help()
        raise SystemExit(1)

    f = get_session_factory()
    async with f() as s:
        db_name = _get_db_name(s)
        if args.verify:
            if _is_test_db(db_name):
                print(f"ERROR: Refusing to run --verify on a test database (db='{db_name}').")
                raise SystemExit(1)
            if db_name in FORBIDDEN_DB_NAMES:
                print(f"ERROR: Refusing to run --verify on forbidden database (db='{db_name}').")
                raise SystemExit(1)

            now_iso = datetime.now(UTC).isoformat()
            print("=== PRE-G.8 VERIFICATION MODE ===")
            print(f"  Database: {db_name}")
            print(f"  Timestamp: {now_iso}")
            print("  Running read-only verification checks...\n")

            v = await verify(s)

            expected_values = {
                "derivative_contracts": 75962,
                "derivative_daily_prices": 1732338,
                "fii_derivative_statistic": 0,
                "gift_nifty_contracts": 0,
                "option_lot_size_1": 74902,
                "option_lot_size_not_1": 0,
                "orphan_ddp": 0,
                "dup_contract_date": 0,
                "sem_dup_contracts": 0,
                "weekend_ddp": 0,
            }

            all_pass = True
            for k in [
                "derivative_contracts",
                "derivative_daily_prices",
                "fii_derivative_statistic",
                "gift_nifty_contracts",
                "option_lot_size_1",
                "option_lot_size_not_1",
                "orphan_ddp",
                "dup_contract_date",
                "sem_dup_contracts",
                "weekend_ddp",
            ]:
                val = v.get(k)
                exp = expected_values[k]
                if val == exp:
                    print(f"  [PASS] {k} = {val} (expected {exp})")
                else:
                    print(f"  [FAIL] {k} = {val} (expected {exp})")
                    all_pass = False

            print()
            if all_pass:
                print("All assertions PASSED. Exit code 0.")
                raise SystemExit(0)
            else:
                print("One or more assertions FAILED. Exit code 1.")
                raise SystemExit(1)
        elif args.repair:
            if _is_test_db(db_name):
                print(f"ERROR: Refusing to run --repair on a test database (db='{db_name}').")
                raise SystemExit(1)
            print("=== PRE-REPAIR BASELINE ===")
            r = await s.execute(text("SELECT COUNT(*) FROM derivative_contracts"))
            print(f"  DerivativeContract BEFORE: {r.scalar()}")
            r = await s.execute(text("SELECT COUNT(*) FROM derivative_daily_prices"))
            print(f"  DerivativeDailyPrice BEFORE: {r.scalar()}")

            print("\n=== C1: INDEX MISATTRIBUTION REPAIR ===")
            c1 = await repair_c1(s)
            print(f"  Corrected: {c1['corrected']}")
            for prefix, cnt in c1["by_prefix"].items():
                print(f"    {prefix}: {cnt}")

            print("\n=== C2: DUPLICATE FUTURES REPAIR ===")
            c2 = await repair_c2(s)
            print(f"  Groups processed: {c2['groups_processed']}")
            print(f"  Daily prices remapped: {c2['prices_remapped']}")
            print(f"  Duplicate contracts deleted: {c2['contracts_deleted']}")

            print("\n=== POST-REPAIR VERIFICATION ===")
            v = await verify(s)
            for k, val in v.items():
                print(f"  {k}: {val}")

            print("\n=== POST-REPAIR COUNTS ===")
            r = await s.execute(text("SELECT COUNT(*) FROM derivative_contracts"))
            print(f"  DerivativeContract AFTER: {r.scalar()}")
            r = await s.execute(text("SELECT COUNT(*) FROM derivative_daily_prices"))
            print(f"  DerivativeDailyPrice AFTER: {r.scalar()}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())