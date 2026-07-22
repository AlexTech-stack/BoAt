from __future__ import annotations

import sys
from typing import Annotated

import grpc
import typer

from boat.v1 import can_tp_pb2

from .output import print_error, print_table

can_tp_app = typer.Typer(help="CAN Transport Protocol (ISO 15765-2) commands.")


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


_RESULT_LABELS = {
    can_tp_pb2.SEND_RESULT_SINGLE_FRAME: "single frame",
    can_tp_pb2.SEND_RESULT_MULTI_FRAME_INITIATED: "multi-frame initiated",
}


@can_tp_app.command("configure")
def can_tp_configure(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal).")],
    source_addr: Annotated[str, typer.Option("--source-addr", help="CAN ID of this node (hex or decimal).")] = "",
    target_addr: Annotated[str, typer.Option("--target-addr", help="CAN ID of the peer node (hex or decimal).")] = "",
    block_size: Annotated[int, typer.Option("--bs", help="Block Size to advertise in FC (0=unlimited).")] = 0,
    st_min: Annotated[int, typer.Option("--stmin", help="Separation Time in ms to advertise in FC.")] = 0,
    rx_buffer_size: Annotated[int, typer.Option("--rx-buffer", help="RX reassembly buffer size.")] = 4095,
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
    iface: Annotated[str, typer.Option("--iface", help="Which loaded CanTp instance to target "
                     "(one per CAN interface). Only needed if more than one is loaded -- "
                     "omit it while there's exactly one.")] = "",
) -> None:
    """Configure an N-SDU connection for ISO 15765-2.

    Both the local (source) and peer (target) CAN addresses are required
    for proper multi-ID session handling. Talks to the live CanTp plugin
    instance running inside the gateway (loaded via BOAT_NODE_PLUGINS).

    \b
    Examples:
      # Single-ID (nsdu_id used as both source and target)
      boat can-tp configure --nsdu-id 0x7E0

      # Dual-ID session (tester sending to ECU)
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --bs 0 --stmin 0

      # With two CanTp instances loaded (vcan0 + vcan1), pick one:
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan1
    """
    resolved_id = int(nsdu_id, 0)
    resolved_source = int(source_addr, 0) if source_addr else 0
    resolved_target = int(target_addr, 0) if target_addr else 0

    config = can_tp_pb2.CanTpConfig(
        nsdu_id=resolved_id,
        source_addr=resolved_source,
        target_addr=resolved_target,
        rx_buffer_size=rx_buffer_size,
        block_size=block_size,
        st_min=st_min,
        can_dlc=can_dlc,
    )

    try:
        resp = ctx.obj["client"].can_tp.Configure(
            can_tp_pb2.ConfigureRequest(config=config, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    # 0/0 falls back to nsdu_id for both, applied server-side -- mirror that
    # here purely for display.
    display_source = resolved_source or resolved_id
    display_target = resolved_target or resolved_id
    print_table(
        ["nsdu_id", "source_addr", "target_addr", "bs", "stmin", "dlc", "iface"],
        [[f"0x{resolved_id:X}", f"0x{display_source:X}", f"0x{display_target:X}",
          block_size, st_min, can_dlc, resp.iface]],
        ctx.obj.get("json_mode", False),
    )


@can_tp_app.command("send")
def can_tp_send(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal).")],
    data: Annotated[str, typer.Option("--data", help="Hex payload (large payloads will be segmented).")],
    source_addr: Annotated[str, typer.Option("--source-addr", help="CAN ID of this node (hex or decimal).")] = "",
    target_addr: Annotated[str, typer.Option("--target-addr", help="CAN ID of the peer node (hex or decimal).")] = "",
    block_size: Annotated[int, typer.Option("--bs", help="Block Size to advertise in FC (0=unlimited).")] = 0,
    st_min: Annotated[int, typer.Option("--stmin", help="Separation Time in ms to advertise in FC.")] = 0,
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
    iface: Annotated[str, typer.Option("--iface", help="Which loaded CanTp instance to target "
                     "(one per CAN interface). Only needed if more than one is loaded -- "
                     "omit it while there's exactly one.")] = "",
) -> None:
    """Send a PDU via ISO 15765-2 (CanTp).

    Payloads that fit in a single CAN frame are sent directly as a Single
    Frame; larger payloads are segmented into a First Frame followed by
    Consecutive Frames, paced by the gateway plugin's flow-control state
    machine -- both cases are handled by this one command.

    \b
    Example:
      boat can-tp send --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --data 0123456789ABCDEF...
    """
    resolved_id = int(nsdu_id, 0)
    payload = bytes.fromhex(data.replace(":", "").replace(" ", ""))
    resolved_source = int(source_addr, 0) if source_addr else 0
    resolved_target = int(target_addr, 0) if target_addr else 0

    config = can_tp_pb2.CanTpConfig(
        nsdu_id=resolved_id,
        source_addr=resolved_source,
        target_addr=resolved_target,
        block_size=block_size,
        st_min=st_min,
        can_dlc=can_dlc,
    )

    client = ctx.obj["client"]
    try:
        client.can_tp.Configure(can_tp_pb2.ConfigureRequest(config=config, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    try:
        send_resp = client.can_tp.Send(
            can_tp_pb2.SendRequest(nsdu_id=resolved_id, data=payload, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    print_table(
        ["nsdu_id", "len", "result"],
        [[f"0x{resolved_id:X}", len(payload), _RESULT_LABELS.get(send_resp.result, "unknown")]],
        ctx.obj.get("json_mode", False),
    )


@can_tp_app.command("list-sessions")
def can_tp_list_sessions(
    ctx: typer.Context,
    iface: Annotated[str, typer.Option("--iface", help="Scope to one loaded CanTp instance. "
                     "Omit to list sessions across every loaded instance (each tagged with "
                     "its iface).")] = "",
) -> None:
    """List currently-configured N-SDU connections (ISO 15765-2 sessions).

    \b
    Examples:
      boat can-tp list-sessions               # every loaded instance
      boat can-tp list-sessions --iface vcan0  # just one
    """
    try:
        resp = ctx.obj["client"].can_tp.ListSessions(can_tp_pb2.ListSessionsRequest(iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    rows = [
        [s.iface, f"0x{s.nsdu_id:X}", f"0x{s.source_addr:X}", f"0x{s.target_addr:X}",
         s.block_size, s.st_min, s.can_dlc, s.extended_addressing, s.rx_state, s.tx_state]
        for s in resp.sessions
    ]
    print_table(
        ["iface", "nsdu_id", "source_addr", "target_addr", "bs", "stmin", "dlc",
         "ext_addr", "rx_state", "tx_state"],
        rows,
        ctx.obj.get("json_mode", False),
    )
