"""Shared authentication helpers for test modules.

Provides a unified way to obtain authenticated AsyncClient instances for both
API (Bearer token) and browser (cookie) flows. Seeds built-in roles/permissions
on first use per test run.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.password import hash_password
from app.core.config import settings
from app.db.session import get_session_factory
from app.main import app
from app.models.auth import Role, User, UserRole
from app.services.role_service import RoleService

PASSWORD = "Valid-Password-7!"


async def seed_builtins() -> None:
    """Seed built-in roles and permissions (idempotent; safe after TRUNCATE).

    The test conftest truncates auth tables per-test, so this must run for every
    authenticated client to repopulate roles/permissions.
    """
    factory = get_session_factory()
    async with factory() as session:
        await RoleService(session).seed_builtins()


async def _seed_builtin_auth() -> None:
    """Seed built-in roles and permissions (idempotent)."""
    await seed_builtins()


async def _create_user(
    username: str,
    email: str,
    *,
    is_superuser: bool = False,
    roles: tuple[str, ...] = (),
    password: str = PASSWORD,
) -> int:
    """Create a user with optional built-in role assignments. Returns the user id."""
    factory = get_session_factory()
    async with factory() as session:
        user = User(
            username=username,
            email=email,
            full_name="Test User",
            password_hash=hash_password(password),
            is_active=True,
            is_verified=True,
            is_superuser=is_superuser,
        )
        session.add(user)
        await session.flush()
        for role_name in roles:
            role = await session.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                raise RuntimeError(f"Role '{role_name}' not seeded")
            session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()
        return user.id


async def create_test_user(
    *,
    is_superuser: bool = False,
    roles: tuple[str, ...] = ("viewer",),
) -> tuple[int, str]:
    """Create a built-in-seeded user and return ``(user_id, username)``."""
    await seed_builtins()
    username = f"u_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"
    user_id = await _create_user(
        username, email, is_superuser=is_superuser, roles=roles
    )
    return user_id, username


async def _login_bearer(client: AsyncClient, identifier: str) -> str:
    """Log in via /api/v1/auth/login and return the access token."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username_or_email": identifier, "password": PASSWORD},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed for {identifier}: {resp.text}")
    return resp.json()["access_token"]


async def get_bearer_token(identifier: str) -> str:
    """Log in as ``identifier`` (username or email) and return the access token."""
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username_or_email": identifier, "password": PASSWORD},
        )
    finally:
        await client.aclose()
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed for {identifier}: {resp.text}")
    return resp.json()["access_token"]


async def _set_browser_cookie(client: AsyncClient, token: str) -> None:
    """Persist the access token into the client's browser (HttpOnly) cookie jar.

    httpx's cookie jar only transmits cookies that were populated via the
    ``Set-Cookie`` response header (a manually-set host-only cookie with an
    empty domain is never sent). We therefore synthesize a response whose
    request URL matches the test base URL and feed it through
    ``extract_cookies`` so the cookie is stored with a correct, matchable
    domain.
    """
    from httpx import Request, Response

    request = Request("GET", f"{client.base_url}/")
    set_cookie = (
        f"{settings.access_cookie_name}={token}; Path=/; "
        "HttpOnly; SameSite=lax"
    )
    response = Response(200, headers={"set-cookie": set_cookie}, request=request)
    client.cookies.extract_cookies(response)


async def _login_browser(client: AsyncClient, identifier: str) -> None:
    """Log in via the browser /login form and set auth cookies.

    The /api/v1/auth/login endpoint returns the access token in the body but
    does not set browser cookies. We obtain the bearer token and then place it
    into the client's cookie jar via a synthesized ``Set-Cookie`` response so
    that HTML (cookie-authenticated) routes resolve the same principal.
    """
    token = await _login_bearer(client, identifier)
    await _set_browser_cookie(client, token)


@asynccontextmanager
async def auth_client(
    *,
    role: str | None = None,
    is_superuser: bool = False,
    browser: bool = False,
) -> AsyncClient:
    """Return an authenticated AsyncClient.

    Args:
        role: Built-in role name (\"viewer\", \"analyst\", \"admin\", \"super_admin\").
        is_superuser: If True, create a superuser (is_superuser flag).
        browser: If True, also set browser auth cookies (for HTML page tests).

    The client has the Authorization header pre-set with a Bearer token.
    When browser=True, the access cookie is also set for cookie-based auth.
    """
    await _seed_builtin_auth()

    username = f"user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"

    if is_superuser:
        await _create_user(username, email, is_superuser=True)
    elif role:
        await _create_user(username, email, roles=(role,))
    else:
        await _create_user(username, email, roles=("viewer",))

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    token = await _login_bearer(client, username)
    client.headers["Authorization"] = f"Bearer {token}"

    if browser:
        await _login_browser(client, username)

    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def viewer_client(browser: bool = False) -> AsyncClient:
    async with auth_client(role="viewer", browser=browser) as c:
        yield c


@asynccontextmanager
async def analyst_client(browser: bool = False) -> AsyncClient:
    async with auth_client(role="analyst", browser=browser) as c:
        yield c


@asynccontextmanager
async def admin_client(browser: bool = False) -> AsyncClient:
    async with auth_client(role="admin", browser=browser) as c:
        yield c


@asynccontextmanager
async def superuser_client(browser: bool = False) -> AsyncClient:
    async with auth_client(is_superuser=True, browser=browser) as c:
        yield c


async def make_api_client(
    *,
    role: str | None = None,
    is_superuser: bool = False,
    browser: bool = False,
) -> AsyncClient:
    """Create a Bearer-authenticated client (non-context-manager variant).

    Caller is responsible for closing the client. When ``browser`` is set the
    access token is also placed in the auth HttpOnly cookie so HTML routes
    resolve the same user.
    """
    await _seed_builtin_auth()
    username = f"user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"

    if is_superuser:
        await _create_user(username, email, is_superuser=True)
    elif role:
        await _create_user(username, email, roles=(role,))
    else:
        await _create_user(username, email, roles=("viewer",))

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    token = await _login_bearer(client, username)
    client.headers["Authorization"] = f"Bearer {token}"
    if browser:
        await _set_browser_cookie(client, token)
    return client