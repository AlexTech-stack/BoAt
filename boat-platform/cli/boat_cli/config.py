# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from typing import Optional

import typer

from boat.v1 import debug_pb2

from .output import print_error

config_app = typer.Typer(help="Inspect the configuration a running gateway actually resolved.")


@config_app.command("show")
def show_config(ctx: typer.Context,
                output: Optional[str] = typer.Option(
                    None, "--output", "-o",
                    help="Write the document to this file instead of stdout. "
                         "Commit it next to the trace a run produced.")) -> None:
    """Print the gateway's effective configuration as JSON.

    This is the same document the gateway writes at startup when
    BOAT_CONFIG_DUMP is set, so a CI job can capture it either way. It records
    what was actually resolved -- interfaces, plugins and their configs, tick
    interval and which env var set it, TLS -- plus any configuration warnings,
    so a run can be reproduced from an artifact rather than from whatever
    environment the operator remembers exporting.

    The document contains no timestamp or hostname, so two identically
    configured gateways emit byte-identical output and can be diffed.
    """
    try:
        response = ctx.obj["client"].debug.GetEffectiveConfig(
            debug_pb2.GetEffectiveConfigRequest())
    except Exception as exc:  # noqa: BLE001 - surface any gRPC failure plainly
        print_error(f"could not read effective config: {exc}")
        raise typer.Exit(code=1)

    document = response.config_json
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(document)
        print(f"Effective config written to {output}")
    else:
        sys.stdout.write(document)
