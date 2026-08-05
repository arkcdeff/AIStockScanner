from __future__ import annotations

from collections.abc import Mapping
from math import ceil
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.notification import NotificationChannel, NotificationPreference
from app.repositories.notification import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
    NotificationPreferenceRepository,
)
from app.schemas.notification import (
    NotificationChannelCreate,
    NotificationChannelUpdate,
    NotificationPreferenceCreate,
    NotificationPreferenceUpdate,
    sanitize_notification_config,
    sanitize_notification_error,
    validate_provider_config,
    validate_quiet_hours,
)


class NotificationUIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_input=False, include_url=False)[0]
    message = str(first.get("msg", "Invalid notification settings"))
    return message.removeprefix("Value error, ")


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class NotificationUIService:
    """Read models and settings orchestration shared by notification HTML pages.

    Delivery state transitions remain in :class:`NotificationService`; this
    service prepares eager-loaded, masked presentation data and settings CRUD.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.channel_repo = NotificationChannelRepository(session)
        self.preference_repo = NotificationPreferenceRepository(session)
        self.delivery_repo = NotificationDeliveryRepository(session)

    # --------------------------------------------------------------- status

    @staticmethod
    def _provider_availability(channels: list[NotificationChannel]) -> dict[str, bool]:
        telegram_configured = bool(
            settings.telegram_bot_token and settings.telegram_default_chat_id
        )
        email_configured = bool(settings.email_host and settings.email_from)
        for channel in channels:
            config = channel.config_json or {}
            if channel.provider == "telegram":
                telegram_configured = telegram_configured or bool(
                    (config.get("bot_token") or settings.telegram_bot_token)
                    and (
                        config.get("recipient")
                        or config.get("chat_id")
                        or settings.telegram_default_chat_id
                    )
                )
            elif channel.provider == "email":
                email_configured = email_configured or bool(
                    (config.get("host") or settings.email_host)
                    and (config.get("from") or settings.email_from)
                )
        return {
            "mock": True,
            "telegram": telegram_configured,
            "email": email_configured,
            "in_app": True,
        }

    async def get_status(
        self, *, channels: list[NotificationChannel] | None = None
    ) -> dict[str, Any]:
        channels = channels or await self.channel_repo.list_filtered(limit=500)
        counts = await self.delivery_repo.status_summary(
            stale_minutes=settings.notification_stale_minutes
        )
        enabled_providers = {channel.provider for channel in channels if channel.enabled}
        return {
            "notifications_enabled": settings.notifications_enabled,
            "provider_availability": self._provider_availability(channels),
            "telegram_enabled": "telegram" in enabled_providers,
            "email_enabled": "email" in enabled_providers,
            "in_app_enabled": "in_app" in enabled_providers,
            **counts,
        }

    async def get_provider_cards(
        self, *, channels: list[NotificationChannel] | None = None
    ) -> list[dict[str, Any]]:
        channels = channels or await self.channel_repo.list_filtered(limit=500)
        availability = self._provider_availability(channels)
        metadata = {
            "mock": ("Mock", "bi-bug", "Deterministic local testing"),
            "telegram": ("Telegram", "bi-telegram", "Telegram Bot delivery"),
            "email": ("Email", "bi-envelope", "SMTP email delivery"),
            "in_app": ("In-app", "bi-window", "Notifications inside this application"),
        }
        cards: list[dict[str, Any]] = []
        for provider in ("mock", "telegram", "email", "in_app"):
            provider_channels = [item for item in channels if item.provider == provider]
            enabled = any(item.enabled for item in provider_channels)
            configured = availability[provider]
            if not enabled:
                health = "disabled"
            elif configured:
                health = "ready"
            else:
                health = "needs_setup"
            title, icon, description = metadata[provider]
            cards.append(
                {
                    "provider": provider,
                    "title": title,
                    "icon": icon,
                    "description": description,
                    "enabled": enabled,
                    "configured": configured,
                    "health": health,
                    "channel_count": len(provider_channels),
                    "can_test": provider in {"mock", "in_app"} or configured,
                }
            )
        return cards

    # --------------------------------------------------------- presentation

    @staticmethod
    def _channel_row(channel: NotificationChannel) -> dict[str, Any]:
        safe_config = sanitize_notification_config(channel.config_json) or {}
        summary = [
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in safe_config.items()
            if value not in (None, "")
        ]
        return {
            "id": channel.id,
            "name": channel.name,
            "provider": channel.provider,
            "enabled": channel.enabled,
            "safe_config": safe_config,
            "config_summary": summary,
            "created_at": channel.created_at,
            "updated_at": channel.updated_at,
        }

    async def get_channels(self) -> list[dict[str, Any]]:
        channels = await self.channel_repo.list_filtered(limit=500)
        return [self._channel_row(channel) for channel in channels]

    async def get_preferences(
        self, *, channels: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        channels = channels or await self.get_channels()
        names = {channel["id"]: channel["name"] for channel in channels}
        preferences = await self.preference_repo.list_filtered(limit=500)
        return [
            {
                "id": item.id,
                "channel_id": item.channel_id,
                "channel_name": names.get(item.channel_id, "Unknown channel"),
                "event_type": item.event_type,
                "enabled": item.enabled,
                "minimum_priority": item.minimum_priority,
                "quiet_hours_start": item.quiet_hours_start,
                "quiet_hours_end": item.quiet_hours_end,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in preferences
        ]

    @staticmethod
    def _delivery_row(delivery, *, include_body: bool = False) -> dict[str, Any]:
        alert = delivery.alert_event
        row = {
            "id": delivery.id,
            "alert_event_id": delivery.alert_event_id,
            "channel_id": delivery.channel_id,
            "channel_name": delivery.channel.name,
            "provider": delivery.channel.provider,
            "notification_type": delivery.notification_type,
            "subject": delivery.subject,
            "status": delivery.status,
            "attempts": delivery.attempt_count,
            "created_at": delivery.created_at,
            "sent_at": delivery.sent_at,
            "next_retry_at": delivery.next_retry_at,
            "unread": delivery.status == "sent",
            "can_retry": delivery.status in {"failed", "retry_scheduled"},
            "can_mark_read": delivery.status == "sent",
            "alert_name": alert.alert_rule.name
            if alert is not None and alert.alert_rule is not None
            else None,
            "symbol": alert.stock.symbol if alert is not None and alert.stock is not None else None,
            "message": alert.message if alert is not None else None,
        }
        if include_body:
            secrets = [
                str(value)
                for key, value in (delivery.channel.config_json or {}).items()
                if key.lower()
                in {"api_key", "bot_token", "chat_id", "password", "recipient", "token"}
                and value
            ]
            row["rendered_body"] = delivery.rendered_body
            row["sanitized_error"] = sanitize_notification_error(
                delivery.error_message, secrets=secrets
            )
        return row

    async def get_recent_history(self, *, limit: int = 10) -> list[dict[str, Any]]:
        deliveries = await self.delivery_repo.list_history(limit=limit)
        return [self._delivery_row(delivery) for delivery in deliveries]

    async def get_history(
        self,
        *,
        filters: dict[str, Any],
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        offset = (page - 1) * page_size
        deliveries = await self.delivery_repo.list_history(
            **filters, offset=offset, limit=page_size
        )
        total = await self.delivery_repo.count_history(**filters)
        return {
            "deliveries": [self._delivery_row(item) for item in deliveries],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, ceil(total / page_size)),
        }

    async def get_delivery_detail(self, delivery_id: int) -> dict[str, Any] | None:
        delivery = await self.delivery_repo.get_detail(delivery_id)
        return self._delivery_row(delivery, include_body=True) if delivery else None

    async def get_dashboard_summary(self) -> dict[str, Any]:
        status = await self.get_status()
        recent = await self.get_recent_history(limit=5)
        if status["failed_count"]:
            overall_status = "attention"
        elif status["pending_count"] or status["retry_scheduled_count"]:
            overall_status = "pending"
        else:
            overall_status = "healthy"
        return {
            **status,
            "pending_retry_count": status["pending_count"] + status["retry_scheduled_count"],
            "recent": recent,
            "latest": recent[0] if recent else None,
            "overall_status": overall_status,
        }

    async def get_stock_history(self, symbol: str, *, limit: int = 5) -> list[dict[str, Any]]:
        deliveries = await self.delivery_repo.list_for_stock_symbol(symbol, limit=limit)
        return [self._delivery_row(item) for item in deliveries]

    # ------------------------------------------------------------ settings

    async def get_channel(self, channel_id: int) -> NotificationChannel | None:
        return await self.channel_repo.get(channel_id)

    async def create_channel(self, values: dict[str, Any]) -> NotificationChannel:
        try:
            payload = NotificationChannelCreate(**values)
            data = payload.model_dump()
            data["config_json"] = validate_provider_config(payload.provider, payload.config_json)
        except (TypeError, ValueError, ValidationError) as exc:
            message = _validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
            raise NotificationUIError(message, status_code=422) from exc
        if await self.channel_repo.get_by_name(payload.name) is not None:
            raise NotificationUIError(
                "A notification channel with this name already exists.", status_code=409
            )
        channel = NotificationChannel(**data)
        try:
            await self.channel_repo.add(channel)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise NotificationUIError(
                "A notification channel with this name already exists.", status_code=409
            ) from exc
        return channel

    async def update_channel(self, channel_id: int, values: dict[str, Any]) -> NotificationChannel:
        channel = await self.channel_repo.get(channel_id)
        if channel is None:
            raise NotificationUIError("Notification channel not found.", status_code=404)
        try:
            payload = NotificationChannelUpdate(**values)
        except ValidationError as exc:
            raise NotificationUIError(_validation_message(exc), status_code=422) from exc
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes:
            duplicate = await self.channel_repo.get_by_name(changes["name"])
            if duplicate is not None and duplicate.id != channel_id:
                raise NotificationUIError(
                    "A notification channel with this name already exists.", status_code=409
                )
        provider = changes.get("provider", channel.provider)
        provider_changed = provider != channel.provider
        if "config_json" in changes:
            supplied = changes["config_json"]
            config = {} if provider_changed else dict(channel.config_json or {})
            if supplied is None:
                config = {}
            else:
                for key, value in supplied.items():
                    if value == "********" and key in config:
                        continue
                    config[key] = value
            try:
                changes["config_json"] = validate_provider_config(provider, config)
            except (TypeError, ValueError) as exc:
                raise NotificationUIError(str(exc), status_code=422) from exc
        elif provider_changed:
            changes["config_json"] = {}
        for key, value in changes.items():
            setattr(channel, key, value)
        try:
            await self.session.commit()
            await self.session.refresh(channel)
        except IntegrityError as exc:
            await self.session.rollback()
            raise NotificationUIError(
                "A notification channel with this name already exists.", status_code=409
            ) from exc
        return channel

    async def delete_channel(self, channel_id: int) -> None:
        channel = await self.channel_repo.get(channel_id)
        if channel is None:
            raise NotificationUIError("Notification channel not found.", status_code=404)
        try:
            await self.session.delete(channel)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise NotificationUIError(
                "This channel is still referenced and cannot be deleted.", status_code=409
            ) from exc

    async def create_preference(self, values: dict[str, Any]) -> NotificationPreference:
        try:
            payload = NotificationPreferenceCreate(**values)
        except ValidationError as exc:
            raise NotificationUIError(_validation_message(exc), status_code=422) from exc
        if await self.channel_repo.get(payload.channel_id) is None:
            raise NotificationUIError("Notification channel not found.", status_code=404)
        if (
            await self.preference_repo.get_for_channel(payload.channel_id, payload.event_type)
            is not None
        ):
            raise NotificationUIError(
                "This preference already exists for the selected channel and event.",
                status_code=409,
            )
        preference = NotificationPreference(**payload.model_dump())
        try:
            await self.preference_repo.add(preference)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise NotificationUIError(
                "This preference already exists for the selected channel and event.",
                status_code=409,
            ) from exc
        return preference

    async def update_preference(
        self, preference_id: int, values: dict[str, Any]
    ) -> NotificationPreference:
        preference = await self.preference_repo.get(preference_id)
        if preference is None:
            raise NotificationUIError("Notification preference not found.", status_code=404)
        try:
            payload = NotificationPreferenceUpdate(**values)
        except ValidationError as exc:
            raise NotificationUIError(_validation_message(exc), status_code=422) from exc
        changes = payload.model_dump(exclude_unset=True)
        channel_id = changes.get("channel_id", preference.channel_id)
        if channel_id != preference.channel_id:
            if await self.channel_repo.get(channel_id) is None:
                raise NotificationUIError("Notification channel not found.", status_code=404)
        event_type = changes.get("event_type", preference.event_type)
        duplicate = await self.preference_repo.get_for_channel(channel_id, event_type)
        if duplicate is not None and duplicate.id != preference_id:
            raise NotificationUIError(
                "This preference already exists for the selected channel and event.",
                status_code=409,
            )
        quiet_start = changes.get("quiet_hours_start", preference.quiet_hours_start)
        quiet_end = changes.get("quiet_hours_end", preference.quiet_hours_end)
        try:
            validate_quiet_hours(quiet_start, quiet_end)
        except ValueError as exc:
            raise NotificationUIError(str(exc), status_code=422) from exc
        for key, value in changes.items():
            setattr(preference, key, value)
        try:
            await self.session.commit()
            await self.session.refresh(preference)
        except IntegrityError as exc:
            await self.session.rollback()
            raise NotificationUIError(
                "This preference already exists for the selected channel and event.",
                status_code=409,
            ) from exc
        return preference

    async def delete_preference(self, preference_id: int) -> None:
        preference = await self.preference_repo.get(preference_id)
        if preference is None:
            raise NotificationUIError("Notification preference not found.", status_code=404)
        await self.session.delete(preference)
        await self.session.commit()

    @staticmethod
    def channel_config_from_form(
        provider: str,
        form: Mapping[str, Any],
        *,
        current_provider: str | None = None,
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = dict(current_config or {}) if current_provider == provider else {}

        def set_text(key: str, field: str | None = None, *, preserve_blank: bool = False) -> None:
            raw = str(form.get(field or key, "")).strip()
            if raw:
                base[key] = raw
            elif not preserve_blank:
                base.pop(key, None)

        if provider == "telegram":
            set_text("bot_token", preserve_blank=True)
            set_text("chat_id", preserve_blank=True)
            base["html_mode"] = _as_bool(form.get("html_mode"))
            set_text("timeout")
        elif provider == "email":
            set_text("host")
            set_text("port")
            set_text("username")
            set_text("password", preserve_blank=True)
            set_text("from", "from_address")
            set_text("recipient", "to_address", preserve_blank=True)
            base["use_tls"] = _as_bool(form.get("use_tls"))
            base["use_starttls"] = _as_bool(form.get("use_starttls"))
            set_text("timeout")
        elif provider == "mock":
            set_text("recipient")
            set_text("fail_with")
        elif provider == "in_app":
            set_text("recipient")
        else:
            raise NotificationUIError("Unsupported notification provider.", status_code=422)
        return validate_provider_config(provider, base)
