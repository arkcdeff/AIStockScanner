from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password, validate_password
from app.models.auth import AuditLog, User, UserRole
from app.repositories.auth import (
    AuditLogRepository,
    RoleRepository,
    UserRepository,
    UserRoleRepository,
    UserSessionRepository,
)


class UserAdminError(Exception):
    """Base exception for admin user management failures."""


class UserNotFoundError(UserAdminError):
    """Raised when a target user does not exist."""


class DuplicateIdentityError(UserAdminError):
    """Raised when a username or email already exists."""


class LastSuperuserError(UserAdminError):
    """Raised when an action would disable or remove the last active superuser."""


class SelfDeletionError(UserAdminError):
    """Raised when an admin attempts to delete their own account."""


class LocksuperuserTransitionError(UserAdminError):
    """Raised when an admin removes their own superuser privileges."""


@dataclass(frozen=True, slots=True)
class PasswordResetResult:
    user_id: int
    password_generated: bool
    temporary_password: str | None
    revoked_sessions: int


class UserAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.user_roles = UserRoleRepository(session)
        self.sessions = UserSessionRepository(session)
        self.audit_logs = AuditLogRepository(session)

    async def _audit(
        self, action: str, *, user_id: int | None, metadata: dict[str, object] | None
    ) -> AuditLog:
        return await self.audit_logs.add(
            AuditLog(
                user_id=user_id,
                action=action,
                entity_type="user",
                entity_id=None,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _access_names(user: User) -> tuple[list[str], list[str]]:
        roles = sorted({role.name for role in user.roles})
        permissions = sorted(
            {permission.code for role in user.roles for permission in role.permissions}
        )
        return roles, permissions

    async def list_users(
        self,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        is_verified: bool | None = None,
        is_superuser: bool | None = None,
        role_id: int | None = None,
        sort: str = "username",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[User], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        users, total = await self.users.search_paginated(
            search=search,
            is_active=is_active,
            is_verified=is_verified,
            is_superuser=is_superuser,
            role_id=role_id,
            sort=sort,
            offset=offset,
            limit=page_size,
        )
        return users, total

    async def _load_with_roles(self, user_id: int) -> User | None:
        return await self.users.get_with_access(user_id)

    async def get_user(self, user_id: int) -> User:
        user = await self._load_with_roles(user_id)
        if user is None:
            raise UserNotFoundError("User not found")
        return user

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        full_name: str | None,
        password: str,
        is_active: bool,
        is_verified: bool,
        is_superuser: bool,
        role_ids: list[int],
        actor_user_id: int,
    ) -> User:
        username = username.strip()
        email = email.strip().lower()
        password_hash = hash_password(password)
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            is_active=is_active,
            is_verified=is_verified,
            is_superuser=is_superuser,
        )
        try:
            await self.users.add(user)
            for role_id in role_ids:
                role = await self.roles.get(role_id)
                if role is not None:
                    await self.user_roles.add(UserRole(user_id=user.id, role_id=role.id))
        except IntegrityError:
            await self.session.rollback()
            raise DuplicateIdentityError("Username or email already exists") from None
        await self._audit(
            "user_created",
            user_id=actor_user_id,
            metadata={
                "target_user_id": user.id,
                "username": user.username,
                "is_superuser": is_superuser,
            },
        )
        await self.session.commit()
        return await self.get_user(user.id)

    async def update_user(
        self,
        user_id: int,
        *,
        full_name: str | None,
        is_superuser: bool | None,
        is_verified: bool | None,
        actor_user_id: int,
    ) -> User:
        user = await self.get_user(user_id)
        if full_name is not None:
            user.full_name = full_name
        if is_superuser is not None and is_superuser != user.is_superuser:
            if user.is_superuser and not is_superuser and actor_user_id == user.id:
                raise LocksuperuserTransitionError(
                    "You cannot remove your own superuser privileges"
                )
            current = await self.users.count_active_superusers()
            if user.is_superuser and current <= 1:
                raise LastSuperuserError("Cannot demote the last active superuser")
            user.is_superuser = is_superuser
        if is_verified is not None:
            user.is_verified = is_verified
        await self._audit(
            "user_updated",
            user_id=actor_user_id,
            metadata={"target_user_id": user_id, "username": user.username},
        )
        await self.session.commit()
        return await self.get_user(user_id)

    async def _set_active(self, user_id: int, *, active: bool, actor_user_id: int) -> User:
        user = await self.get_user(user_id)
        if not active and user.is_active:
            if user.is_superuser:
                current = await self.users.count_active_superusers()
                if current <= 1:
                    raise LastSuperuserError("Cannot deactivate the last active superuser")
            user.is_active = False
        elif active and not user.is_active:
            user.is_active = True
        await self._audit(
            "user_activated" if active else "user_deactivated",
            user_id=actor_user_id,
            metadata={"target_user_id": user_id, "username": user.username},
        )
        await self.session.commit()
        return await self.get_user(user_id)

    async def activate_user(self, user_id: int, *, actor_user_id: int) -> User:
        return await self._set_active(user_id, active=True, actor_user_id=actor_user_id)

    async def deactivate_user(self, user_id: int, *, actor_user_id: int) -> User:
        return await self._set_active(user_id, active=False, actor_user_id=actor_user_id)

    async def _set_verified(self, user_id: int, *, verified: bool, actor_user_id: int) -> User:
        user = await self.get_user(user_id)
        user.is_verified = verified
        await self._audit(
            "user_verified" if verified else "user_unverified",
            user_id=actor_user_id,
            metadata={"target_user_id": user_id, "username": user.username},
        )
        await self.session.commit()
        return await self.get_user(user_id)

    async def verify_user(self, user_id: int, *, actor_user_id: int) -> User:
        return await self._set_verified(user_id, verified=True, actor_user_id=actor_user_id)

    async def unverify_user(self, user_id: int, *, actor_user_id: int) -> User:
        return await self._set_verified(user_id, verified=False, actor_user_id=actor_user_id)

    async def reset_password(
        self,
        user_id: int,
        *,
        new_password: str | None,
        generate: bool,
        actor_user_id: int,
    ) -> PasswordResetResult:
        user = await self.get_user(user_id)
        generated: str | None = None
        if generate:
            generated = self._generate_password()
            password = generated
        elif new_password:
            password = new_password
        else:
            raise UserAdminError("Provide a new password or request a generated one")
        validate_password(password)
        user.password_hash = hash_password(password)
        revoked = await self.sessions.revoke_all_for_user(
            user_id, revoked_at=datetime.now(UTC)
        )
        await self._audit(
            "admin_password_reset",
            user_id=actor_user_id,
            metadata={
                "target_user_id": user_id,
                "username": user.username,
                "generated": generate,
            },
        )
        await self.session.commit()
        return PasswordResetResult(
            user_id=user_id,
            password_generated=generate,
            temporary_password=generated if generate else None,
            revoked_sessions=revoked,
        )

    async def delete_user(self, user_id: int, *, actor_user_id: int) -> None:
        if user_id == actor_user_id:
            raise SelfDeletionError("You cannot delete your own account")
        user = await self.get_user(user_id)
        if user.is_superuser and user.is_active:
            current = await self.users.count_active_superusers()
            if current <= 1:
                raise LastSuperuserError("Cannot delete the last active superuser")
        user.is_active = False
        user.is_superuser = False
        user.is_verified = False
        await self.session.commit()

    @staticmethod
    def _generate_password() -> str:
        alphabet = (
            string.ascii_uppercase
            + string.ascii_lowercase
            + string.digits
            + "!@#$%^&*"
        )
        parts = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%^&*"),
        ]
        parts += [secrets.choice(alphabet) for _ in range(12)]
        return "".join(parts)