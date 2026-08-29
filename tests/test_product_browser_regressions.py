"""Real-browser regression coverage for Stage 1R-R1 page contracts and CSRF."""

from __future__ import annotations

import re
import uuid

import pytest

from app.core.config import settings
from app.services.scanner_engine import ScannerService
from tests.auth_helpers import superuser_client


def _form_csrf(html: str, action: str) -> str:
    match = re.search(
        rf'<form[^>]+action="{re.escape(action)}"[^>]*>(.*?)</form>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, f"form {action} was not rendered"
    token = re.search(
        r'name="_csrf_token"\s+value="([^"]+)"', match.group(1)
    )
    assert token is not None, f"form {action} has no CSRF field"
    assert token.group(1), f"form {action} has an empty CSRF field"
    return token.group(1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/scanner-library",
        "/scanner-library/",
        "/scanner-library?category=trend",
    ],
)
async def test_scanner_library_render_paths_honor_context_contract(path: str) -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert "Scanner Library" in response.text
    assert "scanner_requirements" not in response.text


@pytest.mark.asyncio
async def test_analytics_full_page_renders_controlled_not_ready_state() -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get("/analytics")

    assert response.status_code == 200
    assert "Analytics not ready" in response.text
    assert "Calculate indicators" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["top-rated", "trending", "breakouts"])
async def test_analytics_htmx_tabs_render_when_no_rows_exist(kind: str) -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get(
            "/analytics/table",
            params={"kind": kind},
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 200
    assert "No analytics data available" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "action"),
    [
        ("/market-data", "/market-data/download-latest"),
        ("/scanner-run", "/scanner-run/run"),
        ("/indicators", "/indicators/calculate"),
        ("/pipeline", "/pipeline/run-latest"),
        ("/scheduler", "/scheduler/run-delivery"),
        ("/scheduler", "/scheduler/run-summary"),
    ],
)
async def test_stage1_mutation_forms_render_valid_csrf(path: str, action: str) -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get(path)
        form_token = _form_csrf(response.text, action)

        assert response.status_code == 200
        assert form_token == client.cookies.get(settings.csrf_cookie_name)


@pytest.mark.asyncio
async def test_csrf_token_remains_valid_across_multiple_open_pages() -> None:
    async with superuser_client(browser=True) as client:
        market_data = await client.get("/market-data")
        original = _form_csrf(
            market_data.text, "/market-data/download-latest"
        )

        await client.get("/scheduler")

        assert client.cookies.get(settings.csrf_cookie_name) == original


@pytest.mark.asyncio
async def test_scanner_html_post_accepts_valid_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_scanner(self, scan_code: str):
        return []

    async def fake_persist_results(self, results):
        return None

    monkeypatch.setattr(ScannerService, "run_scanner", fake_run_scanner)
    monkeypatch.setattr(ScannerService, "persist_results", fake_persist_results)

    async with superuser_client(browser=True) as client:
        page = await client.get("/scanner-run")
        token = _form_csrf(page.text, "/scanner-run/run")
        scan_code = re.search(r'<option value="([^"]+)"', page.text)
        assert scan_code is not None

        rejected = await client.post(
            "/scanner-run/run", data={"scan_code": scan_code.group(1)}
        )
        accepted = await client.post(
            "/scanner-run/run",
            data={"scan_code": scan_code.group(1), "_csrf_token": token},
        )

    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "CSRF validation failed"
    assert accepted.status_code == 303
    assert "CSRF validation failed" not in accepted.text


@pytest.mark.asyncio
async def test_representative_htmx_post_accepts_csrf_header() -> None:
    async with superuser_client(browser=True) as client:
        page = await client.get("/admin/permissions")
        token = client.cookies.get(settings.csrf_cookie_name)
        code = f"browser.qa.{uuid.uuid4().hex}"

        response = await client.post(
            "/admin/permissions/create",
            data={"code": code, "description": "Browser CSRF regression"},
            headers={"HX-Request": "true", settings.csrf_header_name: token or ""},
        )

    assert page.status_code == 200
    assert response.status_code == 201
    assert code in response.text
    assert "CSRF validation failed" not in response.text


@pytest.mark.asyncio
async def test_settings_htmx_target_and_safe_preference_post() -> None:
    async with superuser_client(browser=True) as client:
        page = await client.get("/settings")
        token = _form_csrf(page.text, "/preferences/update")

        response = await client.post(
            "/preferences/update",
            data={
                "_csrf_token": token,
                "theme": "system",
                "timezone": "UTC",
                "language": "en",
            },
            headers={"HX-Request": "true"},
        )

    assert page.status_code == 200
    assert 'id="settings-form"' in page.text
    assert 'hx-target="#settings-form"' in page.text
    assert response.status_code == 200
    assert "Preferences updated." in response.text
    assert "CSRF validation failed" not in response.text
