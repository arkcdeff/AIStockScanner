from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import dispose_engine, get_session_factory
from app.services.role_service import RoleService


async def _seed(actor_user_id: int | None) -> dict[str, int]:
    try:
        async with get_session_factory()() as session:
            return await RoleService(session).seed_builtins(actor_user_id=actor_user_id)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.seed_auth",
        description="Seed built-in roles and permissions idempotently.",
    )
    parser.add_argument(
        "--actor-user-id",
        type=int,
        default=None,
        help="Audit actor user id; recorded when provided",
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_seed(args.actor_user_id))
    except Exception as exc:
        print(f"Seeding failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Seeded {result['roles']} built-in roles and "
        f"{result['permissions']} permissions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
