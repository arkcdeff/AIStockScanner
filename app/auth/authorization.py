"""Reusable authorization helpers for API (Bearer) and browser (cookie) routes.

This module is the single source of truth for access-control decisions. It exposes
pure helper functions that inspect an :class:`AuthenticatedUser` and FastAPI/Browser
dependency factories that enforce authentication, permission, role, superuser, admin
and ownership rules across all protected modules.

Authorization events are recorded through a single audit helper. No permission
resolution happens here beyond what is already available on the
:class:`AuthenticatedUser` (roles and permissions are resolved eagerly by
``AuthService``), so there is no N+1 query risk. A request-scoped decision cache is
intentionally NOT used: every check is cheap once the principal is loaded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import (
    ForbiddenError,
    LoginRequiredError,
    get_browser_user,
    is_admin_user,
)
from app.api.dependencies import (
    bearer_scheme,
    get_current_authenticated_user,
    unauthorized_exception,
)
from app.db.session import get_db_session
from app.models.auth import AuditLog
from app.services.auth_service import AuthenticatedUser

DB_SESSION = Annotated[AsyncSession, Depends(get_db_session)]


# --------------------------------------------------------------------------- pure checks


def is_superuser(current: AuthenticatedUser) -> bool:
    """A user is unrestricted when flagged superuser or holding the super_admin role."""
    return bool(current.user.is_superuser or "super_admin" in current.roles)


def user_has_role(current: AuthenticatedUser, *roles: str) -> bool:
    if is_superuser(current):
        return True
    return any(role in current.roles for role in roles)


def user_has_permission(
    current: AuthenticatedUser, *codes: str, require_all: bool = False
) -> bool:
    if is_superuser(current):
        return True
    if require_all:
        return all(code in current.permissions for code in codes)
    return any(code in current.permissions for code in codes)


# --------------------------------------------------------------------------- # audit


async def record_authorization_event(
    session: AsyncSession,
    *,
    action: str,
    user_id: int | None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Persist an authorization audit event. Never includes secrets.

    Audit recording is strictly best-effort: a transient persistence failure
    (including test-environment event-loop mismatches) must never cause an
    authorization decision to fail. Permission checks remain fail-closed; only
    the audit trail is fail-open.
    """
    try:
        session.add(
            AuditLog(
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=None if entity_id is None else str(entity_id),
                metadata_json=metadata,
            )
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - audit must never break authz
        try:
            await session.rollback()
        except Exception:
            pass
        logger.warning(
            "Authorization audit event could not be persisted: action={action} "
            "user_id={user_id} error={error}",
            action=action,
            user_id=user_id,
            error=exc,
        )


async def _deny_permission(
    session: AsyncSession,
    current: AuthenticatedUser,
    *,
    required: list[str],
    metadata: dict[str, object] | None = None,
) -> None:
    base: dict[str, object] = {"required": required, "granted": list(current.permissions)}
    if metadata:
        base.update(metadata)
    await record_authorization_event(
        session,
        action="permission_denied",
        user_id=current.user.id,
        entity_type="authz",
        metadata=base,
    )


async def _deny_access(
    session: AsyncSession,
    current: AuthenticatedUser,
    *,
    entity_type: str = "authz",
    entity_id: str | int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    await record_authorization_event(
        session,
        action="access_denied",
        user_id=current.user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )


# --------------------------------------------------------------------------- # API / Bearer


async def resolve_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None, session: AsyncSession
) -> AuthenticatedUser:
    """Resolve the Bearer user or raise 401, auditing access_denied on failure."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        await record_authorization_event(
            session, action="access_denied", user_id=None, entity_type="api"
        )
        raise unauthorized_exception()
    try:
        return await get_current_authenticated_user(credentials, session)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            await record_authorization_event(
                session, action="access_denied", user_id=None, entity_type="api"
            )
        raise


def require_authenticated() -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency requiring a valid Bearer token."""

    async def dependency(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
        ],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        return await resolve_authenticated_user(credentials, session)

    return dependency


def require_permission(*codes: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: authenticated user with ANY of the given permissions."""

    async def dependency(
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        if not user_has_permission(current, *codes):
            await _deny_permission(session, current, required=list(codes))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return current

    return dependency


def require_any_permission(*codes: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    return require_permission(*codes)


def require_all_permissions(*codes: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: authenticated user with ALL of the given permissions."""

    async def dependency(
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        if not user_has_permission(current, *codes, require_all=True):
            await _deny_permission(
                session, current, required=list(codes), metadata={"mode": "all"}
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return current

    return dependency


def require_role(*roles: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: authenticated user who holds ANY of the given roles."""

    async def dependency(
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        if not user_has_role(current, *roles):
            await record_authorization_event(
                session,
                action="permission_denied",
                user_id=current.user.id,
                entity_type="role",
                metadata={"required": list(roles), "granted": list(current.roles)},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return current

    return dependency


def require_admin() -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: superuser or member of the super_admin role."""

    async def dependency(
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        if not is_admin_user(current):
            await record_authorization_event(
                session,
                action="permission_denied",
                user_id=current.user.id,
                entity_type="role",
                metadata={"reason": "administrator_required"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
            )
        return current

    return dependency


def require_superuser() -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: only the platform superuser."""

    async def dependency(
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        if not current.user.is_superuser:
            await record_authorization_event(
                session,
                action="permission_denied",
                user_id=current.user.id,
                entity_type="role",
                metadata={"reason": "superuser_required"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Superuser access required"
            )
        return current

    return dependency


# --------------------------------------------------------------------------- # ownership

OwnerResolver = Callable[[AsyncSession, int], Awaitable[int | None]]


def require_owner_or_admin(
    resolver: OwnerResolver, *, id_param: str = "resource_id"
) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """FastAPI dependency: resource owner OR an administrator.

    ``resolver`` returns the owner's ``user_id`` for the path resource, or None when
    the resource does not exist. When the current user is not the owner and not an
    administrator an ``access_denied`` event is recorded and access is forbidden.
    When an administrator accesses another user's resource an ``admin_override``
    event is recorded.
    """

    async def dependency(
        request: Request,
        current: Annotated[AuthenticatedUser, Depends(require_authenticated())],
        session: DB_SESSION,
    ) -> AuthenticatedUser:
        try:
            resource_id = int(request.path_params[id_param])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=404, detail="Resource not found") from None
        owner_id = await resolver(session, resource_id)
        if owner_id is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if current.user.id != owner_id and not is_admin_user(current):
            await _deny_access(
                session,
                current,
                entity_id=resource_id,
                metadata={"owner_id": owner_id},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        if current.user.id != owner_id:
            await record_authorization_event(
                session,
                action="admin_override",
                user_id=current.user.id,
                entity_type="resource",
                entity_id=resource_id,
                metadata={"owner_id": owner_id},
            )
        return current

    return dependency


# --------------------------------------------------------------------------- # browser


async def enforce_browser_permission(
    request: Request, session: AsyncSession, *codes: str
) -> AuthenticatedUser:
    """Enforce a browser (cookie) user with ANY of the given permissions.

    Raises :class:`LoginRequiredError` when unauthenticated (handled as a redirect
    to /login) and :class:`ForbiddenError` otherwise (renders the 403 page).
    """
    current = await get_browser_user(request, session)
    if current is None:
        await record_authorization_event(
            session,
            action="access_denied",
            user_id=None,
            entity_type="page",
            metadata={"path": request.url.path, "required": list(codes)},
        )
        raise LoginRequiredError(request.url.path)
    if not user_has_permission(current, *codes):
        await _deny_permission(
            session,
            current,
            required=list(codes),
            metadata={"path": request.url.path},
        )
        raise ForbiddenError()
    return current


def require_browser_permission(*codes: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """Dependency factory enforcing a browser permission on an HTML route."""

    async def dependency(
        request: Request, session: DB_SESSION
    ) -> AuthenticatedUser:
        return await enforce_browser_permission(request, session, *codes)

    return dependency


def require_browser_role(*roles: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """Dependency factory enforcing a browser role on an HTML route."""

    async def dependency(
        request: Request, session: DB_SESSION
    ) -> AuthenticatedUser:
        current = await get_browser_user(request, session)
        if current is None:
            await record_authorization_event(
                session,
                action="access_denied",
                user_id=None,
                entity_type="page",
                metadata={"path": request.url.path, "required": list(roles)},
            )
            raise LoginRequiredError(request.url.path)
        if not user_has_role(current, *roles):
            await record_authorization_event(
                session,
                action="permission_denied",
                user_id=current.user.id,
                entity_type="role",
                metadata={
                    "path": request.url.path,
                    "required": list(roles),
                    "granted": list(current.roles),
                },
            )
            raise ForbiddenError()
        return current

    return dependency