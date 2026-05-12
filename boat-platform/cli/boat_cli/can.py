from __future__ import annotations

import sys

import grpc
import typer

from boat.v1 import can_pb2

from .output import print_error, print_table

can_app = typer.Typer()


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


_CANFD_FDF = 0x04
_CANFD_BRS = 0x01
_CANFD_ESI = 0x02


def _parse_data(hex_str: str, is_fd: bool) -> bytes:
    """Accept 'AABBCCDD' or 'AA:BB:CC:DD' or 'AA BB CC DD'."""
    cleaned = hex_str.replace(":", "").replace(" ", "")
    try:
        data = bytes.fromhex(cleaned)
    except ValueError as exc:
        print_error(f"Invalid hex data: {exc}")
        sys.exit(1)
    max_len = 64 if is_fd else 8
    if len(data) > max_len:
        print_error(f"CAN{'FD' if is_fd else ''} data must be at most {max_len} bytes")
        sys.exit(1)
    return data


def _resolve_payload(data: bytes, dlc: int, is_fd: bool) -> tuple[bytes, int]:
    """Apply DLC override: zero-pad if dlc > len, truncate if dlc < len.

    Returns (final_bytes, final_dlc).  If dlc == -1 the payload length is used as-is.
    """
    max_len = 64 if is_fd else 8
    if dlc < 0:
        return data, len(data)
    dlc = min(dlc, max_len)
    if dlc > len(data):
        return data + bytes(dlc - len(data)), dlc   # zero-pad
    return data[:dlc], dlc                           # truncate


@can_app.command("list-buses")
def list_buses(ctx: typer.Context) -> None:
    """List all registered CAN interfaces on the gateway."""
    try:
        response = ctx.obj["client"].can.ListBuses(can_pb2.ListBusesRequest())
        rows = [[iface] for iface in response.ifaces]
        print_table(["iface"], rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@can_app.command("send")
def send_frame(
    ctx: typer.Context,
    sim_id: str = typer.Option("", "--sim", help="Simulation ID (optional)"),
    can_id: str = typer.Option(..., "--id", help="CAN ID (hex or decimal, e.g. 0x123 or 291)"),
    data: str = typer.Option(..., "--data", help="Payload hex bytes, e.g. DEADBEEF or DE:AD:BE:EF"),
    dlc: int = typer.Option(-1, "--dlc", help="Byte-count override (default: inferred from data length); pad with zeros if larger, truncate if smaller"),
    bus: str = typer.Option("", "--bus", help="CAN interface, e.g. vcan0 (default: all registered)"),
    fd: bool = typer.Option(False, "--fd", help="Send as CAN FD frame (enables CANFD_FDF flag)"),
    brs: bool = typer.Option(False, "--brs", help="Bit-rate switch (CAN FD only, sets CANFD_BRS flag)"),
) -> None:
    """Send a raw CAN or CAN FD frame. Use --fd for FD frames (up to 64 bytes)."""
    try:
        frame_id = int(can_id, 0)
    except ValueError:
        print_error(f"Invalid CAN ID: {can_id}")
        sys.exit(1)

    payload = _parse_data(data, is_fd=fd)
    payload, actual_dlc = _resolve_payload(payload, dlc, is_fd=fd)

    flags = 0
    if fd:
        flags |= _CANFD_FDF
    if brs:
        if not fd:
            print_error("--brs requires --fd")
            sys.exit(1)
        flags |= _CANFD_BRS

    frame = can_pb2.CanFrame(
        can_id=frame_id,
        dlc=actual_dlc,
        data=payload,
        iface=bus,
        flags=flags,
    )
    try:
        response = ctx.obj["client"].can.SendCanFrame(
            can_pb2.SendCanFrameRequest(simulation_id=sim_id, frame=frame)
        )
        print_table(["accepted"], [[response.accepted]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@can_app.command("subscribe")
def subscribe_frames(
    ctx: typer.Context,
    sim_id: str = typer.Option("", "--sim", help="Simulation ID (optional)"),
    count: int = typer.Option(0, "--count", help="Stop after N frames (0 = unlimited)"),
    bus: str = typer.Option("", "--bus", help="CAN interface to listen on (default: all)"),
) -> None:
    """Stream incoming CAN frames. Use --bus to filter by interface."""
    received = 0
    stream = ctx.obj["client"].can.SubscribeCanFrames(
        can_pb2.SubscribeCanFramesRequest(simulation_id=sim_id, iface=bus)
    )
    try:
        for frame in stream:
            hex_data = frame.data.hex(":") if frame.data else ""
            flags_str = ""
            if frame.flags & _CANFD_FDF:
                flags_str += "FDF"
            if frame.flags & _CANFD_BRS:
                flags_str += "+BRS" if flags_str else "BRS"
            if frame.flags & _CANFD_ESI:
                flags_str += "+ESI" if flags_str else "ESI"
            print_table(
                ["iface", "can_id", "dlc", "flags", "data", "timestamp_ns"],
                [[
                    frame.iface or "vcan0",
                    f"0x{frame.can_id:03X}",
                    frame.dlc,
                    flags_str,
                    hex_data,
                    frame.timestamp_ns,
                ]],
                ctx.obj["json_mode"],
            )
            received += 1
            if count > 0 and received >= count:
                break
    except grpc.RpcError as ex:
        _rpc_error(ex)
    finally:
        stream.cancel()
