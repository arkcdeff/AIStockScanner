"""Tests for test database configuration and production protection guards in tests/conftest.py."""

import os
from unittest.mock import patch

import pytest

from tests.conftest import (
    _configure_test_database_urls,
    _sanitize_url_for_display,
)


def test_sanitize_url_masks_password():
    url = "postgresql+asyncpg://postgres:supersecretpassword@localhost:5432/test"
    sanitized = _sanitize_url_for_display(url)
    assert "supersecretpassword" not in sanitized
    assert "postgres:***@localhost:5432" in sanitized


def test_configure_test_db_urls_from_explicit_env():
    with patch.dict(
        os.environ,
        {
            "TEST_DATABASE_URL_ASYNC": "postgresql+asyncpg://user:pass@localhost:5432/my_test_db",
            "TEST_DATABASE_URL_SYNC": "postgresql+psycopg://user:pass@localhost:5432/my_test_db",
        },
        clear=False,
    ):
        async_url, sync_url = _configure_test_database_urls()
        assert async_url == "postgresql+asyncpg://user:pass@localhost:5432/my_test_db"
        assert sync_url == "postgresql+psycopg://user:pass@localhost:5432/my_test_db"


def test_configure_test_db_urls_derives_test_db_from_main_app_url():
    with patch.dict(
        os.environ,
        {
            "TEST_DATABASE_URL_ASYNC": "",
            "TEST_DATABASE_URL_SYNC": "",
            "DATABASE_URL_ASYNC": "postgresql+asyncpg://user:pass@localhost:5432/ai_stock_scanner",
            "DATABASE_URL_SYNC": "postgresql+psycopg://user:pass@localhost:5432/ai_stock_scanner",
        },
        clear=False,
    ):
        async_url, sync_url = _configure_test_database_urls()
        assert async_url.endswith("/test")
        assert sync_url.endswith("/test")
        assert "ai_stock_scanner" not in async_url
        assert "ai_stock_scanner" not in sync_url


def test_configure_test_db_safety_rejects_production_db():
    with patch.dict(
        os.environ,
        {
            "TEST_DATABASE_URL_ASYNC": (
                "postgresql+asyncpg://user:pass@prod-server:5432/production"
            ),
        },
        clear=False,
    ):
        with pytest.raises(
            RuntimeError,
            match=(
                "SAFETY ERROR: Refusing to run destructive tests against non-test "
                "database: 'production'"
            ),
        ):
            _configure_test_database_urls()


def test_configure_test_db_safety_rejects_non_test_db():
    with patch.dict(
        os.environ,
        {
            "TEST_DATABASE_URL_ASYNC": (
                "postgresql+asyncpg://user:pass@localhost:5432/my_live_trading"
            ),
        },
        clear=False,
    ):
        with pytest.raises(
            RuntimeError,
            match="SAFETY ERROR: Refusing to run destructive tests against non-test database",
        ):
            _configure_test_database_urls()
