from __future__ import annotations

from typing import Any

from app.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    NotificationResult,
)
from app.notifications.factory import get_provider
from app.notifications.renderer import NotificationRenderer


class NotificationManager:
    """Orchestrates provider resolution, rendering and sending.

    The manager holds no database access. It maps a provider name/config to a
    concrete provider, renders provider-specific content from a context, and
    normalizes provider results into a common shape.
    """

    def __init__(self, *, renderer: NotificationRenderer | None = None) -> None:
        self.renderer = renderer or NotificationRenderer()

    def resolve_provider(
        self, *, provider: str, config: dict[str, Any] | None = None
    ) -> NotificationProvider:
        return get_provider(provider, config=config)

    def render(
        self,
        *,
        notification_type: str,
        provider: str,
        context: dict,
    ) -> dict[str, str]:
        return self.renderer.render(
            notification_type=notification_type,
            provider=provider,
            context=context,
        )

    async def send(
        self,
        *,
        provider: NotificationProvider,
        message: NotificationMessage,
    ) -> NotificationResult:
        return await provider.send(message)

    def normalize(self, result: NotificationResult) -> dict[str, Any]:
        """Return a plain dict of the interesting result fields."""
        return {
            "success": result.success,
            "provider": result.provider,
            "external_message_id": result.external_message_id,
            "retry_after": result.retry_after,
        }