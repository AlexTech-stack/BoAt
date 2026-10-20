# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""CLI: read and stream gateway metrics."""

from __future__ import annotations

import sys

import grpc
import typer

from boat.metrics_node import MetricsNode

from .output import print_error, print_table

metrics_app = typer.Typer(help="Read or stream gateway metrics.")


@metrics_app.command("get")
def get_metrics(
    ctx: typer.Context,
    simulation_id: str = typer.Option(
        "", "--simulation-id",
        help="Only include event-store metrics for this simulation.",
    ),
) -> None:
    """Print the current metric snapshot."""
    node = MetricsNode(ctx.obj["client"])
    try:
        points = node.get(simulation_id=simulation_id)
    except grpc.RpcError as exc:
        print_error(f"RPC error [{exc.code().name}]: {exc.details()}")
        sys.exit(1)

    rows = [[point.name, point.value, point.tick] for point in points]
    print_table(["name", "value", "tick"], rows, ctx.obj["json_mode"])


@metrics_app.command("watch")
def watch_metrics(ctx: typer.Context) -> None:
    """Stream gateway-scoped metric points until interrupted (Ctrl-C)."""
    node = MetricsNode(ctx.obj["client"])
    try:
        for point in node.stream():
            print_table(
                ["name", "value", "tick"],
                [[point.name, point.value, point.tick]],
                ctx.obj["json_mode"],
            )
            sys.stdout.flush()
    except KeyboardInterrupt:
        return
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.CANCELLED:
            return
        print_error(f"RPC error [{exc.code().name}]: {exc.details()}")
        sys.exit(1)
