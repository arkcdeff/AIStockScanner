from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserPreference,
    UserRole,
    UserSession,
)
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_username(self, username: str) -> User | None:
        return await self.session.scalar(select(User).where(User.username == username))

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def get_by_email_case_insensitive(self, email: str) -> User | None:
        statement = select(User).where(func.lower(User.email) == email.lower()).limit(1)
        return await self.session.scalar(statement)

    async def get_by_identifier_with_access(self, identifier: str) -> User | None:
        options = (
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.preference),
        )
        username_statement = (
            select(User).where(User.username == identifier).options(*options).limit(1)
        )
        user = await self.session.scalar(username_statement)
        if user is not None:
            return user
        email_statement = (
            select(User)
            .where(func.lower(User.email) == identifier.lower())
            .options(*options)
            .limit(1)
        )
        return await self.session.scalar(email_statement)

    async def superuser_exists(self) -> bool:
        statement = select(User.id).where(User.is_superuser.is_(True)).limit(1)
        return await self.session.scalar(statement) is not None

    async def get_with_access(self, user_id: int) -> User | None:
        statement = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.roles).selectinload(Role.permissions),
                selectinload(User.preference),
            )
        )
        return await self.session.scalar(statement)

    async def list_active(self, *, offset: int = 0, limit: int = 100) -> list[User]:
        statement = (
            select(User)
            .where(User.is_active.is_(True))
            .order_by(User.username)
            .offset(offset)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))


class RoleRepository(BaseRepository[Role]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Role, session)

    async def get_by_name(self, name: str) -> Role | None:
        return await self.session.scalar(select(Role).where(Role.name == name))

    async def get_with_permissions(self, role_id: int) -> Role | None:
        statement = select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
        return await self.session.scalar(statement)


class PermissionRepository(BaseRepository[Permission]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Permission, session)

    async def get_by_code(self, code: str) -> Permission | None:
        return await self.session.scalar(select(Permission).where(Permission.code == code))


class UserRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int, role_id: int) -> UserRole | None:
        return await self.session.get(UserRole, (user_id, role_id))

    async def add(self, assignment: UserRole) -> UserRole:
        self.session.add(assignment)
        await self.session.flush()
        return assignment

    async def list_for_user(self, user_id: int) -> list[UserRole]:
        statement = (
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .options(selectinload(UserRole.role))
            .order_by(UserRole.role_id)
        )
        return list(await self.session.scalars(statement))


class RolePermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, role_id: int, permission_id: int) -> RolePermission | None:
        return await self.session.get(RolePermission, (role_id, permission_id))

    async def add(self, assignment: RolePermission) -> RolePermission:
        self.session.add(assignment)
        await self.session.flush()
        return assignment

    async def list_for_role(self, role_id: int) -> list[RolePermission]:
        statement = (
            select(RolePermission)
            .where(RolePermission.role_id == role_id)
            .options(selectinload(RolePermission.permission))
            .order_by(RolePermission.permission_id)
        )
        return list(await self.session.scalars(statement))


class UserSessionRepository(BaseRepository[UserSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(UserSession, session)

    async def get_by_refresh_token_hash(self, token_hash: str) -> UserSession | None:
        statement = select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        return await self.session.scalar(statement)

    async def get_for_update(self, session_id: int) -> UserSession | None:
        statement = select(UserSession).where(UserSession.id == session_id).with_for_update()
        return await self.session.scalar(statement)

    async def get_owned(self, user_id: int, session_id: int) -> UserSession | None:
        statement = select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
        )
        return await self.session.scalar(statement)

    async def list_for_user(self, user_id: int) -> list[UserSession]:
        statement = (
            select(UserSession)
            .where(UserSession.user_id == user_id)
            .order_by(UserSession.expires_at.desc(), UserSession.id.desc())
        )
        return list(await self.session.scalars(statement))

    async def revoke_all_for_user(self, user_id: int, *, revoked_at: datetime) -> int:
        statement = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)

    async def list_active_for_user(
        self, user_id: int, *, at: datetime | None = None
    ) -> list[UserSession]:
        checked_at = at or datetime.now(UTC)
        statement = (
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > checked_at,
            )
            .order_by(UserSession.expires_at.desc())
        )
        return list(await self.session.scalars(statement))


class UserPreferenceRepository(BaseRepository[UserPreference]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(UserPreference, session)

    async def get_for_user(self, user_id: int) -> UserPreference | None:
        return await self.get(user_id)


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AuditLog, session)

    async def list_filtered(
        self,
        *,
        user_id: int | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[AuditLog]:
        statement = select(AuditLog)
        if user_id is not None:
            statement = statement.where(AuditLog.user_id == user_id)
        if action is not None:
            statement = statement.where(AuditLog.action == action)
        if entity_type is not None:
            statement = statement.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            statement = statement.where(AuditLog.entity_id == entity_id)
        statement = statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        statement = statement.offset(offset).limit(limit)
        return list(await self.session.scalars(statement))

    async def session_created_at_map(
        self, user_id: int, session_ids: list[int]
    ) -> dict[int, datetime]:
        if not session_ids:
            return {}
        session_id_expression = AuditLog.metadata_json["session_id"].as_integer()
        statement = select(AuditLog).where(
            AuditLog.user_id == user_id,
            AuditLog.action == "login_success",
            session_id_expression.in_(session_ids),
        )
        rows = await self.session.scalars(statement)
        created: dict[int, datetime] = {}
        for row in rows:
            if not row.metadata_json:
                continue
            session_id = row.metadata_json.get("session_id")
            if isinstance(session_id, int):
                created.setdefault(session_id, row.created_at)
        return created
