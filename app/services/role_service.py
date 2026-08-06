from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from app.repositories.auth import (
    AuditLogRepository,
    PermissionRepository,
    RolePermissionRepository,
    RoleRepository,
    UserRepository,
    UserRoleRepository,
)
from app.services.builtin_auth import built_in_role_specs


class RoleServiceError(Exception):
    """Base exception for role and permission management failures."""


class RoleNotFoundError(RoleServiceError):
    """Raised when a role does not exist."""


class PermissionNotFoundError(RoleServiceError):
    """Raised when a permission does not exist."""


class DuplicateRoleError(RoleServiceError):
    """Raised when a role name already exists."""


class DuplicatePermissionError(RoleServiceError):
    """Raised when a permission code already exists."""


class ProtectedBuiltinError(RoleServiceError):
    """Raised when a built-in role is deleted or mutated unsafely."""


class RoleInUseError(RoleServiceError):
    """Raised when a role still has users and cannot be deleted."""


class PermissionInUseError(RoleServiceError):
    """Raised when a permission is still attached to a role."""


@dataclass(frozen=True, slots=True)
class RoleResult:
    role: Role
    user_count: int


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.roles = RoleRepository(session)
        self.permissions = PermissionRepository(session)
        self.user_roles = UserRoleRepository(session)
        self.role_permissions = RolePermissionRepository(session)
        self.users = UserRepository(session)
        self.audit_logs = AuditLogRepository(session)

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
                entity_type="role",
                entity_id=None,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _permission_ids(role: Role) -> list[int]:
        return [permission.id for permission in role.permissions]

    async def list_roles(self) -> list[RoleResult]:
        roles = await self.roles.list_with_details()
        results = []
        for role in roles:
            count = await self.roles.role_user_count(role.id)
            results.append(RoleResult(role=role, user_count=count))
        return results

    async def get_role(self, role_id: int) -> RoleResult:
        role = await self.roles.get_with_permissions(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        count = await self.roles.role_user_count(role.id)
        return RoleResult(role=role, user_count=count)

    async def create_role(
        self,
        *,
        name: str,
        description: str | None,
        permission_ids: list[int],
        actor_user_id: int,
    ) -> RoleResult:
        normalized = name.strip()
        existing = await self.roles.get_by_name(normalized)
        if existing is not None:
            raise DuplicateRoleError("Role name already exists")
        role = Role(name=normalized, description=description)
        await self.roles.add(role)
        await self._replace_role_permissions(role.id, permission_ids)
        await self._audit(
            "role_created",
            user_id=actor_user_id,
            metadata={"role_id": role.id, "role_name": role.name},
        )
        await self.session.commit()
        return await self.get_role(role.id)

    async def update_role(
        self,
        role_id: int,
        *,
        name: str | None,
        description: str | None,
        permission_ids: list[int] | None,
        actor_user_id: int,
    ) -> RoleResult:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        if role.name in ("super_admin", "admin"):
            raise ProtectedBuiltinError("Built-in roles cannot be renamed")
        if name is not None:
            normalized = name.strip()
            existing = await self.roles.get_by_name(normalized)
            if existing is not None and existing.id != role.id:
                raise DuplicateRoleError("Role name already exists")
            role.name = normalized
        if description is not None:
            role.description = description
        if permission_ids is not None:
            await self._replace_role_permissions(role.id, permission_ids)
        await self._audit(
            "role_updated",
            user_id=actor_user_id,
            metadata={"role_id": role.id, "role_name": role.name},
        )
        await self.session.commit()
        return await self.get_role(role.id)

    async def delete_role(self, role_id: int, *, actor_user_id: int) -> None:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        if role.name in ("super_admin", "admin"):
            raise ProtectedBuiltinError("Built-in roles cannot be deleted")
        user_count = await self.roles.role_user_count(role.id)
        if user_count > 0:
            raise RoleInUseError("Role is still assigned to users and cannot be deleted")
        await self.session.delete(role)
        await self._audit(
            "role_deleted",
            user_id=actor_user_id,
            metadata={"role_id": role_id, "role_name": role.name},
        )
        await self.session.commit()

    async def list_permissions(self) -> list[Permission]:
        return await self.permissions.list_all()

    async def get_permission(self, permission_id: int) -> Permission:
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError("Permission not found")
        return permission

    async def create_permission(
        self,
        *,
        code: str,
        description: str | None,
        actor_user_id: int,
    ) -> Permission:
        normalized = code.strip()
        existing = await self.permissions.get_by_code(normalized)
        if existing is not None:
            raise DuplicatePermissionError("Permission code already exists")
        permission = Permission(code=normalized, description=description)
        await self.permissions.add(permission)
        await self._audit(
            "permission_created",
            user_id=actor_user_id,
            metadata={"permission_id": permission.id, "code": permission.code},
        )
        await self.session.commit()
        return permission

    async def update_permission(
        self,
        permission_id: int,
        *,
        code: str | None,
        description: str | None,
        actor_user_id: int,
    ) -> Permission:
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError("Permission not found")
        if code is not None:
            normalized = code.strip()
            existing = await self.permissions.get_by_code(normalized)
            if existing is not None and existing.id != permission.id:
                raise DuplicatePermissionError("Permission code already exists")
            permission.code = normalized
        if description is not None:
            permission.description = description
        await self._audit(
            "permission_updated",
            user_id=actor_user_id,
            metadata={"permission_id": permission.id, "code": permission.code},
        )
        await self.session.commit()
        return permission

    async def delete_permission(self, permission_id: int, *, actor_user_id: int) -> None:
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError("Permission not found")
        role_count = await self.permissions.permission_role_count(permission_id)
        if role_count > 0:
            raise PermissionInUseError(
                "Permission is still attached to a role and cannot be deleted"
            )
        code = permission.code
        await self.session.delete(permission)
        await self._audit(
            "permission_deleted",
            user_id=actor_user_id,
            metadata={"permission_id": permission_id, "code": code},
        )
        await self.session.commit()

    async def assign_permission(
        self, role_id: int, permission_id: int, *, actor_user_id: int
    ) -> None:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError("Permission not found")
        assignment = RolePermission(role_id=role_id, permission_id=permission_id)
        try:
            await self.role_permissions.add(assignment)
        except IntegrityError:
            await self.session.rollback()
            return  # idempotent: already assigned
        await self._audit(
            "permission_assigned",
            user_id=actor_user_id,
            metadata={
                "role_id": role_id,
                "role_name": role.name,
                "permission_id": permission_id,
                "code": permission.code,
            },
        )
        await self.session.commit()

    async def remove_permission(
        self, role_id: int, permission_id: int, *, actor_user_id: int
    ) -> None:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError("Permission not found")
        assignment = await self.role_permissions.get(role_id, permission_id)
        if assignment is None:
            return  # idempotent: nothing to remove
        await self.session.delete(assignment)
        await self._audit(
            "permission_removed",
            user_id=actor_user_id,
            metadata={
                "role_id": role_id,
                "role_name": role.name,
                "permission_id": permission_id,
                "code": permission.code,
            },
        )
        await self.session.commit()

    async def assign_role(self, user_id: int, role_id: int, *, actor_user_id: int) -> None:
        user = await self.users.get(user_id)
        if user is None:
            raise RoleServiceError("User not found")
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        assignment = UserRole(user_id=user_id, role_id=role_id)
        try:
            await self.user_roles.add(assignment)
        except IntegrityError:
            await self.session.rollback()
            return  # idempotent: already assigned
        await self._audit(
            "role_assigned",
            user_id=actor_user_id,
            metadata={
                "target_user_id": user_id,
                "role_id": role_id,
                "role_name": role.name,
            },
        )
        await self.session.commit()

    async def remove_role(self, user_id: int, role_id: int, *, actor_user_id: int) -> None:
        user = await self.users.get(user_id)
        if user is None:
            raise RoleServiceError("User not found")
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError("Role not found")
        if role.name == "super_admin":
            members = await self.users.count_active_super_admin_role_members()
            if members <= 1:
                raise ProtectedBuiltinError("Cannot remove the last active super-admin")
        assignment = await self.user_roles.get(user_id, role_id)
        if assignment is None:
            return  # idempotent
        await self.session.delete(assignment)
        await self._audit(
            "role_removed",
            user_id=actor_user_id,
            metadata={
                "target_user_id": user_id,
                "role_id": role_id,
                "role_name": role.name,
            },
        )
        await self.session.commit()

    async def seed_builtins(self, *, actor_user_id: int | None = None) -> dict[str, int]:
        await self._seed_permissions()
        role_map: dict[str, Role] = {}
        for spec in built_in_role_specs():
            role = await self.roles.get_by_name(spec.name)
            if role is None:
                role = Role(name=spec.name, description=spec.description)
                await self.roles.add(role)
            else:
                role.description = spec.description
            role_map[spec.name] = role

        permission_codes = {
            permission.code: permission
            for permission in await self.permissions.list_all()
        }
        for spec in built_in_role_specs():
            role = role_map[spec.name]
            existing = await self.roles.get_with_permissions(role.id)
            assigned = {permission.code for permission in existing.permissions}
            for code in spec.permissions:
                permission = permission_codes.get(code)
                if permission is None or code in assigned:
                    continue
                self.session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        await self._audit(
            "builtin_auth_seeded",
            user_id=actor_user_id,
            metadata={"roles": sorted(role_map), "permissions": sorted(permission_codes)},
        )
        await self.session.commit()
        return {
            "roles": len(role_map),
            "permissions": len(permission_codes),
        }

    async def _seed_permissions(self) -> int:
        from app.services.builtin_auth import BUILT_IN_PERMISSION_DEFINITIONS

        created = 0
        for code, description in BUILT_IN_PERMISSION_DEFINITIONS.items():
            permission = await self.permissions.get_by_code(code)
            if permission is None:
                permission = Permission(code=code, description=description)
                await self.permissions.add(permission)
                created += 1
            else:
                permission.description = description
        return created

    async def _replace_role_permissions(self, role_id: int, permission_ids: list[int]) -> None:
        existing = await self.session.scalars(
            select(RolePermission).where(RolePermission.role_id == role_id)
        )
        current = {(row.permission_id): row for row in existing}
        for permission_id in permission_ids:
            if permission_id in current:
                current.pop(permission_id)
                continue
            self.session.add(RolePermission(role_id=role_id, permission_id=permission_id))
        for row in current.values():
            await self.session.delete(row)
        await self.session.flush()