from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import Annotated

import typer
from rich.console import Console

from app.auth.exceptions import PasswordPolicyError
from app.db.session import dispose_engine, get_session_factory
from app.services.auth_service import AuthService, AuthServiceError

app = typer.Typer()
console = Console()


def _read_password(password_env: str | None) -> str:
    if password_env:
        password = os.environ.get(password_env)
        if password is None:
            raise AuthServiceError(
                f"Password environment variable '{password_env}' is not set"
            )
        return password
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise AuthServiceError("Password confirmation does not match")
    return password


async def _create_admin(
    *,
    username: str,
    email: str,
    full_name: str | None,
    password: str,
) -> str:
    try:
        async with get_session_factory()() as session:
            user = await AuthService(session).create_bootstrap_admin(
                username=username,
                email=email,
                full_name=full_name,
                password=password,
            )
        return user.username
    finally:
        await dispose_engine()


def _prompt_details(
    username: str | None, email: str | None, full_name: str | None
) -> tuple[str, str, str | None]:
    username = username or input("Username: ").strip()
    email = email or input("Email: ").strip()
    if full_name is None:
        full_name = input("Full name (optional): ").strip() or None
    return username, email, full_name


@app.command()
def create_admin(
    username: Annotated[
        str | None, typer.Option(help="Admin username; prompted when omitted")
    ] = None,
    email: Annotated[
        str | None, typer.Option(help="Admin email; prompted when omitted")
    ] = None,
    full_name: Annotated[
        str | None, typer.Option(help="Optional full name; prompted when omitted")
    ] = None,
    password_env: Annotated[
        str | None,
        typer.Option(
            help="Read the password from this environment variable instead of prompting"
        ),
    ] = None,
) -> None:
    """Create the first application superuser securely."""
    try:
        username, email, full_name = _prompt_details(username, email, full_name)
        password = _read_password(password_env)
        created_username = asyncio.run(
            _create_admin(
                username=username,
                email=email,
                full_name=full_name,
                password=password,
            )
        )
        console.print(
            f"Superuser '{created_username}' created successfully", style="green"
        )
    except (AuthServiceError, PasswordPolicyError) as exc:
        console.print(f"Admin creation failed: {exc}", style="red")
        raise typer.Exit(1) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.create_admin",
        description="Create the first application superuser securely.",
    )
    parser.add_argument("--username", help="Admin username; prompted when omitted")
    parser.add_argument("--email", help="Admin email; prompted when omitted")
    parser.add_argument("--full-name", help="Optional full name; prompted when omitted")
    parser.add_argument(
        "--password-env",
        metavar="VARIABLE",
        help="Read the password from this environment variable instead of prompting",
    )
    args = parser.parse_args(argv)
    username, email, full_name = _prompt_details(args.username, args.email, args.full_name)
    try:
        password = _read_password(args.password_env)
        created_username = asyncio.run(
            _create_admin(
                username=username,
                email=email,
                full_name=full_name,
                password=password,
            )
        )
    except (AuthServiceError, PasswordPolicyError) as exc:
        print(f"Admin creation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Superuser '{created_username}' created successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())