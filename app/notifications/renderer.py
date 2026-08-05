from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.notifications.exceptions import NotificationConfigError

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_TEMPLATE_NAMES = {
    "alert": {
        "telegram": "alert_telegram.txt",
        "email": "alert_email.txt",
        "email_html": "alert_email.html",
    },
    "daily_summary": {
        "telegram": "daily_summary_telegram.txt",
        "email": "daily_summary_email.txt",
        "email_html": "daily_summary_email.html",
    },
}

_HTML_PROVIDERS = frozenset({"email"})

_NA = "N/A"


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )


def _normalize(value: object) -> Any:
    if value is None:
        return _NA
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, bool):
        return value
    return value


class NotificationRenderer:
    """Renders provider-specific notification content from a plain context dict.

    Missing or ``None`` values are rendered as ``N/A`` so optional analytics
    never break rendering. HTML templates are auto-escaped so user-supplied
    notes cannot inject unsafe markup. Output is deterministic for tests.
    """

    def render(self, *, notification_type: str, provider: str, context: dict) -> dict[str, str]:
        """Return ``{"subject": ..., "text": ..., "html": ...}``.

        ``provider`` is a provider name such as ``telegram`` or ``email``.
        ``email`` produces both plain-text and HTML bodies; all others produce
        plain text only.
        """
        template_set = _TEMPLATE_NAMES.get(notification_type)
        if template_set is None:
            raise NotificationConfigError(f"Unknown notification type: {notification_type!r}")

        template_name = template_set.get(provider)
        if template_name is None:
            template_name = template_set["telegram"]
        html_name = template_set.get("email_html") if provider in _HTML_PROVIDERS else None

        safe_context: dict[str, Any] = {
            key: _normalize(value) for key, value in context.items()
        }

        subject = self._subject(notification_type, safe_context)
        text = _env().get_template(template_name).render(safe_context)
        html = None
        if html_name is not None:
            html = _env().get_template(html_name).render(safe_context)
        return {"subject": subject, "text": text, "html": html}

    @staticmethod
    def _subject(notification_type: str, context: dict[str, Any]) -> str:
        if notification_type == "alert":
            return f"Alert: {context.get('symbol', _NA)} - {context.get('alert_name', _NA)}"
        return f"Daily Summary - {context.get('summary_date', _NA)}"


def templates_dir() -> Path:
    return _TEMPLATES_DIR
