from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    AuthorizationError,
    InactiveUserError,
    SessionBindingError,
    TokenClaimsError,
)
from app.auth.jwt import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    JWTKeyRing,
    TokenClaims,
    decode_access_token,
)
from app.models.auth import User, UserSession


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: int
    session_id: int
    roles: frozenset[str]
    permissions: frozenset[str]
    claims: TokenClaims


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user: User
    principal: AuthenticatedPrincipal


def extract_bearer_token(authorization: str) -> str:
    scheme, separator, token = authorization.strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise TokenClaimsError("Authorization value must contain a bearer token")
    if " " in token.strip():
        raise TokenClaimsError("Bearer token is malformed")
    return token.strip()


def extract_principal(
    token: str,
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    clock_skew_seconds: int | None = None,
) -> AuthenticatedPrincipal:
    claims = decode_access_token(
        token,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        clock_skew_seconds=clock_skew_seconds,
    )
    try:
        user_id = int(claims.subject)
    except ValueError as exc:
        raise TokenClaimsError("Token subject must identify a user") from exc
    if user_id <= 0 or str(user_id) != claims.subject:
        raise TokenClaimsError("Token subject must identify a user")
    return AuthenticatedPrincipal(
        user_id=user_id,
        session_id=claims.session_id,
        roles=frozenset(claims.roles),
        permissions=frozenset(claims.permissions),
        claims=claims,
    )


async def resolve_current_user(
    db: AsyncSession,
    token: str,
    *,
    now: datetime | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    clock_skew_seconds: int | None = None,
) -> CurrentUser:
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    principal = extract_principal(
        token,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        clock_skew_seconds=clock_skew_seconds,
    )
    session = await db.get(UserSession, principal.session_id)
    if (
        session is None
        or session.user_id != principal.user_id
        or session.revoked_at is not None
        or session.expires_at <= checked_at
    ):
        raise SessionBindingError("Access token is not bound to an active session")
    user = await db.get(User, principal.user_id)
    if user is None:
        raise SessionBindingError("Access token user does not exist")
    if not user.is_active:
        raise InactiveUserError("User is inactive")
    return CurrentUser(user=user, principal=principal)


def has_role(principal: AuthenticatedPrincipal, role: str) -> bool:
    return role in principal.roles


def has_permission(principal: AuthenticatedPrincipal, permission: str) -> bool:
    return permission in principal.permissions


def require_roles(
    principal: AuthenticatedPrincipal,
    roles: list[str] | tuple[str, ...] | set[str],
    *,
    require_all: bool = True,
) -> AuthenticatedPrincipal:
    required = set(roles)
    authorized = (
        required.issubset(principal.roles) if require_all else bool(required & principal.roles)
    )
    if required and not authorized:
        raise AuthorizationError("Required role is missing")
    return principal


def require_permissions(
    principal: AuthenticatedPrincipal,
    permissions: list[str] | tuple[str, ...] | set[str],
    *,
    require_all: bool = True,
) -> AuthenticatedPrincipal:
    required = set(permissions)
    authorized = (
        required.issubset(principal.permissions)
        if require_all
        else bool(required & principal.permissions)
    )
    if required and not authorized:
        raise AuthorizationError("Required permission is missing")
    return principal
