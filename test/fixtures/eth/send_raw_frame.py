#!/usr/bin/env python3
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Send one Ethernet frame on a raw AF_PACKET socket.

Used by TC_Ethernet_002 to inject a frame from *outside* the gateway without
depending on scapy or tcpreplay. Requires root or CAP_NET_RAW (the same
privilege the raw Ethernet driver/replay path needs).

Usage:
    sudo python3 test/fixtures/eth/send_raw_frame.py \
        --iface veth1 --src 02:00:00:00:00:01 --dst 02:00:00:00:00:02 \
        --ethertype 0x88B5 --data DEADBEEF
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys


def _mac(text: str) -> bytes:
    parts = text.split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError(f"not a MAC address: {text!r}")
    try:
        return bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a MAC address: {text!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iface", required=True, help="interface to send on")
    parser.add_argument("--src", required=True, type=_mac, help="source MAC")
    parser.add_argument("--dst", required=True, type=_mac, help="destination MAC")
    parser.add_argument("--ethertype", default="0x88B5", help="EtherType, hex or decimal")
    parser.add_argument("--data", default="", help="payload bytes, hex (no separators)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        ethertype = int(args.ethertype, 0)
    except ValueError:
        sys.stderr.write(f"invalid --ethertype: {args.ethertype!r}\n")
        return 2
    try:
        payload = bytes.fromhex(args.data)
    except ValueError:
        sys.stderr.write(f"invalid --data (expected hex): {args.data!r}\n")
        return 2

    frame = args.dst + args.src + struct.pack(">H", ethertype) + payload
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        try:
            sock.bind((args.iface, 0))
            sock.send(frame)
        finally:
            sock.close()
    except PermissionError:
        sys.stderr.write("permission denied: run as root or grant CAP_NET_RAW\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"send failed on {args.iface}: {exc}\n")
        return 1

    print(f"sent {len(frame)} bytes on {args.iface} (EtherType 0x{ethertype:04X})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
