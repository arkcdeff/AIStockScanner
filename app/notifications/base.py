from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class NotificationMessage:
    """A normalized message exchanged with providers.

    ``recipient`` is a provider-specific destination (chat id, email address).
    ``text`` is plain text and ``html`` optional rich content. The provider
    decides which representation to use.
    """

    recipient: str
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationResult:
    """The outcome of a single send attempt."""

    provider: str
    success: bool
    external_message_id: str | None = None
    tried_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    retry_after: int | None = None


class NotificationProvider(ABC):
    """Abstract notification channel provider.

    Implementations translate a :class:`NotificationMessage` to the transport
    of a specific channel (Telegram, email, in-app, mock). Providers must never
    raise unhandled exceptions from :meth:`send`; failures are surfaced via an
    exception imported from :mod:`app.notifications.exceptions`.
    """

    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'telegram')."""

    @abstractmethod
    async def send(self, message: NotificationMessage) -> NotificationResult:
        """Send ``message`` and return the delivery result.

        Raises :class:`NotificationSendError` subclasses on failure.
        """

    @abstractmethod
    async def health(self) -> bool:
        """Return True when the provider is usable/healthy."""