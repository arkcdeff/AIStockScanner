from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import generate_csrf_token, set_csrf_cookie
from app.auth.exceptions import AuthenticationError, TokenConfigurationError
from app.core.config import settings
from app.services.auth_service import (
    AuthenticatedUser,
    AuthService,
    AuthServiceError,
    TokenPair,
)


class LoginRequiredError(Exception):
    """Raised when a protected page needs an authenticated browser user."""

    def __init__(self, next_path: str = "/dashboard", *, expired: bool = False) -> None:
        self.next_path = next_path
        self.expired = expired


class ForbiddenError(Exception):
    """Raised when an authenticated browser user lacks admin rights."""


def get_safe_next(value: str | None, default: str = "/dashboard") -> str:
    """Return only same-origin relative paths to prevent open redirects."""
    if not value:
        return default
    candidate = value.strip()
    if not candidate.startswith("/"):
        return default
    if candidate.startswith(("//", "/\\")) or "\\" in candidate:
        return default
    return candidate


def is_admin_user(current: AuthenticatedUser) -> bool:
    return bool(current.user.is_superuser or "super_admin" in current.roles)


def _cookie_settings() -> dict[str, object]:
    samesite = settings.auth_cookie_samesite.lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    return {
        "secure": settings.auth_cookie_secure,
        "samesite": samesite,
        "path": "/",
    }


def set_auth_cookies(response: Response, result: TokenPair) -> None:
    """Persist access and refresh tokens in secure HttpOnly cookies."""
    now = datetime.now(UTC)
    access_max_age = max(1, int((result.access_expires_at - now).total_seconds()))
    refresh_max_age = max(1, int((result.refresh_expires_at - now).total_seconds()))
    response.set_cookie(
        key=settings.access_cookie_name,
        value=result.access_token,
        httponly=True,
        max_age=access_max_age,
        **_cookie_settings(),
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=result.refresh_token,
        httponly=True,
        max_age=refresh_max_age,
        **_cookie_settings(),
    )


def clear_auth_cookies(response: Response) -> None:
    """Expire browser auth and CSRF cookies."""
    for name in (
        settings.access_cookie_name,
        settings.refresh_cookie_name,
        settings.csrf_cookie_name,
    ):
        response.delete_cookie(name, **_cookie_settings())


async def get_browser_user(
    request: Request, session: AsyncSession
) -> AuthenticatedUser | None:
    """Resolve the browser user from cookies, refreshing tokens when practical."""
    access_token = request.cookies.get(settings.access_cookie_name)
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not access_token and not refresh_token:
        return None

    service = AuthService(session)
    if access_token:
        try:
            return await service.get_current_user(access_token)
        except (AuthenticationError, AuthServiceError, TokenConfigurationError):
            pass

    if refresh_token:
        user_agent = request.headers.get("user-agent")
        ip_address = request.client.host if request.client else None
        try:
            result = await service.refresh(
                refresh_token,
                user_agent,
                ip_address,
            )
        except (AuthenticationError, AuthServiceError, TokenConfigurationError):
            return None
        current = await service.get_current_user(result.access_token)
        request.state.refreshed_tokens = result
        return current
    return None


async def require_browser_user(
    request: Request, session: AsyncSession
) -> AuthenticatedUser:
    current = await get_browser_user(request, session)
    if current is None:
        raise LoginRequiredError(request.url.path, expired=True)
    return current


async def require_browser_admin(request: Request, session: AsyncSession) -> AuthenticatedUser:
    current = await require_browser_user(request, session)
    if not is_admin_user(current):
        raise ForbiddenError()
    return current


def stage_csrf_token(request: Request) -> str:
    """Generate a CSRF token, stage it on the request, and return it for templates."""
    token = generate_csrf_token()
    request.state.csrf_token = token
    return token


def finalize_page_response(request: Request, response: Response) -> Response:
    """Attach refreshed auth cookies and the staged CSRF cookie to a page response."""
    refreshed = getattr(request.state, "refreshed_tokens", None)
    if refreshed is not None:
        set_auth_cookies(response, refreshed)
    token = getattr(request.state, "csrf_token", None)
    if token:
        set_csrf_cookie(response, token)
    return response
