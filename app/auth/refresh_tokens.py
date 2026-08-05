from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    RefreshTokenExpiredError,
    RefreshTokenReplayError,
    RefreshTokenRevokedError,
    SessionBindingError,
    TokenExpiredError,
)
from app.auth.jwt import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    JWTKeyRing,
    TokenClaims,
    create_refresh_token,
    decode_refresh_token,
    get_jwt_key_ring,
)
from app.core.config import settings
from app.models.auth import UserSession


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    token: str
    token_hash: str
    expires_at: datetime
    session: UserSession


def generate_refresh_nonce() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def create_refresh_session(
    db: AsyncSession,
    *,
    user_id: int,
    roles: list[str] | tuple[str, ...] | set[str] = (),
    permissions: list[str] | tuple[str, ...] | set[str] = (),
    user_agent: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
    expires_delta: timedelta | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
) -> IssuedRefreshToken:
    issued_at = (now or _utc_now()).astimezone(UTC).replace(microsecond=0)
    lifetime = expires_delta or timedelta(days=settings.refresh_token_days)
    if lifetime <= timedelta(0):
        raise RefreshTokenExpiredError("Refresh-token lifetime must be positive")
    resolved_key_ring = key_ring or get_jwt_key_ring()
    expires_at = issued_at + lifetime
    record = UserSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(generate_refresh_nonce()),
        expires_at=expires_at,
        last_seen_at=issued_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(record)
    await db.flush()

    token = create_refresh_token(
        subject=user_id,
        session_id=record.id,
        roles=roles,
        permissions=permissions,
        expires_delta=lifetime,
        now=issued_at,
        issuer=issuer,
        audience=audience,
        key_ring=resolved_key_ring,
    )
    record.refresh_token_hash = hash_refresh_token(token)
    await db.flush()
    return IssuedRefreshToken(
        token=token,
        token_hash=record.refresh_token_hash,
        expires_at=expires_at,
        session=record,
    )


async def _get_session_for_update(db: AsyncSession, session_id: int) -> UserSession | None:
    statement = select(UserSession).where(UserSession.id == session_id).with_for_update()
    return await db.scalar(statement)


async def validate_refresh_token(
    db: AsyncSession,
    token: str,
    *,
    now: datetime | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
    for_update: bool = False,
) -> tuple[UserSession, TokenClaims]:
    checked_at = (now or _utc_now()).astimezone(UTC)
    try:
        claims = decode_refresh_token(
            token,
            issuer=issuer,
            audience=audience,
            key_ring=key_ring,
        )
    except TokenExpiredError as exc:
        raise RefreshTokenExpiredError("Refresh token has expired") from exc

    record = (
        await _get_session_for_update(db, claims.session_id)
        if for_update
        else await db.get(UserSession, claims.session_id)
    )
    if record is None or str(record.user_id) != claims.subject:
        raise SessionBindingError("Refresh token is not bound to this session")
    if record.revoked_at is not None:
        raise RefreshTokenRevokedError("Refresh-token session is revoked")
    if record.expires_at <= checked_at:
        raise RefreshTokenExpiredError("Refresh-token session has expired")
    presented_hash = hash_refresh_token(token)
    if not hmac.compare_digest(record.refresh_token_hash, presented_hash):
        raise RefreshTokenReplayError("Refresh token has already been rotated")
    return record, claims


async def rotate_refresh_token(
    db: AsyncSession,
    token: str,
    *,
    roles: list[str] | tuple[str, ...] | set[str] | None = None,
    permissions: list[str] | tuple[str, ...] | set[str] | None = None,
    now: datetime | None = None,
    expires_delta: timedelta | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
) -> IssuedRefreshToken:
    rotated_at = (now or _utc_now()).astimezone(UTC).replace(microsecond=0)
    record, claims = await validate_refresh_token(
        db,
        token,
        now=rotated_at,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        for_update=True,
    )
    lifetime = expires_delta or timedelta(days=settings.refresh_token_days)
    replacement = create_refresh_token(
        subject=claims.subject,
        session_id=record.id,
        roles=claims.roles if roles is None else roles,
        permissions=claims.permissions if permissions is None else permissions,
        expires_delta=lifetime,
        now=rotated_at,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
    )
    replacement_hash = hash_refresh_token(replacement)
    record.refresh_token_hash = replacement_hash
    record.expires_at = rotated_at + lifetime
    record.last_seen_at = rotated_at
    await db.flush()
    return IssuedRefreshToken(
        token=replacement,
        token_hash=replacement_hash,
        expires_at=record.expires_at,
        session=record,
    )


async def revoke_refresh_token(
    db: AsyncSession,
    token: str,
    *,
    now: datetime | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    key_ring: JWTKeyRing | None = None,
) -> UserSession:
    revoked_at = (now or _utc_now()).astimezone(UTC)
    record, _ = await validate_refresh_token(
        db,
        token,
        now=revoked_at,
        issuer=issuer,
        audience=audience,
        key_ring=key_ring,
        for_update=True,
    )
    record.revoked_at = revoked_at
    await db.flush()
    return record
