from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.browser_auth import (
    finalize_page_response,
    require_browser_admin,
    stage_csrf_token,
)
from app.auth import csrf
from app.auth.exceptions import PasswordPolicyError
from app.auth.password import PasswordHashError
from app.core.config import settings
from app.db.session import get_db_session
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
    SelfDeletionError,
    UserAdminError,
    UserAdminService,
    UserNotFoundError,
)

router = APIRouter()
env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)


def _render(template_name: str, context: dict[str, Any]) -> str:
    return env.get_template(template_name).render(**context)


def _checked(form, name: str) -> bool:
    return str(form.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(value, default: int = 1, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


async def _admin_context(
    request: Request, session: AsyncSession, admin, *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "request": request,
        "browser_user": admin,
        "csrf_token": stage_csrf_token(request),
        "admin": admin,
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


def _handle_csrf(request: Request, form) -> None:
    try:
        csrf.verify_request_csrf(request, form)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, "CSRF validation failed") from exc


def _not_found(request: Request, admin, message: str) -> HTMLResponse:
    ctx = {
        "request": request,
        "browser_user": admin,
        "csrf_token": stage_csrf_token(request),
        "message": message,
    }
    return _partial(request, "errors/403.html", ctx, status_code=404)


# --------------------------------------------------------------------------- users


def _users_query(
    search: str | None = None,
    is_active: bool | None = None,
    is_verified: bool | None = None,
    is_superuser: bool | None = None,
    role_id: int | None = None,
    sort: str = "username",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    page = _optional_int(page, 1)
    page_size = min(50, _optional_int(page_size, 25))
    return {
        "search": search,
        "is_active": is_active,
        "is_verified": is_verified,
        "is_superuser": is_superuser,
        "role_id": role_id,
        "sort": sort if sort in {"username", "newest"} else "username",
        "page": page,
        "page_size": page_size,
    }


async def _users_table_context(
    session: AsyncSession, params: dict[str, Any], *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    service = UserAdminService(session)
    users, total = await service.list_users(**params)
    pages = (total + params["page_size"] - 1) // params["page_size"]
    return {
        "users": users,
        "total": total,
        "page": params["page"],
        "page_size": params["page_size"],
        "pages": pages,
        "params": params,
        "flash": flash,
    }


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    search: str | None = None,
    is_active: bool | None = None,
    is_verified: bool | None = None,
    is_superuser: bool | None = None,
    role_id: int | None = None,
    sort: str = "username",
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    params = _users_query(
        search, is_active, is_verified, is_superuser, role_id, sort, page, page_size
    )
    ctx = await _admin_context(request, session, admin)
    ctx.update(await _users_table_context(session, params))
    ctx["roles"] = await RoleService(session).list_roles()
    return _partial(request, "admin/users.html", ctx)


@router.get("/admin/users/table", response_class=HTMLResponse)
async def admin_users_table(
    request: Request,
    search: str | None = None,
    is_active: bool | None = None,
    is_verified: bool | None = None,
    is_superuser: bool | None = None,
    role_id: int | None = None,
    sort: str = "username",
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    await require_browser_admin(request, session)
    params = _users_query(
        search, is_active, is_verified, is_superuser, role_id, sort, page, page_size
    )
    ctx = await _users_table_context(session, params)
    ctx["request"] = request
    return _partial(request, "admin/components/users_table.html", ctx)


@router.post("/admin/users/create", response_class=HTMLResponse)
async def admin_users_create(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    role_ids = [int(value) for value in form.getlist("role_ids") if str(value).strip().isdigit()]
    params = _users_query(
        str(form.get("search") or "") or None,
        sort=str(form.get("sort") or "username"),
        page=_optional_int(form.get("page")),
        page_size=_optional_int(form.get("page_size"), 25),
    )
    try:
        await UserAdminService(session).create_user(
            username=str(form.get("username", "")),
            email=str(form.get("email", "")),
            full_name=str(form.get("full_name", "")).strip() or None,
            password=str(form.get("password", "")),
            is_active=_checked(form, "is_active") or True,
            is_verified=_checked(form, "is_verified"),
            is_superuser=_checked(form, "is_superuser"),
            role_ids=role_ids,
            actor_user_id=admin.user.id,
        )
        flash: dict[str, str] = {"level": "success", "message": "User created."}
        status_code = 201
    except DuplicateIdentityError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    except PasswordPolicyError as exc:
        flash, status_code = {"level": "danger", "message": " ".join(exc.violations)}, 422
    except PasswordHashError:
        flash, status_code = {"level": "danger", "message": "Password hashing failed."}, 400
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _users_table_context(session, params))
    return _partial(request, "admin/components/users_table.html", ctx, status_code=status_code)


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    try:
        user = await UserAdminService(session).get_user(user_id)
    except UserNotFoundError:
        return _not_found(request, admin, "User not found.")
    ctx = await _admin_context(request, session, admin)
    ctx["user"] = user
    roles, permissions = UserAdminService._access_names(user)
    ctx["user_roles"] = roles
    ctx["user_permissions"] = permissions
    ctx["roles"] = await RoleService(session).list_roles()
    return _partial(request, "admin/user_detail.html", ctx)


async def _user_detail_panels_context(
    request: Request,
    session: AsyncSession,
    admin,
    user_id: int,
    *,
    flash: dict[str, str] | None = None,
) -> dict[str, Any]:
    user = await UserAdminService(session).get_user(user_id)
    roles, permissions = UserAdminService._access_names(user)
    return {
        "request": request,
        "browser_user": admin,
        "csrf_token": stage_csrf_token(request),
        "user": user,
        "user_roles": roles,
        "user_permissions": permissions,
        "roles": await RoleService(session).list_roles(),
        "flash": flash,
    }


async def _user_detail_response(
    request: Request,
    session: AsyncSession,
    admin,
    user_id: int,
    *,
    flash: dict[str, str],
    status_code: int,
) -> HTMLResponse:
    try:
        ctx = await _user_detail_panels_context(request, session, admin, user_id, flash=flash)
    except UserNotFoundError:
        return _not_found(request, admin, "User not found.")
    return _partial(
        request, "admin/components/user_actions_panel.html", ctx, status_code=status_code
    )


@router.post("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def admin_user_edit(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await UserAdminService(session).update_user(
            user_id,
            full_name=str(form.get("full_name", "")).strip() or None,
            is_superuser=_checked(form, "is_superuser"),
            is_verified=_checked(form, "is_verified"),
            actor_user_id=admin.user.id,
        )
        flash, status_code = {"level": "success", "message": "User updated."}, 200
    except UserNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except LastSuperuserError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    except LocksuperuserTransitionError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 403
    return await _user_detail_response(
        request, session, admin, user_id, flash=flash, status_code=status_code
    )


async def _user_status_action(
    user_id: int,
    request: Request,
    session: AsyncSession,
    *,
    action: str,
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    service = UserAdminService(session)
    try:
        if action == "activate":
            await service.activate_user(user_id, actor_user_id=admin.user.id)
            message = "User activated."
        elif action == "deactivate":
            await service.deactivate_user(user_id, actor_user_id=admin.user.id)
            message = "User deactivated."
        elif action == "verify":
            await service.verify_user(user_id, actor_user_id=admin.user.id)
            message = "User verified."
        else:
            await service.unverify_user(user_id, actor_user_id=admin.user.id)
            message = "User unverified."
        flash, status_code = {"level": "success", "message": message}, 200
    except UserNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except LastSuperuserError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    return await _user_detail_response(
        request, session, admin, user_id, flash=flash, status_code=status_code
    )


@router.post("/admin/users/{user_id}/activate", response_class=HTMLResponse)
async def admin_user_activate(
    user_id: int, request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    return await _user_status_action(user_id, request, session, action="activate")


@router.post("/admin/users/{user_id}/deactivate", response_class=HTMLResponse)
async def admin_user_deactivate(
    user_id: int, request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    return await _user_status_action(user_id, request, session, action="deactivate")


@router.post("/admin/users/{user_id}/verify", response_class=HTMLResponse)
async def admin_user_verify(
    user_id: int, request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    return await _user_status_action(user_id, request, session, action="verify")


@router.post("/admin/users/{user_id}/unverify", response_class=HTMLResponse)
async def admin_user_unverify(
    user_id: int, request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    return await _user_status_action(user_id, request, session, action="unverify")


@router.post("/admin/users/{user_id}/reset-password", response_class=HTMLResponse)
async def admin_user_reset_password(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    generate = _checked(form, "generate")
    new_password = str(form.get("new_password", "")) or None
    try:
        result = await UserAdminService(session).reset_password(
            user_id,
            new_password=new_password,
            generate=generate,
            actor_user_id=admin.user.id,
        )
        if result.password_generated:
            message = f"Temporary password generated (shown once): {result.temporary_password}"
        else:
            message = "Password has been reset and all sessions revoked."
        flash, status_code = {"level": "success", "message": message}, 200
    except UserNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except UserAdminError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 400
    except PasswordPolicyError as exc:
        flash, status_code = {"level": "danger", "message": " ".join(exc.violations)}, 422
    return await _user_detail_response(
        request, session, admin, user_id, flash=flash, status_code=status_code
    )


@router.post("/admin/users/{user_id}/roles/assign", response_class=HTMLResponse)
async def admin_user_role_assign(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    role_id = _optional_int(str(form.get("role_id", "")), 0)
    try:
        await RoleService(session).assign_role(user_id, role_id, actor_user_id=admin.user.id)
        flash, status_code = {"level": "success", "message": "Role assigned."}, 200
    except (RoleNotFoundError, RoleServiceError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    return await _user_roles_response(
        request, session, admin, user_id, flash=flash, status_code=status_code
    )


@router.post("/admin/users/{user_id}/roles/{role_id}/remove", response_class=HTMLResponse)
async def admin_user_role_remove(
    user_id: int,
    role_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).remove_role(user_id, role_id, actor_user_id=admin.user.id)
        flash, status_code = {"level": "success", "message": "Role removed."}, 200
    except (RoleNotFoundError, RoleServiceError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except ProtectedBuiltinError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    return await _user_roles_response(
        request, session, admin, user_id, flash=flash, status_code=status_code
    )


async def _user_roles_response(
    request: Request,
    session: AsyncSession,
    admin,
    user_id: int,
    *,
    flash: dict[str, str],
    status_code: int,
) -> HTMLResponse:
    try:
        ctx = await _user_detail_panels_context(request, session, admin, user_id, flash=flash)
    except UserNotFoundError:
        return _not_found(request, admin, "User not found.")
    return _partial(
        request, "admin/components/user_roles_panel.html", ctx, status_code=status_code
    )


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def admin_user_delete(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    params = _users_query(
        str(form.get("search") or "") or None,
        sort=str(form.get("sort") or "username"),
        page=_optional_int(form.get("page")),
        page_size=_optional_int(form.get("page_size"), 25),
    )
    try:
        await UserAdminService(session).delete_user(user_id, actor_user_id=admin.user.id)
        flash, status_code = {"level": "success", "message": "User deactivated and removed."}, 200
    except UserNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except (SelfDeletionError, LastSuperuserError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _users_table_context(session, params))
    return _partial(request, "admin/components/users_table.html", ctx, status_code=status_code)


# --------------------------------------------------------------------------- roles


@router.get("/admin/roles", response_class=HTMLResponse)
async def admin_roles_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    ctx = await _admin_context(request, session, admin)
    ctx["roles"] = await RoleService(session).list_roles()
    ctx["permissions"] = await RoleService(session).list_permissions()
    return _partial(request, "admin/roles.html", ctx)


@router.get("/admin/roles/{role_id}", response_class=HTMLResponse)
async def admin_role_detail(
    role_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    try:
        result = await RoleService(session).get_role(role_id)
    except RoleNotFoundError:
        return _not_found(request, admin, "Role not found.")
    ctx = await _admin_context(request, session, admin)
    ctx["role"] = result.role
    ctx["user_count"] = result.user_count
    ctx["role_permissions"] = result.role.permissions
    ctx["permissions"] = await RoleService(session).list_permissions()
    return _partial(request, "admin/role_detail.html", ctx)


async def _roles_table_context(
    session: AsyncSession, *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    return {"roles": await RoleService(session).list_roles(), "flash": flash}


@router.post("/admin/roles/create", response_class=HTMLResponse)
async def admin_role_create(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    permission_ids = [
        int(value) for value in form.getlist("permission_ids") if str(value).strip().isdigit()
    ]
    try:
        await RoleService(session).create_role(
            name=str(form.get("name", "")),
            description=str(form.get("description", "")).strip() or None,
            permission_ids=permission_ids,
            actor_user_id=admin.user.id,
        )
        flash, status_code = {"level": "success", "message": "Role created."}, 201
    except DuplicateRoleError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _roles_table_context(session))
    return _partial(request, "admin/components/roles_table.html", ctx, status_code=status_code)


@router.post("/admin/roles/{role_id}/edit", response_class=HTMLResponse)
async def admin_role_edit(
    role_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).update_role(
            role_id,
            name=str(form.get("name", "")).strip() or None,
            description=str(form.get("description", "")).strip() or None,
            permission_ids=None,
            actor_user_id=admin.user.id,
        )
        flash, status_code = {"level": "success", "message": "Role updated."}, 200
    except RoleNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except DuplicateRoleError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    except ProtectedBuiltinError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _roles_table_context(session))
    return _partial(request, "admin/components/roles_table.html", ctx, status_code=status_code)


@router.post("/admin/roles/{role_id}/delete", response_class=HTMLResponse)
async def admin_role_delete(
    role_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).delete_role(role_id, actor_user_id=admin.user.id)
        flash, status_code = {"level": "success", "message": "Role deleted."}, 200
    except RoleNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except (ProtectedBuiltinError, RoleInUseError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _roles_table_context(session))
    return _partial(request, "admin/components/roles_table.html", ctx, status_code=status_code)


async def _role_permissions_response(
    request: Request,
    session: AsyncSession,
    admin,
    role_id: int,
    *,
    flash: dict[str, str],
    status_code: int,
) -> HTMLResponse:
    try:
        result = await RoleService(session).get_role(role_id)
    except RoleNotFoundError:
        return _not_found(request, admin, "Role not found.")
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx["role"] = result.role
    ctx["user_count"] = result.user_count
    ctx["role_permissions"] = result.role.permissions
    ctx["permissions"] = await RoleService(session).list_permissions()
    return _partial(
        request,
        "admin/components/role_permissions_panel.html",
        ctx,
        status_code=status_code,
    )


@router.post("/admin/roles/{role_id}/permissions/assign", response_class=HTMLResponse)
async def admin_role_permission_assign(
    role_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    permission_id = _optional_int(str(form.get("permission_id", "")), 0)
    try:
        await RoleService(session).assign_permission(
            role_id, permission_id, actor_user_id=admin.user.id
        )
        flash, status_code = {"level": "success", "message": "Permission assigned."}, 200
    except (RoleNotFoundError, PermissionNotFoundError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    return await _role_permissions_response(
        request, session, admin, role_id, flash=flash, status_code=status_code
    )


@router.post(
    "/admin/roles/{role_id}/permissions/{permission_id}/remove", response_class=HTMLResponse
)
async def admin_role_permission_remove(
    role_id: int,
    permission_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).remove_permission(
            role_id, permission_id, actor_user_id=admin.user.id
        )
        flash, status_code = {"level": "success", "message": "Permission removed."}, 200
    except (RoleNotFoundError, PermissionNotFoundError) as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    return await _role_permissions_response(
        request, session, admin, role_id, flash=flash, status_code=status_code
    )


# --------------------------------------------------------------------------- permissions


@router.get("/admin/permissions", response_class=HTMLResponse)
async def admin_permissions_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    ctx = await _admin_context(request, session, admin)
    ctx["permissions"] = await _permission_rows(session)
    return _partial(request, "admin/permissions.html", ctx)


async def _permission_rows(session: AsyncSession) -> list[dict[str, Any]]:
    service = RoleService(session)
    permissions = await service.list_permissions()
    rows = []
    for permission in permissions:
        rows.append(
            {
                "id": permission.id,
                "code": permission.code,
                "description": permission.description,
                "role_count": await service.permissions.permission_role_count(permission.id),
            }
        )
    return rows


async def _permissions_table_context(
    session: AsyncSession, *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    return {"permissions": await _permission_rows(session), "flash": flash}


async def _permissions_table_response(
    request: Request,
    session: AsyncSession,
    admin,
    *,
    flash: dict[str, str],
    status_code: int,
) -> HTMLResponse:
    ctx = await _admin_context(request, session, admin, flash=flash)
    ctx.update(await _permissions_table_context(session))
    return _partial(
        request, "admin/components/permissions_table.html", ctx, status_code=status_code
    )


@router.post("/admin/permissions/create", response_class=HTMLResponse)
async def admin_permission_create(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).create_permission(
            code=str(form.get("code", "")),
            description=str(form.get("description", "")).strip() or None,
            actor_user_id=admin.user.id,
        )
        flash, status_code = {"level": "success", "message": "Permission created."}, 201
    except DuplicatePermissionError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    return await _permissions_table_response(
        request, session, admin, flash=flash, status_code=status_code
    )


@router.post("/admin/permissions/{permission_id}/edit", response_class=HTMLResponse)
async def admin_permission_edit(
    permission_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).update_permission(
            permission_id,
            code=str(form.get("code", "")).strip() or None,
            description=str(form.get("description", "")).strip() or None,
            actor_user_id=admin.user.id,
        )
        flash, status_code = {"level": "success", "message": "Permission updated."}, 200
    except PermissionNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except DuplicatePermissionError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    return await _permissions_table_response(
        request, session, admin, flash=flash, status_code=status_code
    )


@router.post("/admin/permissions/{permission_id}/delete", response_class=HTMLResponse)
async def admin_permission_delete(
    permission_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    admin = await require_browser_admin(request, session)
    form = await request.form()
    _handle_csrf(request, form)
    try:
        await RoleService(session).delete_permission(
            permission_id, actor_user_id=admin.user.id
        )
        flash, status_code = {"level": "success", "message": "Permission deleted."}, 200
    except PermissionNotFoundError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 404
    except PermissionInUseError as exc:
        flash, status_code = {"level": "danger", "message": str(exc)}, 409
    return await _permissions_table_response(
        request, session, admin, flash=flash, status_code=status_code
    )