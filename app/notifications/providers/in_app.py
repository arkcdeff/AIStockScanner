from typing import Any

from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.notifications.exceptions import NotificationConfigError


class InAppProvider(NotificationProvider):
    """In-app notification channel.

    This task builds only the provider abstraction; it does not persist to the
    database. Database integration is added in a later task (15.3). The provider
    contacts no external service and always returns a successful result.
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._external_id_prefix = config.get("external_id_prefix", "inapp-")
        if config.get("fail_if_not_configured", False):
            raise NotificationConfigError("in_app provider requires a recipient at send time")

    def provider_name(self) -> str:
        return "in_app"

    async def send(self, message: NotificationMessage) -> NotificationResult:
        external_id = f"{self._external_id_prefix}{message.recipient}"
        return NotificationResult(
            provider=self.provider_name(), success=True, external_message_id=external_id
        )

    async def health(self) -> bool:
        return True