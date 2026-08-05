from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=1, max_length=320)
    full_name: str | None = Field(default=None, max_length=160)
    password_hash: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    is_verified: bool = False
    is_superuser: bool = False


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    full_name: str | None
    is_active: bool
    is_verified: bool
    is_superuser: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None


class PermissionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    description: str | None = None


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    description: str | None


class UserRoleCreate(BaseModel):
    user_id: int = Field(gt=0)
    role_id: int = Field(gt=0)


class UserRoleRead(UserRoleCreate):
    model_config = ConfigDict(from_attributes=True)


class RolePermissionCreate(BaseModel):
    role_id: int = Field(gt=0)
    permission_id: int = Field(gt=0)


class RolePermissionRead(RolePermissionCreate):
    model_config = ConfigDict(from_attributes=True)


class UserSessionCreate(BaseModel):
    user_id: int = Field(gt=0)
    refresh_token_hash: str = Field(min_length=1, max_length=255)
    expires_at: datetime
    last_seen_at: datetime | None = None
    user_agent: str | None = Field(default=None, max_length=512)
    ip_address: str | None = Field(default=None, max_length=45)
    revoked_at: datetime | None = None


class UserSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    expires_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    ip_address: str | None
    revoked_at: datetime | None


class UserPreferenceCreate(BaseModel):
    user_id: int = Field(gt=0)
    theme: str = Field(default="system", min_length=1, max_length=32)
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)
    language: str = Field(default="en", min_length=1, max_length=16)
    dashboard_layout: dict[str, Any] = Field(default_factory=dict)
    notification_defaults: dict[str, Any] = Field(default_factory=dict)


class UserPreferenceRead(UserPreferenceCreate):
    model_config = ConfigDict(from_attributes=True)


class AuditLogCreate(BaseModel):
    user_id: int | None = Field(default=None, gt=0)
    action: str = Field(min_length=1, max_length=100)
    entity_type: str | None = Field(default=None, max_length=100)
    entity_id: str | None = Field(default=None, max_length=100)
    metadata_json: dict[str, Any] | None = None


class AuditLogRead(AuditLogCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
