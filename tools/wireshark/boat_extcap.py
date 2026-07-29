#!/usr/bin/env python3
"""
BoAt Platform — Wireshark extcap plugin

Streams a running BoAt gateway's unified FrameService.SubscribeFrames feed
into Wireshark as a live capture source, reusing the exact same PCAPNG
encoding boat.pcapng already uses for file export/live recording (see
sdk/python/boat/pcapng.py, ui/recorder.py) -- no new frame encoding here.

CAN and CAN-FD frames are encoded as DLT_CAN_SOCKETCAN, Ethernet as
DLT_EN10MB. PDU and TCP bus types are NOT captured: they have no wire/
link-layer representation (same call TraceReplayer.export_to_pcapng()
already made for file export -- see sdk/python/boat/trace_replay.py).

Install: copy this file into Wireshark's personal extcap folder (see
docs/testing/wireshark-integration-verification.md for exact paths per
OS), and make sure it's executable. Wireshark then invokes it directly
per the extcap CLI protocol -- this is not meant to be run by hand except
for the manual protocol-compliance checks in that same doc.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "boat-platform" / "sdk" / "python"))

import grpc  # noqa: E402

from boat.client import BoAtClient  # noqa: E402
from boat.pcapng import PcapngWriter, DLT_CAN_SOCKETCAN, DLT_EN10MB  # noqa: E402
from boat.v1 import frame_pb2  # noqa: E402

INTERFACE_VALUE = "boat-gateway"
INTERFACE_DISPLAY = "BoAt Gateway (live FrameService)"

_BUS_TYPE_MAP = {
    "can": frame_pb2.Frame.CAN,
    "canfd": frame_pb2.Frame.CANFD,
    "ethernet": frame_pb2.Frame.ETHERNET,
}


def _print_extcap_interfaces() -> None:
    print("extcap {version=1.0}{display=BoAt Gateway}")
    print(f"interface {{value={INTERFACE_VALUE}}}{{display={INTERFACE_DISPLAY}}}")


def _print_extcap_dlts() -> None:
    # The actual capture written to the fifo is a real multi-interface
    # PCAPNG stream (CAN interfaces at DLT_CAN_SOCKETCAN, Ethernet ones at
    # DLT_EN10MB) -- these two lines just advertise what's possible.
    print(f"dlt {{number={DLT_CAN_SOCKETCAN}}}{{name=CAN_SOCKETCAN}}{{display=CAN over SocketCAN}}")
    print(f"dlt {{number={DLT_EN10MB}}}{{name=EN10MB}}{{display=Ethernet}}")


def _print_extcap_config() -> None:
    print('arg {number=0}{call=--host}{display=Gateway address}{type=string}'
          '{default=localhost:50051}{tooltip=host:port of the boat_gateway to subscribe to}')
    print('arg {number=1}{call=--bus-types}{display=Bus types}{type=string}'
          '{default=can,canfd,ethernet}{tooltip=Comma-separated: can,canfd,ethernet (PDU/TCP have no wire encoding, not capturable here)}')
    print('arg {number=2}{call=--iface-filter}{display=Interface filter}{type=string}'
          '{default=}{tooltip=Only capture this gateway-side interface name (empty = all)}')


def _parse_bus_types(raw: str) -> list[int]:
    bus_types: list[int] = []
    for name in raw.split(","):
        name = name.strip().lower()
        if not name:
            continue
        bt = _BUS_TYPE_MAP.get(name)
        if bt is None:
            print(f"boat_extcap: ignoring unknown/unsupported bus type '{name}' "
                  f"(supported: can, canfd, ethernet)", file=sys.stderr)
            continue
        bus_types.append(bt)
    return bus_types


def _run_capture(fifo: str, host: str, bus_types_raw: str, iface_filter: str) -> int:
    writer = PcapngWriter(fifo)
    iface_ids: dict[str, int] = {}
    stop = {"requested": False}
    client = None
    stream = None

    def _handle_sigterm(_signum, _frame) -> None:
        stop["requested"] = True
        if stream is not None:
            try:
                stream.cancel()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        client = BoAtClient(address=host)
        req = frame_pb2.SubscribeFramesRequest()
        if iface_filter:
            req.iface_filter = iface_filter
        for bt in _parse_bus_types(bus_types_raw):
            req.bus_types.append(bt)

        stream = client.frame.SubscribeFrames(req)
        for frame in stream:
            if stop["requested"]:
                break

            ts = frame.timestamp_ns / 1e9 if frame.timestamp_ns else time.time()

            if frame.HasField("can"):
                iface_id = iface_ids.get(frame.iface)
                if iface_id is None:
                    iface_id = writer.add_interface(frame.iface or "can", DLT_CAN_SOCKETCAN)
                    iface_ids[frame.iface] = iface_id
                writer.write_can(iface_id, ts, frame.can.can_id, frame.can.dlc,
                                 bytes(frame.payload[:frame.can.dlc]), frame.can.flags)
            elif frame.HasField("eth"):
                iface_id = iface_ids.get(frame.iface)
                if iface_id is None:
                    iface_id = writer.add_interface(frame.iface or "eth", DLT_EN10MB)
                    iface_ids[frame.iface] = iface_id
                writer.write_eth(iface_id, ts, bytes(frame.eth.dst_mac), bytes(frame.eth.src_mac),
                                 frame.eth.ethertype, bytes(frame.payload))
            # TCP/PDU frames: no wire encoding, silently skipped -- same
            # boundary TraceReplayer.export_to_pcapng() already draws.
    except grpc.RpcError as ex:
        if ex.code() != grpc.StatusCode.CANCELLED:
            print(f"boat_extcap: RPC error [{ex.code().name}]: {ex.details()}", file=sys.stderr)
            return 1
    finally:
        writer.close()
        if client is not None:
            client.close()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--extcap-interfaces", action="store_true")
    parser.add_argument("--extcap-dlts", action="store_true")
    parser.add_argument("--extcap-config", action="store_true")
    parser.add_argument("--extcap-reload-option")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--extcap-interface", default="")
    parser.add_argument("--extcap-version", default="")
    parser.add_argument("--extcap-capture-filter", default="")
    parser.add_argument("--fifo", default="")
    parser.add_argument("--host", default="localhost:50051")
    parser.add_argument("--bus-types", default="can,canfd,ethernet")
    parser.add_argument("--iface-filter", default="")
    args, _unknown = parser.parse_known_args()

    if args.extcap_interfaces:
        _print_extcap_interfaces()
        return 0

    if args.extcap_dlts:
        _print_extcap_dlts()
        return 0

    if args.extcap_config:
        _print_extcap_config()
        return 0

    if args.capture:
        if not args.fifo:
            print("boat_extcap: --capture requires --fifo", file=sys.stderr)
            return 1
        return _run_capture(args.fifo, args.host, args.bus_types, args.iface_filter)

    # No recognized action -- print interfaces as a harmless default,
    # matching common extcap script behavior when invoked bare.
    _print_extcap_interfaces()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
