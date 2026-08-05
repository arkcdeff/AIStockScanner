class NotificationError(RuntimeError):
    """Base exception for the notification provider layer."""


class NotificationProviderError(NotificationError):
    """Raised when a provider cannot be selected or instantiated."""


class NotificationConfigError(NotificationError):
    """Raised when provider configuration is missing or invalid."""


class NotificationSendError(NotificationError):
    """Raised when a provider fails to send a message."""


class NotificationTimeoutError(NotificationSendError):
    """Raised when a provider request times out (retryable)."""


class NotificationTransientError(NotificationSendError):
    """Raised on transient failures that may succeed on retry."""


class NotificationPermanentError(NotificationSendError):
    """Raised on permanent failures that should not be retried."""