from __future__ import annotations

import sys

import grpc
import typer

from boat.v1 import ethernet_pb2

from .completions import complete_iface
from .output import print_error, print_table

eth_app = typer.Typer()


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


def _parse_mac(mac_str: str, label: str) -> bytes:
    """Accept 'AA:BB:CC:DD:EE:FF' or 'AABBCCDDEEFF'."""
    cleaned = mac_str.replace(":", "").replace("-", "")
    try:
        data = bytes.fromhex(cleaned)
    except ValueError as exc:
        print_error(f"Invalid {label}: {exc}")
        sys.exit(1)
    if len(data) != 6:
        print_error(f"{label} must be exactly 6 bytes")
        sys.exit(1)
    return data


def _parse_payload(hex_str: str) -> bytes:
    """Accept 'AABBCC...' or 'AA:BB:CC:...' or 'AA BB CC ...'."""
    cleaned = hex_str.replace(":", "").replace(" ", "")
    try:
        data = bytes.fromhex(cleaned)
    except ValueError as exc:
        print_error(f"Invalid hex payload: {exc}")
        sys.exit(1)
    if len(data) > 1500:
        print_error("Ethernet payload must be at most 1500 bytes")
        sys.exit(1)
    return data


@eth_app.command("list-ifaces")
def list_ifaces(ctx: typer.Context) -> None:
    """List all registered virtual Ethernet interfaces on the gateway."""
    try:
        response = ctx.obj["client"].ethernet.ListInterfaces(
            ethernet_pb2.ListEthernetInterfacesRequest()
        )
        rows = [[iface] for iface in response.ifaces]
        print_table(["iface"], rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@eth_app.command("send")
def send_frame(
    ctx: typer.Context,
    iface: str = typer.Option(..., "--iface", help="Interface name, e.g. veth0", autocompletion=complete_iface),
    src: str = typer.Option("", "--src", help="Source MAC, e.g. AA:BB:CC:DD:EE:FF (optional)"),
    dst: str = typer.Option("", "--dst", help="Destination MAC (optional, broadcast if omitted)"),
    ethertype: str = typer.Option("0x0800", "--ethertype", help="Ethertype (hex or decimal, e.g. 0x0800)"),
    payload: str = typer.Option(..., "--payload", help="Payload hex bytes, e.g. DEADBEEF"),
) -> None:
    """Send a raw Ethernet frame on a virtual interface."""
    try:
        etype = int(ethertype, 0)
    except ValueError:
        print_error(f"Invalid ethertype: {ethertype}")
        sys.exit(1)

    src_bytes = _parse_mac(src, "src MAC") if src else b""
    dst_bytes = _parse_mac(dst, "dst MAC") if dst else b""
    payload_bytes = _parse_payload(payload)

    frame = ethernet_pb2.EthernetFrame(
        iface=iface,
        src_mac=src_bytes,
        dst_mac=dst_bytes,
        ethertype=etype,
        payload=payload_bytes,
    )
    try:
        response = ctx.obj["client"].ethernet.SendFrame(
            ethernet_pb2.SendEthernetFrameRequest(frame=frame)
        )
        print_table(["accepted"], [[response.accepted]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@eth_app.command("subscribe")
def subscribe_frames(
    ctx: typer.Context,
    iface: str = typer.Option("", "--iface", help="Interface to listen on (default: all)", autocompletion=complete_iface),
    ethertype: str = typer.Option("0", "--ethertype", help="Filter by ethertype (0 = all)"),
    count: int = typer.Option(0, "--count", help="Stop after N frames (0 = unlimited)"),
) -> None:
    """Stream incoming Ethernet frames. Use --iface and --ethertype to filter."""
    try:
        etype = int(ethertype, 0)
    except ValueError:
        print_error(f"Invalid ethertype: {ethertype}")
        sys.exit(1)

    stream = ctx.obj["client"].ethernet.SubscribeFrames(
        ethernet_pb2.SubscribeEthernetFramesRequest(iface=iface, ethertype=etype)
    )
    received = 0
    try:
        for frame in stream:
            src_str = frame.src_mac.hex(":") if frame.src_mac else ""
            dst_str = frame.dst_mac.hex(":") if frame.dst_mac else ""
            payload_str = frame.payload.hex(":") if frame.payload else ""
            print_table(
                ["iface", "src_mac", "dst_mac", "ethertype", "payload", "timestamp_ns"],
                [[
                    frame.iface,
                    src_str,
                    dst_str,
                    f"0x{frame.ethertype:04X}",
                    payload_str,
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
