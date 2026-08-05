from typing import Any

from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.notifications.exceptions import NotificationConfigError


class MockProvider(NotificationProvider):
    """Deterministic provider used as the safe default and in tests.

    Never touches the network and always succeeds unless ``fail_with`` is
    supplied in the constructor config. When configured to fail, the exact
    exception class is raised so callers can assert retry behaviour.
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._fail_with: str | None = config.get("fail_with")
        self._external_id_prefix = config.get("external_id_prefix", "mock-")

    def provider_name(self) -> str:
        return "mock"

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if self._fail_with:
            from app.notifications.exceptions import (
                NotificationPermanentError,
                NotificationTimeoutError,
                NotificationTransientError,
            )

            cls = {
                "timeout": NotificationTimeoutError,
                "transient": NotificationTransientError,
                "permanent": NotificationPermanentError,
            }.get(self._fail_with)
            if cls is None:
                raise NotificationConfigError(
                    f"Unknown mock fail_with value: {self._fail_with!r}"
                )
            raise cls(f"mock provider configured to fail with {self._fail_with}")

        external_id = f"{self._external_id_prefix}{message.recipient}"
        return NotificationResult(
            provider=self.provider_name(), success=True, external_message_id=external_id
        )

    async def health(self) -> bool:
        return True