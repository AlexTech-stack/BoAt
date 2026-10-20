# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""CLI: inject and inspect deterministic signal faults."""

from __future__ import annotations

import sys

import grpc
import typer

from boat.fault_node import FaultNode
from boat.v1 import fault_pb2

from .output import print_error, print_table

fault_app = typer.Typer(help="Inject and inspect deterministic signal faults.")


_TYPE_NAMES = {
    fault_pb2.FAULT_TYPE_SIGNAL_STUCK: "stuck",
    fault_pb2.FAULT_TYPE_SIGNAL_CORRUPTION: "noise",
    fault_pb2.FAULT_TYPE_PACKET_DROP: "dropout",
    fault_pb2.FAULT_TYPE_SIGNAL_INVERT: "invert",
    fault_pb2.FAULT_TYPE_DELAY: "delay",
    fault_pb2.FAULT_TYPE_UNSPECIFIED: "unspecified",
}


def _type_name(value: int) -> str:
    return _TYPE_NAMES.get(value, fault_pb2.FaultType.Name(value))


@fault_app.command("inject")
def inject_fault(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Signal name to fault (e.g. vehicle.speed)."),
    fault_type: str = typer.Option(
        ..., "--type", "-t",
        help="stuck, noise, dropout, invert, or delay.",
    ),
    tick: int = typer.Option(..., "--tick", help="Start tick."),
    end_tick: int = typer.Option(
        0, "--end-tick",
        help="Inclusive end tick. 0 means a one-tick fault (same as --tick).",
    ),
    magnitude: float = typer.Option(
        0.0, "--magnitude",
        help="Stuck value, or noise amplitude for --type noise.",
    ),
    delay_ticks: int = typer.Option(
        0, "--delay-ticks",
        help="Delay length for --type delay.",
    ),
    simulation_id: str = typer.Option(
        "", "--simulation-id",
        help="Optional simulation id recorded on the fault event.",
    ),
) -> None:
    """Schedule a fault at a tick or over an inclusive tick range.

    The gateway clears scheduled faults when a simulation is created or reset;
    inject after `boat sim create` (before or during the run).
    """
    node = FaultNode(ctx.obj["client"])
    try:
        event = node.inject_by_name(
            target,
            fault_type,
            tick,
            end_tick=end_tick,
            magnitude=magnitude,
            delay_ticks=delay_ticks,
            simulation_id=simulation_id,
        )
    except ValueError as exc:
        print_error(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        print_error(str(exc))
        sys.exit(1)
    except grpc.RpcError as exc:
        print_error(f"RPC error [{exc.code().name}]: {exc.details()}")
        sys.exit(1)

    print_table(
        ["fault_id", "target", "type", "from", "to", "magnitude", "delay"],
        [[
            event.fault_id,
            event.target,
            _type_name(event.type),
            event.tick,
            event.end_tick,
            event.magnitude,
            event.delay_ticks,
        ]],
        ctx.obj["json_mode"],
    )


@fault_app.command("list")
def list_faults(
    ctx: typer.Context,
    simulation_id: str = typer.Option(
        "", "--simulation-id",
        help="Only list faults recorded for this simulation.",
    ),
) -> None:
    """List faults injected through this gateway (history, including cleared ones)."""
    node = FaultNode(ctx.obj["client"])
    try:
        events = node.list_faults(simulation_id=simulation_id)
    except grpc.RpcError as exc:
        print_error(f"RPC error [{exc.code().name}]: {exc.details()}")
        sys.exit(1)

    rows = [[
        event.fault_id,
        event.target,
        _type_name(event.type),
        event.tick,
        event.end_tick,
        event.magnitude,
        event.delay_ticks,
    ] for event in events]
    print_table(
        ["fault_id", "target", "type", "from", "to", "magnitude", "delay"],
        rows,
        ctx.obj["json_mode"],
    )
