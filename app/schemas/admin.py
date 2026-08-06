from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import UserPreferenceSummary


class PermissionSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    code: str
    description: str | None
    role_count: int = 0


class RoleSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    description: str | None


class RoleDetail(RoleSchema):
    user_count: int = 0
    permissions: list[PermissionSchema] = Field(default_factory=list)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    permission_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Role name must not be blank")
        return value.strip()


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    permission_ids: list[int] | None = None


class PermissionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=512)

    @field_validator("code")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Permission code must not be blank")
        return value.strip()


class PermissionUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=512)


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=1, max_length=320)
    full_name: str | None = Field(default=None, max_length=160)
    password: str = Field(min_length=1, max_length=1024)
    is_active: bool = True
    is_verified: bool = False
    is_superuser: bool = False
    role_ids: list[int] = Field(default_factory=list)

    @field_validator("username", "email")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value.strip()


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=160)
    is_superuser: bool | None = None
    is_verified: bool | None = None


class AdminUserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    username: str
    email: str
    full_name: str | None
    is_active: bool
    is_verified: bool
    is_superuser: bool
    last_login_at: datetime | None
    created_at: datetime


class AdminUserDetail(AdminUserRead):
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    preferences: UserPreferenceSummary | None = None


class UserListResponse(BaseModel):
    items: list[AdminUserRead]
    total: int
    page: int
    page_size: int
    pages: int


class AssignmentResponse(BaseModel):
    assigned: bool
    message: str


class AdminPasswordResetRequest(BaseModel):
    new_password: str | None = Field(default=None, max_length=1024)
    generate: bool = False


class AdminPasswordResetResponse(BaseModel):
    user_id: int
    password_generated: bool
    temporary_password: str | None = None
    message: str