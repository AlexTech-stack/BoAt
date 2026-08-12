#!/usr/bin/env python3
"""Cyclic CAN sender -- sends one configurable CAN(FD) frame on a fixed interval.

A general-purpose node: point it at a bus/frame/cycle via CLI flags, no
demo-specific trigger logic. Useful as a simple periodic traffic generator
for testing other tools (CanTp, PDU routing, dashboards, ...) without
writing a one-off script each time.

Usage:
    python3 nodes/cyclic_can_sender.py --iface vcan0 --can-id 0x300 --data AABBCCDD
    python3 nodes/cyclic_can_sender.py --iface vcan0 --can-id 0x300 --data AABBCCDD \
        --cycle-ms 200 --fd --brs

Gateway address resolution -- same order as the CLI/SDK everywhere else in
this repo: --address flag > BOAT_HOST env var > localhost:50051. --address
defaults to None (not a hardcoded string) specifically so that omitting it
lets BOAT_HOST decide; ui/control_panel.py's "Nodes" web UI still works
unmodified since it always passes --address explicitly (its own gateway
field), while a BOAT_HOST-env-var-driven launcher can omit it entirely.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.frame_node import FrameNode


def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt cyclic CAN sender node")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0", help="CAN interface to send on")
    parser.add_argument("--can-id", default="0x300", help="CAN ID, hex (0x..) or decimal")
    parser.add_argument("--data", default="", help="Payload as hex bytes, e.g. AABBCCDD (empty = 0-byte frame)")
    parser.add_argument("--cycle-ms", type=int, default=1000, help="Send interval in milliseconds")
    parser.add_argument("--fd", action="store_true", help="Send as CAN FD")
    parser.add_argument("--brs", action="store_true", help="Set the Bit Rate Switch flag (only meaningful with --fd)")
    args = parser.parse_args()

    can_id = int(args.can_id, 0)
    payload = bytes.fromhex(args.data) if args.data else b""
    flags = 0
    if args.fd:
        flags |= 0x04  # FDF
        if args.brs:
            flags |= 0x01  # BRS
    elif args.brs:
        print("[cyclic-can-sender] --brs only applies with --fd; ignoring", file=sys.stderr)

    node = FrameNode(args.address)
    stop = False

    def shutdown() -> None:
        nonlocal stop
        print("\n[cyclic-can-sender] shutting down…")
        stop = True

    import signal
    signal.signal(signal.SIGINT, lambda *_: shutdown())
    signal.signal(signal.SIGTERM, lambda *_: shutdown())

    print(f"[cyclic-can-sender] sending 0x{can_id:X} on {args.iface} every {args.cycle_ms} ms "
          f"(fd={args.fd}, brs={args.brs}, data={payload.hex(':') or '(empty)'})")
    while not stop:
        node.send_can(args.iface, can_id, payload, is_fd=args.fd, flags=flags)
        # Sleep in small increments so shutdown is responsive even for a long cycle.
        deadline = time.monotonic() + args.cycle_ms / 1000.0
        while not stop and time.monotonic() < deadline:
            time.sleep(0.05)

    sys.exit(0)


if __name__ == "__main__":
    main()
