from __future__ import annotations

import hmac
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    RefreshTokenExpiredError,
    SessionBindingError,
)
from app.auth.jwt import create_access_token, decode_refresh_token
from app.auth.password import hash_password, verify_password
from app.auth.refresh_tokens import (
    create_refresh_session,
    hash_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from app.auth.security import AuthenticatedPrincipal, resolve_current_user
from app.core.config import settings
from app.models.auth import AuditLog, User, UserSession
from app.repositories.auth import (
    AuditLogRepository,
    UserRepository,
    UserSessionRepository,
)

_BOOTSTRAP_ADMIN_LOCK_ID = 161603
_STANDARD_SESSION_LIFETIME = timedelta(days=1)


class AuthServiceError(Exception):
    """Base exception for authentication service failures."""


class InvalidCredentialsError(AuthServiceError):
    """Raised for all invalid login identities, passwords, and inactive users."""


class DuplicateIdentityError(AuthServiceError):
    """Raised when a username or email already exists."""


class BootstrapAdminExistsError(AuthServiceError):
    """Raised when the one-time superuser bootstrap has already been completed."""


class InvalidBootstrapInputError(AuthServiceError):
    """Raised when bootstrap identity data is invalid."""


class SessionNotFoundError(AuthServiceError):
    """Raised when a session is missing or belongs to another user."""


class InvalidCurrentPasswordError(AuthServiceError):
    """Raised when password confirmation against the stored hash fails."""


class PasswordReuseError(AuthServiceError):
    """Raised when a password change attempts to reuse the current password."""


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    user: User
    session_id: int


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    principal: AuthenticatedPrincipal
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session: UserSession
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class SessionRevocation:
    session_id: int
    newly_revoked: bool


def mask_ip_address(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "***"
    if isinstance(address, ipaddress.IPv4Address):
        octets = str(address).split(".")
        return ".".join([*octets[:3], "xxx"])
    groups = address.exploded.split(":")
    return ":".join([*groups[:3], "*"])


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.sessions = UserSessionRepository(session)
        self.audit_logs = AuditLogRepository(session)

    @staticmethod
    def _access_names(user: User) -> tuple[tuple[str, ...], tuple[str, ...]]:
        roles = tuple(sorted({role.name for role in user.roles}))
        permissions = tuple(
            sorted({permission.code for role in user.roles for permission in role.permissions})
        )
        return roles, permissions

    async def _audit(
        self,
        action: str,
        *,
        user_id: int | None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLog:
        return await self.audit_logs.add(
            AuditLog(
                user_id=user_id,
                action=action,
                entity_type="user" if user_id is not None else "authentication",
                entity_id=str(user_id) if user_id is not None else None,
                metadata_json=metadata,
            )
        )

    async def authenticate_user(self, identifier: str, password: str) -> User | None:
        user = await self.users.get_by_identifier_with_access(identifier.strip())
        if user is None:
            verify_password(password, "invalid-password-hash")
            return None
        password_valid = verify_password(password, user.password_hash)
        if not password_valid or not user.is_active:
            return None
        return user

    async def login(
        self,
        identifier: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
        *,
        remember_me: bool = False,
    ) -> TokenPair:
        user = await self.authenticate_user(identifier, password)
        if user is None:
            await self._audit(
                "login_failed",
                user_id=None,
                metadata={"reason": "invalid_credentials"},
            )
            await self.session.commit()
            raise InvalidCredentialsError("Invalid username/email or password")

        issued_at = datetime.now(UTC).replace(microsecond=0)
        roles, permissions = self._access_names(user)
        refresh_lifetime = (
            timedelta(days=settings.refresh_token_days)
            if remember_me
            else _STANDARD_SESSION_LIFETIME
        )
        try:
            refresh = await create_refresh_session(
                self.session,
                user_id=user.id,
                roles=roles,
                permissions=permissions,
                user_agent=(user_agent[:512] if user_agent else None),
                ip_address=(ip_address[:45] if ip_address else None),
                now=issued_at,
                expires_delta=refresh_lifetime,
            )
            access_token = create_access_token(
                subject=user.id,
                session_id=refresh.session.id,
                roles=roles,
                permissions=permissions,
                now=issued_at,
            )
            access_expires_at = issued_at + timedelta(minutes=settings.access_token_minutes)
            user.last_login_at = issued_at
            await self._audit(
                "login_success",
                user_id=user.id,
                metadata={
                    "session_id": refresh.session.id,
                    "remember_me": remember_me,
                },
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh.token,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh.expires_at,
            user=user,
            session_id=refresh.session.id,
        )

    async def refresh(
        self,
        refresh_token: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> TokenPair:
        refreshed_at = datetime.now(UTC).replace(microsecond=0)
        try:
            record, claims = await validate_refresh_token(
                self.session,
                refresh_token,
                now=refreshed_at,
            )
            user = await self.users.get_with_access(record.user_id)
            if user is None or not user.is_active:
                raise InvalidCredentialsError("Invalid authentication credentials")
            roles, permissions = self._access_names(user)
            remaining_lifetime = claims.expires_at - refreshed_at
            if remaining_lifetime <= timedelta(0):
                raise RefreshTokenExpiredError("Refresh token has expired")
            rotated = await rotate_refresh_token(
                self.session,
                refresh_token,
                roles=roles,
                permissions=permissions,
                now=refreshed_at,
                expires_delta=remaining_lifetime,
            )
            if user_agent:
                rotated.session.user_agent = user_agent[:512]
            if ip_address:
                rotated.session.ip_address = ip_address[:45]
            access_token = create_access_token(
                subject=user.id,
                session_id=rotated.session.id,
                roles=roles,
                permissions=permissions,
                now=refreshed_at,
            )
            access_expires_at = refreshed_at + timedelta(minutes=settings.access_token_minutes)
            await self._audit(
                "token_refresh",
                user_id=user.id,
                metadata={"session_id": rotated.session.id},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return TokenPair(
            access_token=access_token,
            refresh_token=rotated.token,
            access_expires_at=access_expires_at,
            refresh_expires_at=rotated.expires_at,
            user=user,
            session_id=rotated.session.id,
        )

    async def logout(self, refresh_token: str) -> SessionRevocation:
        logged_out_at = datetime.now(UTC)
        try:
            claims = decode_refresh_token(refresh_token)
            record = await self.sessions.get_for_update(claims.session_id)
            if record is None or str(record.user_id) != claims.subject:
                raise SessionBindingError("Refresh token is not bound to this session")
            if not hmac.compare_digest(
                record.refresh_token_hash,
                hash_refresh_token(refresh_token),
            ):
                raise SessionBindingError("Refresh token is not current for this session")
            if record.expires_at <= logged_out_at:
                raise RefreshTokenExpiredError("Refresh-token session has expired")
            if record.revoked_at is not None:
                session_id = record.id
                await self.session.rollback()
                return SessionRevocation(session_id=session_id, newly_revoked=False)
            record = await revoke_refresh_token(
                self.session,
                refresh_token,
                now=logged_out_at,
            )
            await self._audit(
                "logout",
                user_id=record.user_id,
                metadata={"session_id": record.id},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return SessionRevocation(session_id=record.id, newly_revoked=True)

    async def logout_all(self, user_id: int) -> int:
        logged_out_at = datetime.now(UTC)
        try:
            revoked_count = await self.sessions.revoke_all_for_user(
                user_id,
                revoked_at=logged_out_at,
            )
            await self._audit(
                "logout_all",
                user_id=user_id,
                metadata={"revoked_sessions": revoked_count},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return revoked_count

    async def get_current_user(self, token: str) -> AuthenticatedUser:
        current = await resolve_current_user(self.session, token)
        user = await self.users.get_with_access(current.user.id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid authentication credentials")
        roles, permissions = self._access_names(user)
        return AuthenticatedUser(
            user=user,
            principal=current.principal,
            roles=roles,
            permissions=permissions,
        )

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> int:
        # Policy: a successful password change revokes every session, including current.
        user = await self.users.get(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid authentication credentials")
        if not verify_password(current_password, user.password_hash):
            raise InvalidCurrentPasswordError("Current password is invalid")
        if verify_password(new_password, user.password_hash):
            raise PasswordReuseError("New password must differ from the current password")

        new_hash = hash_password(new_password)
        changed_at = datetime.now(UTC)
        try:
            user.password_hash = new_hash
            revoked_count = await self.sessions.revoke_all_for_user(
                user_id,
                revoked_at=changed_at,
            )
            await self._audit(
                "password_changed",
                user_id=user_id,
                metadata={
                    "revoked_sessions": revoked_count,
                    "session_policy": "revoke_all",
                },
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return revoked_count

    async def list_sessions(self, user_id: int) -> list[SessionInfo]:
        records = await self.sessions.list_for_user(user_id)
        created_at = await self.audit_logs.session_created_at_map(
            user_id,
            [record.id for record in records],
        )
        return [
            SessionInfo(session=record, created_at=created_at.get(record.id)) for record in records
        ]

    async def revoke_session(self, user_id: int, session_id: int) -> SessionRevocation:
        revoked_at = datetime.now(UTC)
        try:
            record = await self.sessions.get_for_update(session_id)
            if record is None or record.user_id != user_id:
                raise SessionNotFoundError("Session not found")
            if record.revoked_at is not None:
                revoked_session_id = record.id
                await self.session.rollback()
                return SessionRevocation(
                    session_id=revoked_session_id,
                    newly_revoked=False,
                )
            record.revoked_at = revoked_at
            await self._audit(
                "session_revoked",
                user_id=user_id,
                metadata={"session_id": session_id},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return SessionRevocation(session_id=session_id, newly_revoked=True)

    async def create_bootstrap_admin(
        self,
        *,
        username: str,
        email: str,
        full_name: str | None,
        password: str,
    ) -> User:
        normalized_username = username.strip()
        normalized_email = email.strip().lower()
        normalized_name = full_name.strip() if full_name else None
        if not normalized_username or len(normalized_username) > 64:
            raise InvalidBootstrapInputError("Username must contain 1 to 64 characters")
        if (
            not normalized_email
            or len(normalized_email) > 320
            or "@" not in normalized_email
            or normalized_email.startswith("@")
            or normalized_email.endswith("@")
        ):
            raise InvalidBootstrapInputError("A valid email address is required")
        if normalized_name and len(normalized_name) > 160:
            raise InvalidBootstrapInputError("Full name cannot exceed 160 characters")

        password_hash = hash_password(password)
        try:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _BOOTSTRAP_ADMIN_LOCK_ID},
            )
            if (
                await self.users.get_by_username(normalized_username) is not None
                or await self.users.get_by_email_case_insensitive(normalized_email) is not None
            ):
                raise DuplicateIdentityError("Username or email already exists")
            if await self.users.superuser_exists():
                raise BootstrapAdminExistsError("A bootstrap superuser already exists")

            user = User(
                username=normalized_username,
                email=normalized_email,
                full_name=normalized_name,
                password_hash=password_hash,
                is_active=True,
                is_verified=True,
                is_superuser=True,
            )
            await self.users.add(user)
            await self._audit(
                "admin_created",
                user_id=user.id,
                metadata={"method": "bootstrap_cli"},
            )
            await self.session.commit()
        except (DuplicateIdentityError, BootstrapAdminExistsError):
            await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateIdentityError("Username or email already exists") from exc
        except Exception:
            await self.session.rollback()
            raise
        return user
