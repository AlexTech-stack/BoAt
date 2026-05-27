from __future__ import annotations

import sys
from typing import Annotated, List, Optional

import grpc
import typer

from boat.v1 import pdu_pb2

from .completions import (
    complete_iface,
    complete_json_file,
    complete_pdu_msg_name,
    complete_transport,
)
from .output import print_error, print_table

pdu_app = typer.Typer(help="PDU routing and transmission commands.")


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


def _load_msg(db_path: str, msg_name: str):
    """Load a message entry from the PDU database. Returns the raw dict."""
    import os
    from boat.pdu_db import PduDatabase
    if not os.path.exists(db_path):
        print_error(f"Database not found: '{db_path}'. Use --db to specify the path.")
        sys.exit(1)
    db = PduDatabase(db_path)
    entry = db.by_name(msg_name)
    if entry is None:
        print_error(f"Message '{msg_name}' not found in '{db_path}'.")
        sys.exit(1)
    return entry


def _apply_sigs(msg, sig_list: list[str]) -> None:
    """Parse 'Name=value' pairs and set them on a Message instance."""
    for item in sig_list:
        if "=" not in item:
            print_error(f"--sig must be Name=value, got '{item}'")
            sys.exit(1)
        name, _, val = item.partition("=")
        try:
            msg.set(name.strip(), float(val.strip()))
        except KeyError as e:
            print_error(str(e))
            sys.exit(1)


@pdu_app.command("send")
def send_pdu(
    ctx: typer.Context,
    msg_name: Annotated[str, typer.Option("--msg", help="Message name from the PDU database.", autocompletion=complete_pdu_msg_name)] = "",
    pdu_id: Annotated[str, typer.Option("--id", help="32-bit PDU ID (hex or decimal). Overrides database value.")] = "",
    data: Annotated[str, typer.Option("--data", help="Raw hex payload. Overrides signal packing.")] = "",
    sig: Annotated[Optional[List[str]], typer.Option("--sig", help="Set signal physical value: Name=value (repeatable).")] = None,
    db: Annotated[str, typer.Option("--db", help="PDU database JSON file.", autocompletion=complete_json_file)] = "pdu_db.json",
) -> None:
    """Send a PDU via the gateway.

    Load a message from the database with --msg, optionally override individual
    signals with --sig, or bypass packing entirely with --data.

    Examples\b
      boat pdu send --msg MotorSpeed_PDU --sig MotorSpeed=100
      boat pdu send --id 0x00AC0001 --data DEADBEEF
    """
    if not msg_name and not pdu_id:
        print_error("Provide --msg (database lookup) or --id with --data.")
        sys.exit(1)

    if msg_name:
        from boat.message import Message
        entry  = _load_msg(db, msg_name)
        if entry["BusType"] != "ETH_PDU":
            print_error(f"'{msg_name}' has BusType={entry['BusType']}, expected ETH_PDU.")
            sys.exit(1)
        msg_obj  = Message(entry)
        _apply_sigs(msg_obj, sig or [])
        payload  = bytes.fromhex(data.replace(":", "").replace(" ", "")) if data else msg_obj.pack()
        resolved_id = int(pdu_id, 0) if pdu_id else entry["PduId"]
    else:
        if not data:
            print_error("--data is required when --msg is not used.")
            sys.exit(1)
        payload      = bytes.fromhex(data.replace(":", "").replace(" ", ""))
        resolved_id  = int(pdu_id, 0)

    frame = pdu_pb2.PduFrame(pdu_id=resolved_id, payload=payload)
    try:
        resp = ctx.obj["client"].pdu.SendPdu(pdu_pb2.SendPduRequest(pdu=frame))
        print_table(["pdu_id", "payload", "accepted"],
                    [[f"0x{resolved_id:08X}", payload.hex().upper(), resp.accepted]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("route")
def configure_route(
    ctx: typer.Context,
    pdu_id:   Annotated[str, typer.Option("--id",        help="32-bit PDU ID (hex or decimal).")],
    iface:    Annotated[str, typer.Option("--iface",     help="Network interface, e.g. vcan0 or veth0.", autocompletion=complete_iface)],
    transport:Annotated[str, typer.Option("--transport", help="Transport: can or eth.", autocompletion=complete_transport)],
    can_id:   Annotated[str, typer.Option("--can-id",    help="CAN frame ID override (default: same as pdu_id).")] = "0",
    ethertype:Annotated[str, typer.Option("--ethertype", help="EtherType (default: 0x88B5 sim-only).")] = "0x88B5",
    src_ip:   Annotated[str, typer.Option("--src-ip",    help="Source IP (IPv4 dotted or IPv6). Enables UDP/IP mode.")] = "",
    dst_ip:   Annotated[str, typer.Option("--dst-ip",    help="Destination IP.")] = "",
    src_port: Annotated[int, typer.Option("--src-port")] = 0,
    dst_port: Annotated[int, typer.Option("--dst-port")] = 0,
    ttl:      Annotated[int, typer.Option("--ttl")] = 64,
    vlan_id:  Annotated[int, typer.Option("--vlan")] = 0,
) -> None:
    """Configure a PDU routing rule on the gateway."""
    import socket

    resolved_id = int(pdu_id, 0)
    resolved_can_id = int(can_id, 0)

    transport_map = {"can": pdu_pb2.PDU_TRANSPORT_CAN, "eth": pdu_pb2.PDU_TRANSPORT_ETHERNET}
    t = transport_map.get(transport.lower())
    if t is None:
        print_error(f"--transport must be 'can' or 'eth', got '{transport}'")
        sys.exit(1)

    def _ip_to_bytes(addr: str) -> bytes:
        if not addr:
            return b""
        try:
            return socket.inet_pton(socket.AF_INET, addr)
        except OSError:
            return socket.inet_pton(socket.AF_INET6, addr)

    route = pdu_pb2.PduRoute(
        pdu_id=resolved_id,
        transport=t,
        iface=iface,
        can_id=resolved_can_id,
        ethertype=int(ethertype, 0),
        vlan_id=vlan_id,
        src_ip=_ip_to_bytes(src_ip),
        dst_ip=_ip_to_bytes(dst_ip),
        src_port=src_port,
        dst_port=dst_port,
        ttl=ttl,
    )
    try:
        resp = ctx.obj["client"].pdu.ConfigureRoute(pdu_pb2.ConfigureRouteRequest(route=route))
        print_table(["pdu_id", "iface", "transport", "ok"],
                    [[f"0x{resolved_id:08X}", iface, transport.upper(), resp.ok]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("list-routes")
def list_routes(ctx: typer.Context) -> None:
    """List all configured PDU routing rules."""
    try:
        resp = ctx.obj["client"].pdu.ListRoutes(pdu_pb2.ListRoutesRequest())
        rows = []
        for r in resp.routes:
            t = {pdu_pb2.PDU_TRANSPORT_CAN: "CAN",
                 pdu_pb2.PDU_TRANSPORT_ETHERNET: "ETH"}.get(r.transport, "?")
            rows.append([f"0x{r.pdu_id:08X}", t, r.iface,
                         f"0x{r.can_id:X}" if r.can_id else "-",
                         f"0x{r.ethertype:04X}"])
        print_table(["pdu_id", "transport", "iface", "can_id", "ethertype"],
                    rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("container")
def configure_container(
    ctx: typer.Context,
    msg_name: Annotated[str,  typer.Option("--msg",      help="ETH container message name from the PDU database.", autocompletion=complete_pdu_msg_name)] = "",
    cid:      Annotated[str,  typer.Option("--id",       help="Container ID (hex or decimal). Overrides database value.")] = "",
    iface:    Annotated[str,  typer.Option("--iface",    help="Ethernet interface. Overrides database value.", autocompletion=complete_iface)] = "",
    src_ip:   Annotated[str,  typer.Option("--src-ip",   help="Source IP. Overrides database value.")] = "",
    dst_ip:   Annotated[str,  typer.Option("--dst-ip",   help="Destination IP. Overrides database value.")] = "",
    src_port: Annotated[int,  typer.Option("--src-port")] = 0,
    dst_port: Annotated[int,  typer.Option("--dst-port")] = 0,
    ttl:      Annotated[int,  typer.Option("--ttl")]      = 0,
    vlan_id:  Annotated[int,  typer.Option("--vlan")]     = 0,
    db:       Annotated[str,  typer.Option("--db",       help="PDU database JSON file.", autocompletion=complete_json_file)] = "pdu_db.json",
) -> None:
    """Register an IpduM container on the gateway.

    All PDU IDs in the container share one Ethernet frame: whenever any member
    PDU is sent, the router flushes all currently known payloads as one frame.

    \b
    From database (reads IpduMEntries, IP, ports automatically):
      boat pdu container --msg Motor_PDU_Container --db config/pdu_db_example.json

    Override specific fields:
      boat pdu container --msg Motor_PDU_Container --db config/pdu_db_example.json \\
        --iface enx28107b9f2016 --src-ip 50.50.0.1 --dst-ip 50.50.0.2 \\
        --src-port 5000 --dst-port 5001
    """
    import os, socket
    from boat.v1 import pdu_pb2

    def ip_to_bytes(addr: str) -> bytes:
        try:
            return socket.inet_pton(socket.AF_INET, addr)
        except OSError:
            return socket.inet_pton(socket.AF_INET6, addr)

    if msg_name:
        entry = _load_msg(db, msg_name)
        if entry["BusType"] != "ETH":
            print_error(f"'{msg_name}' has BusType={entry['BusType']}, expected ETH.")
            sys.exit(1)
        # Load the member PDU IDs from the IpduMEntries list in the database
        from boat.pdu_db import PduDatabase
        database = PduDatabase(db)
        pdu_ids = []
        for member_id in entry.get("IpduMEntries", []):
            member = database.by_id(member_id)
            if member is None:
                print_error(f"IpduMEntries references DbId={member_id} which is not in the database.")
                sys.exit(1)
            pdu_ids.append(member["PduId"])

        resolved_cid      = int(cid, 0)  if cid      else entry["DbId"]
        resolved_iface    = iface        if iface    else entry.get("Bus", "")
        resolved_src_ip   = ip_to_bytes(src_ip) if src_ip else ip_to_bytes(entry.get("SrcIP", ""))
        resolved_dst_ip   = ip_to_bytes(dst_ip) if dst_ip else ip_to_bytes(entry.get("DstIP", ""))
        resolved_src_port = src_port or entry.get("SrcPort", 0)
        resolved_dst_port = dst_port or entry.get("DstPort", 0)
        resolved_ttl      = ttl      or entry.get("TTL", 64)
        resolved_vlan     = vlan_id  or entry.get("VlanId", 0)
    else:
        if not cid or not dst_ip or not src_ip:
            print_error("Provide --msg (database mode) or --id, --src-ip, --dst-ip (manual mode).")
            sys.exit(1)
        resolved_cid      = int(cid, 0)
        resolved_iface    = iface
        resolved_src_ip   = ip_to_bytes(src_ip)
        resolved_dst_ip   = ip_to_bytes(dst_ip)
        resolved_src_port = src_port
        resolved_dst_port = dst_port
        resolved_ttl      = ttl or 64
        resolved_vlan     = vlan_id
        pdu_ids           = []  # caller must use manual --msg-less mode only for testing

    container = pdu_pb2.PduContainerDef(
        container_id=resolved_cid,
        iface=resolved_iface,
        src_ip=resolved_src_ip,
        dst_ip=resolved_dst_ip,
        src_port=resolved_src_port,
        dst_port=resolved_dst_port,
        ttl=resolved_ttl,
        vlan_id=resolved_vlan,
        pdu_ids=pdu_ids,
    )
    try:
        resp = ctx.obj["client"].pdu.ConfigureContainer(
            pdu_pb2.ConfigureContainerRequest(container=container)
        )
        print_table(
            ["container_id", "iface", "pdu_ids", "ok"],
            [[str(resolved_cid), resolved_iface,
              str([f"0x{p:08X}" for p in pdu_ids]), resp.ok]],
            ctx.obj["json_mode"],
        )
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("subscribe")
def subscribe_pdus(
    ctx: typer.Context,
    ids:   Annotated[Optional[List[str]], typer.Option("--id", help="PDU ID to subscribe (repeatable, default: all).")] = None,
    count: Annotated[int, typer.Option("--count", help="Stop after N PDUs (0 = unlimited).")] = 0,
) -> None:
    """Stream incoming PDU frames from the gateway."""
    pdu_ids = [int(i, 0) for i in (ids or [])]
    stream  = ctx.obj["client"].pdu.SubscribePdus(
        pdu_pb2.SubscribePdusRequest(pdu_ids=pdu_ids)
    )
    received = 0
    try:
        for frame in stream:
            print_table(
                ["pdu_id", "payload", "source", "iface", "timestamp_ns"],
                [[f"0x{frame.pdu_id:08X}",
                  frame.payload.hex().upper(),
                  frame.source,
                  frame.iface,
                  frame.timestamp_ns]],
                ctx.obj["json_mode"],
            )
            received += 1
            if count > 0 and received >= count:
                break
    except grpc.RpcError as ex:
        _rpc_error(ex)
    finally:
        stream.cancel()
