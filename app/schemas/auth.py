from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class LoginRequest(BaseModel):
    username_or_email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    remember_me: bool = False

    @field_validator("username_or_email")
    @classmethod
    def identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Identifier must not be blank")
        return normalized


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


class UserSummary(BaseModel):
    id: int
    username: str
    email: str
    full_name: str | None
    is_active: bool
    is_verified: bool
    is_superuser: bool


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime
    user: UserSummary
    session_id: int


class UserPreferenceSummary(BaseModel):
    theme: str
    timezone: str
    language: str


class CurrentUserResponse(UserSummary):
    roles: list[str]
    permissions: list[str]
    preferences: UserPreferenceSummary | None
    current_session_id: int


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)
    confirm_password: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def passwords_must_match(self) -> Self:
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirmation must match")
        return self


class SessionRead(BaseModel):
    id: int
    created_at: datetime | None
    expires_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    ip_address: str | None
    revoked: bool
    current_session: bool


class RevokeSessionResponse(BaseModel):
    session_id: int
    revoked: bool
    message: str


class LogoutAllResponse(BaseModel):
    revoked_sessions: int
    message: str


class LogoutResponse(RevokeSessionResponse):
    pass


class ChangePasswordResponse(BaseModel):
    changed: bool
    revoked_sessions: int
    message: str
