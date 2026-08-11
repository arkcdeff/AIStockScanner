from __future__ import annotations

import typer

from app.cli.backtest import app as backtest_app
from app.cli.backtest_replay import app as backtest_replay_app
from app.cli.create_admin import app as create_admin_app
from app.cli.import_nse import app as import_nse_app
from app.cli.nse_history import app as nse_history_app
from app.cli.seed_auth import app as seed_auth_app

app = typer.Typer()
app.add_typer(backtest_app, name="backtest", help="Backtest strategies and runs")
app.add_typer(backtest_replay_app, name="replay", help="Replay scanner signals")
app.add_typer(import_nse_app, name="import-nse", help="Import NSE data")
app.add_typer(nse_history_app, name="nse-history", help="NSE history management")
app.add_typer(create_admin_app, name="create-admin", help="Create admin user")
app.add_typer(seed_auth_app, name="seed-auth", help="Seed roles and permissions")

def main() -> None:
    app()

if __name__ == "__main__":
    main()