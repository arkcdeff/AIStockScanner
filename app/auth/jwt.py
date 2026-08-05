from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt as pyjwt
from jwt import exceptions as jwt_exceptions

from app.auth.exceptions import (
    TokenClaimsError,
    TokenConfigurationError,
    TokenExpiredError,
    TokenSignatureError,
)
from app.core.config import settings

TokenType = Literal["access", "refresh"]
DEFAULT_ISSUER = "ai-stock-scanner"
DEFAULT_AUDIENCE = "ai-stock-scanner"
_ALLOWED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_REQUIRED_CLAIMS = (
    "sub",
    "session_id",
    "roles",
    "permissions",
    "iat",
    "nbf",
    "exp",
    "iss",
    "aud",
    "token_type",
)


@dataclass(frozen=True, slots=True)
class JWTKey:
    key_id: str
    secret: str


@dataclass(frozen=True, slots=True)
class JWTKeyRing:
    keys: tuple[JWTKey, ...]

    @property
    def active(self) -> JWTKey:
        return self.keys[0]

    def get(self, key_id: str) -> JWTKey | None:
        return next((key for key in self.keys if key.key_id == key_id), None)

    @classmethod
    def from_config(cls, value: str) -> JWTKeyRing:
        raw_value = value.strip()
        if not raw_value:
            raise TokenConfigurationError("JWT secret key is not configured")

        parts = [part.strip() for part in raw_value.split(",") if part.strip()]
        keys: list[JWTKey] = []
        for part in parts:
            if ":" in part:
                key_id, secret = (item.strip() for item in part.split(":", 1))
            elif len(parts) == 1:
                key_id, secret = "current", part
            else:
                raise TokenConfigurationError("Every rotated JWT key requires a key ID")
            if not key_id or not secret:
                raise TokenConfigurationError("JWT key IDs and secrets cannot be blank")
            if len(secret) < 32:
                raise TokenConfigurationError("JWT secrets must be at least 32 characters")
            if any(existing.key_id == key_id for existing in keys):
                raise TokenConfigurationError("JWT key IDs must be unique")
            keys.append(JWTKey(key_id=key_id, secret=secret))

        if not keys:
            raise TokenConfigurationError("JWT secret key is not configured")
        return cls(keys=tuple(keys))


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    session_id: int
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    issuer: str
    audience: str
    token_type: TokenType
    token_id: str | None = None


def get_jwt_key_ring(secret_config: str | None = None) -> JWTKeyRing:
    return JWTKeyRing.from_config(
        settings.jwt_secret_key.get_secret_value() if secret_config is None else secret_config
    )


def _get_algorithm(algorithm: str | None = None) -> str:
    selected = settings.jwt_algorithm if algorithm is None else algorithm
    if selected not in _ALLOWED_ALGORITHMS:
        raise TokenConfigurationError("Unsupported JWT algorithm")
    return selected


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_names(values: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    if not all(isinstance(value, str) and value for value in values):
        raise TokenClaimsError("Token roles and permissions must be non-empty strings")
    normalized = tuple(dict.fromkeys(values))
    return tuple(sorted(normalized)) if isinstance(values, set) else normalized


def _create_token(
    *,
    token_type: TokenType,
    subject: str | int,
    session_id: int,
    roles: list[str] | tuple[str, ...] | set[str],
    permissions: list[str] | tuple[str, ...] | set[str],
    expires_delta: timedelta,
    now: datetime | None,
    issuer: str,
    audience: str,
    key_ring: JWTKeyRing | None,
    algorithm: str | None,
) -> str:
    if not str(subject):
        raise TokenClaimsError("Token subject cannot be blank")
    if isinstance(session_id, bool) or session_id <= 0:
        raise TokenClaimsError("Token session ID must be a positive integer")
    if expires_delta <= timedelta(0):
        raise TokenClaimsError("Token lifetime must be positive")
    if not issuer or not audience:
        raise TokenClaimsError("Token issuer and audience cannot be blank")

    issued_at = (now or _utc_now()).astimezone(UTC).replace(microsecond=0)
    expires_at = issued_at + expires_delta
    ring = key_ring or get_jwt_key_ring()
    selected_algorithm = _get_algorithm(algorithm)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "session_id": session_id,
        "roles": list(_normalize_names(roles)),
        "permissions": list(_normalize_names(permissions)),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "iss": issuer,
        "aud": audience,
        "token_type": token_type,
    }
    if token_type == "refresh":
        payload["jti"] = secrets.token_urlsafe(32)
    return pyjwt.encode(
        payload,
        ring.active.secret,
        algorithm=selected_algorithm,
        headers={"kid": ring.active.key_id},
    )


def create_access_token(
    *,
    subject: str | int,
    session_id: int,
    roles: list[str] | tuple[str, ...] | set[str] = (),
    permissions: list[str] | tuple[str, ...] | set[str] = (),
    expires_delta: timedelta | None = None,
    now: datetime | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    algorithm: str | None = None,
) -> str:
    return _create_token(
        token_type="access",
        subject=subject,
        session_id=session_id,
        roles=roles,
        permissions=permissions,
        expires_delta=expires_delta or timedelta(minutes=settings.access_token_minutes),
        now=now,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        algorithm=algorithm,
    )


def create_refresh_token(
    *,
    subject: str | int,
    session_id: int,
    roles: list[str] | tuple[str, ...] | set[str] = (),
    permissions: list[str] | tuple[str, ...] | set[str] = (),
    expires_delta: timedelta | None = None,
    now: datetime | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    algorithm: str | None = None,
) -> str:
    return _create_token(
        token_type="refresh",
        subject=subject,
        session_id=session_id,
        roles=roles,
        permissions=permissions,
        expires_delta=expires_delta or timedelta(days=settings.refresh_token_days),
        now=now,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        algorithm=algorithm,
    )


def _validate_payload(payload: dict[str, Any], expected_type: TokenType) -> TokenClaims:
    subject = payload.get("sub")
    session_id = payload.get("session_id")
    roles = payload.get("roles")
    permissions = payload.get("permissions")
    token_type = payload.get("token_type")
    if not isinstance(subject, str) or not subject:
        raise TokenClaimsError("Invalid token subject")
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id <= 0:
        raise TokenClaimsError("Invalid token session ID")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise TokenClaimsError("Invalid token roles")
    if not isinstance(permissions, list) or not all(
        isinstance(permission, str) for permission in permissions
    ):
        raise TokenClaimsError("Invalid token permissions")
    if token_type != expected_type:
        raise TokenClaimsError(f"Expected a {expected_type} token")
    token_id = payload.get("jti")
    if expected_type == "refresh" and (not isinstance(token_id, str) or not token_id):
        raise TokenClaimsError("Refresh token is missing its identifier")

    try:
        issued_at = datetime.fromtimestamp(payload["iat"], UTC)
        not_before = datetime.fromtimestamp(payload["nbf"], UTC)
        expires_at = datetime.fromtimestamp(payload["exp"], UTC)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise TokenClaimsError("Invalid token timestamps") from exc
    if expires_at <= issued_at or not_before > expires_at:
        raise TokenClaimsError("Invalid token lifetime")

    issuer = payload.get("iss")
    audience = payload.get("aud")
    if not isinstance(issuer, str) or not isinstance(audience, str):
        raise TokenClaimsError("Invalid token issuer or audience")
    return TokenClaims(
        subject=subject,
        session_id=session_id,
        roles=tuple(dict.fromkeys(roles)),
        permissions=tuple(dict.fromkeys(permissions)),
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        issuer=issuer,
        audience=audience,
        token_type=expected_type,
        token_id=token_id if isinstance(token_id, str) else None,
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    algorithm: str | None = None,
    clock_skew_seconds: int | None = None,
) -> TokenClaims:
    if not token:
        raise TokenClaimsError("Token cannot be blank")
    ring = key_ring or get_jwt_key_ring()
    selected_algorithm = _get_algorithm(algorithm)
    try:
        header = pyjwt.get_unverified_header(token)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise TokenSignatureError("Token key ID is missing")
        key = ring.get(key_id)
        if key is None:
            raise TokenSignatureError("Token key is not trusted")
        payload = pyjwt.decode(
            token,
            key.secret,
            algorithms=[selected_algorithm],
            issuer=issuer,
            audience=audience,
            leeway=(
                settings.clock_skew_seconds if clock_skew_seconds is None else clock_skew_seconds
            ),
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except TokenSignatureError:
        raise
    except jwt_exceptions.ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except (jwt_exceptions.InvalidSignatureError, jwt_exceptions.InvalidAlgorithmError) as exc:
        raise TokenSignatureError("Token signature is invalid") from exc
    except jwt_exceptions.InvalidTokenError as exc:
        raise TokenClaimsError("Token claims are invalid") from exc
    return _validate_payload(payload, expected_type)


def decode_access_token(token: str, **kwargs: Any) -> TokenClaims:
    return decode_token(token, expected_type="access", **kwargs)


def decode_refresh_token(token: str, **kwargs: Any) -> TokenClaims:
    return decode_token(token, expected_type="refresh", **kwargs)
