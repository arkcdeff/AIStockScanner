import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_test_database_is_the_test_db_and_has_14d_migration() -> None:
    """The isolated test database carries the G.11-OPT-14D provider-identity migration.

    This is the authoritative regression gate for the test-environment isolation
    bug where pytest's conftest set APP_ENV='testing' too late and the lru_cached
    Settings resolved the production sync database, so alembic implicitly upgraded
    the production database instead of the isolated test database.
    """
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        database_name = (
            await session.execute(text("SELECT current_database()"))
        ).scalar()
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))    ).scalar()
        assert "test" in str(database_name).lower()
        # The isolated test database must be at the current migration head; the
        # set widens as additive milestones are added (14E-A, 14E-B).
        assert version in {"d4e5f6a7b8c1", "e5f6a7b8c9d0", "e6f7a8b9c0d1"}
