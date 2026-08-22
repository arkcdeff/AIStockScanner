"""Sprint 19.1B UI verification tests.

Tests cover:
- Login page renders with design system CSS
- Dashboard renders with shell layout (sidebar + topbar)
- Sidebar grouped navigation sections
- Sidebar permission behavior (admin-only links hidden from non-admin)
- CSRF preserved on migrated pages
- No unfinished trading links
- Dark mode data-bs-theme attribute applied
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.password import hash_password
from app.core.config import settings
from app.db.session import get_session_factory
from app.main import app
from app.models.auth import Role, User, UserRole
from app.services.role_service import RoleService

PASSWORD = "Valid-Password-7!"


async def _open_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_and_create_user(
    username: str,
    email: str,
    *,
    is_superuser: bool = False,
    roles: tuple[str, ...] = ("viewer",),
    password: str = PASSWORD,
) -> int:
    """Seed built-in roles, create a user, and assign roles."""
    factory = get_session_factory()
    async with factory() as session:
        await RoleService(session).seed_builtins()
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
        await session.flush()
        for role_name in roles:
            role = await session.scalar(select(Role).where(Role.name == role_name))
            if role is not None:
                session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()
        return user.id


async def _csrf_for(client: AsyncClient) -> str:
    await client.get("/login")
    return client.cookies.get(settings.csrf_cookie_name) or ""


async def _login(client: AsyncClient, identifier: str, *, password: str = PASSWORD):
    token = await _csrf_for(client)
    await client.post(
        "/login",
        data={
            "username_or_email": identifier,
            "password": password,
            "_csrf_token": token,
            "next": "/dashboard",
        },
    )


# ---------- Login page ----------


@pytest.mark.asyncio
async def test_login_page_uses_design_system() -> None:
    async with await _open_client() as client:
        page = await client.get("/login")
        assert page.status_code == 200
        assert "auth.css" in page.text
        assert "auth-container" in page.text
        assert "auth-card" in page.text
        assert "auth-brand" in page.text
        assert "Sign in" in page.text


@pytest.mark.asyncio
async def test_login_page_has_csrf_token() -> None:
    async with await _open_client() as client:
        page = await client.get("/login")
        assert page.status_code == 200
        assert "_csrf_token" in page.text


# ---------- Dashboard / Shell layout ----------


@pytest.mark.asyncio
async def test_dashboard_renders_shell_layout() -> None:
    await _seed_and_create_user("ui_shell", "ui_shell@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_shell")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "app-sidebar" in resp.text
        assert "app-topbar" in resp.text
        assert "app-main" in resp.text
        assert "design-system.css" in resp.text
        assert "shell.js" in resp.text


@pytest.mark.asyncio
async def test_dashboard_has_sidebar_navigation() -> None:
    await _seed_and_create_user("ui_nav", "ui_nav@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_nav")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "sidebar-menu-label" in resp.text
        assert "/dashboard" in resp.text
        assert "/market-data" in resp.text
        assert "/scanner-library" in resp.text
        assert "/stocks" in resp.text
        assert "/alerts" in resp.text
        assert "/notifications" in resp.text
        assert "/pipeline" in resp.text


@pytest.mark.asyncio
async def test_dashboard_has_theme_toggle() -> None:
    await _seed_and_create_user("ui_theme", "ui_theme@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_theme")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "toggleTheme" in resp.text
        assert "themeIcon" in resp.text


@pytest.mark.asyncio
async def test_dashboard_has_sidebar_toggle() -> None:
    await _seed_and_create_user("ui_stoggle", "ui_stoggle@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_stoggle")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "toggleSidebar" in resp.text
        assert "sidebarToggleIcon" in resp.text


# ---------- Sidebar permission behavior ----------


@pytest.mark.asyncio
async def test_admin_sidebar_shows_admin_section() -> None:
    await _seed_and_create_user(
        "ui_admin5", "ui_admin5@example.com", is_superuser=True
    )
    async with await _open_client() as client:
        await _login(client, "ui_admin5")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "/admin/users" in resp.text
        assert "/admin/roles" in resp.text


@pytest.mark.asyncio
async def test_normal_user_sidebar_hides_admin_section() -> None:
    await _seed_and_create_user("ui_noadmin", "ui_noadmin@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_noadmin")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "/admin/users" not in resp.text
        assert "/admin/roles" not in resp.text


@pytest.mark.asyncio
async def test_viewer_sidebar_shows_market_intelligence() -> None:
    """Viewer role HAS market_intelligence.read, so sidebar link IS shown."""
    await _seed_and_create_user("ui_vwmint", "ui_vwmint@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_vwmint")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert 'href="/market-intelligence"' in resp.text


@pytest.mark.asyncio
async def test_viewer_sidebar_shows_backtests() -> None:
    """Viewer role HAS backtests.read, so sidebar link IS shown."""
    await _seed_and_create_user("ui_vwbt", "ui_vwbt@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_vwbt")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert 'href="/backtests"' in resp.text


# ---------- CSRF preserved ----------


@pytest.mark.asyncio
async def test_csrf_token_present_on_protected_pages() -> None:
    await _seed_and_create_user("ui_csrf", "ui_csrf@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_csrf")
        for path in ["/dashboard", "/stocks", "/alerts"]:
            resp = await client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"
            has_csrf = "_csrf_token" in resp.text or "csrf" in resp.text.lower()
            assert has_csrf, f"CSRF missing on {path}"


# ---------- No unfinished trading links ----------


@pytest.mark.asyncio
async def test_no_trading_links_in_sidebar() -> None:
    await _seed_and_create_user(
        "ui_notrade", "ui_notrade@example.com", is_superuser=True
    )
    async with await _open_client() as client:
        await _login(client, "ui_notrade")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        text_lower = resp.text.lower()
        assert 'href="/trading"' not in text_lower
        assert 'href="/positions"' not in text_lower
        assert 'href="/execution"' not in text_lower


# ---------- Dark mode attribute ----------


@pytest.mark.asyncio
async def test_body_has_data_bs_theme_attribute() -> None:
    await _seed_and_create_user("ui_dark", "ui_dark@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_dark")
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "data-bs-theme" in resp.text


# ---------- Pages use shell layout ----------


@pytest.mark.asyncio
async def test_key_pages_use_shell_layout() -> None:
    """Verify key pages render with shell layout components."""
    await _seed_and_create_user("ui_pages", "ui_pages@example.com")
    async with await _open_client() as client:
        await _login(client, "ui_pages")
        pages = ["/dashboard", "/stocks", "/alerts"]
        for page in pages:
            resp = await client.get(page)
            assert resp.status_code == 200, f"{page} returned {resp.status_code}"
            assert "app-sidebar" in resp.text, f"app-sidebar missing on {page}"
            assert "app-topbar" in resp.text, f"app-topbar missing on {page}"
