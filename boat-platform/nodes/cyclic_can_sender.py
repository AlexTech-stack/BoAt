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

Argument parsing lives in build_parser(), separate from main(), by
convention: ui/launcher_agent.py's node discovery imports this module
(without running main()) and introspects build_parser()'s actions to show
one input field per argument in admin_gui's New Node dialog, with each
argument's help text and default as a placeholder/example. A script
without a module-level build_parser() still works fine everywhere else --
admin_gui just falls back to one flat free-text "Extra args" field for it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.frame_node import FrameNode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BoAt cyclic CAN sender node")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0", help="CAN interface to send on")
    parser.add_argument("--can-id", default="0x300", help="CAN ID, hex (0x..) or decimal")
    parser.add_argument("--data", default="", help="Payload as hex bytes, e.g. AABBCCDD (empty = 0-byte frame)")
    parser.add_argument("--cycle-ms", type=int, default=1000, help="Send interval in milliseconds")
    parser.add_argument("--fd", action="store_true", help="Send as CAN FD")
    parser.add_argument("--brs", action="store_true", help="Set the Bit Rate Switch flag (only meaningful with --fd)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

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
    cycle_s = args.cycle_ms / 1000.0
    while not stop:
        cycle_start = time.monotonic()
        node.send_can(args.iface, can_id, payload, is_fd=args.fd, flags=flags)
        # Deadline is relative to when THIS cycle started, not to when
        # send_can() returned -- send_can() is a synchronous gRPC call
        # (loopback round trip + protobuf serialization), a few ms even
        # locally, and computing the deadline afterward silently stretches
        # every cycle by that amount. Same class of bug the CanTp plugin's
        # TX thread had before it was fixed earlier this session to use a
        # deadline computed before the send, not after.
        deadline = cycle_start + cycle_s
        while not stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Sleep the precise remaining time (capped at 50ms so shutdown
            # stays responsive) instead of always sleeping a full 50ms
            # chunk -- that would round the final wait up by as much as
            # 50ms on top of everything else.
            time.sleep(min(remaining, 0.05))

    sys.exit(0)


if __name__ == "__main__":
    main()
