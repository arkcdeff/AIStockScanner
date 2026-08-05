from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text

from app.db.session import get_session_factory
from app.models.auth import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserPreference,
    UserRole,
    UserSession,
)
from app.repositories.auth import (
    AuditLogRepository,
    PermissionRepository,
    RolePermissionRepository,
    RoleRepository,
    UserRepository,
    UserRoleRepository,
    UserSessionRepository,
)


def _user(*, username: str = "alice", email: str = "alice@example.com") -> User:
    return User(
        username=username,
        email=email,
        full_name="Alice Example",
        password_hash="stored-hash-only",
    )


@pytest.mark.asyncio
async def test_user_insert_defaults_and_repository_lookup() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        await UserRepository(session).add(user)

        assert user.id is not None
        assert user.is_active is True
        assert user.is_verified is False
        assert user.is_superuser is False
        assert user.last_login_at is None
        assert user.created_at is not None
        assert user.updated_at is not None

        repository = UserRepository(session)
        assert await repository.get_by_username("alice") is user
        assert await repository.get_by_email("alice@example.com") is user
        assert await repository.list_active() == [user]
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_field", ["username", "email"])
async def test_user_identity_fields_are_unique(duplicate_field: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        first = _user()
        second = _user(username="bob", email="bob@example.com")
        if duplicate_field == "username":
            second.username = first.username
        else:
            second.email = first.email
        session.add_all([first, second])

        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_required_user_identity_values_cannot_be_blank() -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(_user(username=""))
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_user_role_permission_relationships_and_repositories() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        role = Role(name="analyst", description="Research analyst")
        permission = Permission(code="stocks.read", description="Read stock data")
        session.add_all([user, role, permission])
        await session.flush()

        user_role = await UserRoleRepository(session).add(
            UserRole(user_id=user.id, role_id=role.id)
        )
        role_permission = await RolePermissionRepository(session).add(
            RolePermission(role_id=role.id, permission_id=permission.id)
        )

        assert user_role.user is user
        assert user_role.role is role
        assert role_permission.role is role
        assert role_permission.permission is permission
        assert await UserRoleRepository(session).get(user.id, role.id) is user_role
        assert (
            await RolePermissionRepository(session).get(role.id, permission.id) is role_permission
        )

        loaded_user = await UserRepository(session).get_with_access(user.id)
        loaded_role = await RoleRepository(session).get_with_permissions(role.id)
        assert loaded_user is not None
        assert [item.name for item in loaded_user.roles] == ["analyst"]
        assert [item.code for item in loaded_user.roles[0].permissions] == ["stocks.read"]
        assert loaded_role is not None
        assert [item.code for item in loaded_role.permissions] == ["stocks.read"]
        assert await RoleRepository(session).get_by_name("analyst") is role
        assert await PermissionRepository(session).get_by_code("stocks.read") is permission
        await session.rollback()


@pytest.mark.asyncio
async def test_role_and_permission_identifiers_are_unique() -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add_all([Role(name="admin"), Role(name="admin")])
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()

    async with factory() as session:
        session.add_all([Permission(code="users.read"), Permission(code="users.read")])
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_assignment_composite_keys_prevent_duplicates() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        role = Role(name="viewer")
        session.add_all([user, role])
        await session.flush()
        session.add_all(
            [
                UserRole(user_id=user.id, role_id=role.id),
                UserRole(user_id=user.id, role_id=role.id),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()

    async with factory() as session:
        role = Role(name="editor")
        permission = Permission(code="stocks.write")
        session.add_all([role, permission])
        await session.flush()
        session.add_all(
            [
                RolePermission(role_id=role.id, permission_id=permission.id),
                RolePermission(role_id=role.id, permission_id=permission.id),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_session_preference_and_audit_persistence() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        now = datetime.now(UTC)
        active_session = UserSession(
            user_id=user.id,
            refresh_token_hash="active-session-hash",
            expires_at=now + timedelta(days=7),
            user_agent="pytest",
            ip_address="2001:db8::1",
        )
        revoked_session = UserSession(
            user_id=user.id,
            refresh_token_hash="revoked-session-hash",
            expires_at=now + timedelta(days=7),
            revoked_at=now,
        )
        preference = UserPreference(
            user_id=user.id,
            dashboard_layout={"columns": 2},
            notification_defaults={"daily_summary": True},
        )
        audit_log = AuditLog(
            user_id=user.id,
            action="user.created",
            entity_type="user",
            entity_id=str(user.id),
            metadata_json={"source": "test"},
        )
        session.add_all([active_session, revoked_session, preference, audit_log])
        await session.flush()
        await session.refresh(preference)

        assert active_session.last_seen_at is not None
        assert preference.theme == "system"
        assert preference.timezone == "Asia/Kolkata"
        assert preference.language == "en"
        assert audit_log.created_at is not None
        assert user.preference is preference
        assert audit_log.user is user

        session_repository = UserSessionRepository(session)
        assert (
            await session_repository.get_by_refresh_token_hash("active-session-hash")
            is active_session
        )
        assert await session_repository.list_active_for_user(user.id, at=now) == [active_session]
        assert await AuditLogRepository(session).list_filtered(
            user_id=user.id, action="user.created", entity_type="user"
        ) == [audit_log]
        await session.rollback()


@pytest.mark.asyncio
async def test_refresh_token_hash_is_unique() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        expires_at = datetime.now(UTC) + timedelta(days=1)
        session.add_all(
            [
                UserSession(
                    user_id=user.id,
                    refresh_token_hash="same-hash",
                    expires_at=expires_at,
                ),
                UserSession(
                    user_id=user.id,
                    refresh_token_hash="same-hash",
                    expires_at=expires_at,
                ),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_user_delete_cascades_owned_rows_and_retains_audit_log() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        role = Role(name="member")
        session.add_all([user, role])
        await session.flush()
        assignment = UserRole(user_id=user.id, role_id=role.id)
        user_session = UserSession(
            user_id=user.id,
            refresh_token_hash="cascade-hash",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        preference = UserPreference(user_id=user.id)
        audit_log = AuditLog(user_id=user.id, action="user.deleted")
        session.add_all([assignment, user_session, preference, audit_log])
        await session.flush()
        user_id = user.id
        session_id = user_session.id
        audit_id = audit_log.id

        await session.delete(user)
        await session.flush()
        await session.refresh(audit_log)

        assert await session.get(User, user_id) is None
        assert await session.get(UserRole, (user_id, role.id)) is None
        assert await session.get(UserSession, session_id) is None
        assert await session.get(UserPreference, user_id) is None
        assert await session.get(Role, role.id) is role
        assert await session.get(AuditLog, audit_id) is audit_log
        assert audit_log.user_id is None
        await session.rollback()


@pytest.mark.asyncio
async def test_authentication_tables_columns_indexes_and_constraints_exist() -> None:
    factory = get_session_factory()
    async with factory() as session:
        table_count = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('users', 'roles', 'permissions', 'user_roles', 'role_permissions', "
                "'user_sessions', 'user_preferences', 'audit_logs')"
            )
        )
        assert table_count == 8

        user_columns = set(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'users'"
                    )
                )
            ).scalars()
        )
        assert user_columns == {
            "id",
            "username",
            "email",
            "full_name",
            "password_hash",
            "is_active",
            "is_verified",
            "is_superuser",
            "last_login_at",
            "created_at",
            "updated_at",
        }

        constraint_count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('uq_user_username', 'uq_user_email', 'uq_role_name', "
                "'uq_permission_code', 'uq_user_session_refresh_token_hash', "
                "'pk_user_roles', 'pk_role_permissions')"
            )
        )
        assert constraint_count == 7

        index_count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN "
                "('ix_users_active_verified', 'ix_user_sessions_user_active', "
                "'ix_audit_logs_entity', 'ix_audit_logs_user_created')"
            )
        )
        assert index_count == 4


@pytest.mark.asyncio
async def test_database_foreign_key_delete_rules() -> None:
    factory = get_session_factory()
    async with factory() as session:
        delete_rules = dict(
            (
                await session.execute(
                    text(
                        "SELECT tc.constraint_name, rc.delete_rule "
                        "FROM information_schema.table_constraints tc "
                        "JOIN information_schema.referential_constraints rc "
                        "ON rc.constraint_name = tc.constraint_name "
                        "AND rc.constraint_schema = tc.constraint_schema "
                        "WHERE tc.constraint_schema = 'public' AND tc.constraint_name IN "
                        "('fk_user_roles_user_id_users', "
                        "'fk_role_permissions_role_id_roles', "
                        "'fk_user_sessions_user_id_users', "
                        "'fk_user_preferences_user_id_users', "
                        "'fk_audit_logs_user_id_users')"
                    )
                )
            ).all()
        )
        assert delete_rules == {
            "fk_user_roles_user_id_users": "CASCADE",
            "fk_role_permissions_role_id_roles": "CASCADE",
            "fk_user_sessions_user_id_users": "CASCADE",
            "fk_user_preferences_user_id_users": "CASCADE",
            "fk_audit_logs_user_id_users": "SET NULL",
        }


@pytest.mark.asyncio
async def test_audit_log_entity_filter_uses_string_identifier() -> None:
    factory = get_session_factory()
    async with factory() as session:
        first = AuditLog(
            action="entity.updated",
            entity_type="stock",
            entity_id="RELIANCE",
        )
        second = AuditLog(
            action="entity.updated",
            entity_type="stock",
            entity_id="TCS",
        )
        session.add_all([first, second])
        await session.flush()

        rows = await AuditLogRepository(session).list_filtered(
            entity_type="stock", entity_id="RELIANCE"
        )
        assert rows == [first]
        assert await session.scalar(select(sa.func.count()).select_from(AuditLog)) == 2
        await session.rollback()
