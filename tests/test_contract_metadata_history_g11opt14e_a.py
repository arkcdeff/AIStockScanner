"""
G.11-OPT-14E-A — Metadata History Foundation focused tests.

Deterministic, fixture-only tests for the new per-contract metadata-history
table and repository. No live provider calls, no production touches, no
observation mutation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.db.session import get_session_factory
from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeContractMetadataHistory,
)
from app.models.market_index import MarketIndex
from app.repositories.derivative import DerivativeContractRepository

AS_OF = date(2026, 10, 7)
EXPIRY = date(2026, 10, 19)

FIND_MIGRATION = "d4e5f6a7b8c1_add_derivative_contract_metadata_history"


@pytest.fixture
def session_factory():
    yield get_session_factory()


@pytest.fixture
async def nifty_index():
    factory = get_session_factory()
    async with factory() as s:
        r = (
            await s.execute(
                select(MarketIndex.id).where(MarketIndex.code == "NIFTY")
            )
        ).scalars().first()
        if r is None:
            from app.models.market_index import MarketIndex as MI
            mi = MI(code="NIFTY", country="INDIA", name="Nifty 50")
            s.add(mi)
            await s.flush()
            await s.commit()
            r = mi.id
        else:
            await s.commit()
        return r


@pytest.fixture
async def contract(nifty_index):
    factory = get_session_factory()
    async with factory() as s:
        c = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=nifty_index,
            trading_symbol="NIFTY25OCT26CE30000",
            source_contract_id="NSE_FO|47035",
            expiry_date=EXPIRY,
            strike_price=Decimal("30000"),
            option_type="CE",
            lot_size=65,
            tick_size=Decimal("5.0000"),
            lifecycle_status="ACTIVE",
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            fetched_at=date.today(),
        )
        s.add(c)
        await s.flush()
        await s.commit()
        return c.id


@pytest.fixture
async def history_repo(session_factory):
    async with session_factory() as session:
        yield DerivativeContractRepository(session)


class TestMigrationSchemaAvailable:
    async def test_migration_module_exists(self):
        import pathlib
        import sys
        from importlib import import_module
        pkg_dir = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "versions"
        if str(pkg_dir) not in sys.path:
            sys.path.insert(0, str(pkg_dir))
        mod = import_module(FIND_MIGRATION)
        assert mod.down_revision == "d5e6f7a8b9c0"
        assert mod.revision == "d4e5f6a7b8c1"
        assert hasattr(mod, "upgrade")
        assert hasattr(mod, "downgrade")

    async def test_metadata_table_exists(self, session_factory):
        async with session_factory() as s:
            r = (
                await s.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public' "
                        "AND table_name ILIKE :t"
                    ),
                    {"t": "derivative_contract_metadata_histories"},
                )
            ).scalar()
            assert int(r or 0) == 1

    async def test_metadata_columns(self, session_factory):
        async with session_factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name ILIKE :t "
                        "ORDER BY ordinal_position"
                    ),
                    {"t": "derivative_contract_metadata_histories"},
                )
            ).fetchall()
            cols = {row[0] for row in rows}
            assert cols
            expected = {
                "lot_size",
                "tick_size",
                "freeze_quantity",
                "minimum_lot",
                "is_weekly",
                "effective_from",
                "effective_to",
                "available_at",
                "source_name",
                "source_reference",
                "provider_snapshot_hash",
            }
            missing = expected - cols
            assert not missing, f"missing columns: {sorted(missing)}; present: {sorted(cols)}"

    async def test_legacy_tables_unchanged(self, session_factory):
        async with session_factory() as s:
            for table in ("derivative_contracts", "derivative_lot_size_histories"):
                r = (
                    await s.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = 'public' "
                            "AND table_name ILIKE :t"
                        ),
                        {"t": table},
                    )
                ).scalar()
                assert int(r or 0) == 1


class TestMetadataInsertVersioning:
    async def test_append_metadata_version(self, history_repo, contract):
        repo = history_repo
        fact = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:abc",
        )
        assert fact.contract_id == contract
        assert fact.lot_size == 65
        assert fact.is_weekly is True

    async def test_multiple_versions_intact(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v2",
        )
        assert v1.lot_size == 65
        assert v2.lot_size == 75
        rows = (
            await repo.session.execute(
                select(DerivativeContractMetadataHistory).where(
                    DerivativeContractMetadataHistory.contract_id == contract
                ).order_by(
                    DerivativeContractMetadataHistory.effective_from.asc()
                )
            )
        ).scalars().all()
        assert [r.lot_size for r in rows] == [65, 75]


class TestEffectiveDatedLookup:
    async def test_as_of_returns_correct_version(self, history_repo, contract):
        repo = history_repo
        await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v1",
        )
        await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v2",
        )

        first = await repo.get_metadata_as_of(contract_id=contract, as_of=date(2026, 9, 15))
        assert first is not None
        assert first.lot_size == 65

        later = await repo.get_metadata_as_of(contract_id=contract, as_of=date(2026, 10, 15))
        assert later is not None
        assert later.lot_size == 75

    async def test_latest_returns_current_version(self, history_repo, contract):
        repo = history_repo
        await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v1",
        )
        await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v2",
        )

        fresh = await repo.get_latest_metadata(contract_id=contract)
        assert fresh is not None
        assert fresh.lot_size == 75
        assert fresh.effective_from == date(2026, 10, 1)

    async def test_equal_effective_from_uses_greater_id_for_latest_and_as_of(
        self, history_repo, contract
    ):
        """Tied effective dates must resolve deterministically by id DESC."""
        repo = history_repo
        effective_from = date(2026, 10, 1)
        first = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=effective_from,
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="tie-breaker:first",
            provider_snapshot_hash="sha256:tie-first",
        )
        second = await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=effective_from,
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="tie-breaker:second",
            provider_snapshot_hash="sha256:tie-second",
        )
        assert second.id > first.id

        latest = await repo.get_latest_metadata(contract_id=contract)
        as_of = await repo.get_metadata_as_of(
            contract_id=contract, as_of=date(2026, 10, 15)
        )
        assert latest is not None and latest.id == second.id
        assert as_of is not None and as_of.id == second.id


class TestLotSizeVersioning:
    async def test_lot_size_65_to_75_kept_separate(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v2",
        )
        assert v1.lot_size == 65
        assert v2.lot_size == 75
        assert v1.effective_to is not None
        assert v2.effective_to is None


class TestOtherMutableFields:
    async def test_tick_size_change(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 7),
            effective_to=None,
            available_at=date(2026, 10, 7),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:t1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("10.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 10),
            effective_to=None,
            available_at=date(2026, 10, 10),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:t2",
        )
        assert v1.tick_size == Decimal("5.0000")
        assert v2.tick_size == Decimal("10.0000")

    async def test_freeze_quantity_change(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 7),
            effective_to=None,
            available_at=date(2026, 10, 7),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:f1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 10),
            effective_to=None,
            available_at=date(2026, 10, 10),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:f2",
        )
        assert v1.freeze_quantity == 3510
        assert v2.freeze_quantity == 3900

    async def test_minimum_lot_change(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 7),
            effective_to=None,
            available_at=date(2026, 10, 7),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:m1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=True,
            effective_from=date(2026, 10, 10),
            effective_to=None,
            available_at=date(2026, 10, 10),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:m2",
        )
        assert v1.minimum_lot == 65
        assert v2.minimum_lot == 75

    async def test_is_weekly_change(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 7),
            effective_to=None,
            available_at=date(2026, 10, 7),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:w1",
        )
        v2 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=65,
            is_weekly=False,
            effective_from=date(2026, 10, 10),
            effective_to=None,
            available_at=date(2026, 10, 10),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:w2",
        )
        assert v1.is_weekly is True
        assert v2.is_weekly is False


class TestHistoricalImmutability:
    async def test_version_stays_unchanged(self, history_repo, contract):
        repo = history_repo
        v1 = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v1",
        )
        original_row = (
            await repo.session.execute(
                select(DerivativeContractMetadataHistory).where(
                    DerivativeContractMetadataHistory.id == v1.id
                )
            )
        ).scalar_one()

        await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:v2",
        )

        new_row = (
            await repo.session.execute(
                select(DerivativeContractMetadataHistory).where(
                    DerivativeContractMetadataHistory.id == v1.id
                )
            )
        ).scalar_one()

        assert new_row.lot_size == original_row.lot_size == 65
        assert new_row.effective_from == original_row.effective_from
        assert new_row.effective_to == original_row.effective_to


class TestContractImmutability:
    async def test_contract_lot_size_unchanged(self, history_repo, contract):
        repo = history_repo
        await repo.add_metadata(
            contract_id=contract,
            lot_size=75,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3900,
            minimum_lot=75,
            is_weekly=False,
            effective_from=date(2026, 10, 1),
            effective_to=None,
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:x",
        )

        stored = (
            await repo.session.execute(
                select(DerivativeContract.lot_size).where(
                    DerivativeContract.id == contract
                )
            )
        ).scalars().first()

        assert stored == 65


class TestFingerprintIdentity:
    async def test_metadata_does_not_create_new_identity(self, history_repo, contract):
        repo = history_repo
        original_contract = (
            await repo.session.execute(
                select(DerivativeContract).where(
                    DerivativeContract.id == contract
                )
            )
        ).scalar_one()

        await repo.add_metadata(
            contract_id=contract,
            lot_size=99,
            tick_size=Decimal("5.0000"),
            freeze_quantity=9999,
            minimum_lot=99,
            is_weekly=False,
            effective_from=date(2026, 10, 10),
            effective_to=None,
            available_at=date(2026, 10, 10),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:new",
        )

        same = (
            await repo.session.execute(
                select(DerivativeContract).where(
                    DerivativeContract.id == contract
                )
            )
        ).scalar_one()

        assert same.id == contract
        assert same.idempotency_hash == original_contract.idempotency_hash
        assert same.compute_fingerprint() == original_contract.compute_fingerprint()
        assert same.lot_size == original_contract.lot_size


class TestProvenance:
    async def test_provenance_fields_stored(self, history_repo, contract):
        repo = history_repo
        fact = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 10, 7),
            effective_to=date(2026, 10, 7),
            available_at=date(2026, 10, 7),
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:prov",
        )
        assert fact.source_name == "UPSTOX_OPTION_CONTRACT_MASTER"
        assert fact.source_reference == "GET /option/contract"
        assert fact.provider_snapshot_hash == "sha256:prov"


class TestDuplicateIdempotentMetadataFact:
    async def test_identical_metadata_fact_not_duplicated(self, history_repo, contract):
        repo = history_repo
        first = await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:dup",
        )
        again = await repo.find_metadata_fact(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
        )
        assert again is not None
        assert again.id == first.id

    async def test_identical_metadata_treated_as_duplicate(self, history_repo, contract):
        repo = history_repo
        await repo.add_metadata(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            available_at=AS_OF,
            source_name="UPSTOX_OPTION_CONTRACT_MASTER",
            source_reference="GET /option/contract",
            provider_snapshot_hash="sha256:e1",
        )
        exists = await repo.metadata_fact_exists(
            contract_id=contract,
            lot_size=65,
            tick_size=Decimal("5.0000"),
            freeze_quantity=3510,
            minimum_lot=65,
            is_weekly=True,
            effective_from=date(2026, 9, 1),
        )
        assert exists is True
