from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAdminUser, require_admin_user
from app.auth.authorization import require_permission
from app.auth.password import PasswordHashError, PasswordPolicyError
from app.db.session import get_db_session
from app.schemas.admin import (
    AdminPasswordResetRequest,
    AdminPasswordResetResponse,
    AdminUserCreate,
    AdminUserDetail,
    AdminUserRead,
    AdminUserUpdate,
    AssignmentResponse,
    PermissionCreate,
    PermissionSchema,
    PermissionUpdate,
    RoleCreate,
    RoleDetail,
    RoleUpdate,
    UserListResponse,
)
from app.services.role_service import (
    DuplicatePermissionError,
    DuplicateRoleError,
    PermissionInUseError,
    PermissionNotFoundError,
    ProtectedBuiltinError,
    RoleInUseError,
    RoleNotFoundError,
    RoleService,
    RoleServiceError,
)
from app.services.user_admin import (
    DuplicateIdentityError,
    LastSuperuserError,
    LocksuperuserTransitionError,
    PasswordResetResult,
    SelfDeletionError,
    UserAdminError,
    UserAdminService,
    UserNotFoundError,
)

router = APIRouter(
    prefix="/admin",
    tags=["Administration"],
    dependencies=[Depends(require_admin_user)],
)

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


def _user_read(user) -> AdminUserRead:
    return AdminUserRead.model_validate(user)


def _user_detail(user, roles: list[str], permissions: list[str]) -> AdminUserDetail:
    return AdminUserDetail(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_superuser=user.is_superuser,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        roles=roles,
        permissions=permissions,
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


_users_read = [Depends(require_permission("users.read"))]
_users_write = [Depends(require_permission("users.write"))]
_roles_read = [Depends(require_permission("roles.read"))]
_roles_write = [Depends(require_permission("roles.write"))]
# --------------------------------------------------------------------------- users


@router.get("/users", response_model=UserListResponse, dependencies=_users_read)
async def list_users(
    search: str | None = None,
    is_active: bool | None = None,
    is_verified: bool | None = None,
    is_superuser: bool | None = None,
    role_id: int | None = None,
    sort: str = Query(default="username", pattern="^(username|newest)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: DatabaseSession = None,
) -> UserListResponse:
    service = UserAdminService(session)
    users, total = await service.list_users(
        search=search,
        is_active=is_active,
        is_verified=is_verified,
        is_superuser=is_superuser,
        role_id=role_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    items = [_user_read(user) for user in users]
    return UserListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


def _user_read(user) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_superuser=user.is_superuser,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.post(
        "/users",
        response_model=AdminUserDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=_users_write,
    )
async def create_user(
    payload: AdminUserCreate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).create_user(
            username=payload.username,
            email=payload.email,
            full_name=payload.full_name,
            password=payload.password,
            is_active=payload.is_active,
            is_verified=payload.is_verified,
            is_superuser=payload.is_superuser,
            role_ids=payload.role_ids,
            actor_user_id=admin.user.id,
        )
    except DuplicateIdentityError as exc:
        raise _conflict(str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(exc.violations)
        ) from exc
    except PasswordHashError as exc:
        raise _bad_request("Password hashing failed") from exc
    return _detail(user)


@router.get("/users/{user_id}", response_model=AdminUserDetail, dependencies=_users_read)
async def get_user(user_id: int, session: DatabaseSession) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).get_user(user_id)
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return _detail(user)


@router.put("/users/{user_id}", response_model=AdminUserDetail, dependencies=_users_write)
async def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).update_user(
            user_id,
            full_name=payload.full_name,
            is_superuser=payload.is_superuser,
            is_verified=payload.is_verified,
            actor_user_id=admin.user.id,
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except LastSuperuserError as exc:
        raise _conflict(str(exc)) from exc
    except LocksuperuserTransitionError as exc:
        raise _forbidden(str(exc)) from exc
    return _detail(user)


@router.post("/users/{user_id}/activate", response_model=AdminUserDetail, dependencies=_users_write)
async def activate_user(
    user_id: int, session: DatabaseSession, admin: CurrentAdminUser
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).activate_user(
            user_id, actor_user_id=admin.user.id
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return _detail(user)


@router.post(
        "/users/{user_id}/deactivate",
        response_model=AdminUserDetail,
        dependencies=_users_write,
    )
async def deactivate_user(
    user_id: int, session: DatabaseSession, admin: CurrentAdminUser
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).deactivate_user(
            user_id, actor_user_id=admin.user.id
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except LastSuperuserError as exc:
        raise _conflict(str(exc)) from exc
    return _detail(user)


@router.post("/users/{user_id}/verify", response_model=AdminUserDetail, dependencies=_users_write)
async def verify_user(
    user_id: int, session: DatabaseSession, admin: CurrentAdminUser
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).verify_user(
            user_id, actor_user_id=admin.user.id
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return _detail(user)


@router.post("/users/{user_id}/unverify", response_model=AdminUserDetail, dependencies=_users_write)
async def unverify_user(
    user_id: int, session: DatabaseSession, admin: CurrentAdminUser
) -> AdminUserDetail:
    try:
        user = await UserAdminService(session).unverify_user(
            user_id, actor_user_id=admin.user.id
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return _detail(user)


@router.post(
        "/users/{user_id}/reset-password",
        response_model=AdminPasswordResetResponse,
        dependencies=_users_write,
    )
async def reset_password(
    user_id: int,
    payload: AdminPasswordResetRequest,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AdminPasswordResetResponse:
    try:
        result: PasswordResetResult = await UserAdminService(session).reset_password(
            user_id,
            new_password=payload.new_password,
            generate=payload.generate,
            actor_user_id=admin.user.id,
        )
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except UserAdminError as exc:
        raise _bad_request(str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=list(exc.violations)
        ) from exc
    return AdminPasswordResetResponse(
        user_id=result.user_id,
        password_generated=result.password_generated,
        temporary_password=result.temporary_password,
        message=(
            "A temporary password has been generated"
            if result.password_generated
            else "Password has been reset"
        ),
    )


@router.delete(
        "/users/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=_users_write,
    )
async def delete_user(user_id: int, session: DatabaseSession, admin: CurrentAdminUser) -> None:
    try:
        await UserAdminService(session).delete_user(user_id, actor_user_id=admin.user.id)
    except UserNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except SelfDeletionError as exc:
        raise _conflict(str(exc)) from exc
    except LastSuperuserError as exc:
        raise _conflict(str(exc)) from exc


def _detail(user) -> AdminUserDetail:
    roles, permissions = UserAdminService._access_names(user)
    return _user_detail(user, roles, permissions)


def _service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Administration service unavailable",
    )


# --------------------------------------------------------------------------- role assignment


@router.post(
        "/users/{user_id}/roles/{role_id}",
        response_model=AssignmentResponse,
        dependencies=_roles_write,
    )
async def assign_role(
    user_id: int,
    role_id: int,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AssignmentResponse:
    try:
        await RoleService(session).assign_role(user_id, role_id, actor_user_id=admin.user.id)
    except RoleNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except RoleServiceError as exc:
        raise _not_found(str(exc)) from exc
    return AssignmentResponse(assigned=True, message="Role assigned")


@router.delete(
        "/users/{user_id}/roles/{role_id}",
        response_model=AssignmentResponse,
        dependencies=_roles_write,
    )
async def remove_role(
    user_id: int,
    role_id: int,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AssignmentResponse:
    try:
        await RoleService(session).remove_role(user_id, role_id, actor_user_id=admin.user.id)
    except RoleNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except ProtectedBuiltinError as exc:
        raise _conflict(str(exc)) from exc
    except RoleServiceError as exc:
        raise _not_found(str(exc)) from exc
    return AssignmentResponse(assigned=False, message="Role removed")


# --------------------------------------------------------------------------- roles


@router.get("/roles", response_model=list[RoleDetail], dependencies=_roles_read)
async def list_roles(session: DatabaseSession) -> list[RoleDetail]:
    results = await RoleService(session).list_roles()
    return [
        RoleDetail(
            id=r.role.id,
            name=r.role.name,
            description=r.role.description,
            user_count=r.user_count,
            permissions=[
                _permission_schema(p, 0) for p in r.role.permissions
            ],
        )
        for r in results
    ]


def _permission_schema(permission, role_count: int) -> PermissionSchema:
    return PermissionSchema(
        id=permission.id,
        code=permission.code,
        description=permission.description,
        role_count=role_count,
    )


@router.post(
        "/roles",
        response_model=RoleDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=_roles_write,
    )
async def create_role(
    payload: RoleCreate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> RoleDetail:
    try:
        result = await RoleService(session).create_role(
            name=payload.name,
            description=payload.description,
            permission_ids=payload.permission_ids,
            actor_user_id=admin.user.id,
        )
    except DuplicateRoleError as exc:
        raise _conflict(str(exc)) from exc
    return _role_detail(result)


def _role_detail(result) -> RoleDetail:
    return RoleDetail(
        id=result.role.id,
        name=result.role.name,
        description=result.role.description,
        user_count=result.user_count,
        permissions=[_permission_schema(p, 0) for p in result.role.permissions],
    )


@router.get("/roles/{role_id}", response_model=RoleDetail, dependencies=_roles_read)
async def get_role(role_id: int, session: DatabaseSession) -> RoleDetail:
    try:
        result = await RoleService(session).get_role(role_id)
    except RoleNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return _role_detail(result)


@router.put("/roles/{role_id}", response_model=RoleDetail, dependencies=_roles_write)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> RoleDetail:
    try:
        result = await RoleService(session).update_role(
            role_id,
            name=payload.name,
            description=payload.description,
            permission_ids=payload.permission_ids,
            actor_user_id=admin.user.id,
        )
    except RoleNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except DuplicateRoleError as exc:
        raise _conflict(str(exc)) from exc
    except ProtectedBuiltinError as exc:
        raise _conflict(str(exc)) from exc
    return _role_detail(result)


@router.delete(
        "/roles/{role_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=_roles_write,
    )
async def delete_role(role_id: int, session: DatabaseSession, admin: CurrentAdminUser) -> None:
    try:
        await RoleService(session).delete_role(role_id, actor_user_id=admin.user.id)
    except RoleNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except (ProtectedBuiltinError, RoleInUseError) as exc:
        raise _conflict(str(exc)) from exc


@router.post(
        "/roles/{role_id}/permissions/{permission_id}",
        response_model=AssignmentResponse,
        dependencies=_roles_write,
    )
async def assign_permission(
    role_id: int,
    permission_id: int,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AssignmentResponse:
    try:
        await RoleService(session).assign_permission(
            role_id, permission_id, actor_user_id=admin.user.id
        )
    except (RoleNotFoundError, PermissionNotFoundError) as exc:
        raise _not_found(str(exc)) from exc
    return AssignmentResponse(assigned=True, message="Permission assigned")


@router.delete(
        "/roles/{role_id}/permissions/{permission_id}",
        response_model=AssignmentResponse,
        dependencies=_roles_write,
    )
async def remove_permission(
    role_id: int,
    permission_id: int,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> AssignmentResponse:
    try:
        await RoleService(session).remove_permission(
            role_id, permission_id, actor_user_id=admin.user.id
        )
    except (RoleNotFoundError, PermissionNotFoundError) as exc:
        raise _not_found(str(exc)) from exc
    return AssignmentResponse(assigned=False, message="Permission removed")


# --------------------------------------------------------------------------- permissions


@router.get("/permissions", response_model=list[PermissionSchema], dependencies=_roles_read)
async def list_permissions(session: DatabaseSession) -> list[PermissionSchema]:
    service = RoleService(session)
    permissions = await service.list_permissions()
    return [
        PermissionSchema(
            id=p.id,
            code=p.code,
            description=p.description,
            role_count=0,
        )
        for p in permissions
    ]


@router.post(
        "/permissions",
        response_model=PermissionSchema,
        status_code=status.HTTP_201_CREATED,
        dependencies=_roles_write,
    )
async def create_permission(
    payload: PermissionCreate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> PermissionSchema:
    try:
        permission = await RoleService(session).create_permission(
            code=payload.code,
            description=payload.description,
            actor_user_id=admin.user.id,
        )
    except DuplicatePermissionError as exc:
        raise _conflict(str(exc)) from exc
    return _permission_schema(permission, 0)


@router.get(
        "/permissions/{permission_id}",
        response_model=PermissionSchema,
        dependencies=_roles_read,
    )
async def get_permission(permission_id: int, session: DatabaseSession) -> PermissionSchema:
    try:
        permission = await RoleService(session).get_permission(permission_id)
    except PermissionNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    role_count = await RoleService(session).permissions.permission_role_count(permission_id)
    return _permission_schema(permission, role_count)


@router.put(
        "/permissions/{permission_id}",
        response_model=PermissionSchema,
        dependencies=_roles_write,
    )
async def update_permission(
    permission_id: int,
    payload: PermissionUpdate,
    session: DatabaseSession,
    admin: CurrentAdminUser,
) -> PermissionSchema:
    service = RoleService(session)
    try:
        permission = await service.update_permission(
            permission_id,
            code=payload.code,
            description=payload.description,
            actor_user_id=admin.user.id,
        )
    except PermissionNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except DuplicatePermissionError as exc:
        raise _conflict(str(exc)) from exc
    role_count = await service.permissions.permission_role_count(permission_id)
    return _permission_schema(permission, role_count)


@router.delete(
        "/permissions/{permission_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=_roles_write,
    )
async def delete_permission(
    permission_id: int, session: DatabaseSession, admin: CurrentAdminUser
) -> None:
    try:
        await RoleService(session).delete_permission(
            permission_id, actor_user_id=admin.user.id
        )
    except PermissionNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except PermissionInUseError as exc:
        raise _conflict(str(exc)) from exc


# --------------------------------------------------------------------------- seed


@router.post("/seed", response_model=dict, dependencies=_roles_write)
async def seed_builtins(session: DatabaseSession, admin: CurrentAdminUser) -> dict:
    result = await RoleService(session).seed_builtins(actor_user_id=admin.user.id)
    return result
