"""G.11-OPT-14C — MarketIndex reconciliation tests.

Requirements: one authoritative NIFTY MarketIndex that the derivative pipeline
already resolves, a BANKNIFTY MarketIndex that resolves through the same path, no
duplicate rows, no destroyed historical references, and an idempotent seed.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.market_intelligence.index_catalog import seed_market_index_catalog
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.services.option_chain_persistence_service import OptionChainPersistenceService

MIGRATION_PATH = Path(
    "migrations/versions/c4d5e6f7a8b9_add_banknifty_market_index.py"
)


async def _add_index(
    session,
    code: str,
    name: str,
    provider_symbol: str | None = None,
) -> MarketIndex:
    index = MarketIndex(
        code=code,
        name=name,
        country="INDIA",
        exchange="NSE",
        category="indian_index",
        currency="INR",
        timezone="Asia/Kolkata",
        provider_symbol=provider_symbol,
        is_active=True,
    )
    session.add(index)
    await session.commit()
    await session.refresh(index)
    return index


def _factory():
    from app.db.session import get_session_factory

    return get_session_factory()


async def _resolve(underlying: str) -> MarketIndex:
    factory = _factory()
    async with factory() as session:
        service = OptionChainPersistenceService()
        return await service._resolve_underlying_index(session, None, underlying)


async def _codes() -> dict[str, int]:
    factory = _factory()
    async with factory() as session:
        rows = (await session.execute(select(MarketIndex.code))).scalars().all()
    return {code: rows.count(code) for code in set(rows)}


async def _seed_twice() -> None:
    factory = _factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        await session.commit()
    async with factory() as session:
        await seed_market_index_catalog(session)
        await session.commit()


# ===========================================================================
# A / B / E / F / G — resolution
# ===========================================================================

@pytest.mark.asyncio
async def test_a_and_e_canonical_nifty_resolves() -> None:
    factory = _factory()
    async with factory() as session:
        created = await _add_index(session, "NIFTY", "Nifty 50")

    resolved = await _resolve("NIFTY")

    assert resolved.id == created.id
    assert resolved.code == "NIFTY"


@pytest.mark.asyncio
async def test_b_and_f_banknifty_resolves() -> None:
    factory = _factory()
    async with factory() as session:
        created = await _add_index(session, "BANKNIFTY", "Nifty Bank", "BANKNIFTY")

    resolved = await _resolve("BANKNIFTY")

    assert resolved.id == created.id
    assert resolved.code == "BANKNIFTY"


@pytest.mark.asyncio
async def test_g_both_indices_resolve_independently() -> None:
    factory = _factory()
    async with factory() as session:
        nifty = await _add_index(session, "NIFTY", "Nifty 50")
        bank = await _add_index(session, "BANKNIFTY", "Nifty Bank", "BANKNIFTY")

    assert (await _resolve("NIFTY")).id == nifty.id
    assert (await _resolve("BANKNIFTY")).id == bank.id
    assert nifty.id != bank.id


@pytest.mark.asyncio
async def test_banknifty_resolves_by_provider_symbol_only() -> None:
    """Resolution also works when only the provider symbol carries the identity."""
    factory = _factory()
    async with factory() as session:
        created = await _add_index(session, "NIFTY_BANK", "Nifty Bank", "BANKNIFTY")

    assert (await _resolve("BANKNIFTY")).id == created.id


# ===========================================================================
# C / D / J / K — no duplicates, idempotent seed, aliases reconcile
# ===========================================================================

@pytest.mark.asyncio
async def test_c_d_j_k_seed_is_idempotent_and_alias_safe() -> None:
    factory = _factory()
    async with factory() as session:
        await _add_index(session, "NIFTY", "Nifty 50")
        await _add_index(session, "BANKNIFTY", "Nifty Bank", "BANKNIFTY")

    await _seed_twice()
    before = await _codes()
    await _seed_twice()
    after = await _codes()

    assert before == after, "repeated catalog seeding must not change row identity"

    # K: the catalog's own codes must not spawn a second identity.
    assert "NIFTY_50" not in after
    assert "NIFTY_BANK" not in after
    assert after.get("NIFTY", 0) == 1
    assert after.get("BANKNIFTY", 0) == 1


@pytest.mark.asyncio
async def test_k_alias_row_keeps_its_authoritative_code() -> None:
    """Seeding reconciles onto the existing short-code rows, not the catalog codes."""
    factory = _factory()
    async with factory() as session:
        nifty = await _add_index(session, "NIFTY", "Nifty 50")
        bank = await _add_index(session, "BANKNIFTY", "Nifty Bank", "BANKNIFTY")

    await _seed_twice()

    async with factory() as session:
        codes = set((await session.execute(select(MarketIndex.code))).scalars().all())
        nifty_row = await session.get(MarketIndex, nifty.id)
        bank_row = await session.get(MarketIndex, bank.id)

    assert "NIFTY" in codes and "NIFTY_50" not in codes
    assert "BANKNIFTY" in codes and "NIFTY_BANK" not in codes
    assert nifty_row is not None and nifty_row.code == "NIFTY"
    assert bank_row is not None and bank_row.code == "BANKNIFTY"


# ===========================================================================
# H / I — historical references preserved, contracts untouched
# ===========================================================================

@pytest.mark.asyncio
async def test_h_i_historical_references_and_contracts_survive_seeding() -> None:
    factory = _factory()
    async with factory() as session:
        nifty = await _add_index(session, "NIFTY", "Nifty 50")
        contract = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=nifty.id,
            trading_symbol="NIFTY08OCT2624500CE",
            expiry_date=date(2026, 10, 8),
            strike_price=Decimal("24500"),
            option_type="CE",
            lot_size=65,
            available_at=date(2026, 10, 6),
            source_name="NSE",
        )
        session.add(contract)
        await session.commit()
        await session.refresh(contract)
        contract_id, contract_hash = contract.id, contract.idempotency_hash

    await _seed_twice()

    async with factory() as session:
        stored = await session.get(DerivativeContract, contract_id)
        count = (
            await session.execute(text("SELECT COUNT(*) FROM derivative_contracts"))
        ).scalar()
        nifty_row = await session.get(MarketIndex, nifty.id)

    assert count == 1, "seeding must not add or remove derivative contracts"
    assert stored is not None
    assert stored.idempotency_hash == contract_hash
    assert stored.underlying_index_id == nifty.id
    assert nifty_row is not None, "the referenced NIFTY row must survive seeding"


@pytest.mark.asyncio
async def test_seeding_does_not_delete_existing_rows() -> None:
    factory = _factory()
    async with factory() as session:
        await _add_index(session, "CUSTOM_INDEX", "Custom Index")

    await _seed_twice()

    assert "CUSTOM_INDEX" in await _codes()


# ===========================================================================
# Migration guard — data-only and idempotent by construction
# ===========================================================================

def test_migration_is_data_only_and_guarded() -> None:
    spec = importlib.util.spec_from_file_location("mig_14c", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "b3a4c5d6e7f8"
    assert callable(module.upgrade) and callable(module.downgrade)

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for schema_op in ("op.create_table", "op.add_column", "op.alter_column", "op.drop_column"):
        assert schema_op not in source, f"{schema_op} would make this migration schema-changing"

    # Idempotent by construction: guarded insert, never a blind one.
    assert "NOT EXISTS" in module._INSERT_BANKNIFTY
    assert "BANKNIFTY" in module._INSERT_BANKNIFTY
    assert "NIFTY_BANK" not in module._INSERT_BANKNIFTY
