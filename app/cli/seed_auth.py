from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console

from app.db.session import dispose_engine, get_session_factory
from app.services.role_service import RoleService

app = typer.Typer()
console = Console()

@app.command()
def seed_auth(
    actor_user_id: Annotated[
        int | None, typer.Option(help="Audit actor user id; recorded when provided")
    ] = None,
) -> None:
    """Seed built-in roles and permissions idempotently."""
    async def _seed(actor_user_id: int | None) -> dict[str, int]:
        try:
            async with get_session_factory()() as session:
                return await RoleService(session).seed_builtins(actor_user_id=actor_user_id)
        finally:
            await dispose_engine()

    try:
        result = asyncio.run(_seed(actor_user_id))
        console.print(
            f"Seeded {result['roles']} built-in roles and "
            f"{result['permissions']} permissions", style="green"
        )
    except Exception as exc:
        console.print(f"Seeding failed: {exc}", style="red")
        raise typer.Exit(1) from exc

def main() -> None:
    app()

if __name__ == "__main__":
    main()