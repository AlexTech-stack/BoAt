from __future__ import annotations

import sys
from typing import Annotated

import typer

from boat.v1 import node_plugin_pb2, plugin_pb2

from .output import print_error, print_table

plugin_app = typer.Typer()

_SCOPES = ("sim", "node")


@plugin_app.command("register")
def register_plugin(ctx: typer.Context,
                    path: str = typer.Option(..., "--path"),
                    config: str = typer.Option("", "--config", "-c",
                       help="JSON config string for the plugin")) -> None:
    """Load a plugin into the simulation-scoped PluginManager.

    Node plugins (CanTp, PduRouter, TCP, SOME/IP, Probe) are only ever
    loaded via BOAT_NODE_PLUGINS at gateway startup -- there is no runtime
    register for that always-on manager.
    """
    response = ctx.obj["client"].plugin.RegisterPlugin(
        plugin_pb2.RegisterPluginRequest(path=path, config_json=config))
    print_table(["plugin_id", "name"],
                [[response.plugin.plugin_id, response.plugin.name]],
                ctx.obj["json_mode"])


@plugin_app.command("list")
def list_plugins(ctx: typer.Context) -> None:
    """List loaded plugins across both PluginManager instances.

    "sim" = simulation-scoped, hot-loadable per running scenario (via
    `plugin register`). "node" = always-on, loaded once at gateway startup
    from BOAT_NODE_PLUGINS (CanTp, PduRouter, TCP, SOME/IP, Probe) and kept
    alive regardless of simulation state. See README.md's "Dual
    PluginManager" section.
    """
    client = ctx.obj["client"]
    rows = []
    sim_resp = client.plugin.ListPlugins(plugin_pb2.ListPluginsRequest())
    rows.extend(
        ["sim", item.plugin_id, item.name, bool(item.loaded), item.config_json]
        for item in sim_resp.plugins
    )
    node_resp = client.node_plugin.ListNodePlugins(node_plugin_pb2.ListNodePluginsRequest())
    rows.extend(
        ["node", item.plugin_id, item.name, bool(item.loaded), item.config_json]
        for item in node_resp.plugins
    )
    print_table(["scope", "plugin_id", "name", "loaded", "config_json"], rows, ctx.obj["json_mode"])


@plugin_app.command("info")
def plugin_info(
    ctx: typer.Context,
    name: str,
    scope: Annotated[str, typer.Option("--scope", help="Which PluginManager to query: sim or node.")] = "sim",
) -> None:
    if scope not in _SCOPES:
        print_error(f"--scope must be one of {_SCOPES}, got '{scope}'")
        sys.exit(1)

    client = ctx.obj["client"]
    if scope == "node":
        response = client.node_plugin.GetNodePluginInfo(
            node_plugin_pb2.GetNodePluginInfoRequest(plugin_id=name))
    else:
        response = client.plugin.GetPluginInfo(plugin_pb2.GetPluginInfoRequest(plugin_id=name))

    print_table(
        ["plugin_id", "name", "version", "loaded", "config_json"],
        [[response.plugin.plugin_id, response.plugin.name, response.plugin.version,
          bool(response.plugin.loaded), response.plugin.config_json]],
        ctx.obj["json_mode"],
    )


@plugin_app.command("unload")
def unload_plugin(
    ctx: typer.Context,
    name: str,
    scope: Annotated[str, typer.Option("--scope", help="Which PluginManager to unload from: sim or node.")] = "sim",
    yes: Annotated[bool, typer.Option("--yes", help="Required for --scope node: unloading a node plugin is "
                   "immediate and gateway-wide (not scoped to any simulation), e.g. it can silently drop a "
                   "live CAN-TP session.")] = False,
) -> None:
    if scope not in _SCOPES:
        print_error(f"--scope must be one of {_SCOPES}, got '{scope}'")
        sys.exit(1)

    client = ctx.obj["client"]
    if scope == "node":
        if not yes:
            print_error("unloading a node plugin is immediate and gateway-wide -- pass --yes to confirm")
            sys.exit(1)
        response = client.node_plugin.UnloadNodePlugin(
            node_plugin_pb2.UnloadNodePluginRequest(plugin_id=name, confirm=True))
    else:
        response = client.plugin.UnloadPlugin(plugin_pb2.UnloadPluginRequest(plugin_id=name))

    print_table(["unloaded"], [[bool(response.unloaded)]], ctx.obj["json_mode"])
