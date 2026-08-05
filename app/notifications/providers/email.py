from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.core.config import settings
from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.notifications.exceptions import (
    NotificationConfigError,
    NotificationError,
    NotificationPermanentError,
    NotificationTimeoutError,
    NotificationTransientError,
)


def _build_email_message(message: NotificationMessage, from_addr: str) -> EmailMessage:
    email = EmailMessage()
    email["From"] = from_addr
    email["To"] = message.recipient
    if message.subject:
        email["Subject"] = message.subject
    if message.html:
        email.set_content(message.text or "")
        email.add_alternative(message.html, subtype="html")
    else:
        email.set_content(message.text or "")
    return email


class EmailProvider(NotificationProvider):
    """Async email provider over stdlib ``smtplib``.

    The blocking SMTP call runs in a worker thread via :func:`asyncio.to_thread`
    so the public interface stays async. Supports STARTTLS/TLS, optional
    authentication, HTML with a plain-text fallback, and a connection timeout.
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.host = config.get("host") or settings.email_host
        self.port = int(config.get("port", settings.email_port))
        self.username = config.get("username") or settings.email_username
        self.password = config.get("password") or settings.email_password
        self.from_addr = config.get("from") or settings.email_from
        self.use_tls = bool(config.get("use_tls", settings.email_use_tls))
        self.use_starttls = bool(config.get("use_starttls", settings.email_use_starttls))
        self.timeout = float(config.get("timeout", settings.email_timeout))

    def provider_name(self) -> str:
        return "email"

    def _smtp_class(self) -> type[smtplib.SMTP]:
        if self.use_tls:
            return smtplib.SMTP_SSL
        return smtplib.SMTP

    def _send_sync(self, message: NotificationMessage, from_addr: str) -> None:
        email = _build_email_message(message, from_addr)
        smtp_cls = self._smtp_class()
        with smtp_cls(self.host, self.port, timeout=self.timeout) as smtp:
            if not self.use_tls and self.use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(email)

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.host:
            raise NotificationConfigError(
                "SMTP host is not configured (set EMAIL_HOST)"
            )
        if not message.recipient:
            raise NotificationConfigError("Email recipient is not configured")
        if not self.from_addr:
            raise NotificationConfigError("Email sender is not configured (set EMAIL_FROM)")

        try:
            await asyncio.to_thread(self._send_sync, message, self.from_addr)
        except smtplib.SMTPAuthenticationError as exc:
            raise NotificationPermanentError(f"SMTP authentication failed: {exc}") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise NotificationPermanentError(f"SMTP recipients refused: {exc}") from exc
        except (TimeoutError, OSError) as exc:
            raise NotificationTimeoutError(f"SMTP connection failed/timed out: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise NotificationTransientError(f"SMTP failure: {exc}") from exc

        return NotificationResult(
            provider=self.provider_name(),
            success=True,
            external_message_id=None,
        )

    async def health(self) -> bool:
        try:
            await asyncio.to_thread(self._ping)
            return True
        except NotificationError:
            return False

    def _ping(self) -> None:
        if not self.host:
            raise NotificationConfigError("SMTP host is not configured")
        smtp_cls = self._smtp_class()
        with smtp_cls(self.host, self.port, timeout=5) as smtp:
            smtp.quit()