from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

import httpx

from app.core.config import settings
from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.notifications.exceptions import (
    NotificationConfigError,
    NotificationError,
    NotificationPermanentError,
    NotificationTimeoutError,
    NotificationTransientError,
)

DEFAULT_BASE_URL = "https://api.telegram.org/bot{token}/{method}"


def _escape_html(text: str) -> str:
    """Escape text so it is safe for Telegram's ``parse_mode=HTML``.

    Escapes ``&``, ``<``, ``>`` and quotes so an attacker-supplied string cannot
    inject arbitrary HTML markup into a message.
    """
    return escape(text, quote=True)


def _split_message(text: str, max_length: int) -> list[str]:
    """Split ``text`` into chunks no larger than ``max_length`` chars."""
    text = text or ""
    if max_length <= 0 or len(text) <= max_length:
        return [text]
    first, rest = text[:max_length], text[max_length:]
    return [first, *_split_message(rest, max_length)]


class TelegramProvider(NotificationProvider):
    """Async Telegram Bot API provider built on httpx.

    Supports HTML parse mode with unsafe-HTML escaping, message splitting for the
    4096-character limit, ``Retry-After`` parsing, and classification of retryable
    vs permanent failures. A custom ``transport`` may be injected through config
    for deterministic testing.
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.bot_token = config.get("bot_token") or settings.telegram_bot_token
        self.default_chat_id = config.get("chat_id") or settings.telegram_default_chat_id
        self.base_url = config.get("base_url") or DEFAULT_BASE_URL
        self.html_mode = bool(config.get("html_mode", settings.telegram_html_mode))
        self.max_length = int(
            config.get("max_message_length", settings.telegram_max_message_length)
        )
        self.timeout = float(config.get("timeout", settings.notification_timeout))
        self._transport = config.get("transport")

    def provider_name(self) -> str:
        return "telegram"

    def _endpoint(self, method: str) -> str:
        return self.base_url.format(token=self.bot_token, method=method)

    @staticmethod
    def _parse_retry_after(value: str | None) -> int | None:
        raw = (value or "").strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        try:
            delay = (
                datetime.fromisoformat(raw).astimezone(UTC) - datetime.now(UTC)
            ).total_seconds()
            return max(0, int(delay))
        except ValueError:
            return None

    def _retry_after_from_response(self, response: httpx.Response) -> int | None:
        return self._parse_retry_after(response.headers.get("Retry-After"))

    def _handle_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in (429, 500, 502, 503, 504):
            raise NotificationTransientError(
                f"Telegram transient failure (HTTP {response.status_code})",
            )
        raise NotificationPermanentError(
            f"Telegram permanent failure (HTTP {response.status_code})",
        )

    def _build_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _post(self, client: httpx.AsyncClient, method: str, payload: dict) -> httpx.Response:
        try:
            return await client.post(self._endpoint(method), data=payload)
        except httpx.TimeoutException as exc:
            raise NotificationTimeoutError(f"Telegram request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise NotificationTransientError(f"Telegram network error: {exc}") from exc

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.bot_token:
            raise NotificationConfigError(
                "Telegram bot token is not configured (set TELEGRAM_BOT_TOKEN)"
            )
        if not message.recipient and not self.default_chat_id:
            raise NotificationConfigError("Telegram chat id is not configured")

        chat_id = message.recipient or self.default_chat_id
        content = message.html if (self.html_mode and message.html) else (message.text or "")
        chunks = _split_message(content, self.max_length)

        retry_after: int | None = None
        external_id: str | None = None
        async with self._build_client() as client:
            for chunk in chunks:
                payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
                if self.html_mode:
                    payload["parse_mode"] = "HTML"
                    payload["text"] = _escape_html(chunk)
                response = await self._post(client, "sendMessage", payload)
                self._handle_status(response)
                result_payload = response.json()
                external_id = result_payload.get("result", {}).get("message_id")
                retry_after = self._retry_after_from_response(response)

        return NotificationResult(
            provider=self.provider_name(),
            success=True,
            external_message_id=str(external_id or ""),
            retry_after=retry_after,
        )

    async def health(self) -> bool:
        try:
            async with self._build_client() as client:
                response = await self._post(client, "getMe", {})
                return response.is_success
        except NotificationError:
            return False