from __future__ import annotations

import sys
from typing import Annotated

import typer

from .output import print_error, print_table

can_tp_app = typer.Typer(help="CAN Transport Protocol (ISO 15765-2) commands.")


@can_tp_app.command("send")
def can_tp_send(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal, typically CAN ID).")],
    data: Annotated[str, typer.Option("--data", help="Hex payload (large, will be segmented).")],
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
) -> None:
    """Send a large PDU via ISO 15765-2 segmentation.

    Uses the CanTp plugin's standalone C API directly.

    \b
    Example:
      boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...
    """
    from boat.can_tp import CanTpHandle

    resolved_id = int(nsdu_id, 0)
    payload = bytes.fromhex(data.replace(":", "").replace(" ", ""))

    if len(payload) <= 7:
        print_table(
            ["nsdu_id", "len", "note"],
            [[f"0x{resolved_id:X}", len(payload),
              "Payload fits in a single CAN frame. Use 'boat pdu send --id --data' instead."]],
            ctx.obj.get("json_mode", False),
        )
        return

    # Locate the CanTp plugin .so relative to typical install paths
    import glob as _glob
    candidates = (
        _glob.glob("build/debug/src/plugins/can_tp/can_tp.so") +
        _glob.glob("build/release/src/plugins/can_tp/can_tp.so") +
        _glob.glob("/usr/local/lib/boat/plugins/can_tp.so")
    )
    so_path = candidates[0] if candidates else "./build/debug/src/plugins/can_tp/can_tp.so"

    try:
        handle = CanTpHandle(so_path)
    except FileNotFoundError:
        print_error(
            f"CanTp plugin not found at '{so_path}'. "
            f"Build it first: cmake --build --preset debug"
        )
        sys.exit(1)
    except OSError as ex:
        print_error(f"Failed to load CanTp plugin: {ex}")
        sys.exit(1)

    handle.configure(resolved_id, can_dlc=can_dlc)
    result = handle.send(resolved_id, payload)

    if result:
        print_table(
            ["nsdu_id", "len", "frames_sent"],
            [[f"0x{resolved_id:X}", len(payload), "yes"]],
            ctx.obj.get("json_mode", False),
        )
    else:
        print_error(f"can_tp_send failed for nsdu_id=0x{resolved_id:X}")
        sys.exit(1)
