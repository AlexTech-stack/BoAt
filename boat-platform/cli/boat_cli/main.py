from __future__ import annotations

import typer

from boat.client import BoAtClient

from .plugin import plugin_app
from .replay import replay_app
from .scenario import scenario_app
from .sim import sim_app

app = typer.Typer()

app.add_typer(sim_app, name="sim")
app.add_typer(scenario_app, name="scenario")
app.add_typer(replay_app, name="replay")
app.add_typer(plugin_app, name="plugin")


@app.callback()
def main(
    ctx: typer.Context,
    host: str = typer.Option("localhost:50051", "--host"),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    ctx.obj = {"host": host, "json_mode": json_mode, "client": BoAtClient(address=host)}
