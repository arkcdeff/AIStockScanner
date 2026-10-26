"""Safety regressions for the G.8 pre-repair CLI.

These tests use a fake session context only. They must never connect to a database.
"""
from __future__ import annotations

import pytest

from scripts import g8_pre_repair as repair


@pytest.mark.parametrize("name", ["postgres", "production", "prod", "master", "PRODUCTION", " Prod ", "MASTER"])
def test_forbidden_database_names_are_case_insensitive(name: str) -> None:
    assert repair._is_forbidden_db(name) is True


@pytest.mark.parametrize("name", ["ai_stock_scanner", "market_data", "development"])
def test_normal_database_names_are_not_forbidden(name: str) -> None:
    assert repair._is_forbidden_db(name) is False


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeFactory:
    def __call__(self):
        return _FakeSession()


@pytest.mark.parametrize("mode", ["--verify", "--repair"])
def test_cli_refuses_forbidden_database_before_sql(monkeypatch, capsys, mode: str) -> None:
    monkeypatch.setattr(repair, "get_session_factory", lambda: _FakeFactory())
    monkeypatch.setattr(repair, "_get_db_name", lambda _session: "PRODUCTION")
    monkeypatch.setattr(repair, "_is_test_db", lambda _name: False)
    monkeypatch.setattr(
        repair,
        "verify",
        lambda _session: pytest.fail("SQL verification must not run for a forbidden database"),
    )
    monkeypatch.setattr(
        repair,
        "repair_c1",
        lambda _session: pytest.fail("C1 repair must not run for a forbidden database"),
    )
    monkeypatch.setattr(
        repair,
        "repair_c2",
        lambda _session: pytest.fail("C2 repair must not run for a forbidden database"),
    )
    monkeypatch.setattr("sys.argv", ["g8_pre_repair", mode])

    with pytest.raises(SystemExit) as exc:
        import asyncio
        asyncio.run(repair.main())

    assert exc.value.code == 1
    assert "forbidden database" in capsys.readouterr().out.lower()


def test_cli_rejects_verify_and_repair_together_before_session(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["g8_pre_repair", "--verify", "--repair"])
    monkeypatch.setattr(
        repair,
        "get_session_factory",
        lambda: pytest.fail("Session factory must not be created for conflicting modes"),
    )

    with pytest.raises(SystemExit) as exc:
        import asyncio
        asyncio.run(repair.main())

    assert exc.value.code == 2
