from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.notifications.exceptions import (
    NotificationError,
    NotificationPermanentError,
    NotificationTimeoutError,
    NotificationTransientError,
)

RETRYABLE_STATUSES = frozenset({"failed", "retry_scheduled"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RetryEngine:
    """Decides whether a failed delivery should be retried and when.

    Policy comes from settings:
    - max attempts: ``notification_max_attempts``
    - base delay: ``notification_retry_minutes``
    - stale threshold: ``notification_stale_minutes``
    """

    def __init__(
        self,
        *,
        max_attempts: int | None = None,
        retry_minutes: int | None = None,
        stale_minutes: int | None = None,
    ) -> None:
        self.max_attempts = max_attempts or settings.notification_max_attempts
        self.retry_minutes = retry_minutes or settings.notification_retry_minutes
        self.stale_minutes = stale_minutes or settings.notification_stale_minutes

    def is_retryable(self, error: Exception) -> bool:
        """Return True when the error represents a retryable transient failure."""
        if isinstance(error, (NotificationTimeoutError, NotificationTransientError)):
            return True
        return False

    def is_permanent(self, error: Exception) -> bool:
        if isinstance(error, NotificationPermanentError):
            return True
        if isinstance(error, NotificationError) and not self.is_retryable(error):
            return True
        return False

    def should_retry(self, *, attempt_count: int) -> bool:
        """Return True when a new attempt is still allowed."""
        return attempt_count < self.max_attempts

    def next_retry_at(self, *, retry_count: int = 1) -> datetime:
        """Return the timestamp for the next scheduled retry.

        Retries back off linearly (delay * retry_count) but never below the
        configured base delay.
        """
        minutes = self.retry_minutes * max(1, retry_count)
        return _utcnow() + timedelta(minutes=minutes)

    def is_stale_sending(self, updated_at: datetime | None, *, now: datetime | None = None) -> bool:
        """Return True when a ``sending`` row has not progressed for too long."""
        if updated_at is None:
            return False
        now = now or _utcnow()
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return now - updated_at > timedelta(minutes=self.stale_minutes)