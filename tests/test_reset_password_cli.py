from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import typer

from app.cli import reset_password as reset_password_cli
from app.cli.reset_password import _read_password
from app.services.user_admin import UserNotFoundError

PASSWORD = "Valid-Password-7!"
NEW_PASSWORD = "Changed-Password-8!"


class TestReadPassword:
    def test_reads_password_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_RESET_PASSWORD", NEW_PASSWORD)
        assert _read_password("TEST_RESET_PASSWORD") == NEW_PASSWORD

    def test_raises_when_env_var_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_MISSING_PASSWORD", raising=False)
        with pytest.raises(typer.BadParameter):
            _read_password("TEST_MISSING_PASSWORD")


class TestResetPasswordCli:
    def test_main_resets_password_from_env_without_printing_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("TEST_RESET_PASSWORD", NEW_PASSWORD)

        async def fake_reset_password(**values: object) -> tuple[str, str | None]:
            assert values["username"] == "admin"
            assert values["password"] == NEW_PASSWORD
            assert values["generate"] is False
            return "admin", None

        monkeypatch.setattr(reset_password_cli, "_reset_password", fake_reset_password)

        result = reset_password_cli.main(
            ["--username", "admin", "--password-env", "TEST_RESET_PASSWORD"]
        )

        captured = capsys.readouterr()
        assert result == 0
        assert "reset successfully" in captured.out
        assert NEW_PASSWORD not in captured.out
        assert NEW_PASSWORD not in captured.err

    def test_main_generate_does_not_prompt_for_password(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        async def fake_reset_password(**values: object) -> tuple[str, str | None]:
            assert values["username"] == "admin"
            assert values["generate"] is True
            return "admin", "Temp-Password-1!"

        monkeypatch.setattr(reset_password_cli, "_reset_password", fake_reset_password)
        monkeypatch.setattr(reset_password_cli, "_read_password", AsyncMock())

        result = reset_password_cli.main(["--username", "admin", "--generate"])

        captured = capsys.readouterr()
        assert result == 0
        assert "Temporary password (shown once): Temp-Password-1!" in captured.out

    def test_main_requires_user_identifier(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = reset_password_cli.main([])
        captured = capsys.readouterr()
        assert result == 1
        assert "Specify the user" in captured.err

    def test_main_handles_user_not_found(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def fake_reset_password(**values: object) -> tuple[str, str | None]:
            raise UserNotFoundError("User 'nobody' not found")

        monkeypatch.setattr(reset_password_cli, "_reset_password", fake_reset_password)

        result = reset_password_cli.main(["--username", "nobody", "--generate"])

        captured = capsys.readouterr()
        assert result == 1
        assert "Password reset failed" in captured.err