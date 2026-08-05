from typing import Any

from app.core.config import settings
from app.notifications.base import NotificationProvider
from app.notifications.exceptions import NotificationProviderError
from app.notifications.providers.email import EmailProvider
from app.notifications.providers.in_app import InAppProvider
from app.notifications.providers.mock import MockProvider
from app.notifications.providers.telegram import TelegramProvider

_PROVIDERS: dict[str, type[NotificationProvider]] = {
    "mock": MockProvider,
    "telegram": TelegramProvider,
    "email": EmailProvider,
    "in_app": InAppProvider,
}


def get_provider(
    name: str | None = None, *, config: dict[str, Any] | None = None
) -> NotificationProvider:
    """Instantiate a notification provider by name (default from settings)."""
    selected = (name or settings.notification_provider).strip().lower()
    provider_cls = _PROVIDERS.get(selected)
    if provider_cls is None:
        raise NotificationProviderError(
            f"Unsupported notification provider: {selected!r}. "
            f"Supported providers: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider_cls(config=config)