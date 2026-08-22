from __future__ import annotations

import asyncio
import getpass
import os
import sys
from typing import Annotated

import typer
from rich.console import Console

from app.auth.exceptions import PasswordPolicyError
from app.db.session import dispose_engine, get_session_factory
from app.services.user_admin import UserAdminService, UserNotFoundError

app = typer.Typer()
console = Console()


def _read_password(password_env: str | None) -> str | None:
    """Read password from env var, or prompt interactively; return None for auto-generate."""
    if password_env:
        password = os.environ.get(password_env)
        if password is None:
            raise typer.BadParameter(
                f"Password environment variable '{password_env}' is not set"
            )
        return password
    password = getpass.getpass("New password (leave blank to auto-generate): ")
    if not password:
        return None
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        console.print("Password confirmation does not match", style="red")
        raise typer.Exit(1)
    return password


async def _reset_password(
    *,
    user_id: int | None,
    username: str | None,
    email: str | None,
    password: str | None,
    generate: bool,
) -> tuple[str, str | None]:
    try:
        async with get_session_factory()() as session:
            service = UserAdminService(session)
            # Resolve user by id, username, or email
            if user_id is not None:
                user = await service.get_user(user_id)
            elif username:
                from app.repositories.auth import UserRepository
                user_repo = UserRepository(session)
                user = await user_repo.get_by_username(username)
                if user is None:
                    raise UserNotFoundError(f"User '{username}' not found")
            elif email:
                from app.repositories.auth import UserRepository
                user_repo = UserRepository(session)
                user = await user_repo.get_by_email_case_insensitive(email)
                if user is None:
                    raise UserNotFoundError(f"User with email '{email}' not found")
            else:
                raise UserNotFoundError("Specify user via --user-id, --username, or --email")

            result = await service.reset_password(
                user.id,
                new_password=password,
                generate=generate,
                actor_user_id=user.id,
            )
            return user.username, result.temporary_password
    finally:
        await dispose_engine()


@app.command()
def reset_password(
    user_id: Annotated[
        int | None, typer.Option(help="User ID of the account to reset")
    ] = None,
    username: Annotated[
        str | None, typer.Option(help="Username of the account to reset")
    ] = None,
    email: Annotated[
        str | None, typer.Option(help="Email of the account to reset")
    ] = None,
    password_env: Annotated[
        str | None,
        typer.Option(
            help="Read the new password from this environment variable "
            "(omit to prompt or auto-generate)"
        ),
    ] = None,
    generate: Annotated[
        bool,
        typer.Option(
            help="Generate a strong random password (cannot be used with --password-env)"
        ),
    ] = False,
) -> None:
    """Reset a user's password (admin or any user)."""
    if not any([user_id, username, email]):
        console.print(
            "Specify the user: --user-id <id>, --username <name>, or --email <email>",
            style="red",
        )
        raise typer.Exit(1)

    try:
        password = None
        effective_generate = generate
        if not generate:
            password = _read_password(password_env)
            effective_generate = password is None and password_env is None

        resolved_username, temp_password = asyncio.run(
            _reset_password(
                user_id=user_id,
                username=username,
                email=email,
                password=password,
                generate=effective_generate,
            )
        )
        if temp_password:
            console.print(
                f"Password for '{resolved_username}' reset successfully.\n"
                f"Temporary password (shown once): {temp_password}",
                style="green",
            )
        else:
            console.print(
                f"Password for '{resolved_username}' reset successfully.",
                style="green",
            )
    except UserNotFoundError as exc:
        console.print(f"Password reset failed: {exc}", style="red")
        raise typer.Exit(1) from exc
    except PasswordPolicyError as exc:
        console.print(
            f"Password does not meet policy: {' '.join(exc.violations)}", style="red"
        )
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"Password reset failed: {exc}", style="red")
        raise typer.Exit(1) from exc


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.cli.reset_password",
        description="Reset a user's password.",
    )
    parser.add_argument("--user-id", type=int, help="User ID of the account")
    parser.add_argument("--username", help="Username of the account")
    parser.add_argument("--email", help="Email of the account")
    parser.add_argument(
        "--password-env",
        metavar="VARIABLE",
        help="Read the new password from this environment variable",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a strong random password",
    )
    args = parser.parse_args(argv)
    if not any([args.user_id, args.username, args.email]):
        print("Specify the user: --user-id, --username, or --email", file=sys.stderr)
        return 1
    try:
        password = None
        effective_generate = args.generate
        if not args.generate:
            password = _read_password(args.password_env)
            effective_generate = password is None and args.password_env is None
        resolved_username, temp_password = asyncio.run(
            _reset_password(
                user_id=args.user_id,
                username=args.username,
                email=args.email,
                password=password,
                generate=effective_generate,
            )
        )
        if temp_password:
            print(f"Password for '{resolved_username}' reset successfully.")
            print(f"Temporary password (shown once): {temp_password}")
        else:
            print(f"Password for '{resolved_username}' reset successfully.")
        return 0
    except (UserNotFoundError, PasswordPolicyError) as exc:
        print(f"Password reset failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Password reset failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())