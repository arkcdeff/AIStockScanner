from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, User, UserPreference
from app.repositories.auth import (
    AuditLogRepository,
    UserPreferenceRepository,
    UserRepository,
    UserSessionRepository,
)

VALID_THEMES = frozenset({"system", "light", "dark"})


class PreferenceServiceError(Exception):
    """Base exception for preference and profile management failures."""


class InvalidPreferenceError(PreferenceServiceError):
    """Raised when preference values fail validation."""


class DuplicateIdentityError(PreferenceServiceError):
    """Raised when a profile update collides with another user."""


class PasswordConfirmationError(PreferenceServiceError):
    """Raised when a sensitive profile change lacks valid confirmation."""


class PreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.preferences = UserPreferenceRepository(session)
        self.sessions = UserSessionRepository(session)
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
                entity_type="user" if user_id is not None else "authentication",
                entity_id=str(user_id) if user_id is not None else None,
                metadata_json=metadata,
            )
        )

    async def get_preferences(self, user_id: int) -> UserPreference:
        return await self.preferences.get_or_create(user_id)

    async def update_preferences(
        self,
        user_id: int,
        *,
        theme: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
        dashboard_layout: dict | None = None,
        notification_defaults: dict | None = None,
    ) -> UserPreference:
        preference = await self.preferences.get_or_create(user_id)
        if theme is not None:
            if theme not in VALID_THEMES:
                raise InvalidPreferenceError("Theme must be one of: system, light, dark")
            preference.theme = theme
        if timezone is not None:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise InvalidPreferenceError("Timezone is not recognized") from exc
            preference.timezone = timezone
        if language is not None:
            if len(language) > 16 or not language.replace("-", "").isalnum():
                raise InvalidPreferenceError("Language must be a valid locale code")
            preference.language = language
        if dashboard_layout is not None:
            preference.dashboard_layout = dashboard_layout
        if notification_defaults is not None:
            preference.notification_defaults = notification_defaults
        await self._audit(
            "preferences_updated",
            user_id=user_id,
            metadata={"user_id": user_id},
        )
        await self.session.commit()
        return preference

    async def update_profile(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
        username: str | None = None,
        current_password: str | None = None,
    ) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise PreferenceServiceError("User not found")
        if email is not None or username is not None:
            from app.auth.password import verify_password

            if not current_password:
                raise PasswordConfirmationError(
                    "Current password is required to change email or username"
                )
            if not verify_password(current_password, user.password_hash):
                raise PasswordConfirmationError("Current password is invalid")
        if email is not None:
            normalized_email = email.strip().lower()
            existing = await self.users.get_by_email_case_insensitive(normalized_email)
            if existing is not None and existing.id != user.id:
                raise DuplicateIdentityError("Email is already in use")
            user.email = normalized_email
        if username is not None:
            normalized_username = username.strip()
            existing = await self.users.get_by_username(normalized_username)
            if existing is not None and existing.id != user.id:
                raise DuplicateIdentityError("Username is already in use")
            user.username = normalized_username
        if full_name is not None:
            user.full_name = full_name
        await self._audit(
            "profile_updated",
            user_id=user_id,
            metadata={"user_id": user_id},
        )
        await self.session.commit()
        return await self.users.get_with_access(user_id) or user