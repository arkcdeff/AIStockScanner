from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.password import hash_password, verify_password
from app.db.session import get_session_factory
from app.main import app
from app.models.auth import (
    AuditLog,
    Role,
    User,
    UserPreference,
    UserRole,
    UserSession,
)
from app.services.role_service import RoleService

API = "/api/v1"
ADMIN = f"{API}/admin"
PASSWORD = "Valid-Password-7!"


@asynccontextmanager
async def _client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def _create_user(
    *,
    username: str,
    email: str,
    password_hash: str = hash_password(PASSWORD),
    is_active: bool = True,
    is_superuser: bool = False,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        user = User(
            username=username,
            email=email,
            full_name="Full Name",
            password_hash=password_hash,
            is_active=is_active,
            is_verified=True,
            is_superuser=is_superuser,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _login(client: AsyncClient, identifier: str) -> dict:
    response = await client.post(
        f"{API}/auth/login",
        json={"username_or_email": identifier, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _open_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _admin_client() -> tuple[AsyncClient, dict]:
    await _create_user(username="rootadmin", email="root@example.com", is_superuser=True)
    client = await _open_client()
    login = await _login(client, "rootadmin")
    return client, login


async def _normal_user_client() -> tuple[AsyncClient, dict]:
    await _create_user(username="regular", email="regular@example.com")
    client = await _open_client()
    login = await _login(client, "regular")
    return client, login


# --------------------------------------------------------------------------- access control


@pytest.mark.asyncio
async def test_admin_routes_deny_unauthenticated() -> None:
    async with _client() as client:
        response = await client.get(f"{ADMIN}/users")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_routes_deny_normal_user() -> None:
    client, login = await _normal_user_client()
    try:
        response = await client.get(f"{ADMIN}/users", headers=_bearer(login["access_token"]))
        assert response.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_admin_routes_allow_superuser() -> None:
    client, login = await _admin_client()
    try:
        response = await client.get(f"{ADMIN}/users", headers=_bearer(login["access_token"]))
        assert response.status_code == 200
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_super_admin_role_grants_admin_access() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await RoleService(session).seed_builtins()
        user = User(
            username="roleadmin",
            email="roleadmin@example.com",
            full_name="Role Admin",
            password_hash=hash_password(PASSWORD),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        role = await session.scalar(select(Role).where(Role.name == "super_admin"))
        assert role is not None
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()

    async with _client() as client:
        login = await _login(client, "roleadmin")
        response = await client.get(f"{ADMIN}/users", headers=_bearer(login["access_token"]))
        assert response.status_code == 200


# --------------------------------------------------------------------------- users


@pytest.mark.asyncio
async def test_create_user_hashes_password_audits_and_returns_no_secrets() -> None:
    client, login = await _admin_client()
    try:
        response = await client.post(
            f"{ADMIN}/users",
            headers=_bearer(login["access_token"]),
            json={
                "username": "carol",
                "email": "carol@example.com",
                "full_name": "Carol Developer",
                "password": PASSWORD,
                "is_verified": True,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "carol"
        assert "password_hash" not in response.text
        assert body["is_active"] is True
    finally:
        await client.aclose()

    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.username == "carol"))
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "user_created"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(PASSWORD, user.password_hash)
        assert audit is not None and audit.metadata_json["target_user_id"] == user.id


@pytest.mark.asyncio
async def test_create_user_duplicate_username_and_email_conflict() -> None:
    await _create_user(username="dup", email="dup@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        dup_username = await client.post(
            f"{ADMIN}/users",
            headers=headers,
            json={"username": "dup", "email": "other@example.com", "password": PASSWORD},
        )
        dup_email = await client.post(
            f"{ADMIN}/users",
            headers=headers,
            json={"username": "other", "email": "DUP@example.com", "password": PASSWORD},
        )
        assert dup_username.status_code == dup_email.status_code == 409
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_list_users_pagination_search_and_filter() -> None:
    for index in range(5):
        await _create_user(
            username=f"user{index}",
            email=f"user{index}@example.com",
            is_superuser=(index == 0),
        )
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        page = await client.get(
            f"{ADMIN}/users?page=1&page_size=2&sort=username",
            headers=headers,
        )
        body = page.json()
        assert body["total"] == 6  # 5 + rootadmin
        assert len(body["items"]) == 2
        assert body["pages"] == 3

        search = await client.get(f"{ADMIN}/users?search=user1", headers=headers)
        assert search.json()["total"] == 1

        superusers = await client.get(f"{ADMIN}/users?is_superuser=true", headers=headers)
        assert superusers.json()["total"] == 2  # user0 + rootadmin

        active = await client.get(f"{ADMIN}/users?is_active=true", headers=headers)
        assert active.json()["total"] == 6
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_user_detail_returns_roles_and_permissions() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await RoleService(session).seed_builtins()
        user = User(
            username="analyst",
            email="analyst@example.com",
            full_name="Analyst",
            password_hash=hash_password(PASSWORD),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        role = await session.scalar(select(Role).where(Role.name == "analyst"))
        assert role is not None
        session.add(UserRole(user_id=user.id, role_id=role.id))
        user_id = user.id
        await session.commit()

    client, login = await _admin_client()
    try:
        response = await client.get(
            f"{ADMIN}/users/{user_id}",
            headers=_bearer(login["access_token"]),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["roles"] == ["analyst"]
        assert "stocks.read" in body["permissions"]
        assert "password_hash" not in response.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_update_user_fields_and_protect_last_superuser() -> None:
    target = await _create_user(username="editable", email="editable@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        updated = await client.put(
            f"{ADMIN}/users/{target}",
            headers=headers,
            json={"full_name": "Renamed", "is_verified": False},
        )
        assert updated.status_code == 200
        assert updated.json()["full_name"] == "Renamed"

        # self-demotion forbidden
        self_demote = await client.put(
            f"{ADMIN}/users/{login['user']['id']}",
            headers=headers,
            json={"is_superuser": False},
        )
        assert self_demote.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_deactivate_and_reactivate_user() -> None:
    target = await _create_user(username="toggle", email="toggle@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        deactivated = await client.post(
            f"{ADMIN}/users/{target}/deactivate", headers=headers
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        activated = await client.post(f"{ADMIN}/users/{target}/activate", headers=headers)
        assert activated.status_code == 200
        assert activated.json()["is_active"] is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cannot_deactivate_last_active_superuser() -> None:
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        response = await client.post(
            f"{ADMIN}/users/{login['user']['id']}/deactivate", headers=headers
        )
        assert response.status_code == 409
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_verify_and_unverify_user() -> None:
    target = await _create_user(username="verifier", email="verifier@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        verified = await client.post(f"{ADMIN}/users/{target}/verify", headers=headers)
        assert verified.json()["is_verified"] is True
        unverified = await client.post(f"{ADMIN}/users/{target}/unverify", headers=headers)
        assert unverified.json()["is_verified"] is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_admin_reset_password_revokes_sessions_and_hashes() -> None:
    target = await _create_user(username="resetme", email="resetme@example.com")
    async with _client() as client:
        login = await _login(client, "resetme")
        session_id = login["session_id"]

    client, admin_login = await _admin_client()
    try:
        response = await client.post(
            f"{ADMIN}/users/{target}/reset-password",
            headers=_bearer(admin_login["access_token"]),
            json={"new_password": "Reset-New-Pass-9!"},
        )
        assert response.status_code == 200
        assert response.json()["password_generated"] is False
    finally:
        await client.aclose()

    factory = get_session_factory()
    async with factory() as session:
        record = await session.get(UserSession, session_id)
        user = await session.get(User, target)
        assert record is not None and record.revoked_at is not None
        assert user is not None and verify_password("Reset-New-Pass-9!", user.password_hash)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "admin_password_reset")
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_admin_generate_password_returns_temporary_only_when_requested() -> None:
    target = await _create_user(username="genpass", email="genpass@example.com")
    client, login = await _admin_client()
    try:
        response = await client.post(
            f"{ADMIN}/users/{target}/reset-password",
            headers=_bearer(login["access_token"]),
            json={"generate": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["password_generated"] is True
        assert body["temporary_password"] is not None
        assert len(body["temporary_password"]) >= 12
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_delete_user_is_soft_and_protects_last_superuser_and_self() -> None:
    target = await _create_user(username="deleteme", email="deleteme@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        deleted = await client.delete(f"{ADMIN}/users/{target}", headers=headers)
        assert deleted.status_code == 204
        self_delete = await client.delete(
            f"{ADMIN}/users/{login['user']['id']}", headers=headers
        )
        assert self_delete.status_code == 409
    finally:
        await client.aclose()

    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, target)
        assert user is not None and user.is_active is False


# --------------------------------------------------------------------------- roles and permissions


@pytest.mark.asyncio
async def test_role_crud_duplicate_name_and_protected_builtin() -> None:
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        created = await client.post(
            f"{ADMIN}/roles",
            headers=headers,
            json={"name": "trader", "description": "Trading role"},
        )
        assert created.status_code == 201
        role_id = created.json()["id"]

        duplicate = await client.post(
            f"{ADMIN}/roles",
            headers=headers,
            json={"name": "trader"},
        )
        assert duplicate.status_code == 409

        detail = await client.get(f"{ADMIN}/roles/{role_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["user_count"] == 0

        updated = await client.put(
            f"{ADMIN}/roles/{role_id}",
            headers=headers,
            json={"description": "Updated trading role"},
        )
        assert updated.json()["description"] == "Updated trading role"

        # built-in roles protected
        protected = await client.delete(f"{ADMIN}/roles/{role_id}", headers=headers)
        assert protected.status_code == 204

        seed = await client.post(f"{ADMIN}/seed", headers=headers)
        assert seed.status_code == 200
        roles = await client.get(f"{ADMIN}/roles", headers=headers)
        super_admin = next(r for r in roles.json() if r["name"] == "super_admin")
        denied = await client.delete(
            f"{ADMIN}/roles/{super_admin['id']}", headers=headers
        )
        assert denied.status_code == 409
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_assign_and_remove_role_with_duplicate_guard() -> None:
    target = await _create_user(username="roled", email="roled@example.com")
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        await client.post(f"{ADMIN}/seed", headers=headers)
        roles = (await client.get(f"{ADMIN}/roles", headers=headers)).json()
        analyst = next(r for r in roles if r["name"] == "analyst")

        first = await client.post(
            f"{ADMIN}/users/{target}/roles/{analyst['id']}", headers=headers
        )
        second = await client.post(
            f"{ADMIN}/users/{target}/roles/{analyst['id']}", headers=headers
        )
        assert first.status_code == second.status_code == 200

        detail = await client.get(f"{ADMIN}/users/{target}", headers=headers)
        assert detail.json()["roles"] == ["analyst"]

        removed = await client.delete(
            f"{ADMIN}/users/{target}/roles/{analyst['id']}", headers=headers
        )
        assert removed.status_code == 200
        detail = await client.get(f"{ADMIN}/users/{target}", headers=headers)
        assert detail.json()["roles"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_assign_permission_and_duplicate_guard() -> None:
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        created = await client.post(
            f"{ADMIN}/permissions",
            headers=headers,
            json={"code": "exports.run", "description": "Run exports"},
        )
        assert created.status_code == 201
        permission_id = created.json()["id"]

        duplicate = await client.post(
            f"{ADMIN}/permissions",
            headers=headers,
            json={"code": "exports.run"},
        )
        assert duplicate.status_code == 409

        role = await client.post(
            f"{ADMIN}/roles", headers=headers, json={"name": "exporter"}
        )
        role_id = role.json()["id"]

        assigned = await client.post(
            f"{ADMIN}/roles/{role_id}/permissions/{permission_id}", headers=headers
        )
        assert assigned.status_code == 200
        again = await client.post(
            f"{ADMIN}/roles/{role_id}/permissions/{permission_id}", headers=headers
        )
        assert again.status_code == 200

        role_detail = await client.get(f"{ADMIN}/roles/{role_id}", headers=headers)
        assert any(p["code"] == "exports.run" for p in role_detail.json()["permissions"])

        # permission in use cannot be deleted
        in_use = await client.delete(
            f"{ADMIN}/permissions/{permission_id}", headers=headers
        )
        assert in_use.status_code == 409

        removed = await client.delete(
            f"{ADMIN}/roles/{role_id}/permissions/{permission_id}", headers=headers
        )
        assert removed.status_code == 200
        after = await client.delete(
            f"{ADMIN}/permissions/{permission_id}", headers=headers
        )
        assert after.status_code == 204
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_creates_no_duplicates() -> None:
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        first = await client.post(f"{ADMIN}/seed", headers=headers)
        second = await client.post(f"{ADMIN}/seed", headers=headers)
        assert first.status_code == second.status_code == 200

        roles = (await client.get(f"{ADMIN}/roles", headers=headers)).json()
        permissions = (await client.get(f"{ADMIN}/permissions", headers=headers)).json()
        role_names = [r["name"] for r in roles]
        for expected in ("super_admin", "admin", "analyst", "viewer"):
            assert expected in role_names
        assert len(role_names) == len(set(role_names))
        codes = [p["code"] for p in permissions]
        assert "users.read" in codes and "settings.write" in codes
        assert len(codes) == len(set(codes))
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_seed_cli_module_exists() -> None:
    import app.cli.seed_auth as seed_auth

    assert callable(seed_auth.main)


# --------------------------------------------------------------------------- preferences


@pytest.mark.asyncio
async def test_get_preferences_creates_defaults_automatically() -> None:
    user_id = await _create_user(username="prefuser", email="pref@example.com")
    async with get_session_factory()() as session:
        preference = await session.get(UserPreference, user_id)
        assert preference is None

    async with _client() as client:
        login = await _login(client, "prefuser")
        response = await client.get(
            f"{API}/auth/preferences",
            headers=_bearer(login["access_token"]),
        )
        assert response.status_code == 200
        assert response.json()["theme"] == "system"
        assert response.json()["timezone"] == "Asia/Kolkata"
        assert response.json()["language"] == "en"


@pytest.mark.asyncio
async def test_update_preferences_validates_values() -> None:
    await _create_user(username="prefup", email="prefup@example.com")
    async with _client() as client:
        login = await _login(client, "prefup")
        headers = _bearer(login["access_token"])
        ok = await client.put(
            f"{API}/auth/preferences",
            headers=headers,
            json={"theme": "dark", "timezone": "America/New_York", "language": "hi"},
        )
        assert ok.status_code == 200
        assert ok.json() == {
            "theme": "dark",
            "timezone": "America/New_York",
            "language": "hi",
        }
        bad_theme = await client.put(
            f"{API}/auth/preferences", headers=headers, json={"theme": "neon"}
        )
        assert bad_theme.status_code == 422
        bad_tz = await client.put(
            f"{API}/auth/preferences", headers=headers, json={"timezone": "Mars/Olympus"}
        )
        assert bad_tz.status_code == 422


# --------------------------------------------------------------------------- profile


@pytest.mark.asyncio
async def test_update_profile_full_name_without_password() -> None:
    await _create_user(username="profuser", email="prof@example.com")
    async with _client() as client:
        login = await _login(client, "profuser")
        response = await client.put(
            f"{API}/auth/profile",
            headers=_bearer(login["access_token"]),
            json={"full_name": "New Full Name"},
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "New Full Name"


@pytest.mark.asyncio
async def test_update_profile_email_requires_password_confirmation() -> None:
    await _create_user(username="emailuser", email="old@example.com")
    async with _client() as client:
        login = await _login(client, "emailuser")
        headers = _bearer(login["access_token"])
        missing_password = await client.put(
            f"{API}/auth/profile",
            headers=headers,
            json={"email": "new@example.com"},
        )
        assert missing_password.status_code == 400
        wrong_password = await client.put(
            f"{API}/auth/profile",
            headers=headers,
            json={"email": "new@example.com", "current_password": "Wrong-Pass-9!"},
        )
        assert wrong_password.status_code == 400
        ok = await client.put(
            f"{API}/auth/profile",
            headers=headers,
            json={"email": "new@example.com", "current_password": PASSWORD},
        )
        assert ok.status_code == 200
        assert ok.json()["email"] == "new@example.com"


@pytest.mark.asyncio
async def test_update_profile_duplicate_email_conflict() -> None:
    await _create_user(username="first", email="first@example.com")
    await _create_user(username="second", email="second@example.com")
    async with _client() as client:
        login = await _login(client, "first")
        response = await client.put(
            f"{API}/auth/profile",
            headers=_bearer(login["access_token"]),
            json={"email": "SECOND@example.com", "current_password": PASSWORD},
        )
        assert response.status_code == 409


# --------------------------------------------------------------------------- audit


@pytest.mark.asyncio
async def test_admin_audit_never_stores_credentials() -> None:
    client, login = await _admin_client()
    try:
        headers = _bearer(login["access_token"])
        await client.post(
            f"{ADMIN}/users",
            headers=headers,
            json={"username": "auditme", "email": "auditme@example.com", "password": PASSWORD},
        )
        await client.post(f"{ADMIN}/seed", headers=headers)
    finally:
        await client.aclose()

    factory = get_session_factory()
    async with factory() as session:
        audits = list(await session.scalars(select(AuditLog)))
        serialized = json.dumps([a.metadata_json for a in audits], sort_keys=True)
    assert PASSWORD not in serialized
    assert "password" not in serialized.lower()
    assert "token" not in serialized.lower()
    actions = {audit.action for audit in audits}
    assert {
        "user_created",
        "builtin_auth_seeded",
        "login_success",
    }.issubset(actions)
    assert "admin_password_reset" not in serialized
