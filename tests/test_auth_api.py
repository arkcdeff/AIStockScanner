from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.password import hash_password, verify_password
from app.auth.refresh_tokens import create_refresh_session
from app.cli import create_admin as create_admin_cli
from app.db.session import get_session_factory
from app.main import app
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
from app.services.auth_service import (
    AuthService,
    BootstrapAdminExistsError,
    DuplicateIdentityError,
)

API = "/api/v1/auth"
PASSWORD = "Valid-Password-7!"
NEW_PASSWORD = "Changed-Password-8!"
PASSWORD_HASH = hash_password(PASSWORD)


@asynccontextmanager
async def _client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def _create_user(
    *,
    username: str = "alice",
    email: str = "alice@example.com",
    password_hash: str = PASSWORD_HASH,
    is_active: bool = True,
    with_access: bool = False,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        user = User(
            username=username,
            email=email,
            full_name="Alice Example",
            password_hash=password_hash,
            is_active=is_active,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        if with_access:
            role = Role(name="analyst", description="Research analyst")
            permission = Permission(code="stocks.read", description="Read stocks")
            session.add_all([role, permission])
            await session.flush()
            session.add_all(
                [
                    UserRole(user_id=user.id, role_id=role.id),
                    RolePermission(role_id=role.id, permission_id=permission.id),
                    UserPreference(
                        user_id=user.id,
                        theme="dark",
                        timezone="Asia/Kolkata",
                        language="en",
                    ),
                ]
            )
        await session.commit()
        return user.id


async def _login(
    client: AsyncClient,
    *,
    identifier: str = "alice",
    password: str = PASSWORD,
    remember_me: bool = False,
) -> dict:
    response = await client.post(
        f"{API}/login",
        json={
            "username_or_email": identifier,
            "password": password,
            "remember_me": remember_me,
        },
        headers={"user-agent": "auth-api-pytest"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_bootstrap_admin_service_creates_first_superuser_with_hashed_password() -> None:
    factory = get_session_factory()
    async with factory() as session:
        admin = await AuthService(session).create_bootstrap_admin(
            username="rootadmin",
            email="Root.Admin@Example.com",
            full_name="Root Admin",
            password=PASSWORD,
        )
        admin_id = admin.id

    async with factory() as session:
        stored = await session.get(User, admin_id)
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "admin_created"))
        assert stored is not None
        assert stored.email == "root.admin@example.com"
        assert stored.is_superuser is True
        assert stored.is_active is True
        assert stored.is_verified is True
        assert stored.password_hash != PASSWORD
        assert stored.password_hash.startswith("$argon2id$")
        assert verify_password(PASSWORD, stored.password_hash)
        assert audit is not None
        assert audit.user_id == admin_id


@pytest.mark.asyncio
async def test_bootstrap_admin_rejects_duplicate_identity_and_second_superuser() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = AuthService(session)
        await service.create_bootstrap_admin(
            username="rootadmin",
            email="root@example.com",
            full_name=None,
            password=PASSWORD,
        )

    async with factory() as session:
        with pytest.raises(DuplicateIdentityError):
            await AuthService(session).create_bootstrap_admin(
                username="rootadmin",
                email="different@example.com",
                full_name=None,
                password=PASSWORD,
            )
    async with factory() as session:
        with pytest.raises(DuplicateIdentityError):
            await AuthService(session).create_bootstrap_admin(
                username="different",
                email="ROOT@example.com",
                full_name=None,
                password=PASSWORD,
            )
    async with factory() as session:
        with pytest.raises(BootstrapAdminExistsError):
            await AuthService(session).create_bootstrap_admin(
                username="secondadmin",
                email="second@example.com",
                full_name=None,
                password=PASSWORD,
            )


def test_create_admin_cli_reads_password_from_environment_without_printing_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEST_BOOTSTRAP_PASSWORD", PASSWORD)

    async def fake_create_admin(**values: str | None) -> str:
        assert values["password"] == PASSWORD
        return str(values["username"])

    monkeypatch.setattr(create_admin_cli, "_create_admin", fake_create_admin)

    result = create_admin_cli.main(
        [
            "--username",
            "cliadmin",
            "--email",
            "cliadmin@example.com",
            "--full-name",
            "CLI Admin",
            "--password-env",
            "TEST_BOOTSTRAP_PASSWORD",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "created successfully" in captured.out
    assert PASSWORD not in captured.out
    assert PASSWORD not in captured.err


@pytest.mark.asyncio
async def test_login_by_username_and_email_creates_sessions_audits_and_safe_response() -> None:
    user_id = await _create_user()
    async with _client() as client:
        username_login = await _login(client)
        email_login = await _login(
            client,
            identifier="ALICE@EXAMPLE.COM",
            remember_me=True,
        )

    assert username_login["token_type"] == "bearer"
    assert username_login["user"]["id"] == user_id
    assert username_login["user"]["username"] == "alice"
    assert username_login["session_id"] != email_login["session_id"]
    assert "password_hash" not in json.dumps(username_login)
    assert "refresh_token_hash" not in json.dumps(username_login)
    assert datetime.fromisoformat(email_login["refresh_expires_at"]) > datetime.fromisoformat(
        username_login["refresh_expires_at"]
    )

    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        sessions = list(
            await session.scalars(select(UserSession).where(UserSession.user_id == user_id))
        )
        audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "login_success"))
        )
        assert user is not None and user.last_login_at is not None
        assert len(sessions) == 2
        assert len(audits) == 2
        assert all(item.refresh_token_hash not in username_login.values() for item in sessions)


@pytest.mark.asyncio
async def test_invalid_password_unknown_user_and_inactive_user_share_safe_error() -> None:
    await _create_user()
    async with _client() as client:
        wrong_password = await client.post(
            f"{API}/login",
            json={"username_or_email": "alice", "password": "Wrong-Password-9!"},
        )
        unknown_user = await client.post(
            f"{API}/login",
            json={"username_or_email": "unknown", "password": "Wrong-Password-9!"},
        )
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert wrong_password.headers["www-authenticate"] == "Bearer"

    factory = get_session_factory()
    async with factory() as session:
        failed = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "login_failed"))
        )
        assert len(failed) == 2
        assert all(item.user_id is None for item in failed)


@pytest.mark.asyncio
async def test_inactive_user_login_is_rejected_without_session() -> None:
    user_id = await _create_user(is_active=False)
    async with _client() as client:
        response = await client.post(
            f"{API}/login",
            json={"username_or_email": "alice", "password": PASSWORD},
        )
    assert response.status_code == 401
    factory = get_session_factory()
    async with factory() as session:
        sessions = list(
            await session.scalars(select(UserSession).where(UserSession.user_id == user_id))
        )
        assert sessions == []


@pytest.mark.asyncio
async def test_refresh_rotates_token_issues_access_and_rejects_old_token() -> None:
    await _create_user()
    async with _client() as client:
        login = await _login(client)
        refreshed = await client.post(
            f"{API}/refresh",
            json={"refresh_token": login["refresh_token"]},
            headers={"user-agent": "refreshed-device"},
        )
        assert refreshed.status_code == 200
        refreshed_body = refreshed.json()
        assert refreshed_body["refresh_token"] != login["refresh_token"]
        assert refreshed_body["access_token"] != login["access_token"]
        assert refreshed_body["session_id"] == login["session_id"]

        replay = await client.post(
            f"{API}/refresh",
            json={"refresh_token": login["refresh_token"]},
        )
        assert replay.status_code == 401
        me = await client.get(
            f"{API}/me",
            headers=_bearer(refreshed_body["access_token"]),
        )
        assert me.status_code == 200

    factory = get_session_factory()
    async with factory() as session:
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "token_refresh"))
        record = await session.get(UserSession, login["session_id"])
        assert audit is not None
        assert record is not None
        assert record.user_agent == "refreshed-device"
        assert login["refresh_token"] not in record.refresh_token_hash


@pytest.mark.asyncio
async def test_refresh_rejects_revoked_expired_and_access_tokens() -> None:
    user_id = await _create_user()
    async with _client() as client:
        login = await _login(client)
        logout = await client.post(
            f"{API}/logout",
            json={"refresh_token": login["refresh_token"]},
        )
        assert logout.status_code == 200
        revoked = await client.post(
            f"{API}/refresh",
            json={"refresh_token": login["refresh_token"]},
        )
        access = await client.post(
            f"{API}/refresh",
            json={"refresh_token": login["access_token"]},
        )
        assert revoked.status_code == 401
        assert access.status_code == 401

    factory = get_session_factory()
    async with factory() as session:
        expired = await create_refresh_session(
            session,
            user_id=user_id,
            now=datetime.now(UTC) - timedelta(days=2),
            expires_delta=timedelta(minutes=1),
        )
        await session.commit()
    async with _client() as client:
        response = await client.post(
            f"{API}/refresh",
            json={"refresh_token": expired.token},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_is_idempotent_and_writes_one_audit_event() -> None:
    await _create_user()
    async with _client() as client:
        login = await _login(client)
        first = await client.post(
            f"{API}/logout",
            json={"refresh_token": login["refresh_token"]},
        )
        second = await client.post(
            f"{API}/logout",
            json={"refresh_token": login["refresh_token"]},
        )
    assert first.status_code == 200
    assert first.json()["message"] == "Session revoked"
    assert second.status_code == 200
    assert second.json()["message"] == "Session was already revoked"

    factory = get_session_factory()
    async with factory() as session:
        record = await session.get(UserSession, login["session_id"])
        audits = list(await session.scalars(select(AuditLog).where(AuditLog.action == "logout")))
        assert record is not None and record.revoked_at is not None
        assert len(audits) == 1


@pytest.mark.asyncio
async def test_logout_all_revokes_every_session_including_current_session() -> None:
    await _create_user()
    async with _client() as client:
        first = await _login(client)
        await _login(client, identifier="alice@example.com")
        response = await client.post(
            f"{API}/logout-all",
            headers=_bearer(first["access_token"]),
        )
        assert response.status_code == 200
        assert response.json()["revoked_sessions"] == 2
        me = await client.get(f"{API}/me", headers=_bearer(first["access_token"]))
        assert me.status_code == 401

    factory = get_session_factory()
    async with factory() as session:
        records = list(await session.scalars(select(UserSession)))
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "logout_all"))
        assert all(record.revoked_at is not None for record in records)
        assert audit is not None


@pytest.mark.asyncio
async def test_me_returns_eager_roles_permissions_preferences_and_user_flags() -> None:
    await _create_user(with_access=True)
    async with _client() as client:
        login = await _login(client)
        response = await client.get(
            f"{API}/me",
            headers=_bearer(login["access_token"]),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["roles"] == ["analyst"]
    assert body["permissions"] == ["stocks.read"]
    assert body["preferences"] == {
        "theme": "dark",
        "timezone": "Asia/Kolkata",
        "language": "en",
    }
    assert body["current_session_id"] == login["session_id"]
    assert "password_hash" not in response.text


@pytest.mark.asyncio
async def test_me_rejects_missing_malformed_refresh_revoked_and_inactive_credentials() -> None:
    user_id = await _create_user()
    async with _client() as client:
        login = await _login(client)
        missing = await client.get(f"{API}/me")
        malformed = await client.get(
            f"{API}/me",
            headers=_bearer("not-a-token"),
        )
        refresh = await client.get(
            f"{API}/me",
            headers=_bearer(login["refresh_token"]),
        )
        assert missing.status_code == malformed.status_code == refresh.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"

        await client.post(
            f"{API}/logout",
            json={"refresh_token": login["refresh_token"]},
        )
        revoked = await client.get(
            f"{API}/me",
            headers=_bearer(login["access_token"]),
        )
        assert revoked.status_code == 401

    async with get_session_factory()() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.is_active = False
        await session.commit()
    await _create_user(username="bob", email="bob@example.com")
    async with _client() as client:
        bob_login = await _login(client, identifier="bob")
    async with get_session_factory()() as session:
        bob = await session.scalar(select(User).where(User.username == "bob"))
        assert bob is not None
        bob.is_active = False
        await session.commit()
    async with _client() as client:
        inactive = await client.get(
            f"{API}/me",
            headers=_bearer(bob_login["access_token"]),
        )
        assert inactive.status_code == 401


@pytest.mark.asyncio
async def test_change_password_succeeds_and_revokes_all_sessions() -> None:
    user_id = await _create_user()
    async with _client() as client:
        first = await _login(client)
        await _login(client, identifier="alice@example.com")
        response = await client.post(
            f"{API}/change-password",
            headers=_bearer(first["access_token"]),
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        assert response.status_code == 200
        assert response.json()["revoked_sessions"] == 2
        assert (
            await client.get(f"{API}/me", headers=_bearer(first["access_token"]))
        ).status_code == 401
        old_login = await client.post(
            f"{API}/login",
            json={"username_or_email": "alice", "password": PASSWORD},
        )
        assert old_login.status_code == 401
        new_login = await _login(client, password=NEW_PASSWORD)
        assert new_login["user"]["id"] == user_id

    async with get_session_factory()() as session:
        user = await session.get(User, user_id)
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "password_changed"))
        assert user is not None and verify_password(NEW_PASSWORD, user.password_hash)
        assert audit is not None
        assert audit.metadata_json["session_policy"] == "revoke_all"


@pytest.mark.asyncio
async def test_change_password_validates_current_policy_confirmation_and_reuse() -> None:
    await _create_user()
    async with _client() as client:
        login = await _login(client)
        headers = _bearer(login["access_token"])
        wrong = await client.post(
            f"{API}/change-password",
            headers=headers,
            json={
                "current_password": "Wrong-Password-9!",
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        weak = await client.post(
            f"{API}/change-password",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": "weak",
                "confirm_password": "weak",
            },
        )
        mismatch = await client.post(
            f"{API}/change-password",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": "Different-Password-9!",
            },
        )
        reused = await client.post(
            f"{API}/change-password",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
    assert wrong.status_code == 400
    assert weak.status_code == 422
    assert mismatch.status_code == 422
    assert reused.status_code == 422


@pytest.mark.asyncio
async def test_sessions_list_masks_ip_and_identifies_current_session() -> None:
    await _create_user()
    async with _client() as client:
        first = await _login(client)
        second = await _login(client, identifier="alice@example.com")
        response = await client.get(
            f"{API}/sessions",
            headers=_bearer(first["access_token"]),
        )
    assert response.status_code == 200
    sessions = response.json()
    assert {item["id"] for item in sessions} == {
        first["session_id"],
        second["session_id"],
    }
    assert all(item["created_at"] is not None for item in sessions)
    assert sum(item["current_session"] for item in sessions) == 1
    assert all(item["ip_address"].endswith(".xxx") for item in sessions)
    assert all("refresh_token" not in item for item in sessions)


@pytest.mark.asyncio
async def test_revoke_owned_session_is_safe_and_cannot_revoke_another_users_session() -> None:
    await _create_user()
    await _create_user(username="bob", email="bob@example.com")
    async with _client() as client:
        alice_first = await _login(client)
        alice_second = await _login(client, identifier="alice@example.com")
        bob = await _login(client, identifier="bob")

        denied = await client.delete(
            f"{API}/sessions/{bob['session_id']}",
            headers=_bearer(alice_first["access_token"]),
        )
        assert denied.status_code == 404
        revoked = await client.delete(
            f"{API}/sessions/{alice_second['session_id']}",
            headers=_bearer(alice_first["access_token"]),
        )
        repeated = await client.delete(
            f"{API}/sessions/{alice_second['session_id']}",
            headers=_bearer(alice_first["access_token"]),
        )
    assert revoked.status_code == repeated.status_code == 200
    assert revoked.json()["message"] == "Session revoked"
    assert repeated.json()["message"] == "Session was already revoked"

    async with get_session_factory()() as session:
        bob_record = await session.get(UserSession, bob["session_id"])
        alice_record = await session.get(UserSession, alice_second["session_id"])
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "session_revoked"))
        assert bob_record is not None and bob_record.revoked_at is None
        assert alice_record is not None and alice_record.revoked_at is not None
        assert audit is not None


@pytest.mark.asyncio
async def test_audit_metadata_never_stores_passwords_or_raw_tokens() -> None:
    await _create_user()
    async with _client() as client:
        login = await _login(client)
        refreshed = await client.post(
            f"{API}/refresh",
            json={"refresh_token": login["refresh_token"]},
        )
        assert refreshed.status_code == 200
        await client.post(
            f"{API}/logout",
            json={"refresh_token": refreshed.json()["refresh_token"]},
        )

    async with get_session_factory()() as session:
        audits = list(await session.scalars(select(AuditLog)))
        serialized_metadata = json.dumps(
            [audit.metadata_json for audit in audits],
            sort_keys=True,
        )
    assert PASSWORD not in serialized_metadata
    assert login["access_token"] not in serialized_metadata
    assert login["refresh_token"] not in serialized_metadata
    assert refreshed.json()["refresh_token"] not in serialized_metadata
    assert "password" not in serialized_metadata.lower()
    assert "token" not in serialized_metadata.lower()
