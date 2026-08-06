from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping

from fastapi import HTTPException, Request, status

from app.core.config import settings

_CSRF_PART_COUNT = 2
_VALID_SAMESITE = frozenset({"lax", "strict", "none"})


def effective_csrf_secret() -> str:
    """Return the CSRF signing secret, falling back to the JWT secret safely."""
    secret = settings.csrf_secret_key.get_secret_value().strip()
    if not secret:
        secret = settings.jwt_secret_key.get_secret_value().strip()
    return secret


def _sign(value: str) -> str:
    digest = hmac.new(
        effective_csrf_secret().encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_csrf_token() -> str:
    """Produce a token formed from a random part plus its HMAC signature."""
    random = secrets.token_urlsafe(32)
    return f"{random}.{_sign(random)}"


def verify_csrf_token(token: str | None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != _CSRF_PART_COUNT:
        return False
    random, signature = parts
    expected = _sign(random)
    return hmac.compare_digest(signature, expected)


def _is_valid_samesite(value: str) -> bool:
    return value.lower() in _VALID_SAMESITE


def set_csrf_cookie(response, token: str | None = None) -> str:
    """Set a signed CSRF cookie (non-HttpOnly so forms and HTMX can echo it)."""
    if token is None:
        token = generate_csrf_token()
    samesite = settings.auth_cookie_samesite.lower()
    if not _is_valid_samesite(samesite):
        samesite = "lax"
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        samesite=samesite,
        secure=settings.auth_cookie_secure,
        path="/",
    )
    return token


def verify_request_csrf(request: Request, form: Mapping[str, str] | None = None) -> None:
    """Validate a CSRF token echoed back from the browser for a state-changing action."""
    cookie_value = request.cookies.get(settings.csrf_cookie_name, "")
    submitted = request.headers.get(settings.csrf_header_name, "")
    if not submitted and form is not None:
        submitted = str(form.get("_csrf_token", "") or "")
    if not cookie_value or not submitted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
    if not hmac.compare_digest(cookie_value, submitted) or not verify_csrf_token(submitted):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")