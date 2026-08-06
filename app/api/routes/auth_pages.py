from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import (
    clear_auth_cookies,
    finalize_page_response,
    get_browser_user,
    get_safe_next,
    require_browser_user,
    set_auth_cookies,
    stage_csrf_token,
)
from app.auth import csrf
from app.auth.exceptions import AuthenticationError, PasswordPolicyError, TokenConfigurationError
from app.auth.password import PasswordHashError
from app.core.config import settings
from app.db.session import get_db_session
from app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    PasswordReuseError,
    SessionNotFoundError,
    mask_ip_address,
)
from app.services.preference_service import (
    DuplicateIdentityError,
    InvalidPreferenceError,
    PasswordConfirmationError,
    PreferenceService,
    PreferenceServiceError,
)

router = APIRouter()
env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)


def _render(template_name: str, context: dict[str, Any]) -> str:
    return env.get_template(template_name).render(**context)


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _theme(preference) -> str:
    theme = getattr(preference, "theme", None) if preference else None
    return "dark" if theme == "dark" else "light"


async def _get_preference(session: AsyncSession, user_id: int) -> Any:
    try:
        return await PreferenceService(session).get_preferences(user_id)
    except Exception:
        return None


async def _page_context(
    request: Request, session: AsyncSession, current, *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    preference = await _get_preference(session, current.user.id)
    return {
        "request": request,
        "browser_user": current,
        "csrf_token": stage_csrf_token(request),
        "body_theme": _theme(preference),
        "user": current.user,
        "roles": current.roles,
        "permissions": current.permissions,
        "preference": preference,
        "flash": flash,
    }


def _partial(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    html = _render(template_name, context)
    return finalize_page_response(request, HTMLResponse(content=html, status_code=status_code))


_CSRF_FAILED_FLASH: dict[str, str] = {"level": "danger", "message": "CSRF validation failed"}


async def _csrf_failed_partial(
    request: Request, session: AsyncSession, current, template_name: str, status_code: int
) -> HTMLResponse:
    ctx = await _page_context(request, session, current, flash=_CSRF_FAILED_FLASH)
    return _partial(request, template_name, ctx, status_code=status_code)


# --------------------------------------------------------------------------- login / logout


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str | None = None,
    expired: int = 0,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    if await get_browser_user(request, session) is not None:
        response = RedirectResponse(get_safe_next(next, "/dashboard"), status_code=302)
        return finalize_page_response(request, response)

    csrf_token = stage_csrf_token(request)
    page = HTMLResponse(
        content=_render(
            "auth/login.html",
            {
                "request": request,
                "csrf_token": csrf_token,
                "next": get_safe_next(next, "/dashboard"),
                "expired_message": (
                    "Your session has expired. Please sign in again." if expired else None
                ),
            },
        )
    )
    return finalize_page_response(request, page)


def _login_error_page(request: Request, form, message: str, status_code: int = 401) -> HTMLResponse:
    csrf_token = stage_csrf_token(request)
    page = HTMLResponse(
        content=_render(
            "auth/login.html",
            {
                "request": request,
                "csrf_token": csrf_token,
                "next": get_safe_next(str(form.get("next", "") or ""), "/dashboard"),
                "error": message,
            },
        ),
        status_code=status_code,
    )
    return finalize_page_response(request, page)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException:
        return _login_error_page(
            request, form, "Invalid form submission. Please try again.", 403
        )

    identifier = str(form.get("username_or_email", "")).strip()
    password = str(form.get("password", ""))
    remember_me = (
        str(form.get("remember_me", "")).strip().lower() in {"1", "true", "yes", "on"}
    )
    safe_next = get_safe_next(str(form.get("next") or ""), "/dashboard")
    user_agent = request.headers.get("user-agent")

    try:
        result = await AuthService(session).login(
            identifier, password, user_agent, _client_host(request), remember_me=remember_me
        )
    except InvalidCredentialsError:
        return _login_error_page(request, form, "Invalid username/email or password.", 401)
    except TokenConfigurationError:
        return _login_error_page(
            request,
            form,
            "Authentication is temporarily unavailable. Please try again later.",
            503,
        )

    response = RedirectResponse(safe_next, status_code=303)
    set_auth_cookies(response, result)
    return finalize_page_response(request, response)


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    form = await request.form()
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    access_token = request.cookies.get(settings.access_cookie_name)
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)

    if csrf_cookie:
        csrf.verify_request_csrf(request, form)
    elif refresh_token or access_token:
        raise HTTPException(403, "CSRF validation failed")

    if refresh_token:
        try:
            await AuthService(session).logout(refresh_token)
        except (AuthenticationError, TokenConfigurationError):
            pass

    response = RedirectResponse("/login", status_code=303)
    clear_auth_cookies(response)
    return response


# --------------------------------------------------------------------------- profile


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    return _partial(request, "auth/profile.html", await _page_context(request, session, current))


@router.post("/profile/update", response_class=HTMLResponse)
async def profile_update(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        return await _csrf_failed_partial(
            request, session, current, "auth/components/profile_form.html", exc.status_code
        )

    full_name = str(form.get("full_name", "")).strip() or None
    username = str(form.get("username", "")).strip() or None
    email = str(form.get("email", "")).strip() or None
    current_password = str(form.get("current_password", "")) or None

    flash: dict[str, str] = {"level": "success", "message": "Profile updated."}
    status_code = 200
    try:
        await PreferenceService(session).update_profile(
            current.user.id,
            full_name=full_name,
            email=email,
            username=username,
            current_password=current_password,
        )
        # Reload so the rendered profile reflects the change immediately.
        refreshed = await AuthService(session).get_current_user(
            request.cookies.get(settings.access_cookie_name, "")
        )
        current = refreshed
    except PasswordConfirmationError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 400
    except DuplicateIdentityError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    except PreferenceServiceError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404

    ctx = await _page_context(request, session, current, flash=flash)
    return _partial(request, "auth/components/profile_form.html", ctx, status_code=status_code)


# --------------------------------------------------------------------------- settings


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    return _partial(request, "auth/settings.html", await _page_context(request, session, current))


@router.post("/preferences/update", response_class=HTMLResponse)
async def preferences_update(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        return await _csrf_failed_partial(
            request, session, current, "auth/components/settings_form.html", exc.status_code
        )

    theme = str(form.get("theme", "")).strip() or None
    timezone = str(form.get("timezone", "")).strip() or None
    language = str(form.get("language", "")).strip() or None
    flash = {"level": "success", "message": "Preferences updated."}
    status_code = 200
    try:
        await PreferenceService(session).update_preferences(
            current.user.id, theme=theme, timezone=timezone, language=language
        )
    except InvalidPreferenceError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 422

    ctx = await _page_context(request, session, current, flash=flash)
    return _partial(request, "auth/components/settings_form.html", ctx, status_code=status_code)


# --------------------------------------------------------------------------- sessions


async def _sessions_context(session: AsyncSession, current) -> list[dict[str, Any]]:
    records = await AuthService(session).list_sessions(current.user.id)
    return [
        {
            "id": r.session.id,
            "created_at": r.created_at,
            "expires_at": r.session.expires_at,
            "last_seen_at": r.session.last_seen_at,
            "user_agent": r.session.user_agent,
            "ip_address": mask_ip_address(r.session.ip_address),
            "revoked": r.session.revoked_at is not None,
            "current_session": r.session.id == current.principal.session_id,
        }
        for r in records
    ]


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    ctx = await _page_context(request, session, current)
    ctx["sessions"] = await _sessions_context(session, current)
    ctx["current_session_id"] = current.principal.session_id
    return _partial(request, "auth/sessions.html", ctx)


@router.post("/sessions/{session_id}/revoke", response_class=HTMLResponse)
async def revoke_session(
    session_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await get_browser_user(request, session)
    if current is None:
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        return await _csrf_failed_partial(
            request, session, current, "auth/components/sessions_table.html", exc.status_code
        )
    status_code = 200
    try:
        result = await AuthService(session).revoke_session(current.user.id, session_id)
        flash: dict[str, str] = {"level": "success", "message": result.message}
    except SessionNotFoundError:
        flash = {"level": "danger", "message": "Session not found."}
        status_code = 404

    if session_id == current.principal.session_id:
        response = RedirectResponse("/login", status_code=303)
        clear_auth_cookies(response)
        return response

    ctx = await _page_context(request, session, current, flash=flash)
    ctx["sessions"] = await _sessions_context(session, current)
    ctx["current_session_id"] = current.principal.session_id
    return _partial(request, "auth/components/sessions_table.html", ctx, status_code=status_code)


@router.post("/sessions/revoke-all", response_class=HTMLResponse)
async def revoke_all_sessions(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    await require_browser_user(request, session)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        current = await get_browser_user(request, session)
        return await _csrf_failed_partial(
            request, session, current, "auth/components/sessions_table.html", exc.status_code
        )

    current = await require_browser_user(request, session)
    await AuthService(session).logout_all(current.user.id)
    response = RedirectResponse("/login", status_code=303)
    clear_auth_cookies(response)
    return response


# --------------------------------------------------------------------------- password


@router.post("/password/change", response_class=HTMLResponse)
async def password_change(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current = await require_browser_user(request, session)
    form = await request.form()
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        return await _csrf_failed_partial(
            request, session, current, "auth/components/password_form.html", exc.status_code
        )

    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))
    if new_password != confirm_password:
        ctx = await _page_context(
            request,
            session,
            current,
            flash={"level": "danger", "message": "New password and confirmation must match."},
        )
        return _partial(request, "auth/components/password_form.html", ctx, status_code=422)

    try:
        await AuthService(session).change_password(
            current.user.id, current_password, new_password
        )
    except InvalidCurrentPasswordError:
        ctx = await _page_context(
            request,
            session,
            current,
            flash={"level": "danger", "message": "Current password is invalid."},
        )
        return _partial(request, "auth/components/password_form.html", ctx, status_code=400)
    except PasswordReuseError as exc:
        ctx = await _page_context(
            request, session, current, flash={"level": "danger", "message": str(exc)}
        )
        return _partial(request, "auth/components/password_form.html", ctx, status_code=422)
    except PasswordPolicyError as exc:
        ctx = await _page_context(
            request,
            session,
            current,
            flash={"level": "danger", "message": " ".join(exc.violations)},
        )
        return _partial(request, "auth/components/password_form.html", ctx, status_code=422)
    except PasswordHashError:
        ctx = await _page_context(
            request,
            session,
            current,
            flash={
                "level": "danger",
                "message": "Could not update the password. Try again.",
            },
        )
        return _partial(request, "auth/components/password_form.html", ctx, status_code=503)

    response = RedirectResponse("/login", status_code=303)
    clear_auth_cookies(response)
    return response