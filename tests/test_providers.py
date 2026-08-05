import httpx
import pytest

from app.notifications.base import NotificationMessage
from app.notifications.exceptions import (
    NotificationConfigError,
    NotificationPermanentError,
    NotificationProviderError,
    NotificationTimeoutError,
    NotificationTransientError,
)
from app.notifications.factory import get_provider
from app.notifications.providers.email import EmailProvider
from app.notifications.providers.in_app import InAppProvider
from app.notifications.providers.mock import MockProvider
from app.notifications.providers.telegram import TelegramProvider, _escape_html, _split_message


def _message(recipient: str = "dest", text: str = "hello", html: str | None = None):
    return NotificationMessage(recipient=recipient, subject="s", text=text, html=html)


# ------------------------------------------------------------------ factory


def test_factory_returns_mock_by_default():
    provider = get_provider()
    assert isinstance(provider, MockProvider)


def test_factory_known_providers():
    assert isinstance(get_provider("mock"), MockProvider)
    assert isinstance(get_provider("telegram"), TelegramProvider)
    assert isinstance(get_provider("email"), EmailProvider)
    assert isinstance(get_provider("in_app"), InAppProvider)


def test_factory_unknown_provider_raises():
    with pytest.raises(NotificationProviderError):
        get_provider("slack")


# ------------------------------------------------------------------ mock


@pytest.mark.asyncio
async def test_mock_provider_succeeds():
    provider = MockProvider()
    result = await provider.send(_message(recipient="A"))
    assert result.success is True
    assert result.external_message_id == "mock-A"
    assert await provider.health() is True


@pytest.mark.asyncio
async def test_mock_provider_configured_timeout_failure():
    provider = MockProvider(config={"fail_with": "timeout"})
    with pytest.raises(NotificationTimeoutError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_mock_provider_configured_transient_failure():
    provider = MockProvider(config={"fail_with": "transient"})
    with pytest.raises(NotificationTransientError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_mock_provider_configured_permanent_failure():
    provider = MockProvider(config={"fail_with": "permanent"})
    with pytest.raises(NotificationPermanentError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_mock_provider_unknown_fail_mode_raises_config_error():
    provider = MockProvider(config={"fail_with": "nope"})
    with pytest.raises(NotificationConfigError):
        await provider.send(_message())


# ---------------------------------------------------------------- telegram


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})


def _telegram(handler, **config) -> TelegramProvider:
    return TelegramProvider(
        config={
            "bot_token": "TOKEN",
            "chat_id": "123",
            "base_url": "http://tg/{method}",
            "transport": httpx.MockTransport(handler),
            **config,
        }
    )


@pytest.mark.asyncio
async def test_telegram_no_token_raises_config_error():
    provider = TelegramProvider(config={})
    with pytest.raises(NotificationConfigError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_telegram_no_chat_raises_config_error():
    provider = TelegramProvider(config={"bot_token": "TOKEN"})
    with pytest.raises(NotificationConfigError):
        await provider.send(_message(recipient=""))


@pytest.mark.asyncio
async def test_telegram_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    provider = _telegram(handler)
    result = await provider.send(_message(recipient="abc", text="hi"))
    assert result.success is True
    assert result.external_message_id == "7"
    assert captured["path"].endswith("/sendMessage")
    assert "chat_id=abc" in captured["body"]


@pytest.mark.asyncio
async def test_telegram_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    provider = _telegram(handler, timeout=0.001)
    with pytest.raises(NotificationTimeoutError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_telegram_permanent_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False})

    provider = _telegram(handler)
    with pytest.raises(NotificationPermanentError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_telegram_transient_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"ok": False})

    provider = _telegram(handler)
    with pytest.raises(NotificationTransientError):
        await provider.send(_message())


def test_escape_html_escapes_unsafe_tags():
    assert _escape_html('<img src=x onerror="alert(1)">') == (
        "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
    )


def test_split_message():
    chunks = _split_message("a" * 5000, 100)
    assert len(chunks) == 50
    assert all(len(c) <= 100 for c in chunks)
    assert _split_message("short", 100) == ["short"]


def test_parse_retry_after_seconds():
    provider = TelegramProvider(config={"bot_token": "T", "chat_id": "1"})
    assert provider._parse_retry_after("12") == 12
    assert provider._parse_retry_after("") is None


@pytest.mark.asyncio
async def test_telegram_health_true():
    provider = _telegram(_ok_handler)
    assert await provider.health() is True


@pytest.mark.asyncio
async def test_telegram_health_false_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    provider = _telegram(handler, timeout=0.001)
    assert await provider.health() is False


# ------------------------------------------------------------------ email


@pytest.mark.asyncio
async def test_email_success(monkeypatch):
    calls = {}

    def fake_send_sync(provider, message, from_addr):
        calls["to"] = message.recipient
        calls["subject"] = message.subject

    monkeypatch.setattr(EmailProvider, "_send_sync", fake_send_sync, raising=True)
    provider = EmailProvider(
        config={"host": "smtp.test", "from": "a@b.com", "use_tls": False, "use_starttls": True}
    )
    result = await provider.send(_message(recipient="x@y.com"))
    assert result.success is True
    assert calls["to"] == "x@y.com"
    assert calls["subject"] == "s"


@pytest.mark.asyncio
async def test_email_no_host_raises():
    provider = EmailProvider(config={})
    with pytest.raises(NotificationConfigError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_email_no_sender_raises():
    provider = EmailProvider(config={"host": "smtp.test"})
    with pytest.raises(NotificationConfigError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_email_timeout(monkeypatch):
    def fake_send_sync(provider, message, from_addr):
        raise TimeoutError("smtp timed out")

    monkeypatch.setattr(EmailProvider, "_send_sync", fake_send_sync, raising=True)
    provider = EmailProvider(config={"host": "smtp.test", "from": "a@b.com"})
    with pytest.raises(NotificationTimeoutError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_email_auth_failure(monkeypatch):
    import smtplib

    def fake_send_sync(provider, message, from_addr):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(EmailProvider, "_send_sync", fake_send_sync, raising=True)
    provider = EmailProvider(config={"host": "smtp.test", "from": "a@b.com"})
    with pytest.raises(NotificationPermanentError):
        await provider.send(_message())


@pytest.mark.asyncio
async def test_email_health(monkeypatch):
    def fake_ping(provider):
        return None

    monkeypatch.setattr(EmailProvider, "_ping", fake_ping, raising=True)
    provider = EmailProvider(config={"host": "smtp.test", "from": "a@b.com"})
    assert await provider.health() is True


# ------------------------------------------------------------------ in_app


@pytest.mark.asyncio
async def test_in_app_success():
    provider = InAppProvider()
    result = await provider.send(_message(recipient="user1"))
    assert result.success is True
    assert result.external_message_id == "inapp-user1"
    assert await provider.health() is True