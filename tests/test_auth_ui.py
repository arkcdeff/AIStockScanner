from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.password import hash_password
from app.core.config import settings
from app.db.session import get_session_factory
from app.main import app
from app.models.auth import User

PASSWORD = "Valid-Password-7!"


async def _open_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _create_user(
    username: str,
    email: str,
    *,
    is_superuser: bool = False,
    password: str = PASSWORD,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        user = User(
            username=username,
            email=email,
            full_name="Full Name",
            password_hash=hash_password(password),
            is_active=True,
            is_verified=True,
            is_superuser=is_superuser,
        )
        session.add(user)
        await session.commit()
        return user.id


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _cookie(raw: str, name: str) -> str:
    for part in raw.split("|"):
        if part.lstrip().startswith(f"{name}="):
            return part
    return ""


def _has_attr(raw_cookie: str, attribute: str) -> bool:
    return attribute.lower() in raw_cookie.lower()


async def _csrf_for(client: AsyncClient) -> str:
    """Load the login page so the CSRF cookie is set, then return its value."""
    page = await client.get("/login")
    assert page.status_code == 200, page.text
    return client.cookies.get(settings.csrf_cookie_name) or ""


async def _login(
    client: AsyncClient, identifier: str, *, password: str = PASSWORD, expected: int = 303
):
    token = await _csrf_for(client)
    response = await client.post(
        "/login",
        data={
            "username_or_email": identifier,
            "password": password,
            "_csrf_token": token,
            "next": "/dashboard",
        },
    )
    assert response.status_code == expected, response.text
    return response


# --------------------------------------------------------------------------- login / logout


@pytest.mark.asyncio
async def test_login_page_renders_and_sets_csrf_cookie() -> None:
    async with await _open_client() as client:
        page = await client.get("/login")
        assert page.status_code == 200
        assert "Sign in" in page.text
        csrf_cookie = next(
            (c for c in _set_cookie_headers(page) if c.startswith("csrf_token=")), None
        )
        assert csrf_cookie is not None
        assert not _has_attr(csrf_cookie, "HttpOnly")


@pytest.mark.asyncio
async def test_login_sets_httponly_cookies() -> None:
    await _create_user(username="ui_login", email="ui_login@example.com")
    async with await _open_client() as client:
        response = await _login(client, "ui_login")
        raw = "|".join(_set_cookie_headers(response))
        for name in (settings.access_cookie_name, settings.refresh_cookie_name):
            cookie = _cookie(raw, name)
            assert cookie, f"missing {name} cookie"
            assert _has_attr(cookie, "HttpOnly")
            assert _has_attr(cookie, "SameSite")
        assert "Secure" not in raw  # dev default
        assert response.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_login_bad_credentials_shows_error() -> None:
    await _create_user(username="ui_bad", email="ui_bad@example.com")
    async with await _open_client() as client:
        response = await _login(client, "ui_bad", password="Wrong-Pass-1!", expected=401)
        assert "Invalid username/email or password" in response.text


@pytest.mark.asyncio
async def test_login_missing_csrf_is_rejected() -> None:
    await _create_user(username="ui_nocsrf", email="ui_nocsrf@example.com")
    async with await _open_client() as client:
        response = await client.post(
            "/login",
            data={"username_or_email": "ui_nocsrf", "password": PASSWORD},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_logout_clears_cookies() -> None:
    await _create_user(username="ui_out", email="ui_out@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_out")
        token = client.cookies.get(settings.csrf_cookie_name)
        response = await client.post("/logout", data={"_csrf_token": token or ""})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert client.cookies.get(settings.access_cookie_name) is None
        assert client.cookies.get(settings.refresh_cookie_name) is None


@pytest.mark.asyncio
async def test_logout_requires_csrf_when_authenticated() -> None:
    await _create_user("ui_out_csrf", "ui_out_csrf@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_out_csrf")
        response = await client.post("/logout")
        assert response.status_code == 403


# --------------------------------------------------------------------------- protection


@pytest.mark.asyncio
async def test_protected_profile_redirects_anon_to_login() -> None:
    async with await _open_client() as client:
        response = await client.get("/profile", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login?next=/profile")


@pytest.mark.asyncio
async def test_protected_admin_redirects_anon_to_login() -> None:
    async with await _open_client() as client:
        response = await client.get("/admin/users", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_profile_renders_after_login_and_keeps_csrf() -> None:
    await _create_user("ui_prof", "ui_prof@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_prof")
        response = await client.get("/profile")
        assert response.status_code == 200
        assert "ui_prof" in response.text
        assert client.cookies.get(settings.csrf_cookie_name) is not None
        assert "Bearer" not in response.text
        assert "password_hash" not in response.text


@pytest.mark.asyncio
async def test_normal_user_forbidden_from_admin() -> None:
    await _create_user("ui_user", "ui_user@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_user")
        response = await client.get("/admin/users")
        assert response.status_code == 403


# --------------------------------------------------------------------------- profile / preferences


@pytest.mark.asyncio
async def test_profile_update_full_name() -> None:
    await _create_user("ui_upd", "ui_upd@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_upd")
        token = client.cookies.get(settings.csrf_cookie_name)
        response = await client.post(
            "/profile/update",
            data={"full_name": "Updated Name", "_csrf_token": token or ""},
        )
        assert response.status_code == 200
        assert "Updated Name" in response.text


@pytest.mark.asyncio
async def test_profile_update_requires_csrf() -> None:
    await _create_user("ui_upd_csrf", "ui_upd_csrf@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_upd_csrf")
        response = await client.post("/profile/update", data={"full_name": "Hax"})
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_preferences_update_validates() -> None:
    await _create_user("ui_pref", "ui_pref@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_pref")
        token = client.cookies.get(settings.csrf_cookie_name)
        good = await client.post(
            "/preferences/update",
            data={
                "theme": "dark",
                "timezone": "America/New_York",
                "language": "hi",
                "_csrf_token": token or "",
            },
        )
        assert good.status_code == 200
        assert "dark" in good.text
        # The page response rotated the CSRF cookie; use the fresh value.
        token = client.cookies.get(settings.csrf_cookie_name)
        bad = await client.post(
            "/preferences/update",
            data={"theme": "neon", "_csrf_token": token or ""},
        )
        assert bad.status_code == 422


# --------------------------------------------------------------------------- sessions


@pytest.mark.asyncio
async def test_sessions_page_lists_active_sessions() -> None:
    user_id = await _create_user("ui_sesh", "ui_sesh@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_sesh")
        listing = await client.get("/sessions")
        assert listing.status_code == 200
        assert "ui_sesh" in listing.text or "Session" in listing.text

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import func, select

        from app.models.auth import UserSession

        count = await session.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        )
        assert count == 1


@pytest.mark.asyncio
async def test_revoke_unknown_session_returns_404() -> None:
    await _create_user("ui_rev", "ui_rev@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_rev")
        token = client.cookies.get(settings.csrf_cookie_name)
        response = await client.post(
            "/sessions/9999/revoke", data={"_csrf_token": token or ""}
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_revoke_all_logs_out_and_clears_cookies() -> None:
    await _create_user("ui_all", "ui_all@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_all")
        token = client.cookies.get(settings.csrf_cookie_name)
        response = await client.post("/sessions/revoke-all", data={"_csrf_token": token or ""})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert client.cookies.get(settings.access_cookie_name) is None


# --------------------------------------------------------------------------- password


@pytest.mark.asyncio
async def test_change_password_logs_out_and_new_password_works() -> None:
    await _create_user("ui_pw", "ui_pw@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_pw")
        token = client.cookies.get(settings.csrf_cookie_name)
        changed = await client.post(
            "/password/change",
            data={
                "current_password": PASSWORD,
                "new_password": "New-Pass-11!",
                "confirm_password": "New-Pass-11!",
                "_csrf_token": token or "",
            },
        )
        assert changed.status_code == 303
        assert client.cookies.get(settings.access_cookie_name) is None

    # Old password no longer works.
    async with await _open_client() as client:
        response = await _login(client, "ui_pw", password=PASSWORD, expected=401)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_change_password_wrong_current_password() -> None:
    await _create_user("ui_pwbad", "ui_pwbad@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_pwbad")
        token = client.cookies.get(settings.csrf_cookie_name)
        response = await client.post(
            "/password/change",
            data={
                "current_password": "Wrong-Pass-1!",
                "new_password": "New-Pass-11!",
                "confirm_password": "New-Pass-11!",
                "_csrf_token": token or "",
            },
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_change_password_requires_csrf() -> None:
    await _create_user("ui_pwcsrf", "ui_pwcsrf@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_pwcsrf")
        response = await client.post(
            "/password/change",
            data={
                "current_password": PASSWORD,
                "new_password": "New-Pass-11!",
                "confirm_password": "New-Pass-11!",
            },
        )
        assert response.status_code == 403


# --------------------------------------------------------------------------- admin UI


@pytest.mark.asyncio
async def test_admin_users_page_lists_users() -> None:
    await _create_user("ui_citizen", "ui_citizen@example.com")
    await _create_user("ui_root", "ui_root@example.com", is_superuser=True)
    async with await _open_client() as client:
        await _login(client, "ui_root")
        response = await client.get("/admin/users")
        assert response.status_code == 200
        assert "ui_citizen" in response.text
        assert "ui_root" in response.text


@pytest.mark.asyncio
async def test_admin_create_user_lists_new_user() -> None:
    await _create_user("ui_admin1", "ui_admin1@example.com", is_superuser=True)
    async with await _open_client() as client:
        await _login(client, "ui_admin1")
        token = client.cookies.get(settings.csrf_cookie_name)
        created = await client.post(
            "/admin/users/create",
            data={
                "username": "brandnew",
                "email": "brandnew@example.com",
                "full_name": "Brand",
                "password": "Brand-New-Pass-9!",
                "is_active": "1",
                "is_verified": "1",
                "_csrf_token": token or "",
            },
        )
        assert created.status_code == 201
        assert "brandnew" in created.text

        listing = await client.get("/admin/users")
        assert listing.status_code == 200
        assert "brandnew" in listing.text


@pytest.mark.asyncio
async def test_admin_roles_and_permissions_pages() -> None:
    await _create_user("ui_admin2", "ui_admin2@example.com", is_superuser=True)
    async with await _open_client() as client:
        await _login(client, "ui_admin2")
        roles = await client.get("/admin/roles")
        assert roles.status_code == 200
        permissions = await client.get("/admin/permissions")
        assert permissions.status_code == 200


@pytest.mark.asyncio
async def test_admin_create_permission() -> None:
    await _create_user("ui_admin3", "ui_admin3@example.com", is_superuser=True)
    async with await _open_client() as client:
        await _login(client, "ui_admin3")
        token = client.cookies.get(settings.csrf_cookie_name)
        code = "reports.export"
        created = await client.post(
            "/admin/permissions/create",
            data={"code": code, "description": "Export reports", "_csrf_token": token or ""},
        )
        assert created.status_code == 201
        assert code in created.text


@pytest.mark.asyncio
async def test_admin_create_role() -> None:
    await _create_user("ui_admin4", "ui_admin4@example.com", is_superuser=True)
    async with await _open_client() as client:
        await _login(client, "ui_admin4")
        token = client.cookies.get(settings.csrf_cookie_name)
        created = await client.post(
            "/admin/roles/create",
            data={"name": "treasury", "description": "Treasury role", "_csrf_token": token or ""},
        )
        assert created.status_code == 201
        assert "treasury" in created.text


# --------------------------------------------------------------------------- security flags


@pytest.mark.asyncio
async def test_secure_flag_present_when_enabled() -> None:
    from datetime import UTC, datetime, timedelta

    from fastapi.responses import Response

    from app.api.browser_auth import set_auth_cookies
    from app.services.auth_service import TokenPair

    user_id = await _create_user("ui_sec", "ui_sec@example.com")
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        now = datetime.now(UTC)
        pair = TokenPair(
            access_token="fake-access",
            refresh_token="fake-refresh",
            access_expires_at=now + timedelta(minutes=15),
            refresh_expires_at=now + timedelta(days=30),
            user=user,
            session_id=1,
        )

    original = settings.auth_cookie_secure
    settings.auth_cookie_secure = True
    try:
        response = Response()
        set_auth_cookies(response, pair)
        raw = "|".join(v.decode() for k, v in response.raw_headers if k.decode() == "set-cookie")
        access = _cookie(raw, settings.access_cookie_name)
        assert access and _has_attr(access, "Secure")
        refresh = _cookie(raw, settings.refresh_cookie_name)
        assert refresh and _has_attr(refresh, "Secure")
    finally:
        settings.auth_cookie_secure = original


@pytest.mark.asyncio
async def test_samesite_lax_on_cookies() -> None:
    await _create_user("ui_ss", "ui_ss@example.com")
    async with await _open_client() as client:
        response = await _login(client, "ui_ss")
        raw = "|".join(_set_cookie_headers(response))
        access = _cookie(raw, settings.access_cookie_name)
        assert access and _has_attr(access, "samesite=lax")


@pytest.mark.asyncio
async def test_bearer_api_still_available_after_browser_ui() -> None:
    await _create_user("ui_api", "ui_api@example.com")
    factory = get_session_factory()
    async with factory() as session:
        from app.services.auth_service import AuthService

        result = await AuthService(session).login(
            "ui_api", PASSWORD, "test-agent", "127.0.0.1"
        )
        access = result.access_token

    async with await _open_client() as client:
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert response.status_code == 200
        assert response.json()["username"] == "ui_api"
