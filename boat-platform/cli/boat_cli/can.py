from __future__ import annotations

import sys

import grpc
import typer

from boat.v1 import can_pb2

from .completions import complete_can_msg_name, complete_iface, complete_json_file
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
        rows = [[b.iface, b.driver, b.state, "✓" if b.fd_support else "—", b.bitrate or "—"]
                for b in response.buses]
        print_table(["iface", "driver", "state", "fd", "bitrate"], rows,
                     ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@can_app.command("detect")
def detect(ctx: typer.Context) -> None:
    """Detect available CAN hardware on this host (no gateway required)."""
    import os
    import glob as _glob

    net_sys = "/sys/class/net"
    entries = sorted(os.listdir(net_sys)) if os.path.isdir(net_sys) else []

    rows = []
    for name in entries:
        iface_type = _read_sysfs(os.path.join(net_sys, name, "type"))
        if iface_type != "280":          # ARPHRD_CAN
            continue

        mtu_s = _read_sysfs(os.path.join(net_sys, name, "mtu"))
        fd = "✓" if mtu_s and int(mtu_s) >= 72 else "—"

        operstate = _read_sysfs(os.path.join(net_sys, name, "operstate")) or "unknown"

        # Determine driver: physical interfaces have device/driver symlink.
        driver_link = os.path.join(net_sys, name, "device", "driver")
        if os.path.islink(driver_link):
            driver = os.path.basename(os.readlink(driver_link))
        else:
            driver = "vcan"

        # For USB devices, try to read the USB vendor/product ID.
        usb_id = ""
        uevent = os.path.join(net_sys, name, "device", "uevent")
        if os.path.isfile(uevent):
            with open(uevent) as f:
                for line in f:
                    if line.startswith("PRODUCT="):
                        parts = line.strip().split("=")[1].split("/")
                        if len(parts) >= 2:
                            usb_id = f"{parts[0]}:{parts[1]}"
                        break

        # Attempt to name the device from USB ID.
        device_name = driver
        if usb_id == "c72:11":
            device_name = "PEAK System PCAN-USB Pro FD"
        elif usb_id:
            device_name = f"USB device {usb_id}"

        rows.append([device_name, driver, name, fd, operstate])

    if not rows:
        print_table(["device", "driver", "iface", "fd", "state"],
                     [["(no CAN interfaces found)", "", "", "", ""]],
                     ctx.obj["json_mode"])
    else:
        print_table(["device", "driver", "iface", "fd", "state"], rows,
                     ctx.obj["json_mode"])


def _read_sysfs(path: str) -> str | None:
    """Read a sysfs file and return its contents stripped, or None."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


@can_app.command("send")
def send_frame(
    ctx: typer.Context,
    sim_id:   str            = typer.Option("",    "--sim",  help="Simulation ID (optional)"),
    can_id:   str            = typer.Option("",    "--id",   help="CAN ID (hex or decimal). Overrides database value."),
    data:     str            = typer.Option("",    "--data", help="Raw payload hex. Overrides signal packing."),
    msg_name: str            = typer.Option("",    "--msg",  help="Message name from PDU database (loads signals from DB).", autocompletion=complete_can_msg_name),
    sig:      list[str]      = typer.Option([],    "--sig",  help="Set signal physical value: Name=value (repeatable)."),
    db_path:  str            = typer.Option("pdu_db.json", "--db", help="PDU database JSON file.", autocompletion=complete_json_file),
    dlc:      int            = typer.Option(-1,    "--dlc",  help="Byte-count override; pads or truncates payload."),
    bus:      str            = typer.Option("",    "--bus",  help="CAN interface, e.g. vcan0 (default: from DB or all).", autocompletion=complete_iface),
    fd:       bool           = typer.Option(False, "--fd",   help="Send as CAN FD frame."),
    brs:      bool           = typer.Option(False, "--brs",  help="Bit-rate switch (CAN FD only)."),
) -> None:
    """Send a CAN or CAN FD frame — raw or from the PDU database.

    \b
    Raw mode (no --msg):
      boat can send --id 0x7B --data DEADBEEF --bus vcan0

    Database mode (--msg loads signals, --sig overrides values):
      boat can send --msg Motor_1 --db pdu_db.json --sig MotorSpeed=100 --sig Clamp15=1
      boat can send --msg Motor_1 --db pdu_db.json --data DEADBEEF --bus vcan0
    """
    import os

    if msg_name:
        # ── database mode ─────────────────────────────────────────────
        from boat.message import Message
        from boat.pdu_db  import PduDatabase

        if not os.path.exists(db_path):
            print_error(f"Database not found: '{db_path}'. Use --db to specify the path.")
            sys.exit(1)
        db  = PduDatabase(db_path)
        entry = db.by_name(msg_name)
        if entry is None:
            print_error(f"Message '{msg_name}' not in database. Use 'boat db list' to see names.")
            sys.exit(1)
        if entry["BusType"] not in ("CAN", "CANFD"):
            print_error(f"'{msg_name}' has BusType={entry['BusType']}, expected CAN or CANFD.")
            sys.exit(1)

        msg_obj = Message(entry)
        for item in sig:
            if "=" not in item:
                print_error(f"--sig must be Name=value, got '{item}'")
                sys.exit(1)
            name, _, val = item.partition("=")
            try:
                msg_obj.set(name.strip(), float(val.strip()))
            except KeyError as e:
                print_error(str(e))
                sys.exit(1)

        payload  = bytes.fromhex(data.replace(":", "").replace(" ", "")) if data \
                   else msg_obj.pack()
        frame_id = int(can_id, 0) if can_id else entry["Identifier"]
        iface    = bus or entry["Bus"]
        is_fd    = fd or (entry["BusType"] == "CANFD")
    else:
        # ── raw mode ──────────────────────────────────────────────────
        if not can_id:
            print_error("Provide --id (raw mode) or --msg (database mode).")
            sys.exit(1)
        if not data:
            print_error("Provide --data (raw mode) or --msg (database mode).")
            sys.exit(1)
        try:
            frame_id = int(can_id, 0)
        except ValueError:
            print_error(f"Invalid CAN ID: {can_id}")
            sys.exit(1)
        payload = _parse_data(data, is_fd=fd)
        iface   = bus
        is_fd   = fd

    payload, actual_dlc = _resolve_payload(payload, dlc, is_fd=is_fd)

    flags = 0
    if is_fd:
        flags |= _CANFD_FDF
    if brs:
        if not is_fd:
            print_error("--brs requires --fd or a CANFD message")
            sys.exit(1)
        flags |= _CANFD_BRS

    frame = can_pb2.CanFrame(
        can_id=frame_id,
        dlc=actual_dlc,
        data=payload,
        iface=iface,
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
